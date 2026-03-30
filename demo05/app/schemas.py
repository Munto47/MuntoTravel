from typing import List, Optional

from pydantic import BaseModel, Field


# ── 问卷相关 ──────────────────────────────────────────────────────────────────

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
    score: int          # 2~8（两题之和）
    label: str          # 低 / 中 / 中高 / 高
    description: str    # 注入 LLM 的自然语言描述


class UserProfile(BaseModel):
    dimensions: List[DimensionScore]
    profile_text: str       # 完整画像文本，直接追加到 System Prompt
    personality_label: str  # 如 "美食探索家 · 户外冒险者"
    personality_desc: str   # 2~3 句个性化概述
    budget_level: str


# ── 行程相关（与 demo04 保持一致）──────────────────────────────────────────────

class TripRequest(BaseModel):
    city: str = Field(..., description="目的地城市")
    travel_days: int = Field(..., ge=1, le=7)
    preferences: List[str] = Field(default_factory=list)
    budget_level: str = Field(default="medium")
    notes: str = Field(default="")


class PlanWithProfileRequest(BaseModel):
    """前端提交的联合请求：问卷答案 + 行程要求"""
    answers: QuestionAnswer
    city: str
    travel_days: int = Field(..., ge=1, le=7)
    notes: str = ""


# ── 天气 / 行程数据模型（与 demo04 保持一致）─────────────────────────────────

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


class DayPlan(BaseModel):
    day: int
    theme: str
    morning: str
    afternoon: str
    evening: str
    meals: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)
    profile_note: str = Field(
        default="",
        description="本日行程中体现用户画像的具体说明（引用真实景点/餐厅名）",
    )


class TripPlan(BaseModel):
    city: str
    travel_days: int
    summary: str
    days: List[DayPlan]
    packing_tips: List[str] = Field(default_factory=list)
    budget_advice: str
    profile_applications: List[str] = Field(
        default_factory=list,
        description="整体画像应用说明列表，每条对应一个维度的具体调整",
    )


class TripPlanWithProfileResponse(BaseModel):
    success: bool
    message: str
    user_profile: Optional[UserProfile] = None
    data: Optional[TripPlan] = None
    agent_log: List[str] = Field(default_factory=list)
