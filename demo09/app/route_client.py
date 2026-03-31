"""
route_client.py —— 城市内景点间路线规划（增强版）

调用策略（升级后）：
  1. 高德步行 API（/v3/direction/walking）：距离 ≤ 1.5km
  2. 高德骑行 API（/v5/direction/bicycling）：1.5km < 距离 ≤ 5km
  3. 高德公交 API（/v3/direction/transit/integrated）：距离 > 5km（含线路名）
  4. 无 AMAP_API_KEY 或 API 失败时：基于坐标距离估算

增强特性（vs demo09 初版）：
  - poi_coords 预加载：将 poi_agent 搜索到的坐标注入缓存，避免重复 geocode
  - 骑行模式（新增）：1.5~5km 优先骑行，适合景区内短途移动
  - 公交线路名提取：从 API 响应中提取线路名，丰富提示信息
  - 坐标缓存持久化：写入 data/coord_cache.json，跨请求复用
  - Geocode 质量门控：拒绝省/市级精度结果，避免错误坐标导致路线异常

demo09 新 LangGraph 知识点（不变）：
  route_node 是 planner_node 之后的顺序节点：
    planner_node（LLM 决定去哪里）→ route_node（工具计算怎么走）→ END
"""

import asyncio
import json
import math
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from .logger import get_logger
from .schemas import RouteSegment

load_dotenv()
logger = get_logger(__name__)


# ── AMAP API 端点 ──────────────────────────────────────────────────────────────

_GEOCODE_URL   = "https://restapi.amap.com/v3/geocode/geo"
_WALKING_URL   = "https://restapi.amap.com/v3/direction/walking"
_BICYCLING_URL = "https://restapi.amap.com/v5/direction/bicycling"   # 骑行用 v5（status 字段统一）
_TRANSIT_URL   = "https://restapi.amap.com/v3/direction/transit/integrated"

# 单次请求超时
_TIMEOUT_GEOCODE = 5
_TIMEOUT_ROUTE   = 8

# 距离阈值（米）
_WALK_MAX_M  = 1500    # ≤ 1.5km  → 步行
_CYCLE_MAX_M = 5000    # ≤ 5km    → 骑行（适合景区间短途）
_DRIVE_MAX_M = 50_000  # > 50km   → 跨城，不规划（跳过）

# Geocode 精度门控：拒绝国家/省/城市级别（对路线规划无意义）
_COARSE_LEVELS = {"国家", "省", "城市", "城区"}


# ── 坐标缓存（内存 + JSON 持久化）────────────────────────────────────────────

_CACHE_FILE = Path("data/coord_cache.json")


def _load_coord_cache() -> dict[str, str]:
    """从磁盘加载坐标缓存，模块首次导入时调用。文件不存在时返回空字典。"""
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[Route] 坐标缓存加载失败: %s", e)
    return {}


def _save_coord_cache() -> None:
    """将当前内存缓存持久化到磁盘（异常时仅记录日志，不中断主流程）。"""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(_coord_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("[Route] 坐标缓存保存失败: %s", e)


# 模块级缓存，从磁盘预加载
_coord_cache: dict[str, str] = _load_coord_cache()


def preload_coords(poi_coords: dict[str, str], city: str) -> int:
    """
    将 poi_agent 搜索到的坐标注入内存缓存（格式 key = "name|city"）。
    已存在的 key 不覆盖。返回新增条数。
    调用此函数后，相同地点的 _geocode() 请求会直接命中缓存。
    """
    added = 0
    for name, coord in poi_coords.items():
        key = f"{name}|{city}"
        if key not in _coord_cache and coord:
            _coord_cache[key] = coord
            added += 1
    if added:
        _save_coord_cache()
        logger.debug("[Route] 预加载 %d 个 POI 坐标到缓存", added)
    return added


# ── 地名清洗 ──────────────────────────────────────────────────────────────────

def _clean_location_name(name: str) -> str:
    """
    清洗 LLM 生成的地点名称，去除括号/注释，保留核心名称用于 geocode。
    例："楼外楼（西湖店，推荐西湖醋鱼）" → "楼外楼"
         "知味观（南山路店）"              → "知味观"
    """
    import re
    cleaned = re.sub(r'[（(【\[].*?[）)\]】]', '', name).strip()
    cleaned = re.split(r'[，,·\-—]', cleaned)[0].strip()
    return cleaned or name


# ── 地理编码 ──────────────────────────────────────────────────────────────────

async def _geocode(name: str, city: str, api_key: str) -> str | None:
    """
    将地名转换为经纬度字符串 'lng,lat'。

    查找顺序：
      1. 内存缓存（包含 poi_agent 预加载的坐标）
      2. 高德 geocode API（原名 → 清洗名）
      3. Geocode 质量门控：拒绝省/市级精度结果

    成功的 geocode 结果写入持久化缓存。
    """
    cache_key = f"{name}|{city}"
    if cache_key in _coord_cache:
        return _coord_cache[cache_key]

    clean_name = _clean_location_name(name)
    if clean_name != name:
        clean_key = f"{clean_name}|{city}"
        if clean_key in _coord_cache:
            _coord_cache[cache_key] = _coord_cache[clean_key]
            return _coord_cache[clean_key]

    for try_name in ([name, clean_name] if clean_name != name else [name]):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_GEOCODE) as c:
                resp = await c.get(_GEOCODE_URL, params={
                    "key":     api_key,
                    "address": f"{city}{try_name}",
                    "city":    city,
                    "output":  "json",
                })
                resp.raise_for_status()
                data = resp.json()

            if data.get("status") == "1" and data.get("geocodes"):
                geocode = data["geocodes"][0]
                level   = geocode.get("level", "")
                if level in _COARSE_LEVELS:
                    logger.debug("[Route] geocode 精度不足 %s（level=%s），尝试清洗名", try_name, level)
                    continue

                loc = geocode["location"]
                _coord_cache[cache_key] = loc
                _save_coord_cache()
                logger.debug("[Route] geocode OK: %s → %s（level=%s）", try_name, loc, level)
                return loc

        except Exception as e:
            logger.debug("[Route] geocode 失败 %s: %s", try_name, e)

    return None


# ── 距离计算 ──────────────────────────────────────────────────────────────────

def _haversine_m(coord1: str, coord2: str) -> int:
    """根据两个 'lng,lat' 字符串计算球面距离（米）。"""
    try:
        lng1, lat1 = map(float, coord1.split(","))
        lng2, lat2 = map(float, coord2.split(","))
        r = 6_371_000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlng / 2) ** 2
        return int(2 * r * math.asin(math.sqrt(a)))
    except Exception:
        return 0


# ── 估算降级 ──────────────────────────────────────────────────────────────────

def _estimate_segment(from_name: str, to_name: str, distance_m: int) -> RouteSegment:
    """
    无 API 结果时按距离估算路线段，标记 is_estimated=True。
    前端可根据此字段显示"(估算)"标注。
    """
    if distance_m == 0:
        return RouteSegment(
            from_name=from_name, to_name=to_name,
            mode="市内公交", duration_min=20, distance_m=0,
            tip="建议提前查询导航确认路线",
            is_estimated=True,
        )
    if distance_m <= _WALK_MAX_M:
        mins = max(5, int(distance_m / 80))
        return RouteSegment(
            from_name=from_name, to_name=to_name,
            mode="步行", duration_min=mins, distance_m=distance_m,
            tip=f"约 {distance_m}m，步行即可",
            is_estimated=True,
        )
    if distance_m <= _CYCLE_MAX_M:
        mins = max(5, int(distance_m / 200))
        return RouteSegment(
            from_name=from_name, to_name=to_name,
            mode="骑行", duration_min=mins, distance_m=distance_m,
            tip="建议使用共享单车",
            is_estimated=True,
        )
    mins = max(10, int(distance_m / 500))
    return RouteSegment(
        from_name=from_name, to_name=to_name,
        mode="打车参考", duration_min=mins, distance_m=distance_m,
        tip="建议使用高德地图实时导航",
        is_estimated=True,
    )


# ── 路线 API 调用 ─────────────────────────────────────────────────────────────

async def _walking_route(origin: str, dest: str, api_key: str) -> tuple[int, int] | None:
    """调用高德步行 v3 API，返回 (duration_sec, distance_m) 或 None。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_ROUTE) as c:
            resp = await c.get(_WALKING_URL, params={
                "key": api_key, "origin": origin, "destination": dest,
            })
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "1":
            path = data["route"]["paths"][0]
            return int(path["duration"]), int(path["distance"])
    except Exception as e:
        logger.debug("[Route] 步行 API 失败: %s", e)
    return None


async def _bicycling_route(origin: str, dest: str, api_key: str) -> tuple[int, int] | None:
    """
    调用高德骑行 v5 API，返回 (duration_sec, distance_m) 或 None。
    v5 骑行使用 status 字段（与 v3 统一，解决了 v4 用 errcode 的格式差异）。
    response 中距离/时间为整数（v3 是字符串）。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_ROUTE) as c:
            resp = await c.get(_BICYCLING_URL, params={
                "key": api_key, "origin": origin, "destination": dest,
                "output": "json",
            })
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "1":
            # v5 bicycling 可能用 route 或 data 包裹 paths，两者都兼容
            route_data = data.get("route") or data.get("data") or {}
            paths = route_data.get("paths", [])
            if paths:
                path = paths[0]
                return int(path["duration"]), int(path["distance"])
    except Exception as e:
        logger.debug("[Route] 骑行 API 失败: %s", e)
    return None


def _extract_transit_line(transit: dict) -> str:
    """从公交路线响应中提取第一条线路名称（找 segments 中第一个 bus 类型段）。"""
    try:
        for segment in transit.get("segments", []):
            buslines = segment.get("bus", {}).get("buslines", [])
            if buslines:
                return buslines[0].get("name", "")
    except Exception:
        pass
    return ""


async def _transit_route(
    origin: str, dest: str, city: str, api_key: str
) -> tuple[int, int, str] | None:
    """
    调用高德公交 v3 API，返回 (duration_sec, distance_m, line_name) 或 None。
    新增：提取线路名称，丰富 RouteSegment.tip 信息。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_ROUTE) as c:
            resp = await c.get(_TRANSIT_URL, params={
                "key": api_key, "origin": origin, "destination": dest,
                "city": city, "nightflag": 0, "output": "json",
            })
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "1" and data.get("route", {}).get("transits"):
            t         = data["route"]["transits"][0]
            dist      = int(data["route"].get("distance", 0))
            line_name = _extract_transit_line(t)
            return int(t["duration"]), dist, line_name
    except Exception as e:
        logger.debug("[Route] 公交 API 失败: %s", e)
    return None


# ── 单段路线计算 ───────────────────────────────────────────────────────────────

async def _get_segment(
    from_name: str, to_name: str,
    city: str, api_key: str,
) -> RouteSegment:
    """
    计算两个地点之间的单段路线。
    优先级：步行（≤1.5km）→ 骑行（≤5km）→ 公交（>5km）→ 估算（无 API 或失败）
    """
    if not api_key:
        return _estimate_segment(from_name, to_name, 0)

    # 并行 geocode 两个地点（poi_agent 预加载的坐标会直接命中缓存）
    from_coord, to_coord = await asyncio.gather(
        _geocode(from_name, city, api_key),
        _geocode(to_name,   city, api_key),
    )

    if not from_coord or not to_coord:
        logger.debug("[Route] geocode 失败，跳过 %s→%s", from_name, to_name)
        return _estimate_segment(from_name, to_name, 0)

    dist_m = _haversine_m(from_coord, to_coord)

    if dist_m > _DRIVE_MAX_M:
        # 超过 50km 可能是数据异常（如 LLM 生成了跨城地名）
        return _estimate_segment(from_name, to_name, dist_m)

    # ① 步行模式（≤ 1.5km）
    if dist_m <= _WALK_MAX_M:
        result = await _walking_route(from_coord, to_coord, api_key)
        if result:
            dur_sec, real_dist = result
            dur_min = max(1, dur_sec // 60)
            return RouteSegment(
                from_name=from_name, to_name=to_name,
                mode="步行", duration_min=dur_min, distance_m=real_dist,
                tip=f"步行约 {dur_min} 分钟",
            )

    # ② 骑行模式（1.5km < 距离 ≤ 5km，适合景区间移动）
    elif dist_m <= _CYCLE_MAX_M:
        result = await _bicycling_route(from_coord, to_coord, api_key)
        if result:
            dur_sec, real_dist = result
            dur_min = max(3, dur_sec // 60)
            return RouteSegment(
                from_name=from_name, to_name=to_name,
                mode="骑行", duration_min=dur_min, distance_m=real_dist,
                tip=f"骑行约 {dur_min} 分钟，可使用共享单车",
            )

    # ③ 公交/地铁模式（> 5km）
    else:
        result = await _transit_route(from_coord, to_coord, city, api_key)
        if result:
            dur_sec, real_dist, line_name = result
            dur_min = max(5, dur_sec // 60)
            tip = f"公交约 {dur_min} 分钟"
            if line_name:
                tip += f"（乘坐 {line_name}）"
            return RouteSegment(
                from_name=from_name, to_name=to_name,
                mode="公交/地铁", duration_min=dur_min, distance_m=real_dist or dist_m,
                tip=tip,
            )

    # API 调用失败，降级为距离估算
    return _estimate_segment(from_name, to_name, dist_m)


# ── 公共入口 ──────────────────────────────────────────────────────────────────

async def plan_day_routes(
    locations: list[str],
    city: str,
    api_key: str = "",
    poi_coords: dict[str, str] | None = None,
) -> list[RouteSegment]:
    """
    对一天内有序地点列表，并发计算相邻两点之间的路线段。

    Args:
        locations:  按时间顺序的地点名称列表（至少 2 个）
        city:       目的地城市（用于 geocode 和公交查询）
        api_key:    高德 API Key（空则全部降级估算）
        poi_coords: poi_agent 搜索到的坐标字典（name → "lng,lat"）
                    预加载到缓存，减少后续 geocode 请求

    Returns:
        len(locations) - 1 个 RouteSegment 组成的列表
    """
    if len(locations) < 2:
        return []

    # 预加载 POI 搜索坐标到缓存，命中率提升后减少 geocode 调用
    if poi_coords and api_key:
        preload_coords(poi_coords, city)

    t0    = time.perf_counter()
    pairs = list(zip(locations[:-1], locations[1:]))

    segments = await asyncio.gather(*[
        _get_segment(frm, to, city, api_key)
        for frm, to in pairs
    ])

    ms    = int((time.perf_counter() - t0) * 1000)
    modes = [s.mode for s in segments]
    logger.info("[Route] %d段路线 (%dms): %s", len(segments), ms, " → ".join(modes))
    return list(segments)
