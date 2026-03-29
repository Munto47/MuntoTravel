import httpx

from .schemas import DayWeather, WeatherInfo

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO 天气代码 → 中文描述
# 完整代码表参见 https://open-meteo.com/en/docs#weathervariables
WMO_DESCRIPTIONS: dict[int, str] = {
    0: "晴天",
    1: "基本晴朗", 2: "局部多云", 3: "阴天",
    45: "有雾", 48: "有雾沉积",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "强毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    77: "冰粒",
    80: "小阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "强阵雪",
    95: "雷雨", 96: "雷雨伴小冰雹", 99: "雷雨伴大冰雹",
}


async def get_weather(city: str, days: int) -> WeatherInfo | None:
    """
    根据城市名查询未来 days 天的天气预报。

    使用两个完全免费、无需 API Key 的接口：
    1. Open-Meteo Geocoding API：城市名 → 经纬度
    2. Open-Meteo Forecast API：经纬度 → 天气预报

    查询失败时返回 None，调用方应将其视为「无天气数据」，
    而不是错误——天气数据是增强信息，不是必须项。
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            # 第一步：城市名 → 经纬度
            geo_resp = await client.get(
                GEOCODING_URL,
                params={"name": city, "count": 1, "language": "zh", "format": "json"},
            )
            geo_resp.raise_for_status()
            results = geo_resp.json().get("results")
            if not results:
                return None

            lat = results[0]["latitude"]
            lon = results[0]["longitude"]
            print(f"lat: {lat}, lon: {lon}")
            # 第二步：经纬度 → 天气预报
            forecast_resp = await client.get(
                FORECAST_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "timezone": "auto",
                    "forecast_days": min(days, 7),
                },
            )
            forecast_resp.raise_for_status()
            daily = forecast_resp.json()["daily"]

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
        return WeatherInfo(city=city, days=day_list)

    except Exception:
        return None
