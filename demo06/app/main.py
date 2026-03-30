from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .graph import graph, run_graph
from .profiler import compute_user_profile
from .schemas import (
    PlanWithProfileRequest, QuestionAnswer,
    TripPlanWithProfileResponse, TripRequest,
)

app = FastAPI(title="MuntoTravel Transport + Profile Agent", version="0.6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.post("/api/questionnaire/analyze")
async def analyze_questionnaire(answers: QuestionAnswer):
    """分析问卷答案，立即返回用户旅行画像（纯计算，< 10ms）"""
    profile = compute_user_profile(answers)
    return {"success": True, "user_profile": profile}


@app.post("/api/trip/plan-with-profile", response_model=TripPlanWithProfileResponse)
async def plan_trip_with_profile(request: PlanWithProfileRequest):
    """
    基于问卷画像 + 出发城市生成个性化行程：
      1. 从答案计算 UserProfile
      2. 把画像文本注入 LangGraph Agent 的 System Prompt
      3. 若有出发城市，Agent 自动调用 plan_transport 规划交通
      4. Agent 调用天气 / 景点工具，最终生成 JSON 行程
    """
    profile = compute_user_profile(request.answers)

    trip_req = TripRequest(
        city=request.city,
        origin=request.origin,          # demo06 新增
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


@app.post("/api/trip/plan", response_model=TripPlanWithProfileResponse)
async def plan_trip_simple(request: TripRequest):
    """无问卷的快速规划接口（有出发城市时同样触发交通规划）"""
    plan, agent_log = await run_graph(request)
    return TripPlanWithProfileResponse(
        success=True,
        message="行程规划完成",
        data=plan,
        agent_log=agent_log,
    )


@app.get("/api/graph")
async def get_graph_diagram():
    try:
        mermaid = graph.get_graph().draw_mermaid()
    except Exception:
        mermaid = (
            "flowchart TD\n"
            "    START --> agent\n"
            "    agent -->|有工具调用| tools\n"
            "    tools --> agent\n"
            "    agent -->|无工具调用| generate\n"
            "    generate --> END"
        )
    return {"mermaid": mermaid}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.6.0", "feature": "transport-planning"}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
