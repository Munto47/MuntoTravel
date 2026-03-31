"""
agents.py —— 三个专家 Agent（无 LLM）

核心改进（v2，AMAP v5 升级版）：
  1. POI 搜索升级到高德 v5 API（/v5/place/text）：
     - 获取每个 POI 的评分（biz_ext.rating）、营业时间、坐标
     - 上下文格式升级为："灵隐寺（⭐4.8，07:00-18:30）"
     - 将搜索结果坐标写入 poi_coords 状态字段，供 route_node 预加载缓存
       → 避免 route_node 为相同地点重复发 geocode 请求
  2. notes 解析器：将用户备注转化为 POI 查询的「禁用关键词」和「偏好关键词」
  3. notes 传递给 weather_agent 和 poi_agent，planner 也能看到完整 notes
"""

import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from dotenv import load_dotenv

from .logger import get_logger
from .schemas import AgentLog
from .transport_client import get_transport_options
from .weather_client import get_weather

load_dotenv()
logger = get_logger(__name__)


# ── POI 数据结构（v5 富信息）────────────────────────────────────────────────

@dataclass
class POIItem:
    """高德 v5 POI 搜索结果的结构化表示。"""
    name: str
    coord: str       # "lng,lat"，来自 v5 location 对象；空字符串表示无坐标
    rating: str      # "4.8"，来自 biz_ext.rating；空字符串表示无评分
    open_time: str   # "08:00-18:00"，来自 biz_ext.open_time；空字符串表示无营业时间


# ── 高德 POI 分类关键词 ────────────────────────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, str] = {
    "历史文化": "历史博物馆|名胜古迹|文化遗址|纪念馆|古城|历史街区|文化博物馆",
    "自然风景": "国家公园|自然风景区|湿地公园|森林公园|地质公园|风景名胜区",
    "美食探索": "网红餐厅|当地特色小吃|老字号餐厅|美食街|特色风味餐厅|人气餐厅",
    "购物":     "购物中心|步行街|特色市集|创意市集|商业街|文创商店",
    "夜生活":   "夜市|夜游|夜景观景台|酒吧街|夜间演出|特色夜宵",
    "亲子游":   "动物园|亲子乐园|儿童科技馆|亲子农场|主题乐园|自然教育基地",
}


# ── 备注解析规则 ──────────────────────────────────────────────────────────────

_NOTES_RULES: list[tuple[list[str], list[str], list[str]]] = [
    # (触发词, avoid_keywords, prefer_keywords)
    (["老人", "老年", "年迈", "不能爬", "爬不了", "行动不便"],
     ["登山", "爬山", "山顶", "陡坡", "高强度徒步", "攀岩"],
     ["无障碍", "平坦", "休闲", "慢游"]),

    (["轮椅", "残疾", "残障"],
     ["台阶", "爬山", "登山"],
     ["无障碍通道", "轮椅友好"]),

    (["素食", "纯素", "吃素", "不吃肉", "只吃蔬菜", "蔬菜"],
     ["烤肉", "火锅", "烧烤", "肉食", "海鲜大排档"],
     ["素食餐厅", "蔬食", "素斋", "轻食", "素菜"]),

    (["海鲜过敏", "不吃海鲜", "海鲜禁忌", "对海鲜过敏"],
     ["海鲜楼", "生蚝", "螃蟹", "龙虾"],
     []),

    (["清真", "伊斯兰", "穆斯林", "不吃猪"],
     ["猪肉", "烤猪"],
     ["清真餐厅", "清真"]),

    (["孩子", "小孩", "儿童", "宝宝", "婴儿"],
     ["刺激", "恐怖", "危险"],
     ["亲子", "儿童友好", "家庭"]),

    (["预算有限", "省钱", "穷游", "经济实惠"],
     ["高端", "奢华", "米其林"],
     ["平价", "实惠", "性价比"]),
]


def _parse_notes(notes: str) -> dict:
    """将用户备注解析为 POI 查询的关键词修改建议。"""
    if not notes:
        return {"avoid": [], "prefer": [], "summary": ""}

    notes_lower = notes.lower()
    avoid: list[str] = []
    prefer: list[str] = []
    matched: list[str] = []

    for triggers, avd, prf in _NOTES_RULES:
        if any(t in notes_lower for t in triggers):
            avoid.extend(avd)
            prefer.extend(prf)
            matched.append(triggers[0])

    summary = f"备注限制条件：{', '.join(matched)}" if matched else ""
    return {"avoid": list(set(avoid)), "prefer": list(set(prefer)), "summary": summary}


# ── 高德 POI v5 查询 ──────────────────────────────────────────────────────────

_AMAP_POI_URL = "https://restapi.amap.com/v5/place/text"


def _build_poi_keywords(category: str, prefer: list[str]) -> str:
    """将 notes 偏好关键词注入到原始类别关键词中，提升搜索相关性。"""
    base = _CATEGORY_KEYWORDS.get(category, category)
    if prefer:
        extra = "|".join(prefer[:3])
        return f"{base}|{extra}"
    return base


def _parse_v5_location(loc_field) -> str:
    """
    解析 v5 POI 的 location 字段为 'lng,lat' 字符串。
    v5 返回对象格式 {"lng": "114.8", "lat": "27.8"}；v3 是字符串 "114.8,27.8"。
    两种格式都兼容处理。
    """
    if not loc_field:
        return ""
    if isinstance(loc_field, dict):
        lng = loc_field.get("lng", "")
        lat = loc_field.get("lat", "")
        if lng and lat:
            return f"{lng},{lat}"
    elif isinstance(loc_field, str) and "," in loc_field:
        return loc_field
    return ""


async def _query_amap_poi(
    city: str,
    category: str,
    api_key: str,
    prefer: list[str],
    avoid: list[str],
) -> list[POIItem] | None:
    """
    高德 POI v5 搜索：
      - /v5/place/text：返回评分、营业时间、坐标等富信息
      - sortrule=1：综合热度排序（高人气优先，接近"榜单"效果）
      - 注入 prefer 关键词让搜索更贴合用户需求
      - 对结果进行 avoid 过滤，排除不适合的场所

    与 v3 的主要区别：
      - 分页参数：page_size + page_num（替代 offset + page）
      - location 字段：对象格式（替代字符串）
      - 默认返回更丰富的 biz_ext 信息（评分、营业时间）
    """
    kw = _build_poi_keywords(category, prefer)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(_AMAP_POI_URL, params={
                "key":       api_key,
                "keywords":  kw,
                "city":      city,
                "page_size": 20,    # v5 分页参数（v3 是 offset）
                "page_num":  1,
                "sortrule":  1,     # 综合热度排序
                "output":    "json",
            })
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") == "1" and data.get("pois"):
            results: list[POIItem] = []
            for poi in data["pois"]:
                name = poi.get("name", "")
                if not name:
                    continue

                # 过滤 avoid 关键词
                if avoid and any(a in name for a in avoid):
                    logger.debug("[POI] 过滤场所（备注限制）: %s", name)
                    continue

                coord     = _parse_v5_location(poi.get("location"))
                biz_ext   = poi.get("biz_ext") or {}
                rating    = biz_ext.get("rating") or ""
                open_time = biz_ext.get("open_time") or ""

                results.append(POIItem(
                    name=name,
                    coord=coord,
                    rating=rating,
                    open_time=open_time,
                ))
                if len(results) >= 8:
                    break

            return results if results else None

    except Exception as e:
        logger.warning("[POI] AMAP v5 查询失败 %s·%s: %s", city, category, e)
    return None


# ── 本地 POI 数据库（降级用）────────────────────────────────────────────────────

_LOCAL_POI_DB: dict[str, dict[str, list[str]]] = {
    "杭州": {
        "历史文化": ["西湖（断桥残雪、雷峰塔）", "灵隐寺", "岳王庙", "河坊街", "胡雪岩故居", "南宋御街"],
        "自然风景": ["西湖游船", "西溪湿地公园", "九溪十八涧", "植物园"],
        "美食探索": ["楼外楼（西湖醋鱼）", "知味观（叫花鸡）", "南宋御街小吃", "胡庆余堂奎元馆"],
        "购物":     ["湖滨银泰", "武林广场商圈", "丝绸文化街"],
        "夜生活":   ["西湖夜游船", "湖滨路夜市", "吴山广场夜市"],
        "亲子游":   ["杭州动物园", "西溪湿地亲子营地", "浙江自然博物院", "杭州乐园"],
    },
    "北京": {
        "历史文化": ["故宫博物院", "天坛公园", "颐和园", "长城（慕田峪）", "南锣鼓巷", "798艺术区"],
        "自然风景": ["香山公园", "奥林匹克森林公园", "植物园"],
        "美食探索": ["全聚德烤鸭（前门店）", "簋街小龙虾", "护国寺小吃", "大栅栏老字号"],
        "购物":     ["王府井步行街", "三里屯太古里", "南锣鼓巷手工艺"],
        "夜生活":   ["后海酒吧街", "什刹海夜景", "长安街夜景"],
        "亲子游":   ["北京动物园（大熊猫）", "中国科技馆", "北京自然博物馆"],
    },
    "成都": {
        "历史文化": ["武侯祠", "锦里古街", "杜甫草堂", "宽窄巷子", "金沙遗址博物馆"],
        "自然风景": ["都江堰", "青城山", "西岭雪山", "龙泉山城市森林公园"],
        "美食探索": ["成都火锅（大龙燚/海底捞）", "夫妻肺片（老字号）", "担担面", "钟水饺"],
        "购物":     ["春熙路IFS", "太古里", "锦里手工艺"],
        "夜生活":   ["锦里夜景", "东郊记忆文创园", "玉林路小酒馆"],
        "亲子游":   ["大熊猫繁育研究基地", "成都动物园", "欢乐谷"],
    },
    "上海": {
        "历史文化": ["外滩建筑群", "豫园", "田子坊", "新天地石库门", "武康路历史建筑"],
        "自然风景": ["世纪公园", "辰山植物园", "滨江大道"],
        "美食探索": ["南翔小笼（豫园店）", "蟹黄汤包（绿波廊）", "城隍庙小吃街", "美罗城周边"],
        "购物":     ["南京路步行街", "徐家汇商圈", "K11艺术购物中心"],
        "夜生活":   ["外滩灯光秀", "陆家嘴天际线", "黄浦江夜游"],
        "亲子游":   ["上海科技馆", "上海海洋水族馆", "迪士尼乐园"],
    },
    "西安": {
        "历史文化": ["兵马俑", "华清宫", "古城墙", "大雁塔", "碑林博物馆", "陕西历史博物馆"],
        "自然风景": ["华山", "骊山国家森林公园"],
        "美食探索": ["肉夹馍（老童家）", "羊肉泡馍", "biangbiang面", "回民街小吃"],
        "购物":     ["大唐不夜城", "书院门古玩街", "钟鼓楼商圈"],
        "夜生活":   ["大唐不夜城夜游", "城墙灯光秀", "曲江夜市"],
        "亲子游":   ["陕西历史博物馆", "秦岭野生动物园", "西安欢乐谷"],
    },
    "重庆": {
        "历史文化": ["磁器口古镇", "三峡博物馆", "解放碑步行街", "湖广会馆"],
        "自然风景": ["武隆天坑地缝", "缙云山", "南山植物园"],
        "美食探索": ["重庆火锅（刘一手/德庄）", "酸辣粉（小面馆）", "洪崖洞夜市"],
        "购物":     ["解放碑商圈", "观音桥步行街"],
        "夜生活":   ["洪崖洞灯光秀", "南山一棵树观景台夜景", "长嘉汇夜市"],
        "亲子游":   ["重庆动物园（大熊猫）", "重庆科技馆", "欢乐谷"],
    },
}

_DEFAULT_POI: dict[str, list[str]] = {
    "历史文化": ["当地著名博物馆", "历史街区"],
    "自然风景": ["城市公园", "近郊风景区"],
    "美食探索": ["当地特色餐厅（问询酒店前台推荐）", "夜市小吃街"],
    "购物":     ["市中心商业区"],
    "夜生活":   ["城市夜景观景台"],
    "亲子游":   ["动物园", "科技馆"],
}


def _format_poi_context(category: str, items: list[POIItem]) -> str:
    """
    将 POIItem 列表格式化为 LLM 友好的上下文文本。
    有评分/营业时间时附加元信息，帮助 LLM 做更好的推荐。
    例："历史文化：灵隐寺（⭐4.8，07:00-18:30）、西湖断桥（⭐4.9，全天）"
    """
    formatted = []
    for poi in items[:6]:
        meta = []
        if poi.rating:
            meta.append(f"⭐{poi.rating}")
        if poi.open_time:
            meta.append(poi.open_time)
        label = poi.name + (f"（{'，'.join(meta)}）" if meta else "")
        formatted.append(label)
    return f"  {category}：{'、'.join(formatted)}"


# ── 专家 Agent 函数 ───────────────────────────────────────────────────────────

async def weather_agent(state: dict) -> dict:
    """天气专员：获取目的地天气，notes 传递给 planner 增强上下文。"""
    city: str = state["city"]
    days: int = state["days"]
    notes: str = state.get("notes", "")
    t0 = time.perf_counter()
    logger.info("[WeatherAgent] 开始查询：%s %d天", city, days)

    result = await get_weather(city, days)
    ms = int((time.perf_counter() - t0) * 1000)

    if result:
        text = result.to_prompt_text()
        if notes:
            text += f"\n  ⚠️ 用户备注（请规划时结合天气数据参考）：{notes}"
        log = AgentLog(
            agent="weather", label="天气专员", icon="🌤️",
            status="ok", duration_ms=ms,
            detail=f"{city} {len(result.days)}天 · {result.source}",
            source=result.source,
        )
        logger.info("[WeatherAgent] 完成 (%dms): %s %d天 · %s", ms, city, len(result.days), result.source)
    else:
        text = f"未能获取{city}的实时天气数据，请根据季节特征推测，并在 tips 中提醒查询天气预报。"
        log = AgentLog(
            agent="weather", label="天气专员", icon="🌤️",
            status="warn", duration_ms=ms,
            detail=f"{city} · 天气数据不可用，将使用季节推测",
        )
        logger.warning("[WeatherAgent] 失败 (%dms): %s 天气数据不可用", ms, city)

    return {
        "context_pieces": [f"【天气信息】\n{text}"],
        "agent_logs": [log.model_dump()],
    }


async def poi_agent(state: dict) -> dict:
    """
    景点专员（v5 升级版）：
      - 使用高德 v5 API 获取 POI 评分、营业时间和坐标
      - poi_coords：将搜索结果坐标写入状态，route_node 预加载缓存用
        → 同一地点无需再次 geocode，减少 API 请求并提升速度
      - 上下文格式升级为带富信息版："景点名（⭐评分，营业时间）"
    """
    city: str            = state["city"]
    preferences: list[str] = state["preferences"]
    notes: str           = state.get("notes", "")
    t0 = time.perf_counter()

    notes_hints = _parse_notes(notes)
    avoid  = notes_hints["avoid"]
    prefer = notes_hints["prefer"]
    if notes_hints["summary"]:
        logger.info("[POIAgent] 备注解析：%s → avoid=%s prefer=%s",
                    notes_hints["summary"], avoid, prefer)

    logger.info("[POIAgent] 开始查询：%s · %s", city, " / ".join(preferences))

    api_key     = os.getenv("AMAP_API_KEY", "").strip()
    city_db     = _LOCAL_POI_DB.get(city, _DEFAULT_POI)
    context_lines: list[str]    = []
    poi_coords:    dict[str, str] = {}   # name → "lng,lat"，供 route_node 预加载
    source_used = "内置数据库"
    amap_count  = 0

    for category in preferences:
        items: list[POIItem] | None = None

        if api_key:
            items = await _query_amap_poi(city, category, api_key, prefer, avoid)
            if items:
                source_used = "高德地图 POI v5"
                amap_count += 1
                for poi in items:
                    if poi.coord and poi.name not in poi_coords:
                        poi_coords[poi.name] = poi.coord

        if not items:
            # 本地库降级：创建无富信息的 POIItem
            raw      = city_db.get(category, _DEFAULT_POI.get(category, []))
            filtered = [i for i in raw if not any(a in i for a in avoid)] or raw[:6]
            items    = [POIItem(name=n, coord="", rating="", open_time="") for n in filtered]

        context_lines.append(_format_poi_context(category, items))

    ms = int((time.perf_counter() - t0) * 1000)

    text_parts = [f"【{city} 景点餐厅推荐 · 来源：{source_used}】"]
    text_parts.extend(context_lines)
    if notes_hints["summary"]:
        text_parts.append(f"  ⚠️ {notes_hints['summary']}，Planner 请在推荐中遵守此限制")
    text = "\n".join(text_parts)

    fail_count  = len(preferences) - amap_count
    coord_count = len(poi_coords)
    status = "ok" if fail_count == 0 else ("warn" if amap_count > 0 else "ok")
    log = AgentLog(
        agent="poi", label="景点专员", icon="📍",
        status=status, duration_ms=ms,
        detail=f"{city} {len(preferences)}类 · {source_used} · 坐标{coord_count}个",
        source=source_used,
    )
    logger.info("[POIAgent] 完成 (%dms): %d类 · %s（高德%d/本地%d）· 坐标%d个%s",
                ms, len(preferences), source_used, amap_count, fail_count, coord_count,
                f" | {notes_hints['summary']}" if notes_hints["summary"] else "")

    return {
        "context_pieces": [text],
        "poi_coords":     poi_coords,
        "agent_logs":     [log.model_dump()],
    }


async def transport_agent(state: dict) -> dict:
    """交通专员：获取城市间交通方案（自驾 + 列车）。"""
    origin: str = state["origin"]
    city: str   = state["city"]
    t0 = time.perf_counter()
    logger.info("[TransportAgent] 开始规划：%s → %s", origin, city)

    result = await get_transport_options(origin, city)
    ms = int((time.perf_counter() - t0) * 1000)

    if result:
        text = result.to_prompt_text()
        log = AgentLog(
            agent="transport", label="交通专员", icon="🚄",
            status="ok", duration_ms=ms,
            detail=f"{origin}→{city} · 自驾{len(result.drive_options)}方案 / 列车{len(result.train_options)}类",
            source=result.data_source,
        )
        logger.info("[TransportAgent] 完成 (%dms): %s→%s · %s",
                    ms, origin, city, result.data_source)
        return {
            "context_pieces": [text],
            "transport_result": result.to_dict(),
            "agent_logs": [log.model_dump()],
        }
    else:
        log = AgentLog(
            agent="transport", label="交通专员", icon="🚄",
            status="skip", duration_ms=ms,
            detail=f"{origin}→{city} · 无数据",
        )
        logger.warning("[TransportAgent] 无结果 (%dms): %s→%s", ms, origin, city)
        return {"agent_logs": [log.model_dump()]}
