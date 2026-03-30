"""
main.py —— FastAPI 入口

demo08 API 设计：
  POST /api/trip/plan   接收简单请求（兴趣多选），驱动 Multi-Agent 图，返回行程 + 执行日志

移除的接口（demo05-07 遗留）：
  - POST /api/questionnaire/analyze   ← 问卷分析（问卷停用后不再需要）
  - POST /api/trip/plan-with-profile  ← 含画像的规划（问卷系统优化后再启用）

保留的接口：
  - GET  /api/health                  ← 健康检查
  - POST /api/trip/plan               ← 主流程（新版）

HTTP 中间件：
  log_requests —— 记录每个请求的方法、路径、响应码、耗时
"""

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .graph import run_graph
from .logger import configure_logging, get_logger
from .schemas import PlanRequest, PlanResponse, TransportDetailSchema

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="MuntoTravel demo08 — Multi-Agent 旅行规划", version="0.8.0")


# ── HTTP 中间件：请求日志 ────────────────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    # 屏蔽静态资源的日志噪音
    if not request.url.path.startswith("/static"):
        logger.info("[HTTP] %s %s → %d  (%.0fms)",
                    request.method, request.url.path, response.status_code, ms)
    return response


# ── 健康检查 ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "demo": "08", "arch": "multi-agent-parallel"}


# ── 主规划接口 ────────────────────────────────────────────────────────────────

@app.post("/api/trip/plan", response_model=PlanResponse)
async def plan_trip(request: PlanRequest):
    """
    Multi-Agent 旅行规划接口。

    流程（全部在 LangGraph 图内并行执行）：
      ① weather_agent  → 获取目的地天气
      ② poi_agent      → 按兴趣获取景点/餐厅推荐
      ③ transport_agent → 获取城市间交通方案（有 origin 时）
                         ↓ Fan-in
      ④ planner_node   → LLM 综合生成完整行程

    响应附带 agent_logs（各专家的执行状态+耗时），
    前端可用来渲染「Agent 执行时间线」。
    """
    t_req = time.perf_counter()
    logger.info("[plan] 收到请求 · %s → %s %dd · 偏好:%s · 预算:%s",
                request.origin or "本地",
                request.city, request.travel_days,
                " / ".join(request.preferences or ["默认"]),
                request.budget_level)

    try:
        plan, agent_logs, transport_result = await run_graph(request)
    except Exception as e:
        logger.error("[plan] 图执行异常: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    if plan is None:
        logger.warning("[plan] 行程生成失败，返回 success=False")
        return PlanResponse(
            success=False,
            message="行程生成失败，请稍后重试",
            agent_logs=agent_logs,
        )

    # 将 transport_result dict 转换为 Pydantic Schema（用于前端交通卡片渲染）
    transport_detail = None
    if transport_result:
        try:
            transport_detail = TransportDetailSchema.model_validate(transport_result)
        except Exception as e:
            logger.warning("[plan] transport_detail 解析失败: %s", e)

    total_ms = (time.perf_counter() - t_req) * 1000
    logger.info("[plan] 请求完成 (%.0fms) · %d个Agent · %s %dd",
                total_ms, len(agent_logs), plan.city, plan.travel_days)

    return PlanResponse(
        success=True,
        message="行程规划完成",
        data=plan,
        transport_detail=transport_detail,
        agent_logs=agent_logs,
    )


# ── 静态文件（前端页面）─────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
