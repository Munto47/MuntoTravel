"""服务端生成高德静态地图预览 URL（Key 不暴露给前端）。"""

from __future__ import annotations

from urllib.parse import quote

_STATICMAP_URL = "https://restapi.amap.com/v3/staticmap"


def build_static_map_url(
    coords: list[str],
    api_key: str,
    *,
    size: str = "600*300",
    zoom: str = "11",
) -> str:
    """
    coords: GCJ02「lng,lat」列表，最多取前 10 个作为标注。
    以第一个坐标为中心点。
    """
    cleaned = [c.strip() for c in coords if c and "," in c][:10]
    if not cleaned or not api_key:
        return ""
    center = cleaned[0]
    markers = "|".join(f"mid,{i},{c}" for i, c in enumerate(cleaned))
    q = (
        f"key={quote(api_key, safe='')}"
        f"&location={quote(center, safe=',')}"
        f"&zoom={zoom}&size={quote(size, safe='*')}"
        f"&markers={quote(markers, safe='')}"
    )
    return f"{_STATICMAP_URL}?{q}"
