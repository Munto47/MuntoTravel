"""
main.py —— FastAPI 入口（demo09）

与 demo08 相同结构，仅将请求透传给 graph.run_graph。
demo09 内部多了 route_node 步骤，外部 API 接口保持不变。
"""

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from .graph import run_graph
from .logger import configure_logging, get_logger
from .schemas import PlanRequest, PlanResponse, TransportDetailSchema

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="MuntoTravel demo09 — 两阶段 Multi-Agent + 路线规划", version="0.9.0")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    if not request.url.path.startswith("/static"):
        logger.info("[HTTP] %s %s → %d  (%.0fms)",
                    request.method, request.url.path, response.status_code, ms)
    return response


@app.get("/api/health")
async def health():
    return {"status": "ok", "demo": "09", "arch": "two-phase-multi-agent"}


@app.post("/api/trip/plan", response_model=PlanResponse)
async def plan_trip(request: PlanRequest):
    """
    两阶段 Multi-Agent 规划。

    Phase 1（并行）：天气/景点/交通专员同时工作
    Phase 2（顺序）：Planner（LLM）→ Route（高德 API）

    新增字段：hotel（住宿地点，用于每日路线起点）
    新增响应：每日 DayPlan 包含 route_segments（景点间路线段）
    """
    t_req = time.perf_counter()
    logger.info("[plan] %s→%s %dd hotel=%s prefs=%s",
                request.origin or "本地", request.city, request.travel_days,
                request.hotel or "未填", "/".join(request.preferences or []))

    try:
        plan, agent_logs, transport_result = await run_graph(request)
    except Exception as e:
        logger.error("[plan] 图执行异常: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    if plan is None:
        return PlanResponse(success=False, message="行程生成失败，请稍后重试",
                            agent_logs=agent_logs)

    transport_detail = None
    if transport_result:
        try:
            transport_detail = TransportDetailSchema.model_validate(transport_result)
        except Exception as e:
            logger.warning("[plan] transport_detail 解析失败: %s", e)

    total_ms = (time.perf_counter() - t_req) * 1000
    total_segs = sum(len(d.route_segments) for d in plan.days)
    logger.info("[plan] 完成 (%.0fms) · %dAgent · %s %dd · %d路线段",
                total_ms, len(agent_logs), plan.city, plan.travel_days, total_segs)

    return PlanResponse(
        success=True, message="行程规划完成",
        data=plan, transport_detail=transport_detail, agent_logs=agent_logs,
    )


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
