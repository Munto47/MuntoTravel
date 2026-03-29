"""
tools.py —— 工具定义与执行器

这个文件做两件事：
1. 用 OpenAI 规定的 JSON Schema 格式描述每个工具（TOOL_DEFINITIONS）
   → 这份描述会被发给 LLM，LLM 读懂它之后才知道可以调哪些工具、参数是什么
2. 实现每个工具的实际执行逻辑（execute_tool）
   → 当 LLM 决定调用某个工具时，我们负责真正执行并把结果返回给 LLM

get_attractions 的实现策略：
  - 配置了 AMAP_API_KEY → 调用高德地图 POI 搜索 API（真实数据）
  - 未配置 AMAP_API_KEY → 自动降级到本地 mock 数据库
"""

import json
import os

import httpx
from dotenv import load_dotenv

from .weather_client import get_weather as _get_weather

load_dotenv()

# ── 高德地图 POI API 配置 ─────────────────────────────────────────────────────

_AMAP_POI_URL = "https://restapi.amap.com/v3/place/text"

# 各类别对应的高德搜索关键词
# 关键词越精准，返回的 POI 质量越高
_CATEGORY_KEYWORDS: dict[str, str] = {
    "历史文化": "历史博物馆|名胜古迹|文化遗址|纪念馆|古城",
    "自然风景": "自然风景区|国家公园|湿地公园|森林公园|风景名胜",
    "美食":     "地方特色餐厅|老字号|美食街|特色小吃",
    "购物":     "购物中心|步行街|商业街|特色商店",
    "夜生活":   "酒吧|夜市|夜景|夜游|演出",
    "亲子":     "动物园|儿童乐园|科技馆|亲子农场|主题公园",
}

# ── 工具定义（发给 LLM 看的「工具说明书」）────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "查询目的地未来几天的实时天气预报。"
                "获取天气后可以据此安排晴天户外、雨天室内的合理行程。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "目的地城市名称，如：杭州、北京、成都",
                    },
                    "days": {
                        "type": "integer",
                        "description": "需要查询的天数，与行程天数保持一致，范围 1-7",
                    },
                },
                "required": ["city", "days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_attractions",
            "description": (
                "查询城市的热门景点、餐厅或活动推荐列表，按旅行偏好类别筛选。"
                "建议根据用户的不同偏好多次调用，分别查询不同类别。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "目的地城市名称",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["历史文化", "自然风景", "美食", "购物", "夜生活", "亲子"],
                        "description": "景点或活动的类别",
                    },
                },
                "required": ["city", "category"],
            },
        },
    },
]

# ── 景点数据库（Mock）────────────────────────────────────────────────────────
# 这是一个静态的本地数据库，模拟真实 POI API 的返回。
# demo04+ 可以替换为高德地图 API。

_ATTRACTIONS_DB: dict[str, dict[str, list[str]]] = {
    "杭州": {
        "历史文化": ["西湖（断桥残雪、雷峰塔）", "灵隐寺", "岳王庙", "河坊街", "胡雪岩故居"],
        "自然风景": ["西湖游船", "西溪湿地公园", "九溪十八涧", "杭州植物园"],
        "美食": ["楼外楼（西湖醋鱼）", "知味观（叫花鸡）", "南宋御街小吃", "龙井茶园采茶体验"],
        "购物": ["湖滨银泰", "武林广场商圈", "南宋御街文创"],
        "夜生活": ["西湖夜游", "湖滨路夜市", "御街夜景步行"],
        "亲子": ["杭州动物园", "西溪湿地亲子营地", "浙江自然博物院"],
    },
    "北京": {
        "历史文化": ["故宫博物院", "天坛公园", "颐和园", "长城（慕田峪）", "南锣鼓巷"],
        "自然风景": ["香山公园", "奥林匹克森林公园", "玉渊潭公园"],
        "美食": ["全聚德烤鸭", "簋街小龙虾", "王府井小吃街", "护国寺小吃"],
        "购物": ["王府井步行街", "三里屯太古里", "南锣鼓巷手工艺品"],
        "夜生活": ["后海酒吧街", "三里屯 Village", "798 艺术区夜间展览"],
        "亲子": ["北京动物园（大熊猫）", "中国科技馆", "自然博物馆"],
    },
    "成都": {
        "历史文化": ["武侯祠", "锦里古街", "杜甫草堂", "宽窄巷子"],
        "自然风景": ["都江堰", "青城山", "西岭雪山"],
        "美食": ["成都火锅（麻辣香锅）", "夫妻肺片", "龙抄手", "担担面", "兔头"],
        "购物": ["春熙路 IFS", "太古里", "锦里手工艺品"],
        "夜生活": ["锦里夜景", "玉林路小酒馆", "东郊记忆"],
        "亲子": ["大熊猫繁育研究基地", "金沙遗址博物馆", "欢乐谷"],
    },
    "上海": {
        "历史文化": ["外滩", "豫园", "田子坊", "新天地石库门"],
        "自然风景": ["世纪公园", "滨江大道（陆家嘴）", "辰山植物园"],
        "美食": ["南翔小笼包", "蟹黄汤包", "城隍庙小吃广场", "本帮菜"],
        "购物": ["南京路步行街", "淮海路", "徐家汇商圈"],
        "夜生活": ["外滩灯光秀", "陆家嘴夜景", "衡山路酒吧区"],
        "亲子": ["上海科技馆", "自然博物馆", "上海海洋水族馆"],
    },
    "西安": {
        "历史文化": ["兵马俑", "华清宫", "古城墙", "大雁塔", "碑林博物馆"],
        "自然风景": ["华山", "秦岭终南山", "骊山"],
        "美食": ["肉夹馍", "羊肉泡馍", "biangbiang 面", "回民街小吃"],
        "购物": ["大唐不夜城", "回民街文创", "书院门古玩街"],
        "夜生活": ["大唐不夜城夜游", "城墙灯光秀"],
        "亲子": ["陕西历史博物馆", "大唐芙蓉园", "秦岭野生动物园"],
    },
}

_DEFAULT_ATTRACTIONS: dict[str, list[str]] = {
    "历史文化": ["当地博物馆", "古城区历史街区", "文物保护单位"],
    "自然风景": ["城市公园", "近郊自然风景区", "湿地保护区"],
    "美食": ["当地特色餐厅", "夜市小吃街", "老字号餐馆"],
    "购物": ["市中心商业区", "特色手工艺品市场"],
    "夜生活": ["城市夜景观景台", "特色酒吧一条街"],
    "亲子": ["动物园", "科技馆", "儿童主题乐园"],
}


# ── 高德地图 POI 查询 ─────────────────────────────────────────────────────────

async def _fetch_amap_poi(city: str, category: str, api_key: str) -> list[str] | None:
    """
    调用高德地图 POI 搜索 API，返回景点名称列表。
    失败时返回 None，由调用方决定是否降级到 mock。

    API 文档：https://lbs.amap.com/api/webservice/guide/api/search
    """
    keywords = _CATEGORY_KEYWORDS.get(category, category)

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(_AMAP_POI_URL, params={
                "key":        api_key,
                "keywords":   keywords,
                "city":       city,
                "offset":     10,        # 每次返回最多 10 条
                "extensions": "base",
                "output":     "json",
            })
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            print(f"[AMAP] API 返回错误：{data.get('info', '未知错误')}")
            return None

        pois = data.get("pois", [])
        if not pois:
            return None

        return [poi["name"] for poi in pois[:8]]

    except Exception as e:
        print(f"[AMAP] 请求失败：{e}")
        return None


# ── 工具执行器 ────────────────────────────────────────────────────────────────

async def execute_tool(name: str, arguments: str) -> str:
    """
    执行 LLM 请求的工具调用，返回结果字符串。

    参数：
        name      - 工具名称，对应 TOOL_DEFINITIONS 中的 function.name
        arguments - LLM 生成的参数 JSON 字符串

    返回：
        工具执行结果的文本描述，会被追加回 messages 供 LLM 继续使用
    """
    args = json.loads(arguments)

    if name == "get_weather":
        city = args["city"]
        days = args["days"]
        weather = await _get_weather(city, days)
        if weather:
            return weather.to_prompt_text()
        return f"未能获取 {city} 的实时天气数据，建议根据季节特征推测，并在 tips 中提示游客出发前查看天气预报。"

    if name == "get_attractions":
        city = args["city"]
        category = args["category"]

        # ── 优先使用高德真实数据 ───────────────────────────────────────────────
        amap_key = os.getenv("AMAP_API_KEY", "").strip()
        if amap_key:
            poi_names = await _fetch_amap_poi(city, category, amap_key)
            if poi_names:
                source_tag = "（数据来源：高德地图）"
                return f"{city} · {category} 热门推荐{source_tag}：{'、'.join(poi_names)}"
            print(f"[AMAP] {city}·{category} 查询失败，降级到 mock 数据")
        else:
            print(f"[AMAP] 未配置 AMAP_API_KEY，使用 mock 数据")

        # ── 降级到本地 mock 数据库 ─────────────────────────────────────────────
        city_data = _ATTRACTIONS_DB.get(city, _DEFAULT_ATTRACTIONS)
        attractions = city_data.get(category, [f"{city}当地{category}推荐"])
        source_tag = "（数据来源：内置数据库）"
        return f"{city} · {category} 热门推荐{source_tag}：{'、'.join(attractions)}"

    return f"未知工具：{name}，请只使用已定义的工具。"
