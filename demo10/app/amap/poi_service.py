"""
POI 2.0：按「住宿 / 景点 / 餐饮」检索，带 business+navi 富信息。
分类码参考高德 POI 分类（100000 宾馆酒店、110000 风景名胜、050000 餐饮）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..logger import get_logger
from .client import amap_get, get_amap_key

logger = get_logger(__name__)

_TEXT_URL = "https://restapi.amap.com/v5/place/text"
_DETAIL_URL = "https://restapi.amap.com/v5/place/detail"


class POICategory(str, Enum):
    HOTEL = "hotel"
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"


# (keywords 辅助词, types 分类码)
_CATEGORY_QUERY: dict[POICategory, tuple[str, str]] = {
    POICategory.HOTEL:       ("酒店", "100000"),
    POICategory.ATTRACTION:  ("", "110000"),
    POICategory.RESTAURANT:  ("美食", "050000"),
}

_CATEGORY_LABEL_CN: dict[POICategory, str] = {
    POICategory.HOTEL: "住宿",
    POICategory.ATTRACTION: "景点",
    POICategory.RESTAURANT: "餐饮",
}


@dataclass
class RichPOI:
    name: str
    poi_id: str = ""
    address: str = ""
    location: str = ""       # 中心点 lng,lat
    entr_location: str = ""
    rating: str = ""
    cost: str = ""
    opentime_today: str = ""
    tel: str = ""
    tag: str = ""
    typecode: str = ""
    citycode: str = ""
    adcode: str = ""
    adname: str = ""         # 区县
    business_area: str = ""

    def routing_coord(self) -> str:
        return self.entr_location if self.entr_location else self.location

    def to_prompt_line(self) -> str:
        parts = [self.name]
        meta = []
        if self.rating:
            meta.append(f"⭐{self.rating}")
        if self.opentime_today:
            meta.append(self.opentime_today)
        if self.cost:
            meta.append(f"人均¥{self.cost}")
        if self.address:
            meta.append(self.address)
        if meta:
            parts.append(f"（{'，'.join(meta)}）")
        return "".join(parts)


def _parse_location(loc: Any) -> str:
    if not loc:
        return ""
    if isinstance(loc, str) and "," in loc:
        return loc
    if isinstance(loc, dict):
        lng, lat = loc.get("lng", ""), loc.get("lat", "")
        if lng and lat:
            return f"{lng},{lat}"
    return ""


def _poi_from_dict(poi: dict) -> RichPOI:
    business = poi.get("business") or {}
    navi = poi.get("navi") or {}
    return RichPOI(
        name=poi.get("name", "") or "",
        poi_id=poi.get("id", "") or "",
        address=poi.get("address", "") or "",
        location=_parse_location(poi.get("location")),
        entr_location=_parse_location(navi.get("entr_location")),
        rating=business.get("rating") or "",
        cost=business.get("cost") or "",
        opentime_today=business.get("opentime_today") or "",
        tel=business.get("tel") or poi.get("tel", "") or "",
        tag=business.get("tag") or "",
        typecode=poi.get("typecode", "") or "",
        citycode=poi.get("citycode", "") or "",
        adcode=poi.get("adcode", "") or "",
        adname=poi.get("adname", "") or "",
        business_area=business.get("business_area") or "",
    )


def richpoi_to_dict(poi: RichPOI, category_cn: str) -> dict:
    """转为可 JSON 序列化的字典（对齐 RichPOISchema）。"""
    return {
        "name": poi.name,
        "category": category_cn,
        "poi_id": poi.poi_id,
        "address": poi.address,
        "location": poi.location,
        "entr_location": poi.entr_location,
        "rating": poi.rating,
        "cost": poi.cost,
        "opentime_today": poi.opentime_today,
        "tel": poi.tel,
        "tag": poi.tag,
        "typecode": poi.typecode,
        "citycode": poi.citycode,
        "adcode": poi.adcode,
        "adname": poi.adname,
        "business_area": poi.business_area,
    }


async def _place_text(
    *,
    api_key: str,
    region: str,
    keywords: str,
    types: str,
    page_size: int = 10,
    page_num: int = 1,
) -> list[dict]:
    # v5：keywords 与 types 二选一必填；景点类可仅传 types
    params: dict[str, str | int] = {
        "key": api_key,
        "region": region,
        "city_limit": "true",
        "page_size": min(page_size, 25),
        "page_num": max(1, min(page_num, 5)),
        "show_fields": "business,navi,photos",
        "output": "json",
        "types": types,
    }
    if keywords:
        params["keywords"] = keywords
    data = await amap_get(_TEXT_URL, params)
    if not data or str(data.get("status")) != "1":
        return []
    return list(data.get("pois") or [])


async def search_pois_for_city(
    city: str,
    category: POICategory,
    api_key: str | None = None,
    limit: int = 8,
    *,
    keyword_override: str | None = None,
    page_num: int = 1,
) -> list[RichPOI]:
    """
    按城市与类别检索 POI（text 已含 business/navi）。

    keyword_override: 非 None 时覆盖默认关键词（传空字符串表示仅按 types 检索，适用于已带 types 的景点类）。
    page_num: 高德分页 1~5，与不同关键词组合可拉开结果差异。
    """
    key = api_key or get_amap_key()
    if not key:
        return []

    default_kw, types = _CATEGORY_QUERY[category]
    kw = default_kw if keyword_override is None else keyword_override
    pois_raw = await _place_text(
        api_key=key,
        region=city,
        keywords=kw,
        types=types,
        page_size=max(limit + 4, 12),
        page_num=page_num,
    )
    items = [_poi_from_dict(p) for p in pois_raw if p.get("name")]
    return [x for x in items if x.name][:limit]


async def search_pois_merged_pages(
    city: str,
    category: POICategory,
    api_key: str | None = None,
    limit: int = 8,
    *,
    keyword_override: str | None = None,
    page_nums: list[int],
    per_page_fetch: int = 14,
) -> list[RichPOI]:
    """
    多页合并去重：同一关键词下连续请求多页，按 poi_id（无则 name）去重，再截断到 limit。
    用于在同一检索意图下扩大候选，减少「永远第一页」的固化。
    """
    key = api_key or get_amap_key()
    if not key or not page_nums:
        return []

    seen: set[str] = set()
    merged: list[RichPOI] = []
    for pn in page_nums:
        pn = max(1, min(int(pn), 5))
        chunk = await search_pois_for_city(
            city, category, key,
            limit=min(per_page_fetch, 25),
            keyword_override=keyword_override,
            page_num=pn,
        )
        for x in chunk:
            dedup = (x.poi_id or "").strip() or x.name
            if not dedup:
                continue
            if dedup in seen:
                continue
            seen.add(dedup)
            merged.append(x)
            if len(merged) >= limit:
                return merged[:limit]
    return merged[:limit]


def category_label_cn(cat: POICategory) -> str:
    return _CATEGORY_LABEL_CN[cat]
