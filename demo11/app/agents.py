"""
agents.py —— 三个专家 Agent（无 LLM）

POI：按「住宿 / 景点 / 餐饮」调用高德 POI 2.0（app.amap.poi_service），
     填充 rich_poi_catalog、poi_coords、district_hint；无 Key 时回退本地示例库。
"""

import asyncio
import hashlib
import os
import re
import time
from collections import defaultdict

from dotenv import load_dotenv

from .amap.poi_service import (
    POICategory,
    category_label_cn,
    richpoi_to_dict,
    search_pois_merged_pages,
)
from .amap.static_map import build_static_map_url
from .logger import get_logger
from .schemas import AgentLog
from .transport_client import get_transport_options
from .weather_client import get_weather

load_dotenv()
logger = get_logger(__name__)


# ── 兴趣偏好关键词（用于备注解析）──────────────────────────────────────────────

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


def _detect_travel_style(notes: str, profile_note: str, budget_level: str) -> str:
    """结合预算与自由文本，粗分档次，驱动 POI 关键词与检索翻页。"""
    if budget_level == "high":
        return "luxury"
    if budget_level == "low":
        return "economy"
    text = f"{notes} {profile_note}"
    lux = (
        "奢华", "豪华", "高端", "顶奢", "五星", "米其林", "私享", "度假村",
        "别墅", "景观套房", "行政酒廊",
    )
    eco = (
        "简约", "极简", "穷游", "省钱", "轻装", "学生", "经济型", "青旅",
        "背包", "平价", "性价比", "省吃俭用",
    )
    if any(w in text for w in lux):
        return "luxury"
    if any(w in text for w in eco):
        return "economy"
    return "balanced"


def _build_hotel_keywords(budget_level: str, style: str) -> str:
    if style == "luxury" or budget_level == "high":
        return "豪华酒店|五星酒店|精品酒店|度假酒店|国际酒店|套房"
    if style == "economy" or budget_level == "low":
        return "经济型酒店|快捷酒店|青年旅舍|民宿|平价宾馆|公寓酒店"
    return "商务酒店|舒适型|连锁酒店|亚朵|全季|智选假日"


def _build_restaurant_keywords(
    budget_level: str, style: str, prefer: list[str], preferences: list[str],
) -> str:
    parts: list[str] = []
    if style == "luxury" or budget_level == "high":
        parts.append("高端餐厅|黑珍珠|景观餐厅|私房菜|精品料理")
    elif style == "economy" or budget_level == "low":
        parts.append("小吃|面馆|快餐|平价|食堂|夜市|早餐店")
    else:
        parts.append("本帮菜|家常菜|人气餐厅|特色菜|网红店")
    for p in preferences:
        if p in ("美食探索", "购物", "夜生活"):
            parts.append("美食|夜宵|商圈美食")
            break
    if prefer:
        parts.append("|".join(prefer[:6]))
    return "|".join(parts)[:200]


def _build_attraction_keywords(preferences: list[str]) -> str:
    chunks: list[str] = []
    for p in preferences[:6]:
        c = _CATEGORY_KEYWORDS.get(p, "")
        if c:
            chunks.append(c)
    return "|".join(chunks)[:200]


def _truncate_for_prompt(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _search_seed(city: str, notes: str, profile: str, budget: str, prefs: tuple[str, ...]) -> int:
    """与进程无关的稳定整数，便于同一组输入复现分页与本地轮换。"""
    raw = "|".join((city, notes, profile, budget, ",".join(prefs))).encode("utf-8")
    return int(hashlib.md5(raw).hexdigest()[:12], 16)


def _page_triple(seed: int) -> tuple[int, int, int]:
    """住宿 / 景点 / 餐饮使用不同分页，减少每次命中同一批默认结果。"""
    return (
        1 + (seed % 3),
        1 + ((seed // 3) % 3),
        1 + ((seed // 11) % 3),
    )


def _page_pair(primary: int) -> list[int]:
    """主分页 + 相邻页合并检索，扩大候选。"""
    p = max(1, min(int(primary), 5))
    q = p + 1 if p < 5 else 1
    if q == p:
        q = 2
    return [p, q]


def _merge_kw(base: str, extra: str, max_len: int = 200) -> str:
    extra = (extra or "").strip()
    if not extra:
        return base[:max_len]
    if extra in base:
        return base[:max_len]
    return f"{base}|{extra}"[:max_len]


_PROFILE_SKIP_PREFIXES = (
    "用户画像", "出行节奏", "体验深度", "社交风格", "消费风格", "体力水平",
    "【用户画像】", "- ", "• ",
)


def _extract_user_phrases_for_search(notes: str, profile_note: str) -> str:
    """从备注与画像中抽短句，拼入高德 keywords（以 | 分隔），不依赖规则表。"""
    text = f"{notes} {profile_note}"
    parts = re.split(r"[\n，。；;、\|]+", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 2 or len(p) > 36:
            continue
        if any(p.startswith(x) for x in _PROFILE_SKIP_PREFIXES):
            continue
        if re.match(r"^Q\d+[A-Z]$", p):
            continue
        out.append(p)
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    if not uniq:
        return ""
    return "|".join(uniq[:5])[:90]


def _lodging_mentioned(notes: str, profile_note: str) -> bool:
    t = f"{notes} {profile_note}"
    return any(k in t for k in ("住", "酒店", "民宿", "公寓", "客栈", "宿", "入住"))


def _scenic_user_hint(notes: str, profile_note: str, user_kw: str) -> bool:
    """是否把用户原话并入景点检索（避免「想吃火锅」污染景点）。"""
    t = f"{notes} {profile_note}"
    if any(x in t for x in ("景点", "博物馆", "公园", "古镇", "拍照", "打卡", "徒步", "登山", "观景")):
        return True
    return any(x in user_kw for x in ("馆", "公园", "园", "湖", "山", "寺", "塔", "古镇", "湿地", "森林"))


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


def _empty_row(name: str, category_cn: str) -> dict:
    return {
        "name": name,
        "category": category_cn,
        "poi_id": "",
        "address": "",
        "location": "",
        "entr_location": "",
        "rating": "",
        "cost": "",
        "opentime_today": "",
        "tel": "",
        "tag": "",
        "typecode": "",
        "citycode": "",
        "adcode": "",
        "adname": "",
        "business_area": "",
    }


def _district_hint_from_catalog(rows: list[dict]) -> str:
    by_ad: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        ad = (r.get("adname") or r.get("adcode") or "").strip() or "未分区"
        name = (r.get("name") or "").strip()
        if name:
            by_ad[ad].append(name)
    if not by_ad:
        return ""
    lines = []
    for ad, names in list(by_ad.items())[:10]:
        uniq = list(dict.fromkeys(names))[:6]
        lines.append(f"  - {ad}：{'、'.join(uniq)}")
    return "【区域分布提示】同一日尽量将餐饮与景点安排在相邻行政区，减少跨区往返：\n" + "\n".join(lines)


def _local_fallback_three_cat(
    city: str,
    avoid: list[str],
    *,
    budget_level: str = "medium",
    preferences: list[str] | None = None,
    notes: str = "",
    profile_note: str = "",
) -> tuple[list[dict], list[str], dict[str, str]]:
    """无 AMAP Key 时：用内置示例库拼住宿/景点/餐饮说明（无坐标），并按画像轮换条目。"""
    preferences = preferences or ["历史文化", "自然风景", "美食探索"]
    city_db = _LOCAL_POI_DB.get(city, {})
    defaults = _DEFAULT_POI
    style = _detect_travel_style(notes, profile_note, budget_level)
    seed = _search_seed(city, notes, profile_note, budget_level, tuple(preferences))

    def take(cat_key: str, n: int) -> list[str]:
        raw = city_db.get(cat_key, defaults.get(cat_key, []))
        got = [x for x in raw if not any(a in x for a in avoid)]
        return (got or raw)[:n]

    if style == "luxury" or budget_level == "high":
        hotel_lines = ["市中心豪华/五星酒店（含礼宾服务）", "景观精品设计酒店"]
    elif style == "economy" or budget_level == "low":
        hotel_lines = ["地铁口经济型连锁/青旅", "近商圈平价民宿"]
    else:
        hotel_lines = ["地铁/商圈附近酒店（请在 OTA 检索）", "近核心景点住宿"]

    attr_pool: list[str] = []
    for pk in preferences:
        attr_pool.extend(take(pk, 10))
    if not attr_pool:
        attr_pool = take("历史文化", 6) + take("自然风景", 5)
    seen: set[str] = set()
    uniq_attr: list[str] = []
    for x in attr_pool:
        if x not in seen:
            seen.add(x)
            uniq_attr.append(x)
    start = (seed % max(1, len(uniq_attr) - 5)) if uniq_attr else 0
    attr_lines = uniq_attr[start:start + 6] if uniq_attr else []

    food_raw = take("美食探索", 14)
    fs = (seed // 2) % max(1, len(food_raw) - 5) if len(food_raw) > 5 else 0
    food_lines = food_raw[fs:fs + 6] if food_raw else take("美食探索", 6)

    rich: list[dict] = []
    lines: list[str] = []

    for n in hotel_lines:
        rich.append(_empty_row(n, "住宿"))
    lines.append(f"  住宿：{'、'.join(hotel_lines)}")

    for n in attr_lines:
        rich.append(_empty_row(n, "景点"))
    lines.append(f"  景点：{'、'.join(attr_lines) if attr_lines else '（请结合偏好自选）'}")

    for n in food_lines:
        rich.append(_empty_row(n, "餐饮"))
    lines.append(f"  餐饮：{'、'.join(food_lines)}")

    return rich, lines, {}


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


def _name_allowed(name: str, avoid: list[str]) -> bool:
    return not avoid or not any(a in name for a in avoid)


async def poi_agent(state: dict) -> dict:
    """
    景点专员：按「住宿 / 景点 / 餐饮」检索高德 POI 2.0，写入 rich_poi_catalog、
    poi_coords（供 route 预加载）、district_hint（按区县聚合）。

    检索词与分页随预算、偏好、备注、画像变化，避免每次命中同一批默认结果。
    """
    city: str = state["city"]
    notes: str = state.get("notes", "")
    preferences: list[str] = state.get("preferences") or ["历史文化", "自然风景", "美食探索"]
    budget_level = (state.get("budget_level") or "medium").strip().lower()
    if budget_level not in ("low", "medium", "high"):
        budget_level = "medium"
    profile_note: str = (state.get("profile_note") or "").strip()
    t0 = time.perf_counter()

    notes_hints = _parse_notes(notes)
    avoid = notes_hints["avoid"]
    prefer = notes_hints["prefer"]
    style = _detect_travel_style(notes, profile_note, budget_level)
    seed = _search_seed(city, notes, profile_note, budget_level, tuple(preferences))
    ph, pa, pr = _page_triple(seed)

    kw_h = _build_hotel_keywords(budget_level, style)
    kw_a = _build_attraction_keywords(preferences)
    kw_r = _build_restaurant_keywords(budget_level, style, prefer, preferences)

    user_kw = _extract_user_phrases_for_search(notes, profile_note)
    if user_kw:
        kw_r = _merge_kw(kw_r, user_kw)
        if _lodging_mentioned(notes, profile_note):
            kw_h = _merge_kw(kw_h, user_kw)
        if _scenic_user_hint(notes, profile_note, user_kw):
            kw_a = _merge_kw(kw_a, user_kw) if kw_a else user_kw

    if notes_hints["summary"]:
        logger.info("[POIAgent] 备注解析：%s → avoid=%s prefer=%s",
                    notes_hints["summary"], avoid, prefer)
    logger.info(
        "[POIAgent] 开始查询：%s 预算=%s 风格=%s 分页 h/a/r=%d/%d/%d 用户原话词=%s",
        city, budget_level, style, ph, pa, pr, user_kw or "无",
    )

    api_key = os.getenv("AMAP_API_KEY", "").strip()
    rich_catalog: list[dict] = []
    poi_coords: dict[str, str] = {}
    context_lines: list[str] = []
    source_used = "内置数据库"

    profile_line = (
        f"【POI 检索画像】预算档：{budget_level}；识别风格：{style}；"
        f"旅行偏好：{'、'.join(preferences)}；主分页（住/景/餐）：{ph}/{pa}/{pr}（每类合并相邻一页）。"
        f"用户原话片段：{_truncate_for_prompt(user_kw, 72) if user_kw else '（无）'}。"
        f"住宿词：{_truncate_for_prompt(kw_h, 96)}；"
        f"景点词：{_truncate_for_prompt(kw_a or '（风景名胜类）', 96)}；"
        f"餐饮词：{_truncate_for_prompt(kw_r, 96)}"
    )

    if api_key:
        source_used = "高德地图 POI 2.0"
        attr_kw = kw_a if kw_a else None
        hotel_items, attr_items, rest_items = await asyncio.gather(
            search_pois_merged_pages(
                city, POICategory.HOTEL, api_key, limit=8,
                keyword_override=kw_h, page_nums=_page_pair(ph),
            ),
            search_pois_merged_pages(
                city, POICategory.ATTRACTION, api_key, limit=8,
                keyword_override=attr_kw, page_nums=_page_pair(pa),
            ),
            search_pois_merged_pages(
                city, POICategory.RESTAURANT, api_key, limit=8,
                keyword_override=kw_r, page_nums=_page_pair(pr),
            ),
        )
        hotel_items = [x for x in hotel_items if _name_allowed(x.name, avoid)]
        attr_items = [x for x in attr_items if _name_allowed(x.name, avoid)]
        rest_items = [x for x in rest_items if _name_allowed(x.name, avoid)]

        for label, items in (
            (category_label_cn(POICategory.HOTEL), hotel_items),
            (category_label_cn(POICategory.ATTRACTION), attr_items),
            (category_label_cn(POICategory.RESTAURANT), rest_items),
        ):
            lines = [x.to_prompt_line() for x in items[:6]]
            context_lines.append(f"  {label}：{'、'.join(lines) if lines else '（无）'}")
            for x in items:
                rich_catalog.append(richpoi_to_dict(x, label))
                rc = x.routing_coord()
                if rc and x.name not in poi_coords:
                    poi_coords[x.name] = rc

        district_hint = _district_hint_from_catalog(rich_catalog)
        map_coords = [d["location"] for d in rich_catalog if d.get("location")]
        static_map_url = build_static_map_url(map_coords[:8], api_key)
        if not rich_catalog:
            source_used = "内置数据库"
            rich_catalog, context_lines, poi_coords = _local_fallback_three_cat(
                city, avoid,
                budget_level=budget_level,
                preferences=preferences,
                notes=notes,
                profile_note=profile_note,
            )
            district_hint = ""
            static_map_url = ""
    else:
        rich_catalog, context_lines, poi_coords = _local_fallback_three_cat(
            city, avoid,
            budget_level=budget_level,
            preferences=preferences,
            notes=notes,
            profile_note=profile_note,
        )
        district_hint = ""
        static_map_url = ""

    ms = int((time.perf_counter() - t0) * 1000)
    coord_count = len(poi_coords)
    log = AgentLog(
        agent="poi", label="景点专员", icon="📍",
        status="ok", duration_ms=ms,
        detail=f"{city} {budget_level}/{style} · {source_used} · 坐标{coord_count}个 · POI{len(rich_catalog)}条",
        source=source_used,
    )
    logger.info("[POIAgent] 完成 (%dms): %s · 坐标%d个",
                ms, source_used, coord_count)

    text_parts = [
        f"【{city} 住宿/景点/餐饮推荐 · 来源：{source_used}】",
        profile_line,
    ]
    text_parts.extend(context_lines)
    if notes_hints["summary"]:
        text_parts.append(f"  ⚠️ {notes_hints['summary']}，Planner 请在推荐中遵守此限制")
    text = "\n".join(text_parts)

    return {
        "context_pieces": [text],
        "poi_coords": poi_coords,
        "rich_poi_catalog": rich_catalog,
        "district_hint": district_hint,
        "static_map_url": static_map_url,
        "agent_logs": [log.model_dump()],
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
