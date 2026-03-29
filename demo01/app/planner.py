from .llm_client import generate_trip_plan_json
from .schemas import DayPlan, TripPlan, TripRequest


def _build_fallback_plan(request: TripRequest) -> TripPlan:
    preferences = request.preferences or ["城市漫游", "当地美食"]
    days = []

    for day_index in range(request.travel_days):
        focus = preferences[day_index % len(preferences)]
        days.append(
            DayPlan(
                day=day_index + 1,
                theme=f"{focus}探索日",
                morning=f"在{request.city}安排轻松的早餐和城市地标打卡，围绕“{focus}”开始当天行程。",
                afternoon=f"挑选1到2个与“{focus}”相关的核心地点，控制节奏，留出拍照和休息时间。",
                evening=f"傍晚回到热闹街区，体验当地晚餐，并安排一次轻量散步或夜景活动。",
                meals=[
                    "早餐：选择口碑稳定的本地早餐店",
                    "午餐：在核心景点附近安排一顿特色餐",
                    "晚餐：优先选择评价高、步行可达的餐厅",
                ],
                tips=[
                    "上午尽量安排热门点，避开高峰排队。",
                    "下午保持一个主景点 + 一个补充点的节奏。",
                    "晚间活动控制在住宿点附近，减少返程压力。",
                ],
            )
        )

    return TripPlan(
        city=request.city,
        travel_days=request.travel_days,
        summary=(
            f"这是一份适合在 {request.city} 进行 {request.travel_days} 天游玩的入门版行程，"
            f"整体围绕 {', '.join(preferences)} 展开，优先保证节奏舒适、路线简单。"
        ),
        days=days,
        packing_tips=[
            "准备舒适的步行鞋。",
            "随身带充电宝和水杯。",
            "提前查看天气和景点开放时间。",
        ],
        budget_advice=(
            f"当前按 {request.budget_level} 预算设计，建议把主要开销留给交通、门票和一两顿特色正餐。"
        ),
    )


async def create_trip_plan(request: TripRequest) -> TripPlan:
    try:
        raw_plan = await generate_trip_plan_json(request)
        return TripPlan(**raw_plan)
    except Exception:
        return _build_fallback_plan(request)
