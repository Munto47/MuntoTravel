"""transport_client.py —— 高德地图路径规划 API 封装

有 AMAP_API_KEY  → 调用高德 Geocoding / Driving / Transit 实时接口
无 AMAP_API_KEY  → 使用内置主要城市对数据库降级

设计原则：与 weather_client.py 保持一致的「双源 + 降级」模式
"""

from __future__ import annotations
import os
import httpx

# ── 高德 API 端点 ────────────────────────────────────────────────────────────────
_GEO_URL     = "https://restapi.amap.com/v3/geocode/geo"
_DRIVE_URL   = "https://restapi.amap.com/v3/direction/driving"
_TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"


# ── 数据类（轻量级，不依赖 schemas.py 避免循环引用）───────────────────────────────
class TransportOption:
    def __init__(
        self,
        mode: str,
        mode_name: str,
        duration_minutes: int,
        distance_km: float,
        cost_estimate: str,
        key_info: str,
        tips: list[str] | None = None,
    ):
        self.mode = mode
        self.mode_name = mode_name
        self.duration_minutes = duration_minutes
        self.distance_km = distance_km
        self.cost_estimate = cost_estimate
        self.key_info = key_info
        self.tips = tips or []

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "mode_name": self.mode_name,
            "duration_minutes": self.duration_minutes,
            "distance_km": self.distance_km,
            "cost_estimate": self.cost_estimate,
            "key_info": self.key_info,
            "tips": self.tips,
        }


class TransportResult:
    def __init__(
        self,
        origin: str,
        destination: str,
        options: list[TransportOption],
        source: str,
    ):
        self.origin = origin
        self.destination = destination
        self.options = options
        self.source = source

    def to_prompt_text(self) -> str:
        """生成适合注入 LLM 提示词的文字描述"""
        lines = [f"【交通方案：{self.origin} → {self.destination}（数据来源：{self.source}）】"]
        for opt in self.options:
            h, m = divmod(opt.duration_minutes, 60)
            duration_str = f"{h}小时{m}分钟" if h else f"{m}分钟"
            lines.append(f"\n✦ {opt.mode_name}")
            lines.append(f"  时长：约{duration_str}  距离：约{opt.distance_km:.0f}km")
            lines.append(f"  费用：{opt.cost_estimate}")
            lines.append(f"  说明：{opt.key_info}")
            for tip in opt.tips:
                lines.append(f"  💡 {tip}")
        lines.append("\n请在第一天行程中写明出发安排，在最后一天写明返程参考。")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "options": [o.to_dict() for o in self.options],
            "source": self.source,
        }


# ── 内置城市对数据库（常见跨城路线参考数据）─────────────────────────────────────────
# key: frozenset{城市A, 城市B}  value: {driving: {...}, transit: {...}}
_ROUTE_DB: dict[frozenset, dict] = {
    frozenset({"北京", "上海"}): {
        "driving": {
            "duration": 720, "distance": 1318,
            "cost": "约180元过路费",
            "key_info": "走 G2 京沪高速，全程约12小时",
            "tips": ["长途驾车建议2人轮驾", "沿途有多个服务区可休息"],
        },
        "transit": {
            "duration": 330, "distance": 1318,
            "cost": "二等座约553元，一等座约935元",
            "key_info": "京沪高铁（G字头），北京南 → 上海虹桥，约5.5小时",
            "tips": ["班次密集，30分钟内必有一班", "建议提前3-7天购票"],
        },
    },
    frozenset({"上海", "杭州"}): {
        "driving": {
            "duration": 120, "distance": 180,
            "cost": "约35元过路费",
            "key_info": "沪杭高速，全程约2小时",
            "tips": ["周末节假日高峰期可能拥堵，建议错峰出行"],
        },
        "transit": {
            "duration": 50, "distance": 175,
            "cost": "二等座约73元",
            "key_info": "沪杭高铁（G字头），上海虹桥 → 杭州东，约50分钟",
            "tips": ["班次极密集，随买随走", "杭州东站地铁直达西湖景区"],
        },
    },
    frozenset({"北京", "西安"}): {
        "driving": {
            "duration": 720, "distance": 1200,
            "cost": "约120元过路费",
            "key_info": "走 G5 京昆高速，全程约12小时",
            "tips": ["长途驾车疲劳，强烈建议高铁"],
        },
        "transit": {
            "duration": 340, "distance": 1215,
            "cost": "二等座约374元，一等座约598元",
            "key_info": "高铁（G/D字头），北京西 → 西安北，约5.5小时",
            "tips": ["G字头最快，全程无需换乘"],
        },
    },
    frozenset({"北京", "成都"}): {
        "driving": {
            "duration": 1140, "distance": 1870,
            "cost": "约200元过路费",
            "key_info": "G5 京昆高速，全程约19小时，路途极长",
            "tips": ["强烈建议高铁或飞机"],
        },
        "transit": {
            "duration": 500, "distance": 1881,
            "cost": "二等座约812元，一等座约1295元",
            "key_info": "高铁（G字头），北京西 → 成都东，约8小时",
            "tips": ["全程直达，部分班次需提前半个月购票"],
        },
    },
    frozenset({"成都", "重庆"}): {
        "driving": {
            "duration": 150, "distance": 310,
            "cost": "约50元过路费",
            "key_info": "成渝高速，全程约2.5小时",
            "tips": ["也可走成渝环线高速，沿途风景更好"],
        },
        "transit": {
            "duration": 90, "distance": 308,
            "cost": "二等座约165元",
            "key_info": "成渝高铁（G字头），成都东 → 重庆北，约1.5小时",
            "tips": ["班次密集，出行灵活"],
        },
    },
    frozenset({"上海", "南京"}): {
        "driving": {
            "duration": 180, "distance": 300,
            "cost": "约55元过路费",
            "key_info": "沪宁高速，全程约3小时",
            "tips": ["周末早高峰可能拥堵"],
        },
        "transit": {
            "duration": 75, "distance": 301,
            "cost": "二等座约139元",
            "key_info": "沪宁城际/高铁，上海虹桥 → 南京南，约1小时15分",
            "tips": ["班次密集，南京南站地铁可达市区"],
        },
    },
    frozenset({"广州", "深圳"}): {
        "driving": {
            "duration": 90, "distance": 140,
            "cost": "约35元过路费",
            "key_info": "广深高速，全程约1.5小时",
            "tips": ["高峰期可能拥堵2小时以上，建议高铁"],
        },
        "transit": {
            "duration": 30, "distance": 105,
            "cost": "二等座约75元",
            "key_info": "广深高铁（G字头），广州南 → 深圳北，约30分钟",
            "tips": ["班次极密集，5分钟一班"],
        },
    },
    frozenset({"北京", "天津"}): {
        "driving": {
            "duration": 90, "distance": 137,
            "cost": "约30元过路费",
            "key_info": "京津高速，全程约1.5小时",
            "tips": [],
        },
        "transit": {
            "duration": 30, "distance": 117,
            "cost": "二等座约54元",
            "key_info": "京津城际（G字头），北京南 → 天津，约30分钟",
            "tips": ["班次极密集，10分钟一班，堪比地铁"],
        },
    },
    frozenset({"上海", "苏州"}): {
        "driving": {
            "duration": 80, "distance": 100,
            "cost": "约25元过路费",
            "key_info": "沪宁高速，全程约1.5小时",
            "tips": [],
        },
        "transit": {
            "duration": 30, "distance": 84,
            "cost": "二等座约24元",
            "key_info": "沪宁城际高铁，上海虹桥 → 苏州北，约30分钟",
            "tips": ["非常便捷，当日往返轻松"],
        },
    },
    frozenset({"西安", "成都"}): {
        "driving": {
            "duration": 360, "distance": 700,
            "cost": "约90元过路费",
            "key_info": "全程约6小时，部分路段在山区",
            "tips": ["山区路段注意安全，冬季可能结冰"],
        },
        "transit": {
            "duration": 240, "distance": 698,
            "cost": "二等座约245元，一等座约393元",
            "key_info": "西成高铁（G字头），西安北 → 成都东，约4小时",
            "tips": ["穿越秦岭山脉，沿途风景壮观"],
        },
    },
    frozenset({"广州", "桂林"}): {
        "driving": {
            "duration": 300, "distance": 480,
            "cost": "约80元过路费",
            "key_info": "广昆高速，全程约5小时",
            "tips": [],
        },
        "transit": {
            "duration": 150, "distance": 499,
            "cost": "二等座约223元",
            "key_info": "高铁，广州南 → 桂林北，约2.5小时",
            "tips": ["桂林北站距离市区较远，可转乘大巴或滴滴"],
        },
    },
    frozenset({"上海", "黄山"}): {
        "driving": {
            "duration": 210, "distance": 370,
            "cost": "约60元过路费",
            "key_info": "全程约3.5小时",
            "tips": [],
        },
        "transit": {
            "duration": 120, "distance": 399,
            "cost": "二等座约177元",
            "key_info": "高铁，上海虹桥 → 黄山北，约2小时",
            "tips": ["黄山北站可换乘景区班车"],
        },
    },
    frozenset({"北京", "青岛"}): {
        "driving": {
            "duration": 360, "distance": 630,
            "cost": "约100元过路费",
            "key_info": "京沪高速，全程约6小时",
            "tips": [],
        },
        "transit": {
            "duration": 300, "distance": 814,
            "cost": "二等座约321元",
            "key_info": "高铁（G字头），北京南 → 青岛，约5小时",
            "tips": ["青岛站地处市区，更方便"],
        },
    },
}


def _make_option(mode: str, mode_name: str, data: dict) -> TransportOption:
    return TransportOption(
        mode=mode,
        mode_name=mode_name,
        duration_minutes=data["duration"],
        distance_km=float(data["distance"]),
        cost_estimate=data["cost"],
        key_info=data["key_info"],
        tips=data.get("tips", []),
    )


def _fallback_routes(origin: str, destination: str) -> TransportResult:
    """内置数据库降级查询"""
    key = frozenset({origin, destination})
    data = _ROUTE_DB.get(key)

    if not data:
        return TransportResult(
            origin=origin,
            destination=destination,
            options=[
                TransportOption(
                    mode="unknown",
                    mode_name="暂无详细数据",
                    duration_minutes=0,
                    distance_km=0.0,
                    cost_estimate="暂无数据",
                    key_info=f"暂无 {origin}→{destination} 的内置数据，建议在高德地图或12306查询",
                    tips=["可使用高德地图 App 查询实时路线和票价"],
                )
            ],
            source="内置数据（该路线暂无详细记录）",
        )

    options = []
    if "driving" in data:
        options.append(_make_option("driving", "自驾", data["driving"]))
    if "transit" in data:
        options.append(_make_option("transit", "高铁/公共交通", data["transit"]))

    return TransportResult(
        origin=origin,
        destination=destination,
        options=options,
        source="内置参考数据（实际班次请在12306或高德地图确认）",
    )


# ── 高德实时 API（有 API Key 时使用）────────────────────────────────────────────────
async def _geocode(city: str, api_key: str) -> str | None:
    """城市名 → 高德坐标字符串（经度,纬度）"""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _GEO_URL,
                params={"key": api_key, "address": city, "output": "JSON"},
            )
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "1" and data.get("geocodes"):
            return data["geocodes"][0]["location"]
    except Exception as e:
        print(f"[Transport] Geocode 失败 [{city}]: {e}")
    return None


async def _fetch_driving(
    origin_loc: str, dest_loc: str, api_key: str
) -> TransportOption | None:
    """调用高德驾车路线 API"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _DRIVE_URL,
                params={
                    "key": api_key,
                    "origin": origin_loc,
                    "destination": dest_loc,
                    "strategy": "0",
                    "extensions": "base",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            return None

        path = data["route"]["paths"][0]
        duration_min = int(path["duration"]) // 60
        distance_km = int(path["distance"]) / 1000
        tolls = int(path.get("tolls", 0))
        cost_str = f"约{tolls}元过路费" if tolls > 0 else "过路费较少（具体以实际为准）"

        return TransportOption(
            mode="driving",
            mode_name="自驾",
            duration_minutes=duration_min,
            distance_km=distance_km,
            cost_estimate=cost_str,
            key_info=f"全程约{distance_km:.0f}km",
            tips=["实际路况和费用请以高德地图为准"],
        )
    except Exception as e:
        print(f"[Transport] 驾车路线 API 失败: {e}")
        return None


async def _fetch_transit(
    origin_loc: str,
    dest_loc: str,
    origin: str,
    destination: str,
    api_key: str,
) -> TransportOption | None:
    """调用高德公共交通路线 API"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _TRANSIT_URL,
                params={
                    "key": api_key,
                    "origin": origin_loc,
                    "destination": dest_loc,
                    "city": origin,
                    "cityd": destination,
                    "strategy": "0",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        route = data.get("route", {})
        transits = route.get("transits", [])
        if data.get("status") != "1" or not transits:
            return None

        t = transits[0]
        duration_min = int(t.get("duration", 0)) // 60
        distance_km = float(route.get("distance", 0)) / 1000

        # 提取关键换乘线路名称
        seg_names: list[str] = []
        for seg in t.get("segments", []):
            for line in seg.get("bus", {}).get("buslines", []):
                name = line.get("name", "")
                if name:
                    seg_names.append(name)
        key_info = "、".join(seg_names[:2]) if seg_names else "公共交通（详情见高德地图）"

        cost_val = route.get("taxi_cost", "")
        cost_str = f"约{cost_val}元" if cost_val else "票价待查（请在12306确认）"

        return TransportOption(
            mode="transit",
            mode_name="高铁/公共交通",
            duration_minutes=duration_min,
            distance_km=distance_km,
            cost_estimate=cost_str,
            key_info=key_info,
            tips=["建议提前在12306或铁路12306 App购票"],
        )
    except Exception as e:
        print(f"[Transport] 公共交通 API 失败: {e}")
        return None


# ── 对外接口 ─────────────────────────────────────────────────────────────────────
async def get_transport_options(origin: str, destination: str) -> TransportResult | None:
    """
    获取出发城市到目的地的交通方案。

    优先调用高德地图实时 API（需要 AMAP_API_KEY），失败时降级到内置数据库。
    若出发地和目的地相同，返回 None。
    """
    origin = origin.strip()
    destination = destination.strip()

    if not origin or not destination or origin == destination:
        return None

    api_key = os.getenv("AMAP_API_KEY", "").strip()

    if api_key:
        origin_loc = await _geocode(origin, api_key)
        dest_loc = await _geocode(destination, api_key)

        if origin_loc and dest_loc:
            options: list[TransportOption] = []

            driving = await _fetch_driving(origin_loc, dest_loc, api_key)
            if driving:
                options.append(driving)

            transit = await _fetch_transit(origin_loc, dest_loc, origin, destination, api_key)
            if transit:
                options.append(transit)

            if options:
                return TransportResult(
                    origin=origin,
                    destination=destination,
                    options=options,
                    source="高德地图（实时数据）",
                )

        print(f"[Transport] 高德 API 调用失败，降级到内置数据库")

    return _fallback_routes(origin, destination)
