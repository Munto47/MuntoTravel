"""
tools.py —— LangChain 工具定义（demo07 版）

与 demo06 的关键差异：
  demo06：有 plan_transport 工具，LangGraph Agent 自主决定调用时机
  demo07：移除 plan_transport，交通数据改由 main.py 预先计算并注入 LLM 上下文

  为何改变？
  - 交通数据是"一次性确定的"（出发地和目的地固定），不需要 LLM 自主判断调用时机
  - 预算模式更可靠：直接控制何时调用、调用哪个城市对，结果保证可结构化返回
  - 列车数据（本地库）不依赖外部 API，没有失败的可能
  - 这是 context enrichment（demo02风格）和 tool use（demo03+风格）的架构选择，
    应根据数据特性选择：动态决策用 tool，确定性预处理用 context enrichment

保留的工具：
  get_weather      → 目的地天气（仍用 tool：LLM 自主决定查几天）
  get_attractions  → 景点推荐（仍用 tool：LLM 自主决定查哪些类别）
"""

import os
from typing import Literal

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

from .weather_client import get_weather as _get_weather

load_dotenv()

_AMAP_POI_URL = "https://restapi.amap.com/v3/place/text"

_CATEGORY_KEYWORDS: dict[str, str] = {
    "历史文化": "历史博物馆|名胜古迹|文化遗址|纪念馆|古城",
    "自然风景": "自然风景区|国家公园|湿地公园|森林公园|风景名胜",
    "美食":     "地方特色餐厅|老字号|美食街|特色小吃",
    "购物":     "购物中心|步行街|商业街|特色商店",
    "夜生活":   "酒吧|夜市|夜景|夜游|演出",
    "亲子":     "动物园|儿童乐园|科技馆|亲子农场|主题公园",
}

_ATTRACTIONS_DB: dict[str, dict[str, list[str]]] = {
    "杭州": {
        "历史文化": ["西湖（断桥残雪、雷峰塔）", "灵隐寺", "岳王庙", "河坊街", "胡雪岩故居"],
        "自然风景": ["西湖游船", "西溪湿地公园", "九溪十八涧", "杭州植物园"],
        "美食": ["楼外楼（西湖醋鱼）", "知味观（叫花鸡）", "南宋御街小吃", "龙井茶园"],
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
        "美食": ["成都火锅", "夫妻肺片", "龙抄手", "担担面"],
        "购物": ["春熙路 IFS", "太古里", "锦里手工艺品"],
        "夜生活": ["锦里夜景", "玉林路小酒馆", "东郊记忆"],
        "亲子": ["大熊猫繁育研究基地", "金沙遗址博物馆", "欢乐谷"],
    },
    "上海": {
        "历史文化": ["外滩", "豫园", "田子坊", "新天地石库门"],
        "自然风景": ["世纪公园", "滨江大道", "辰山植物园"],
        "美食": ["南翔小笼包", "蟹黄汤包", "城隍庙小吃广场"],
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
    "重庆": {
        "历史文化": ["磁器口古镇", "三峡博物馆", "解放碑步行街"],
        "自然风景": ["武隆天坑地缝", "南山植物园", "缙云山"],
        "美食": ["重庆火锅", "酸辣粉", "重庆小面", "洪崖洞夜市"],
        "购物": ["解放碑商圈", "观音桥步行街", "磁器口手工艺品"],
        "夜生活": ["洪崖洞灯光秀", "南山一棵树观景台夜景"],
        "亲子": ["重庆动物园（大熊猫）", "重庆科技馆", "欢乐谷"],
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


async def _fetch_amap_poi(city: str, category: str, api_key: str) -> list[str] | None:
    keywords = _CATEGORY_KEYWORDS.get(category, category)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(_AMAP_POI_URL, params={
                "key": api_key, "keywords": keywords,
                "city": city, "offset": 10,
                "extensions": "base", "output": "json",
            })
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "1" or not data.get("pois"):
            return None
        return [poi["name"] for poi in data["pois"][:8]]
    except Exception as e:
        print(f"[AMAP] 请求失败：{e}")
        return None


@tool
async def get_weather(city: str, days: int) -> str:
    """
    查询目的地未来几天的实时天气预报。
    获取天气后可以据此安排晴天户外、雨天室内的合理行程。
    city: 目的地城市名称，如：杭州、北京、成都
    days: 查询天数，与行程天数一致，范围 1-7
    """
    weather = await _get_weather(city, days)
    if weather:
        return weather.to_prompt_text()
    return f"未能获取 {city} 的实时天气数据，请根据季节特征推测，并在 tips 中提示游客出发前查看天气预报。"


@tool
async def get_attractions(
    city: str,
    category: Literal["历史文化", "自然风景", "美食", "购物", "夜生活", "亲子"],
) -> str:
    """
    查询城市的热门景点、餐厅或活动推荐列表，按旅行偏好类别筛选。
    建议根据用户的不同偏好多次调用，分别查询不同类别。
    city: 目的地城市名称
    category: 景点或活动的类别
    """
    amap_key = os.getenv("AMAP_API_KEY", "").strip()
    if amap_key:
        poi_names = await _fetch_amap_poi(city, category, amap_key)
        if poi_names:
            return f"{city} · {category} 热门推荐（高德地图）：{'、'.join(poi_names)}"
        print(f"[AMAP] {city}·{category} 失败，降级 mock")

    city_data = _ATTRACTIONS_DB.get(city, _DEFAULT_ATTRACTIONS)
    attractions = city_data.get(category, [f"{city}当地{category}推荐"])
    return f"{city} · {category} 热门推荐（内置数据）：{'、'.join(attractions)}"


TOOLS = [get_weather, get_attractions]
