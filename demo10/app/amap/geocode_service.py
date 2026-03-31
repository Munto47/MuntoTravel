"""地理编码 / 逆地理编码（v3）。"""

from __future__ import annotations

from ..logger import get_logger
from .client import amap_get, get_amap_key

logger = get_logger(__name__)

_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"


async def geocode_address(address: str, city: str, api_key: str | None = None) -> dict | None:
    """
    结构化地址 -> 坐标与元数据。
    返回 {"location": "lng,lat", "level": str, "citycode": str, "adcode": str} 或 None。
    """
    key = api_key or get_amap_key()
    if not key:
        return None
    data = await amap_get(_GEOCODE_URL, {
        "key": key,
        "address": address,
        "city": city or "",
        "output": "json",
    })
    if not data or data.get("status") != "1" or not data.get("geocodes"):
        return None
    g = data["geocodes"][0]
    return {
        "location": g.get("location", ""),
        "level": g.get("level", ""),
        "citycode": g.get("citycode", ""),
        "adcode": g.get("adcode", ""),
    }


async def regeo_location(location: str, api_key: str | None = None) -> dict | None:
    """
    坐标 -> 行政区等信息。
    location: "lng,lat"
    返回 {"district", "adcode", "citycode", "formatted"} 或 None。
    """
    key = api_key or get_amap_key()
    if not key or not location:
        return None
    data = await amap_get(_REGEO_URL, {
        "key": key,
        "location": location,
        "extensions": "base",
        "output": "json",
    })
    if not data or data.get("status") != "1":
        return None
    reo = data.get("regeocode") or {}
    comp = reo.get("addressComponent") or {}
    return {
        "district": comp.get("district") or "",
        "adcode": comp.get("adcode") or "",
        "citycode": comp.get("citycode") or "",
        "formatted": reo.get("formatted_address") or "",
    }
