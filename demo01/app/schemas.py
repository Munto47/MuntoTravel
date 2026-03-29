from typing import List, Optional

from pydantic import BaseModel, Field


class TripRequest(BaseModel):
    city: str = Field(..., description="Destination city")
    travel_days: int = Field(..., ge=1, le=7, description="Number of travel days")
    preferences: List[str] = Field(default_factory=list, description="Travel preferences")
    budget_level: str = Field(default="medium", description="Budget level")
    notes: Optional[str] = Field(default="", description="Extra notes from the traveler")


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


class TripPlanResponse(BaseModel):
    success: bool
    message: str
    data: Optional[TripPlan] = None
