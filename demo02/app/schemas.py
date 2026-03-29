from typing import List, Optional

from pydantic import BaseModel, Field


# ── 天气相关 ──────────────────────────────────────────────────────────────────

class DayWeather(BaseModel):
    date: str
    condition: str
    temp_max: float
    temp_min: float
    precipitation: float


class WeatherInfo(BaseModel):
    city: str
    days: List[DayWeather]

    def to_prompt_text(self) -> str:
        """把天气数据转成适合注入 prompt 的纯文本。"""
        lines = [f"【{self.city} 出行天气预报】"]
        for d in self.days:
            rain_note = "（注意携带雨具）" if d.precipitation >= 5 else ""
            lines.append(
                f"  {d.date}：{d.condition}，"
                f"{d.temp_min:.0f}°C ~ {d.temp_max:.0f}°C，"
                f"降水量 {d.precipitation:.1f}mm{rain_note}"
            )
        return "\n".join(lines)


# ── 行程相关（与 demo01 保持兼容，新增 weather_context） ──────────────────────

class TripRequest(BaseModel):
    city: str = Field(..., description="目的地城市")
    travel_days: int = Field(..., ge=1, le=7, description="出行天数")
    preferences: List[str] = Field(default_factory=list, description="旅行偏好")
    budget_level: str = Field(default="medium", description="预算级别")
    notes: Optional[str] = Field(default="", description="额外备注")


class DayPlan(BaseModel):
    day: int
    theme: str
    morning: str
    afternoon: str
    evening: str
    meals: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)


class TripPlan(BaseModel):
    city: str
    travel_days: int
    summary: str
    days: List[DayPlan]
    packing_tips: List[str] = Field(default_factory=list)
    budget_advice: str
    weather_context: Optional[str] = Field(default=None, description="注入的天气摘要，供前端展示")


class TripPlanResponse(BaseModel):
    success: bool
    message: str
    data: Optional[TripPlan] = None
