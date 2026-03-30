"""
weather_client.py —— 双源天气查询

主源：和风天气（QWeather）
  - 中国公司，对国内城市覆盖最完整，直接支持中文城市名
  - 需要配置 QWEATHER_API_KEY 和 QWEATHER_API_HOST
  - 申请地址：https://dev.qweather.com → 控制台 → 创建项目

备源：Open-Meteo + 本地坐标缓存
  - 完全免费，无需 Key
  - 跳过 Geocoding API（不稳定），直接用预置坐标查天气
  - 仅覆盖缓存内的 ~40 个主要城市

策略：
  1. 配置了 QWeather → 优先用 QWeather，失败时降级到备源
  2. 未配置 QWeather → 直接用备源
  3. 两源都失败   → 返回 None，Agent 不注入天气上下文
"""

import asyncio
import os

import httpx
from dotenv import load_dotenv

from .schemas import DayWeather, WeatherInfo

load_dotenv()

# ── 和风天气 API ──────────────────────────────────────────────────────────────

def _qweather_urls(host: str) -> tuple[str, str]:
    base = f"https://{host}"
    return f"{base}/geo/v2/city/lookup", f"{base}/v7/weather/7d"


def _qweather_headers(api_key: str) -> dict[str, str]:
    """
    和风天气支持两种认证方式，通过 QWEATHER_AUTH_TYPE 环境变量切换：
      apikey（默认）：X-QW-Api-Key 请求头 —— 控制台直接生成的 API KEY
      jwt           ：Authorization: Bearer —— 需要用 Ed25519 私钥签名生成的 JWT Token
    两种方式不能混用，混用会导致 401。
    """
    auth_type = os.getenv("QWEATHER_AUTH_TYPE", "apikey").lower()
    if auth_type == "jwt":
        return {"Authorization": f"Bearer {api_key}"}
    return {"X-QW-Api-Key": api_key}


async def _get_qweather(
    city: str, days: int, api_key: str, api_host: str
) -> WeatherInfo | None:
    """
    两步查询：
    Step 1  城市名 → location_id（GeoAPI）
    Step 2  location_id → 7天预报（Weather API，取前 days 天）
    """
    geo_url, forecast_url = _qweather_urls(api_host)
    headers = _qweather_headers(api_key)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1：城市搜索
            geo_resp = await client.get(
                geo_url,
                params={"location": city, "lang": "zh", "range": "cn", "number": 1},
                headers=headers,
            )
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()

            if geo_data.get("code") != "200" or not geo_data.get("location"):
                print(f"[QWeather] 城市搜索失败，code={geo_data.get('code')}，city={city}")
                return None

            location_id = geo_data["location"][0]["id"]
            city_name   = geo_data["location"][0]["name"]

            # Step 2：天气预报（固定请求 7d，按需截取）
            forecast_resp = await client.get(
                forecast_url,
                params={"location": location_id, "lang": "zh"},
                headers=headers,
            )
            forecast_resp.raise_for_status()
            forecast_data = forecast_resp.json()

            if forecast_data.get("code") != "200":
                print(f"[QWeather] 预报查询失败，code={forecast_data.get('code')}")
                return None

        daily = forecast_data["daily"][: days]
        day_list = [
            DayWeather(
                date=d["fxDate"],
                condition=f"白天{d['textDay']} / 夜间{d['textNight']}",
                temp_max=float(d["tempMax"]),
                temp_min=float(d["tempMin"]),
                precipitation=float(d["precip"]),
                wind_desc=f"{d['windDirDay']} {d['windScaleDay']}级",
            )
            for d in daily
        ]
        return WeatherInfo(city=city_name, days=day_list, source="和风天气")

    except Exception as e:
        print(f"[QWeather] 请求异常：{e}")
        return None


# ── Open-Meteo 备源（仅坐标缓存城市，不调 Geocoding API）────────────────────

_OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO_DESCRIPTIONS: dict[int, str] = {
    0: "晴天",
    1: "基本晴朗", 2: "局部多云", 3: "阴天",
    45: "有雾", 48: "有雾沉积",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "强毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "小阵雨", 81: "中阵雨", 82: "强阵雨",
    95: "雷雨", 96: "雷雨伴小冰雹", 99: "雷雨伴大冰雹",
}

# 预置坐标缓存（WGS84），跳过不稳定的 Geocoding API
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "北京": (39.9042, 116.4074), "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644), "深圳": (22.5431, 114.0579),
    "杭州": (30.2741, 120.1551), "成都": (30.5728, 104.0668),
    "西安": (34.3416, 108.9398), "南京": (32.0603, 118.7969),
    "武汉": (30.5928, 114.3055), "重庆": (29.4316, 106.9123),
    "苏州": (31.2989, 120.5853), "厦门": (24.4798, 118.0894),
    "青岛": (36.0671, 120.3826), "大连": (38.9140, 121.6147),
    "长沙": (28.2278, 112.9388), "昆明": (25.0389, 102.7183),
    "哈尔滨": (45.8038, 126.5349), "沈阳": (41.8057, 123.4315),
    "天津": (39.3434, 117.3616), "郑州": (34.7466, 113.6253),
    "济南": (36.6512, 116.9972), "合肥": (31.8206, 117.2272),
    "福州": (26.0745, 119.2965), "南昌": (28.6820, 115.8579),
    "太原": (37.8706, 112.5489), "石家庄": (38.0428, 114.5149),
    "乌鲁木齐": (43.8256, 87.6168), "呼和浩特": (40.8414, 111.7519),
    "兰州": (36.0611, 103.8343), "西宁": (36.6171, 101.7782),
    "银川": (38.4872, 106.2309), "拉萨": (29.6500, 91.1000),
    "海口": (20.0440, 110.1999), "三亚": (18.2528, 109.5119),
    "桂林": (25.2736, 110.2908), "丽江": (26.8721, 100.2299),
    "黄山": (30.1335, 118.1762), "张家界": (29.1170, 110.4790),
    "九寨沟": (33.2600, 103.9170),
}


def _lookup_coords(city: str) -> tuple[float, float] | None:
    if city in _CITY_COORDS:
        return _CITY_COORDS[city]
    for key, coords in _CITY_COORDS.items():
        if city.startswith(key) or key.startswith(city):
            return coords
    return None


async def _get_openmeteo(city: str, days: int) -> WeatherInfo | None:
    """
    用预置坐标直接调用 Open-Meteo Forecast API。
    城市不在缓存中则直接返回 None（不调 Geocoding API）。
    失败时最多重试 1 次。
    """
    coords = _lookup_coords(city)
    if not coords:
        print(f"[OpenMeteo] {city} 不在坐标缓存中，跳过")
        return None

    lat, lon = coords
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
        "forecast_days": min(days, 7),
    }

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(_OPENMETEO_FORECAST_URL, params=params)
                resp.raise_for_status()
                daily = resp.json()["daily"]

            day_list = [
                DayWeather(
                    date=daily["time"][i],
                    condition=WMO_DESCRIPTIONS.get(int(daily["weathercode"][i]), "未知"),
                    temp_max=round(daily["temperature_2m_max"][i], 1),
                    temp_min=round(daily["temperature_2m_min"][i], 1),
                    precipitation=round(daily["precipitation_sum"][i], 1),
                )
                for i in range(min(days, len(daily["time"])))
            ]
            return WeatherInfo(city=city, days=day_list, source="Open-Meteo")

        except Exception as e:
            if attempt == 0:
                print(f"[OpenMeteo] 第1次失败，1s 后重试：{e}")
                await asyncio.sleep(1)
            else:
                print(f"[OpenMeteo] 最终失败：{e}")
    return None


# ── 对外统一入口 ──────────────────────────────────────────────────────────────

async def get_weather(city: str, days: int) -> WeatherInfo | None:
    """
    按优先级查询天气：和风天气 → Open-Meteo → None

    调用方（tools.py）对 None 的处理：
    返回「未能获取实时天气」的提示，Agent 会在行程建议中注明此情况。
    """
    qweather_key  = os.getenv("QWEATHER_API_KEY", "").strip()
    qweather_host = os.getenv("QWEATHER_API_HOST", "").strip()

    # 主源：和风天气
    if qweather_key and qweather_host:
        result = await _get_qweather(city, days, qweather_key, qweather_host)
        if result:
            print(f"[Weather] 和风天气成功：{city}，{len(result.days)} 天")
            return result
        print(f"[Weather] 和风天气失败，降级到 Open-Meteo")
    else:
        print(f"[Weather] 未配置 QWeather，使用 Open-Meteo")

    # 备源：Open-Meteo + 坐标缓存
    result = await _get_openmeteo(city, days)
    if result:
        print(f"[Weather] Open-Meteo 成功：{city}，{len(result.days)} 天")
    return result
