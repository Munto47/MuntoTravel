"""
schemas.py —— demo11 数据模型

demo10 → demo11 新增：
  PlanResponse 新增 coord_map 字段（地点名 → "lng,lat"）
  由 route_node 在路线计算后从 _coord_cache 提取，供前端高德 JSAPI 2.0 地图可视化

问卷系统（demo10 不变）：
  1. GET /api/questionnaire        → 返回 10 题问卷
  2. POST /api/profile {answers}   → 返回 profile_note 字符串
  3. POST /api/trip/plan {... profile_note} → 规划时 LLM 参考画像
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── 兴趣偏好 ─────────────────────────────────────────────────────────────────

PREFERENCE_OPTIONS = ["历史文化", "自然风景", "美食探索", "购物", "夜生活", "亲子游"]
BUDGET_OPTIONS = Literal["low", "medium", "high"]


# ── Agent 执行日志 ────────────────────────────────────────────────────────────

class AgentLog(BaseModel):
    agent: str
    label: str
    icon: str
    status: str       # ok / warn / skip / fail
    duration_ms: int
    detail: str
    source: str = ""


# ── 路线段（route_node 填充）────────────────────────────────────────────────

class RouteSegment(BaseModel):
    """
    相邻两个地点之间的路线信息。
    由 route_node 在 planner_node 之后填充，不经过 LLM。

    mode 取值：
      步行     —— 距离 ≤ 1.5km，高德步行 API
      骑行     —— 1.5km < 距离 ≤ 5km，高德骑行 v5 API
      公交/地铁 —— 距离 > 5km，高德公交 API（含线路名）
      打车参考  —— 无 API key 时的估算值
      市内公交  —— geocode 失败时的兜底估算
    """
    from_name: str
    to_name: str
    mode: str           # "步行" / "骑行" / "公交/地铁" / "打车参考" / "市内公交"
    duration_min: int   # 预计耗时（分钟）
    distance_m: int     # 距离（米），0 表示无坐标数据
    tip: str = ""       # 补充提示，如"乘坐 X路公交"
    is_estimated: bool = False  # True 表示为估算值（非真实 API 计算）


# ── 请求模型 ─────────────────────────────────────────────────────────────────

class PlanRequest(BaseModel):
    city: str           = Field(..., description="目的地城市")
    origin: str         = Field(default="", description="出发城市（可选）")
    hotel: str          = Field(default="", description="住宿地点（可选，用于每日路线起点）")
    travel_days: int    = Field(..., ge=1, le=7)
    preferences: List[str] = Field(default_factory=list)
    budget_level: str   = Field(default="medium")
    notes: str          = Field(default="")
    profile_note: str   = Field(default="", description="由问卷生成的用户画像文本（可选）")


# ── 问卷相关模型（demo10 新增）────────────────────────────────────────────────

class ProfileRequest(BaseModel):
    """前端提交问卷答案的请求体。"""
    answers: List[str] = Field(..., description="选项 ID 列表，如 ['Q1A', 'Q3B', 'Q5A']")


class ProfileResponse(BaseModel):
    """compute_profile() 的 API 返回。"""
    profile_note: str   = Field(description="可直接插入 planner 的用户画像文本")
    dimension_count: int = Field(description="覆盖了几个维度")


# ── 天气模型 ─────────────────────────────────────────────────────────────────

class DayWeather(BaseModel):
    date: str
    condition: str
    temp_max: float
    temp_min: float
    precipitation: float
    wind_desc: str = ""


class WeatherInfo(BaseModel):
    city: str
    days: List[DayWeather]
    source: str = "未知"

    def to_prompt_text(self) -> str:
        lines = [f"【{self.city} 出行天气预报（{self.source}）】"]
        for d in self.days:
            rain = "（注意携带雨具）" if d.precipitation >= 5 else ""
            wind = f"，{d.wind_desc}" if d.wind_desc else ""
            lines.append(
                f"  {d.date}：{d.condition}，{d.temp_min:.0f}°C~{d.temp_max:.0f}°C，"
                f"降水{d.precipitation:.1f}mm{rain}{wind}"
            )
        return "\n".join(lines)


# ── 交通模型 ─────────────────────────────────────────────────────────────────

class DriveRouteSchema(BaseModel):
    strategy: str
    duration_minutes: int
    distance_km: float
    toll_yuan: int
    main_highways: List[str]
    tips: List[str]


class TrainScheduleSampleSchema(BaseModel):
    number: str
    dep: str
    arr: str


class TrainTypeSchema(BaseModel):
    train_type: str
    prefix: str
    speed_desc: str
    from_station: str
    to_station: str
    duration_desc: str
    frequency: str
    prices: Dict[str, int]
    sample_trains: List[TrainScheduleSampleSchema]
    highlights: List[str]
    booking_tips: str


class TransportDetailSchema(BaseModel):
    origin: str
    destination: str
    drive_options: List[DriveRouteSchema]
    train_options: List[TrainTypeSchema]
    data_source: str
    data_note: str = "列车时刻表仅供参考，实际班次及票价请在铁路12306确认"
    # 非京沪城际时：提示用户至 12306 查询；京沪时可为空
    train_notice: str = ""


class RichPOISchema(BaseModel):
    """高德 POI 2.0 富信息（用于前端与规划上下文）。"""
    name: str
    category: str = ""       # 住宿 / 景点 / 餐饮
    poi_id: str = ""
    address: str = ""
    location: str = ""
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


# ── 行程模型 ─────────────────────────────────────────────────────────────────

class DayPlan(BaseModel):
    day: int
    theme: str
    weather_note: str = ""    # 当天天气摘要，格式："晴 · 15~25°C"

    # LLM 输出的有序地点列表（供 route_node 规划路线）
    # 示例：["酒店", "西湖断桥", "楼外楼", "雷峰塔", "知味观", "西溪湿地"]
    locations: List[str] = Field(
        default_factory=list,
        description="当天按时间顺序的地点列表，route_node 用来逐段计算路线"
    )

    breakfast: str = ""
    morning: str
    lunch: str = ""
    afternoon: str
    dinner: str = ""
    evening: str

    # route_node 在 planner 之后填充
    route_segments: List[RouteSegment] = Field(
        default_factory=list,
        description="相邻地点间的路线段，由 route_node 填充，不由 LLM 生成"
    )
    tips: List[str] = Field(default_factory=list)


class TripPlan(BaseModel):
    city: str
    travel_days: int
    summary: str
    transport_summary: str = ""
    days: List[DayPlan]
    packing_tips: List[str] = Field(default_factory=list)
    budget_advice: str


# ── API 响应模型 ──────────────────────────────────────────────────────────────

class PlanResponse(BaseModel):
    success: bool
    message: str
    data: Optional[TripPlan] = None
    transport_detail: Optional[TransportDetailSchema] = None
    rich_poi_catalog: List[RichPOISchema] = Field(
        default_factory=list,
        description="本次检索到的住宿/景点/餐饮 POI（高德字段）",
    )
    static_map_url: str = Field(default="", description="可选：静态地图预览 URL（服务端生成）")
    coord_map: Dict[str, str] = Field(
        default_factory=dict,
        description="demo11 新增：行程地点坐标字典（地点名 → 'lng,lat'），供前端地图渲染",
    )
    agent_logs: List[AgentLog] = Field(default_factory=list)
