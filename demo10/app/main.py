"""
main.py —— FastAPI 入口（demo10）

demo09 → demo10 新增端点：
  GET  /api/questionnaire          → 返回 10 题问卷（供前端渲染）
  POST /api/profile {answers}      → 将答案转为 profile_note 字符串

规划端点 /api/trip/plan 新增可选字段：
  profile_note: str                → 由前端问卷生成后填入，LLM 规划时参考用户画像
"""

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from .graph import run_graph
from .logger import configure_logging, get_logger
from .profiler import compute_profile, get_questionnaire_for_api
from .schemas import (
    PlanRequest, PlanResponse, ProfileRequest, ProfileResponse,
    RichPOISchema, TransportDetailSchema,
)

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title="MuntoTravel demo10 — 两阶段 Multi-Agent + 用户画像问卷",
    version="0.10.0",
)


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
    return {"status": "ok", "demo": "10", "arch": "two-phase-multi-agent+questionnaire"}


# ── 问卷相关端点（demo10 新增）────────────────────────────────────────────────

@app.get("/api/questionnaire")
async def get_questionnaire():
    """
    返回 10 题旅行画像问卷（不含 prompt 字段，仅供前端展示）。
    前端拿到后渲染问卷界面，用户选完后 POST /api/profile。
    """
    return {"questions": get_questionnaire_for_api()}


@app.post("/api/profile", response_model=ProfileResponse)
async def compute_user_profile(req: ProfileRequest):
    """
    将问卷答案（选项 ID 列表）转换为用户画像描述字符串。
    前端获得 profile_note 后，将其填入 /api/trip/plan 请求的 profile_note 字段。
    """
    if not req.answers:
        raise HTTPException(status_code=400, detail="answers 不能为空")

    profile_note = compute_profile(req.answers)
    dim_count = len([line for line in profile_note.splitlines() if line.strip().startswith("-")])

    logger.info("[Profile] answers=%d → profile_note=%d维度", len(req.answers), dim_count)
    return ProfileResponse(profile_note=profile_note, dimension_count=dim_count)


# ── 规划端点 ──────────────────────────────────────────────────────────────────

@app.post("/api/trip/plan", response_model=PlanResponse)
async def plan_trip(request: PlanRequest):
    """
    两阶段 Multi-Agent 规划（demo10：支持用户画像注入）。

    Phase 1（并行）：天气/景点/交通专员同时工作
    Phase 2（顺序）：Planner（LLM，参考 profile_note）→ Route（高德 API）

    新增字段：profile_note（来自 /api/profile，可选）
    """
    t_req = time.perf_counter()
    logger.info("[plan] %s→%s %dd hotel=%s prefs=%s profile=%s",
                request.origin or "本地", request.city, request.travel_days,
                request.hotel or "未填", "/".join(request.preferences or []),
                "有" if request.profile_note else "无")

    try:
        plan, agent_logs, transport_result, rich_catalog, static_map_url = await run_graph(request)
    except Exception as e:
        logger.error("[plan] 图执行异常: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    if plan is None:
        return PlanResponse(success=False, message="行程生成失败，请稍后重试",
                            agent_logs=agent_logs)

    rich_poi_models: list[RichPOISchema] = []
    for row in rich_catalog:
        if isinstance(row, dict):
            try:
                rich_poi_models.append(RichPOISchema.model_validate(row))
            except Exception:
                pass

    transport_detail = None
    if transport_result:
        try:
            transport_detail = TransportDetailSchema.model_validate(transport_result)
        except Exception as e:
            logger.warning("[plan] transport_detail 解析失败: %s", e)

    total_ms   = (time.perf_counter() - t_req) * 1000
    total_segs = sum(len(d.route_segments) for d in plan.days)
    logger.info("[plan] 完成 (%.0fms) · %dAgent · %s %dd · %d路线段",
                total_ms, len(agent_logs), plan.city, plan.travel_days, total_segs)

    return PlanResponse(
        success=True, message="行程规划完成",
        data=plan, transport_detail=transport_detail,
        rich_poi_catalog=rich_poi_models,
        static_map_url=static_map_url or "",
        agent_logs=agent_logs,
    )


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
