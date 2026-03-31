"""
main.py —— FastAPI 入口（demo11）

demo10 → demo11 变更：
  /api/trip/plan 响应新增 coord_map 字段（地点名 → "lng,lat"）
  供前端高德 JSAPI 2.0 地图可视化使用

  新增环境变量：
    AMAP_JS_KEY — 高德 Web JS API Key（前端地图渲染，与 AMAP_API_KEY 不同，须在控制台申请 Web 端 Key）
  通过 /api/config 端点暴露给前端（安全：仅暴露非敏感的 JS Key）

其他端点不变（问卷/画像 同 demo10）
"""

import os
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
    title="MuntoTravel demo11 — 两阶段 Multi-Agent + 地图可视化",
    version="0.11.0",
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
    return {"status": "ok", "demo": "11", "arch": "two-phase-multi-agent+map-viz"}


@app.get("/api/config")
async def get_config():
    """
    向前端暴露非敏感配置（JS API Key 不是秘密，设计上允许前端直接使用）。
    不暴露 OPENAI_API_KEY / AMAP_API_KEY 等服务端 Key。
    """
    return {"amap_js_key": os.getenv("AMAP_JS_KEY", "")}


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
    两阶段 Multi-Agent 规划（demo11：支持用户画像注入 + 地图坐标导出）。

    Phase 1（并行）：天气/景点/交通专员同时工作
    Phase 2（顺序）：Planner（LLM）→ Route（高德 API + 坐标导出）

    demo11 新增响应字段：coord_map（地点名 → "lng,lat"，供前端高德地图渲染）
    """
    t_req = time.perf_counter()
    logger.info("[plan] %s→%s %dd hotel=%s prefs=%s profile=%s",
                request.origin or "本地", request.city, request.travel_days,
                request.hotel or "未填", "/".join(request.preferences or []),
                "有" if request.profile_note else "无")

    try:
        plan, agent_logs, transport_result, rich_catalog, static_map_url, coord_map = \
            await run_graph(request)
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
    logger.info("[plan] 完成 (%.0fms) · %dAgent · %s %dd · %d路线段 · %d坐标",
                total_ms, len(agent_logs), plan.city, plan.travel_days,
                total_segs, len(coord_map))

    return PlanResponse(
        success=True, message="行程规划完成",
        data=plan, transport_detail=transport_detail,
        rich_poi_catalog=rich_poi_models,
        static_map_url=static_map_url or "",
        coord_map=coord_map,
        agent_logs=agent_logs,
    )


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
