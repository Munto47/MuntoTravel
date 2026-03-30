"""
main.py —— FastAPI HTTP 层

新增端点：
  GET /api/graph   → 返回 LangGraph 图结构的 Mermaid 描述，供前端可视化
  POST /api/trip/plan → 调用 run_graph()，与 demo03 接口完全一致
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .graph import graph, run_graph
from .schemas import TripPlanResponse, TripRequest

app = FastAPI(title="MuntoTravel Agent", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/graph")
async def get_graph_diagram():
    """
    返回 LangGraph 图结构的 Mermaid 字符串。
    LangGraph 编译后的图自带 get_graph().draw_mermaid() 方法，
    可生成标准 Mermaid flowchart 语法，前端用 Mermaid.js 渲染成可视化流程图。
    """
    try:
        mermaid = graph.get_graph().draw_mermaid()
    except Exception:
        mermaid = (
            "flowchart TD\n"
            "    START([__start__]) --> agent[agent]\n"
            "    agent -->|tool_calls| tools[tools]\n"
            "    tools --> agent\n"
            "    agent -->|stop| generate[generate]\n"
            "    generate --> END([__end__])\n"
        )
    return {"mermaid": mermaid}


@app.post("/api/trip/plan", response_model=TripPlanResponse)
async def plan_trip(request: TripRequest):
    plan, agent_log = await run_graph(request)
    return TripPlanResponse(
        success=True,
        message="行程规划完成（LangGraph）",
        data=plan,
        agent_log=agent_log,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.4.0", "framework": "LangGraph"}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
