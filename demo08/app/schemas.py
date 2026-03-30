"""
schemas.py —— demo08 数据模型

与 demo07 的核心变化：
  - 移除所有问卷相关模型（QuestionAnswer / DimensionScore / UserProfile）
  - 新增 AgentLog：记录每个专家 Agent 的执行状态、耗时、数据摘要
  - 简化 TripPlan（移除 profile_note / profile_applications，问卷系统后续优化后再启用）
  - PlanRequest 替代 PlanWithProfileRequest（简单的兴趣多选）

架构说明（Multi-Agent）：
  demo07：一个 Agent + 多个 @tool，Agent 自主决定调用哪些工具，顺序执行
  demo08：多个专家 Agent 并行运行（天气/景点/交通），无 LLM 参与数据获取，
          所有数据汇聚后由 Planner Agent（LLM）一次性生成行程
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── 兴趣偏好（替代问卷，简单多选）───────────────────────────────────────────────

PREFERENCE_OPTIONS = ["历史文化", "自然风景", "美食探索", "购物", "夜生活", "亲子游"]

BUDGET_OPTIONS = Literal["low", "medium", "high"]


# ── 专家 Agent 执行日志 ──────────────────────────────────────────────────────────

class AgentLog(BaseModel):
    """
    单个专家 Agent 的执行记录。
    前端用于渲染「Agent 执行时间线」，让开发者看清楚各专家的工作情况和数据来源。
    """
    agent: str          # 内部标识：weather / poi / transport / planner
    label: str          # 展示名称：天气专员 / 景点专员 / 交通专员 / 行程规划师
    icon: str           # 图标 emoji
    status: str         # ok / warn / skip / fail
    duration_ms: int    # 耗时（毫秒）
    detail: str         # 人类可读的摘要，如"杭州 3天 · Open-Meteo"
    source: str = ""    # 数据来源标注，如"高德地图实时" / "内置参考数据"


# ── 请求模型 ──────────────────────────────────────────────────────────────────

class PlanRequest(BaseModel):
    """
    简洁的规划请求：兴趣多选 + 基本行程信息。
    问卷系统停用后的临时方案，后续问卷优化完成后将重新引入画像注入。
    """
    city: str = Field(..., description="目的地城市")
    origin: str = Field(default="", description="出发城市（可选，填写后生成交通方案）")
    travel_days: int = Field(..., ge=1, le=7)
    preferences: List[str] = Field(
        default_factory=list,
        description="旅行偏好（多选）：历史文化/自然风景/美食探索/购物/夜生活/亲子游"
    )
    budget_level: str = Field(default="medium", description="low / medium / high")
    notes: str = Field(default="", description="额外备注")


# ── 天气模型（与 demo07 保持兼容）────────────────────────────────────────────────

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


# ── 交通模型（与 demo07 保持兼容）────────────────────────────────────────────────

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


# ── 行程模型（简化版，移除 profile 字段）─────────────────────────────────────────

class DayPlan(BaseModel):
    day: int
    theme: str
    weather_note: str = ""  # 当天天气摘要，格式："晴 · 15~25°C" 或 "小雨 · 12~18°C · 建议带伞"
    breakfast: str = ""
    morning: str
    lunch: str = ""
    afternoon: str
    dinner: str = ""
    evening: str
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
    agent_logs: List[AgentLog] = Field(
        default_factory=list,
        description="各专家 Agent 的执行记录，用于前端渲染执行时间线"
    )
