"""共享 HTTP 与响应解析。"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 8.0


def get_amap_key() -> str:
    return (os.getenv("AMAP_API_KEY") or "").strip()


async def amap_get(url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """GET 请求，返回 JSON dict；失败返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.debug("[Amap] GET %s 失败: %s", url, e)
    return None
