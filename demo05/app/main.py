from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .graph import graph, run_graph
from .profiler import compute_user_profile
from .schemas import (
    PlanWithProfileRequest, QuestionAnswer,
    TripPlanWithProfileResponse, TripRequest,
)

app = FastAPI(title="MuntoTravel Personalized Agent", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.post("/api/questionnaire/analyze")
async def analyze_questionnaire(answers: QuestionAnswer):
    """
    分析问卷答案，立即返回用户旅行画像（纯计算，无 LLM 调用，< 10ms）。
    前端在用户完成最后一题后调用，用于展示画像揭晓页面。
    """
    profile = compute_user_profile(answers)
    return {"success": True, "user_profile": profile}


@app.post("/api/trip/plan-with-profile", response_model=TripPlanWithProfileResponse)
async def plan_trip_with_profile(request: PlanWithProfileRequest):
    """
    基于问卷画像生成个性化行程：
      1. 从答案计算 UserProfile
      2. 把画像文本注入 LangGraph Agent 的 System Prompt
      3. Agent 自主调用天气/景点工具，最终生成 JSON 行程
    """
    profile = compute_user_profile(request.answers)

    trip_req = TripRequest(
        city=request.city,
        travel_days=request.travel_days,
        budget_level=request.answers.budget_level,
        preferences=[],
        notes=request.notes,
    )

    plan, agent_log = await run_graph(trip_req, profile.profile_text)

    return TripPlanWithProfileResponse(
        success=True,
        message="个性化行程规划完成",
        user_profile=profile,
        data=plan,
        agent_log=agent_log,
    )


@app.get("/api/graph")
async def get_graph_diagram():
    try:
        mermaid = graph.get_graph().draw_mermaid()
    except Exception:
        mermaid = "flowchart TD\n    START --> agent --> tools --> agent --> generate --> END"
    return {"mermaid": mermaid}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.5.0", "feature": "personalized-planning"}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
