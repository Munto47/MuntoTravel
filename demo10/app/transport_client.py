"""
transport_client.py —— 城际交通（高德驾车 + 可选列车参考）

策略（demo10 重构）：
  - 自驾：高德 v5 /v5/direction/driving（最快 + 少收费），失败则内置降级
  - 列车：仅「北京 ↔ 上海」保留内置参考示例（标注非实时）；其他城市对不展示列车，
          以 train_notice 引导用户至 12306 查询
  12306 无公开 API，除京沪外不提供易误导的虚构班次。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .logger import get_logger

logger = get_logger(__name__)

# ── 高德 API 端点 ────────────────────────────────────────────────────────────
_GEO_URL   = "https://restapi.amap.com/v3/geocode/geo"
_DRIVE_URL = "https://restapi.amap.com/v5/direction/driving"

# 非京沪城市对：不展示列车参考时的统一提示
TRAIN_NOTICE_12306 = (
    "火车/高铁班次与票价请至 12306 官网或 App 查询；"
    "下方仅展示高德实时驾车路线供参考。"
)


# ── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class DriveRouteOption:
    """单个自驾策略方案（来自高德实时 API 或内置数据）"""
    strategy: str           # "最快路线" / "少收费路线"
    duration_minutes: int
    distance_km: float
    toll_yuan: int          # 过路费（元）
    main_highways: list[str]  # ["G2 京沪高速", "G15 沈海高速"]
    tips: list[str]

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "duration_minutes": self.duration_minutes,
            "distance_km": self.distance_km,
            "toll_yuan": self.toll_yuan,
            "main_highways": self.main_highways,
            "tips": self.tips,
        }

    def to_text(self) -> str:
        h, m = divmod(self.duration_minutes, 60)
        dur = f"{h}小时{m}分钟" if h else f"{m}分钟"
        hw = "→".join(self.main_highways) if self.main_highways else "无高速"
        toll = f"过路费约{self.toll_yuan}元" if self.toll_yuan > 0 else "基本无过路费"
        return f"[{self.strategy}] 约{dur} | {self.distance_km:.0f}km | {toll} | 路线：{hw}"


@dataclass
class TrainScheduleSample:
    """具体班次示例（参考数据，非实时）"""
    number: str   # "G7"
    dep: str      # "08:00"
    arr: str      # "08:51"  （次日到达写 "09:47+1"）

    def to_dict(self) -> dict:
        return {"number": self.number, "dep": self.dep, "arr": self.arr}

    def to_text(self) -> str:
        return f"{self.number} {self.dep}→{self.arr}"


@dataclass
class TrainTypeOption:
    """
    单个列车类型方案（高铁 / 动车 / 普通列车），
    包含具体的班次示例、票价表、换乘提示
    """
    train_type: str           # "高铁" / "动车组" / "普通列车（夜铺）"
    prefix: str               # "G、C" / "D" / "K、T、Z"
    speed_desc: str           # "最高时速 350km/h"
    from_station: str         # "上海虹桥"
    to_station: str           # "杭州东"
    duration_desc: str        # "约 50-65 分钟"
    frequency: str            # "约 5-10 分钟一班（早 6:30 - 晚 23:00）"
    prices: dict[str, int]    # {"二等座": 73, "一等座": 117, "商务座": 238}
    sample_trains: list[TrainScheduleSample]
    highlights: list[str]     # 优势列表
    booking_tips: str         # 购票建议

    def to_dict(self) -> dict:
        return {
            "train_type": self.train_type,
            "prefix": self.prefix,
            "speed_desc": self.speed_desc,
            "from_station": self.from_station,
            "to_station": self.to_station,
            "duration_desc": self.duration_desc,
            "frequency": self.frequency,
            "prices": self.prices,
            "sample_trains": [t.to_dict() for t in self.sample_trains],
            "highlights": self.highlights,
            "booking_tips": self.booking_tips,
        }

    def to_text(self) -> str:
        price_str = " | ".join(f"{k} {v}元" for k, v in self.prices.items())
        samples = " / ".join(t.to_text() for t in self.sample_trains[:3])
        return (
            f"【{self.train_type} · {self.prefix}字头 · {self.speed_desc}】\n"
            f"  {self.from_station} → {self.to_station}，{self.duration_desc}，{self.frequency}\n"
            f"  参考票价：{price_str}\n"
            f"  典型班次：{samples}"
        )


@dataclass
class TransportResult:
    origin: str
    destination: str
    drive_options: list[DriveRouteOption]   # 1-2 个自驾方案
    train_options: list[TrainTypeOption]    # 仅京沪可能非空
    data_source: str                        # "高德地图实时" / "内置参考数据"
    data_note: str = "列车时刻表仅供参考，实际班次及票价请在铁路12306确认"
    train_notice: str = ""                  # 非京沪时填充 TRAIN_NOTICE_12306

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "drive_options": [d.to_dict() for d in self.drive_options],
            "train_options": [t.to_dict() for t in self.train_options],
            "data_source": self.data_source,
            "data_note": self.data_note,
            "train_notice": self.train_notice,
        }

    def to_prompt_text(self) -> str:
        """生成注入 LLM 的上下文文本"""
        lines = [
            f"【{self.origin} → {self.destination} 交通方案（{self.data_source}）】",
        ]

        if self.drive_options:
            lines.append("\n◆ 自驾")
            for opt in self.drive_options:
                lines.append(f"  {opt.to_text()}")
                for tip in opt.tips:
                    lines.append(f"  💡 {tip}")

        if self.train_options:
            lines.append("\n◆ 铁路出行（京沪参考示例，非实时）")
            for train in self.train_options:
                lines.append(f"  {train.to_text()}")
        elif self.train_notice:
            lines.append(f"\n◆ 铁路出行\n  {self.train_notice}")

        lines.append(f"\n⚠ {self.data_note}")
        lines.append(
            "【行程安排要求】"
            "第一天请在上午/早晨写明具体的出发方式（选择哪种交通工具，几点出发，几点到达）；"
            "最后一天请写明返程安排（几点出发，估计几点到家）。"
        )
        return "\n".join(lines)


# ── 列车参考数据库（仅北京 ↔ 上海；其余城市对请用 12306）──────────────────────

def _ts(no: str, dep: str, arr: str) -> TrainScheduleSample:
    return TrainScheduleSample(no, dep, arr)


def _normalize_city_name(name: str) -> str:
    """用于城市对匹配：北京市→北京，上海市→上海。"""
    n = (name or "").strip()
    if n in ("北京市", "北京"):
        return "北京"
    if n in ("上海市", "上海"):
        return "上海"
    if len(n) > 1 and n.endswith("市"):
        return n[:-1]
    return n


def _is_beijing_shanghai_pair(origin: str, destination: str) -> bool:
    a, b = _normalize_city_name(origin), _normalize_city_name(destination)
    return frozenset({a, b}) == frozenset({"北京", "上海"})


_TRAIN_DB: dict[frozenset, list[TrainTypeOption]] = {
    frozenset({"北京", "上海"}): [
        TrainTypeOption(
            train_type="高铁（参考示例，非实时）",
            prefix="G",
            speed_desc="最高时速 350km/h",
            from_station="北京南",
            to_station="上海虹桥",
            duration_desc="约 4.5-5.5 小时（视停站数量）",
            frequency="约 15-30 分钟一班",
            prices={"二等座": 553, "一等座": 935, "商务座": 1748},
            sample_trains=[_ts("G1", "07:00", "12:28"), _ts("G3", "08:00", "13:28")],
            highlights=["示例车次，请以 12306 为准", "北京南与上海虹桥均为枢纽站"],
            booking_tips="实时班次与票价请以铁路 12306 为准",
        ),
        TrainTypeOption(
            train_type="普通列车（夜铺·参考示例）",
            prefix="T、Z",
            speed_desc="最高时速 160km/h",
            from_station="北京站 / 北京西",
            to_station="上海站",
            duration_desc="约 12-14 小时",
            frequency="每日数班（请以 12306 为准）",
            prices={"硬卧（下铺）": 330, "硬卧（上铺）": 285, "软卧": 510},
            sample_trains=[_ts("T109", "19:33", "09:47+1")],
            highlights=["仅为路线类型说明，非实时时刻表"],
            booking_tips="请以 12306 查询可售车次",
        ),
    ],
}


# ── 自驾内置参考数据（无 API Key 时降级）────────────────────────────────────────
_DRIVE_FALLBACK: dict[frozenset, list[DriveRouteOption]] = {
    frozenset({"上海", "杭州"}): [
        DriveRouteOption("最快路线", 120, 180, 35, ["G92 杭州湾环线高速", "G15 沈海高速"], ["周末早高峰 G92 可能拥堵，建议错峰出行"]),
        DriveRouteOption("少收费路线", 150, 195, 5, ["S19 申嘉湖高速", "G104 国道"], ["全程基本免费，但路况较复杂，不建议新手"]),
    ],
    frozenset({"北京", "上海"}): [
        DriveRouteOption("最快路线", 720, 1318, 180, ["G2 京沪高速"], ["全程约12小时，建议2人轮驾", "强烈推荐高铁替代"]),
    ],
    frozenset({"北京", "西安"}): [
        DriveRouteOption("最快路线", 720, 1200, 120, ["G5 京昆高速", "G30 连霍高速"], ["山区路段注意安全，冬季可能结冰"]),
    ],
    frozenset({"北京", "成都"}): [
        DriveRouteOption("最快路线", 1140, 1870, 200, ["G5 京昆高速"], ["全程约19小时，路途极长，强烈推荐高铁或飞机"]),
    ],
    frozenset({"成都", "重庆"}): [
        DriveRouteOption("最快路线", 150, 310, 50, ["G93 成渝环线高速"], []),
        DriveRouteOption("景观路线", 180, 330, 20, ["G318 成渝老路"], ["风景更好，途经资阳、大足，适合自驾游玩"]),
    ],
    frozenset({"上海", "南京"}): [
        DriveRouteOption("最快路线", 180, 300, 55, ["G42 沪宁高速"], ["周末早高峰可能拥堵"]),
        DriveRouteOption("少收费路线", 210, 310, 15, ["S38 江苏省道"], ["较省钱，但路况复杂"]),
    ],
    frozenset({"广州", "深圳"}): [
        DriveRouteOption("最快路线", 90, 140, 35, ["G4 京港澳高速", "G94 珠三角环线高速"], ["高峰期可能堵到2小时以上，强烈推荐城际高铁"]),
    ],
    frozenset({"北京", "天津"}): [
        DriveRouteOption("最快路线", 90, 137, 30, ["G2 京沪高速（京津段）"], []),
        DriveRouteOption("少收费路线", 110, 150, 5, ["G103 京津公路"], ["走 G103 基本免费，但路况较差"]),
    ],
    frozenset({"上海", "苏州"}): [
        DriveRouteOption("最快路线", 80, 100, 25, ["G42 沪宁高速"], []),
        DriveRouteOption("少收费路线", 100, 110, 0, ["S227 省道"], ["几乎全程免费，风景较好，适合慢行"]),
    ],
    frozenset({"西安", "成都"}): [
        DriveRouteOption("最快路线", 360, 700, 90, ["G5 京昆高速（穿越秦岭）"], ["山区路段弯道多，冬季需谨慎"]),
    ],
    frozenset({"广州", "桂林"}): [
        DriveRouteOption("最快路线", 300, 480, 80, ["G72 泉南高速", "G65 包茂高速"], ["途经梧州，沿途可欣赏广西丘陵风光"]),
    ],
    frozenset({"上海", "黄山"}): [
        DriveRouteOption("最快路线", 210, 370, 60, ["G50 沪渝高速", "G3 京台高速"], ["黄山市区→景区还需约1小时盘山公路"]),
    ],
    frozenset({"北京", "青岛"}): [
        DriveRouteOption("最快路线", 360, 630, 100, ["G15 沈海高速", "G18 荣乌高速"], ["途经天津，可安排半日游"]),
    ],
}


# ── 高德 API 调用 ────────────────────────────────────────────────────────────

async def _geocode(city: str, api_key: str) -> str | None:
    """城市名 → 高德坐标字符串（经度,纬度）"""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(_GEO_URL, params={"key": api_key, "address": city})
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "1" and data.get("geocodes"):
            loc = data["geocodes"][0]["location"]
            ms = int((time.perf_counter() - t0) * 1000)
            logger.debug("[Geocode OK] %s → %s (%dms)", city, loc, ms)
            return loc
        logger.warning("[Geocode !!] %s 返回空结果，status=%s", city, data.get("status"))
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        logger.warning("[Geocode !!] %s 失败 (%dms): %s", city, ms, e)
    return None


def _extract_highways(steps: list[dict]) -> list[str]:
    """从高德驾车 steps 提取主要道路名（v5 用 road_name，v3 用 road）。"""
    seen: set[str] = set()
    result: list[str] = []
    for step in steps:
        road = (step.get("road_name") or step.get("road") or "").strip()
        if not road:
            road = (step.get("instruction") or "").strip()
        for kw in ["高速", "国道", "快速路"]:
            if kw in road and road not in seen:
                seen.add(road)
                result.append(road[:12] if len(road) > 12 else road)
                break
    return result[:4]


async def _fetch_drive_strategy(
    origin_loc: str,
    dest_loc: str,
    api_key: str,
    strategy: int,
    label: str,
) -> DriveRouteOption | None:
    """调用高德驾车路线 API，单个策略"""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(_DRIVE_URL, params={
                "key": api_key,
                "origin": origin_loc,
                "destination": dest_loc,
                "strategy": strategy,
                "show_fields": "cost",
                "output": "json",
            })
            resp.raise_for_status()
            data = resp.json()

        ms = int((time.perf_counter() - t0) * 1000)

        if data.get("status") != "1":
            logger.warning("[Drive !!] 策略%d(%s) 失败 (%dms): %s", strategy, label, ms, data.get("info"))
            return None

        path = data["route"]["paths"][0]
        cost = path.get("cost") or {}
        dur_sec = int(cost.get("duration") or 0)
        if not dur_sec:
            dur_sec = int(path.get("duration") or 0)
        duration_min = max(1, dur_sec // 60)
        distance_km  = int(path.get("distance", 0) or 0) / 1000
        toll_yuan    = int(cost.get("tolls") or path.get("tolls") or 0)
        steps        = path.get("steps", [])
        highways     = _extract_highways(steps)

        h, m = divmod(duration_min, 60)
        dur_str = f"{h}h{m}min" if h else f"{m}min"
        hw_str  = "→".join(highways) if highways else "无高速"
        logger.info(
            "[Drive OK] 策略%d(%s) → %s | %.0fkm | toll=%d元 | %s (%dms)",
            strategy, label, dur_str, distance_km, toll_yuan, hw_str, ms,
        )

        tips: list[str] = []
        if strategy == 1 and toll_yuan == 0:
            tips.append("此路线基本无过路费，适合自驾探索")
        if duration_min > 360:
            tips.append("长途驾车建议2人以上轮驾")

        return DriveRouteOption(
            strategy=label,
            duration_minutes=duration_min,
            distance_km=distance_km,
            toll_yuan=toll_yuan,
            main_highways=highways,
            tips=tips,
        )
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        logger.warning("[Drive !!] 策略%d(%s) 请求失败 (%dms): %s", strategy, label, ms, e)
        return None


async def _fetch_amap_drive_options(
    origin: str, destination: str, api_key: str
) -> list[DriveRouteOption]:
    """调用高德 API，返回最多两个自驾方案（最快 + 少收费）"""
    origin_loc = await _geocode(origin, api_key)
    dest_loc   = await _geocode(destination, api_key)

    if not origin_loc or not dest_loc:
        logger.warning("[Drive] Geocode 不完整（origin=%s, dest=%s），降级内置数据",
                       "OK" if origin_loc else "FAIL", "OK" if dest_loc else "FAIL")
        return []

    fastest  = await _fetch_drive_strategy(origin_loc, dest_loc, api_key, 0, "最快路线")
    cheapest = await _fetch_drive_strategy(origin_loc, dest_loc, api_key, 1, "少收费路线")

    options: list[DriveRouteOption] = []
    if fastest:
        options.append(fastest)

    # 只有费用差距 > 15 元才显示第二方案（差距太小没有实际意义）
    if cheapest and fastest:
        toll_diff = abs(cheapest.toll_yuan - fastest.toll_yuan)
        if toll_diff > 15:
            options.append(cheapest)
            logger.info("[Drive] 过路费差 %d元 > 15元，展示第2方案（少收费路线）", toll_diff)
        else:
            logger.debug("[Drive] 过路费差 %d元 ≤ 15元，仅展示最快路线", toll_diff)

    return options


# ── 对外接口 ─────────────────────────────────────────────────────────────────

async def get_transport_options(
    origin: str, destination: str
) -> TransportResult | None:
    """
    获取两城市间完整的交通方案。
    自驾：优先高德实时 API，失败降级内置数据库
    列车：始终使用内置数据库（分高铁/动车/普通列车三类）
    """
    origin      = origin.strip()
    destination = destination.strip()
    if not origin or not destination or origin == destination:
        logger.debug("get_transport_options: 无效输入（origin=%r, dest=%r），跳过", origin, destination)
        return None

    t0  = time.perf_counter()
    key = frozenset({origin, destination})
    logger.info("交通方案请求：%s → %s", origin, destination)

    # ── 列车：仅京沪展示参考示例，其余城市对 train_notice 引导 12306 ───────────
    nk = frozenset({_normalize_city_name(origin), _normalize_city_name(destination)})
    if _is_beijing_shanghai_pair(origin, destination):
        train_options = list(_TRAIN_DB.get(nk, []))
        train_notice = ""
        if train_options:
            logger.info("[Train DB] 京沪参考示例 → %d 类", len(train_options))
    else:
        train_options = []
        train_notice = TRAIN_NOTICE_12306
        logger.info("[Train] 非京沪城市对，不展示列车参考；提示 12306")

    # ── 自驾数据 ───────────────────────────────────────────────────────────
    api_key = os.getenv("AMAP_API_KEY", "").strip()
    drive_options: list[DriveRouteOption] = []
    drive_source = ""

    if api_key:
        logger.info("[Drive] AMAP_API_KEY 已配置，调用高德实时驾车 API ...")
        drive_options = await _fetch_amap_drive_options(origin, destination, api_key)
        if drive_options:
            drive_source = "高德地图实时"
            logger.info("[Drive] 高德 API 返回 %d 个方案", len(drive_options))
        else:
            logger.warning("[Drive] 高德 API 无有效数据，降级内置参考数据")
    else:
        logger.warning("[Drive] 未配置 AMAP_API_KEY，使用内置自驾参考数据（设置 AMAP_API_KEY 可获得实时数据）")

    if not drive_options:
        drive_options = list(_DRIVE_FALLBACK.get(key, []))
        drive_source  = "内置参考"
        if drive_options:
            logger.info("[Drive] 内置数据库命中：%s↔%s → %d 个方案", origin, destination, len(drive_options))
        else:
            logger.warning("[Drive] 内置数据库无记录：%s↔%s", origin, destination)

    # ── 数据来源汇总 ────────────────────────────────────────────────────────
    if not drive_options and not train_options:
        logger.warning("无任何数据：%s→%s，返回占位提示", origin, destination)
        return TransportResult(
            origin=origin,
            destination=destination,
            drive_options=[DriveRouteOption(
                strategy="参考路线", duration_minutes=0, distance_km=0, toll_yuan=0,
                main_highways=[],
                tips=[f"暂无 {origin}→{destination} 的内置数据，请使用高德地图查询自驾路线"],
            )],
            train_options=[],
            data_source="内置数据（暂无该城市对记录）",
            train_notice=train_notice,
        )

    if drive_source == "高德地图实时":
        source = (
            "高德地图（实时驾车）+ 京沪列车参考（若适用）"
            if train_options
            else "高德地图（实时驾车）"
        )
    else:
        source = (
            "内置参考（自驾）+ 京沪列车参考（若适用）"
            if train_options
            else "内置参考（自驾）；列车请查 12306"
        )

    elapsed = time.perf_counter() - t0
    logger.info(
        "交通方案完成 (%.2fs) → 自驾:%d[%s] 列车:%d 来源：%s",
        elapsed, len(drive_options), drive_source, len(train_options), source,
    )

    return TransportResult(
        origin=origin,
        destination=destination,
        drive_options=drive_options,
        train_options=train_options,
        data_source=source,
        train_notice=train_notice,
    )
