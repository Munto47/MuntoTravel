from typing import List, Optional

from pydantic import BaseModel, Field


# ── 天气相关（与 demo02 保持一致）────────────────────────────────────────────

class DayWeather(BaseModel):
    date: str
    condition: str
    temp_max: float
    temp_min: float
    precipitation: float
    wind_desc: str = ""          # 风向风力描述，和风天气提供，Open-Meteo 不提供


class WeatherInfo(BaseModel):
    city: str
    days: List[DayWeather]
    source: str = "未知"         # 数据来源标注，便于 debug

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


# ── 行程相关 ──────────────────────────────────────────────────────────────────

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


# ── 响应信封（新增 agent_log，记录 Agent 的推理过程）────────────────────────

class TripPlanResponse(BaseModel):
    success: bool
    message: str
    data: Optional[TripPlan] = None
    agent_log: List[str] = Field(
        default_factory=list,
        description="Agent 的工具调用记录，供前端展示推理过程",
    )
