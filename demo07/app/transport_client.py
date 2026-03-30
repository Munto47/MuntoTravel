"""
transport_client.py —— demo07 精细化交通规划

demo06 vs demo07 的核心差异：
  demo06：高铁和公共交通混为一谈（transit），数据粗糙
  demo07：
    1. 自驾：调用高德 API，返回「最快路线」和「少收费路线」两个方案，
            提取主要途经高速路名，为后续端到端路径规划做基础
    2. 列车：严格拆分三类，各有独立数据卡片
       - 高铁 (G/C)：时速 350km/h，高铁专线站，票价高
       - 动车 (D)  ：时速 250km/h，兼顾普速站，票价中等
       - 普通列车 (K/Z/T)：夜铺为主，时速 120-160km/h，票价低
    3. 每类列车附带：具体班次示例（车次+发到时间）、价格表、上车/下车站

设计原则：
  有 AMAP_API_KEY → 实时调用高德 Geocoding + Driving（策略0和策略1）
  无 AMAP_API_KEY → 使用内置自驾参考数据降级
  列车数据：始终使用内置数据库（12306 无公开 API，以参考数据为准）
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
_DRIVE_URL = "https://restapi.amap.com/v3/direction/driving"


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
    train_options: list[TrainTypeOption]    # 1-3 个列车类型
    data_source: str                        # "高德地图实时" / "内置参考数据"
    data_note: str = "列车时刻表仅供参考，实际班次及票价请在铁路12306确认"

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "drive_options": [d.to_dict() for d in self.drive_options],
            "train_options": [t.to_dict() for t in self.train_options],
            "data_source": self.data_source,
            "data_note": self.data_note,
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
            lines.append("\n◆ 铁路出行")
            for train in self.train_options:
                lines.append(f"  {train.to_text()}")

        lines.append(f"\n⚠ {self.data_note}")
        lines.append(
            "【行程安排要求】"
            "第一天请在上午/早晨写明具体的出发方式（选择哪种交通工具，几点出发，几点到达）；"
            "最后一天请写明返程安排（几点出发，估计几点到家）。"
        )
        return "\n".join(lines)


# ── 列车参考数据库 ────────────────────────────────────────────────────────────
# 按 frozenset{城市A, 城市B} 索引，值为 TrainTypeOption 参数列表
# 所有数据均为参考数据，实际班次以12306为准

def _ts(no: str, dep: str, arr: str) -> TrainScheduleSample:
    return TrainScheduleSample(no, dep, arr)


_TRAIN_DB: dict[frozenset, list[TrainTypeOption]] = {

    frozenset({"上海", "杭州"}): [
        TrainTypeOption(
            train_type="高铁 / 城际",
            prefix="G、C",
            speed_desc="最高时速 350km/h",
            from_station="上海虹桥",
            to_station="杭州东",
            duration_desc="约 50-65 分钟",
            frequency="约 5-10 分钟一班（早 6:30 - 晚 23:00）",
            prices={"二等座": 73, "一等座": 117, "商务座": 238},
            sample_trains=[_ts("G7", "08:00", "08:51"), _ts("G9", "09:00", "09:51"), _ts("G19", "10:05", "10:51")],
            highlights=["班次极密集，随买随走", "上海虹桥站接地铁2/10号线", "准点率极高，全程快捷"],
            booking_tips="铁路12306 App，当日购票即可",
        ),
    ],

    frozenset({"北京", "上海"}): [
        TrainTypeOption(
            train_type="高铁",
            prefix="G",
            speed_desc="最高时速 350km/h",
            from_station="北京南",
            to_station="上海虹桥",
            duration_desc="约 4.5-5.5 小时（视停站数量）",
            frequency="约 15-30 分钟一班",
            prices={"二等座": 553, "一等座": 935, "商务座": 1748},
            sample_trains=[_ts("G1", "07:00", "12:28"), _ts("G3", "08:00", "13:28"), _ts("G7", "08:30", "13:48")],
            highlights=["全程最快约 4h18min（G2次）", "北京南→上海虹桥，两端均为交通枢纽", "黄金周建议提前30天购票"],
            booking_tips="节假日建议提前 7-30 天在铁路12306购票",
        ),
        TrainTypeOption(
            train_type="普通列车（夜铺）",
            prefix="T、Z",
            speed_desc="最高时速 160km/h",
            from_station="北京站 / 北京西",
            to_station="上海站",
            duration_desc="约 12-14 小时（晚发次日上午到）",
            frequency="每日 3-5 班（傍晚出发）",
            prices={"硬卧（下铺）": 330, "硬卧（上铺）": 285, "软卧": 510},
            sample_trains=[_ts("T109", "19:33", "09:47+1"), _ts("Z7", "20:00", "09:09+1")],
            highlights=["睡一觉到达，省一晚住宿", "到上海市中心站（虹桥→市区需再转），可直达南京西路", "票价约为高铁一半"],
            booking_tips="建议提前 7-15 天购票，热门班次卧铺抢手",
        ),
    ],

    frozenset({"北京", "西安"}): [
        TrainTypeOption(
            train_type="高铁",
            prefix="G",
            speed_desc="最高时速 350km/h",
            from_station="北京西",
            to_station="西安北",
            duration_desc="约 4.5-5.5 小时",
            frequency="约 30-60 分钟一班",
            prices={"二等座": 374, "一等座": 598, "商务座": 1197},
            sample_trains=[_ts("G87", "07:05", "11:58"), _ts("G91", "08:15", "13:08"), _ts("G649", "09:00", "14:01")],
            highlights=["走郑西高铁途经郑州", "西安北站地铁14号线直达市区约20分钟"],
            booking_tips="建议提前 3-7 天购票",
        ),
        TrainTypeOption(
            train_type="普通列车（夜铺）",
            prefix="K、Z",
            speed_desc="最高时速 160km/h",
            from_station="北京西",
            to_station="西安站",
            duration_desc="约 11-13 小时（过夜直达）",
            frequency="每日 2-4 班",
            prices={"硬卧（下铺）": 220, "硬卧（上铺）": 193, "软卧": 345},
            sample_trains=[_ts("K691", "22:00", "09:55+1"), _ts("Z19", "21:08", "08:03+1")],
            highlights=["到达西安老站，步行可达钟鼓楼、回民街", "价格实惠，适合背包旅行"],
            booking_tips="建议提前 7 天购票",
        ),
    ],

    frozenset({"北京", "成都"}): [
        TrainTypeOption(
            train_type="高铁",
            prefix="G",
            speed_desc="最高时速 350km/h",
            from_station="北京西",
            to_station="成都东",
            duration_desc="约 7.5-8.5 小时（全程直达）",
            frequency="约 1-2 小时一班",
            prices={"二等座": 812, "一等座": 1295, "商务座": 2597},
            sample_trains=[_ts("G307", "07:40", "15:58"), _ts("G89", "09:00", "17:28"), _ts("G309", "10:30", "18:48")],
            highlights=["全程无需换乘", "成都东站地铁7号线直达市区", "途经武汉，可欣赏华中风光"],
            booking_tips="建议提前 7-14 天购票，旺季提前更多",
        ),
        TrainTypeOption(
            train_type="普通列车（长途卧铺）",
            prefix="K",
            speed_desc="最高时速 120km/h",
            from_station="北京西",
            to_station="成都",
            duration_desc="约 26-30 小时（过夜+半天）",
            frequency="每日 1-2 班",
            prices={"硬卧（下铺）": 360, "硬卧（上铺）": 320, "软卧": 580},
            sample_trains=[_ts("K17", "18:40", "21:24+1")],
            highlights=["适合极度预算紧张的旅行者", "可携带大件行李，卧铺可充分休息"],
            booking_tips="建议提前 15 天购票，旅游旺季提前更多",
        ),
    ],

    frozenset({"成都", "重庆"}): [
        TrainTypeOption(
            train_type="高铁 / 动车（城际）",
            prefix="G、D",
            speed_desc="最高时速 350km/h",
            from_station="成都东",
            to_station="重庆北",
            duration_desc="约 1-1.5 小时",
            frequency="约 10-15 分钟一班（高峰期更密）",
            prices={"二等座": 165, "一等座": 265, "商务座": 528},
            sample_trains=[_ts("G8504", "08:00", "08:56"), _ts("G8506", "09:05", "10:03"), _ts("G8512", "10:00", "10:58")],
            highlights=["成渝城际堪比地铁频率", "重庆北站地铁10号线直达市中心", "当日往返完全可行"],
            booking_tips="随买随走，可在站内自动取票机购票",
        ),
    ],

    frozenset({"上海", "南京"}): [
        TrainTypeOption(
            train_type="高铁 / 动车",
            prefix="G、D",
            speed_desc="最高时速 350km/h",
            from_station="上海虹桥",
            to_station="南京南",
            duration_desc="约 1-1.5 小时",
            frequency="约 10-15 分钟一班",
            prices={"二等座": 139, "一等座": 222, "商务座": 446},
            sample_trains=[_ts("G103", "07:00", "08:16"), _ts("G107", "08:00", "09:16"), _ts("G109", "09:00", "10:16")],
            highlights=["南京南站地铁1/3号线直达夫子庙/新街口", "班次密集，无需提前购票"],
            booking_tips="节假日建议提前 1-3 天购票",
        ),
    ],

    frozenset({"广州", "深圳"}): [
        TrainTypeOption(
            train_type="城际高铁",
            prefix="G、C",
            speed_desc="最高时速 350km/h",
            from_station="广州南",
            to_station="深圳北",
            duration_desc="约 30-40 分钟",
            frequency="约 5 分钟一班（堪比地铁）",
            prices={"二等座": 75, "一等座": 120, "商务座": 240},
            sample_trains=[_ts("G6001", "07:00", "07:34"), _ts("G6003", "07:05", "07:39"), _ts("G6005", "07:10", "07:44")],
            highlights=["全国班次最密集城际线路之一", "深圳北站地铁4/5号线直达福田/南山"],
            booking_tips="随买随走，自动售票机即可购票",
        ),
        TrainTypeOption(
            train_type="普通城际列车",
            prefix="T、K",
            speed_desc="最高时速 160km/h",
            from_station="广州站",
            to_station="深圳站（罗湖）",
            duration_desc="约 1 小时",
            frequency="约 30-60 分钟一班",
            prices={"硬座": 45, "软座": 75},
            sample_trains=[_ts("T812", "08:30", "09:28"), _ts("K832", "09:00", "10:02")],
            highlights=["到达罗湖口岸旁，步行即可过关去香港", "价格比城际高铁便宜约40%"],
            booking_tips="广州站当天购票即可",
        ),
    ],

    frozenset({"北京", "天津"}): [
        TrainTypeOption(
            train_type="城际高铁",
            prefix="C、G",
            speed_desc="最高时速 350km/h",
            from_station="北京南",
            to_station="天津",
            duration_desc="约 30 分钟",
            frequency="约 10 分钟一班（高峰期更密）",
            prices={"二等座": 54, "一等座": 80, "商务座": 140},
            sample_trains=[_ts("C2001", "06:15", "06:44"), _ts("C2003", "06:40", "07:10"), _ts("G9001", "07:00", "07:30")],
            highlights=["京津城际是中国首条城际高铁", "天津站紧邻海河景区，出站步行即游", "可随时日归"],
            booking_tips="随买随走",
        ),
    ],

    frozenset({"上海", "苏州"}): [
        TrainTypeOption(
            train_type="高铁",
            prefix="G、D",
            speed_desc="最高时速 350km/h",
            from_station="上海虹桥",
            to_station="苏州北",
            duration_desc="约 24-35 分钟",
            frequency="约 5-10 分钟一班",
            prices={"二等座": 24, "一等座": 39, "商务座": 78},
            sample_trains=[_ts("G7011", "07:00", "07:24"), _ts("G7015", "08:00", "08:24"), _ts("G7019", "09:00", "09:24")],
            highlights=["全国票价最低廉的高铁路段之一", "苏州北站地铁2号线直达观前街/拙政园", "极适合日归出行"],
            booking_tips="随买随走，当天购票无需提前",
        ),
    ],

    frozenset({"西安", "成都"}): [
        TrainTypeOption(
            train_type="高铁（西成高铁）",
            prefix="G、D",
            speed_desc="最高时速 250km/h（山区限速）",
            from_station="西安北",
            to_station="成都东",
            duration_desc="约 3.5-4.5 小时",
            frequency="约 30-60 分钟一班",
            prices={"二等座": 245, "一等座": 393, "商务座": 786},
            sample_trains=[_ts("G2201", "07:15", "11:03"), _ts("G2205", "08:30", "12:28"), _ts("G2209", "10:00", "13:56")],
            highlights=["穿越秦岭，全程经过 21 座隧道", "沿途风景壮观，是中国最美高铁路线之一", "终点站成都东接地铁7号线"],
            booking_tips="建议提前 3-7 天购票，节假日提前更多",
        ),
    ],

    frozenset({"广州", "桂林"}): [
        TrainTypeOption(
            train_type="动车 / 高铁",
            prefix="G、D",
            speed_desc="最高时速 250km/h",
            from_station="广州南",
            to_station="桂林北",
            duration_desc="约 2.5-3 小时",
            frequency="约 30-60 分钟一班",
            prices={"二等座": 223, "一等座": 357, "商务座": 714},
            sample_trains=[_ts("D2801", "07:50", "10:45"), _ts("G2901", "09:00", "11:41"), _ts("D2803", "10:20", "13:12")],
            highlights=["桂林北站需换乘大巴/滴滴约30分钟入市区", "部分班次停靠桂林站（市区站，步行可达象鼻山）"],
            booking_tips="建议提前 3-7 天购票",
        ),
    ],

    frozenset({"上海", "黄山"}): [
        TrainTypeOption(
            train_type="高铁 / 动车",
            prefix="G、D",
            speed_desc="最高时速 250km/h",
            from_station="上海虹桥",
            to_station="黄山北",
            duration_desc="约 2-2.5 小时",
            frequency="约 30-60 分钟一班",
            prices={"二等座": 177, "一等座": 283, "商务座": 566},
            sample_trains=[_ts("G2381", "07:47", "10:03"), _ts("G2383", "09:05", "11:17"), _ts("G2385", "10:50", "13:06")],
            highlights=["黄山北→景区大门约1小时车程（需打车/乘景区大巴）", "旺季（4-5月、9-10月）请提早出发"],
            booking_tips="旅游旺季提前 7-14 天购票",
        ),
    ],

    frozenset({"北京", "青岛"}): [
        TrainTypeOption(
            train_type="高铁",
            prefix="G",
            speed_desc="最高时速 350km/h",
            from_station="北京南",
            to_station="青岛",
            duration_desc="约 4.5-5 小时",
            frequency="约 30-60 分钟一班",
            prices={"二等座": 321, "一等座": 513, "商务座": 1026},
            sample_trains=[_ts("G195", "07:30", "12:37"), _ts("G53", "08:00", "12:55"), _ts("G197", "09:00", "14:09")],
            highlights=["青岛站位于市区，步行可达五四广场、栈桥", "暑期（7-8月）请提前购票"],
            booking_tips="暑期提前 7-14 天购票",
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
    """从高德 Driving steps 中提取主要高速/国道名称（去重保序）"""
    seen: set[str] = set()
    result: list[str] = []
    for step in steps:
        road = step.get("road", "").strip()
        if not road:
            road = step.get("instruction", "")
        # 只保留明确的公路名称
        for kw in ["高速", "国道", "快速路"]:
            if kw in road and road not in seen:
                seen.add(road)
                # 截断过长的名称
                result.append(road[:12] if len(road) > 12 else road)
                break
    return result[:4]  # 最多4条


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
                "extensions": "all",   # 获取 steps 用于提取高速名
            })
            resp.raise_for_status()
            data = resp.json()

        ms = int((time.perf_counter() - t0) * 1000)

        if data.get("status") != "1":
            logger.warning("[Drive !!] 策略%d(%s) 失败 (%dms): %s", strategy, label, ms, data.get("info"))
            return None

        path = data["route"]["paths"][0]
        duration_min = int(path["duration"]) // 60
        distance_km  = int(path["distance"]) / 1000
        toll_yuan    = int(path.get("tolls", 0))
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

    # ── 列车数据（本地库）──────────────────────────────────────────────────
    train_options = list(_TRAIN_DB.get(key, []))
    if train_options:
        train_types = [t.train_type for t in train_options]
        logger.info("[Train DB OK] %s↔%s 命中 → %d 类：%s",
                    origin, destination, len(train_options), " / ".join(train_types))
    else:
        logger.warning("[Train DB !!] %s↔%s 无记录（可在 _TRAIN_DB 中补充）", origin, destination)

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
        )

    if drive_source == "高德地图实时":
        source = "高德地图（实时驾车）+ 内置列车参考数据"
    else:
        source = "内置参考数据（自驾+列车均为参考值，请以导航/12306为准）"

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
    )
