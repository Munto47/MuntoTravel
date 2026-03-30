"""
schemas.py —— demo07 数据模型

demo07 相比 demo06 的关键变化：
  1. TransportInfo（LLM生成的简单摘要）→ 替换为 transport_summary: str
  2. 新增 Pydantic 序列化模型：DriveRouteSchema / TrainScheduleSampleSchema /
     TrainTypeSchema / TransportDetailSchema（对应 transport_client.py 的数据类）
  3. TripPlanWithProfileResponse 新增 transport_detail 字段
  4. TripRequest / PlanWithProfileRequest 保留 origin 字段
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ── 问卷相关（与 demo05/06 完全一致）────────────────────────────────────────────

class QuestionAnswer(BaseModel):
    """16 道情景题的答案，每题 A=1 B=2 C=3 D=4"""
    q1:  int = Field(..., ge=1, le=4)
    q2:  int = Field(..., ge=1, le=4)
    q3:  int = Field(..., ge=1, le=4)
    q4:  int = Field(..., ge=1, le=4)
    q5:  int = Field(..., ge=1, le=4)
    q6:  int = Field(..., ge=1, le=4)
    q7:  int = Field(..., ge=1, le=4)
    q8:  int = Field(..., ge=1, le=4)
    q9:  int = Field(..., ge=1, le=4)
    q10: int = Field(..., ge=1, le=4)
    q11: int = Field(..., ge=1, le=4)
    q12: int = Field(..., ge=1, le=4)
    q13: int = Field(..., ge=1, le=4)
    q14: int = Field(..., ge=1, le=4)
    q15: int = Field(..., ge=1, le=4)
    q16: int = Field(..., ge=1, le=4)
    budget_level: str = Field(default="medium", description="low/medium/high")


class DimensionScore(BaseModel):
    key: str
    name: str
    icon: str
    score: int
    label: str
    description: str


class UserProfile(BaseModel):
    dimensions: List[DimensionScore]
    profile_text: str
    personality_label: str
    personality_desc: str
    budget_level: str


# ── 交通详细数据（对应 transport_client.py，供 API 响应序列化）─────────────────

class DriveRouteSchema(BaseModel):
    """单个自驾策略（前端渲染使用）"""
    strategy: str               # "最快路线" / "少收费路线"
    duration_minutes: int
    distance_km: float
    toll_yuan: int
    main_highways: List[str]    # ["G2 京沪高速"]
    tips: List[str]


class TrainScheduleSampleSchema(BaseModel):
    """具体班次示例"""
    number: str     # "G7"
    dep: str        # "08:00"
    arr: str        # "08:51"


class TrainTypeSchema(BaseModel):
    """单类列车方案（高铁 / 动车 / 普通列车）"""
    train_type: str                 # "高铁" / "动车组" / "普通列车（夜铺）"
    prefix: str                     # "G、C" / "D" / "K、T、Z"
    speed_desc: str                 # "最高时速 350km/h"
    from_station: str
    to_station: str
    duration_desc: str
    frequency: str
    prices: Dict[str, int]          # {"二等座": 73, "一等座": 117}
    sample_trains: List[TrainScheduleSampleSchema]
    highlights: List[str]
    booking_tips: str


class TransportDetailSchema(BaseModel):
    """
    交通方案详情（由 main.py 预先计算，与 LLM 生成的行程分离返回）。

    设计原因（与 demo06 的关键区别）：
      demo06：plan_transport 是 LangGraph 的一个 @tool，LLM 决定调用并把文字摘要写进 TripPlan
      demo07：transport_client 在 main.py 里预先调用，结构化数据直接返回给前端，
              同时以文字形式注入 LLM 上下文（用于 Day1/LastDay 叙事）
    这种"预算 + 注入"模式适合：一次性查询、结构化展示要求高、不需要 LLM 自主判断时机的场景
    """
    origin: str
    destination: str
    drive_options: List[DriveRouteSchema]
    train_options: List[TrainTypeSchema]
    data_source: str
    data_note: str = "列车时刻表仅供参考，实际班次及票价请在铁路12306确认"


# ── 行程相关 ──────────────────────────────────────────────────────────────────

class TripRequest(BaseModel):
    city: str = Field(..., description="目的地城市")
    origin: str = Field(default="", description="出发城市（填写后生成交通方案）")
    travel_days: int = Field(..., ge=1, le=7)
    preferences: List[str] = Field(default_factory=list)
    budget_level: str = Field(default="medium")
    notes: str = Field(default="")


class PlanWithProfileRequest(BaseModel):
    """前端提交的联合请求：问卷答案 + 行程要求"""
    answers: QuestionAnswer
    city: str
    origin: str = Field(default="", description="出发城市")
    travel_days: int = Field(..., ge=1, le=7)
    notes: str = ""


# ── 天气数据模型 ──────────────────────────────────────────────────────────────

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
        lines = [f"【{self.city} 出行天气预报（来源：{self.source}）】"]
        for d in self.days:
            rain_note = "（注意携带雨具）" if d.precipitation >= 5 else ""
            wind_note = f"，{d.wind_desc}" if d.wind_desc else ""
            lines.append(
                f"  {d.date}：{d.condition}，"
                f"{d.temp_min:.0f}°C ~ {d.temp_max:.0f}°C，"
                f"降水量 {d.precipitation:.1f}mm{rain_note}{wind_note}"
            )
        return "\n".join(lines)


# ── 行程数据模型 ──────────────────────────────────────────────────────────────

class DayPlan(BaseModel):
    day: int
    theme: str
    morning: str
    afternoon: str
    evening: str
    meals: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)
    profile_note: str = Field(default="", description="本日画像应用说明")


class TripPlan(BaseModel):
    city: str
    travel_days: int
    summary: str
    transport_summary: str = Field(
        default="",
        description="交通方案一句话总结，如：推荐乘高铁约50分钟直达，第一天上午09:00从虹桥出发",
    )
    days: List[DayPlan]
    packing_tips: List[str] = Field(default_factory=list)
    budget_advice: str
    profile_applications: List[str] = Field(default_factory=list)


# ── API 响应 ──────────────────────────────────────────────────────────────────

class TripPlanWithProfileResponse(BaseModel):
    success: bool
    message: str
    user_profile: Optional[UserProfile] = None
    data: Optional[TripPlan] = None
    agent_log: List[str] = Field(default_factory=list)
    transport_detail: Optional[TransportDetailSchema] = Field(
        default=None,
        description="结构化交通方案（由 main.py 预算，与 LLM 行程分离），前端用于渲染交通卡片",
    )
