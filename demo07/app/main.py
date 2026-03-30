"""
main.py —— demo07 FastAPI 入口

与 demo06 的关键差异：
  交通数据：pre-compute 模式
    1. main.py 在调用 LangGraph 之前，先调用 transport_client.get_transport_options()
    2. 得到结构化 TransportResult（自驾双策略 + 列车三分类）
    3. 将 to_prompt_text() 文字注入 run_graph() 的 transport_text 参数
    4. 同时把 TransportResult.to_dict() 作为 transport_detail 返回给前端
    5. 前端用 transport_detail 渲染丰富的交通卡片（完全结构化，不依赖 LLM 重新输出）

好处：
  - 交通卡片数据 100% 可靠（不经过 LLM，不会 hallucination）
  - LLM 只需在叙事里引用交通信息（Day1 出发 / 最后一天返程），无需重新结构化
  - 可复用：transport_client 返回的数据可直接用于未来的地图路径规划
"""

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .graph import graph, run_graph
from .logger import configure_logging, get_logger
from .profiler import compute_user_profile
from .schemas import (
    DriveRouteSchema, PlanWithProfileRequest, QuestionAnswer,
    TrainScheduleSampleSchema, TrainTypeSchema, TransportDetailSchema,
    TripPlanWithProfileResponse, TripRequest,
)
from .transport_client import get_transport_options

# 应用启动时初始化日志系统
configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="MuntoTravel Refined Transport Agent", version="0.7.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """HTTP 请求日志中间件：记录每个 API 请求的路径、耗时和状态码"""
    # 静态文件不记录
    if request.url.path.startswith("/") and "." in request.url.path.split("/")[-1]:
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - t0
    if request.url.path.startswith("/api"):
        lvl = "info" if response.status_code < 400 else "warning"
        getattr(logger, lvl)(
            "HTTP %s %s → %d (%.2fs)",
            request.method, request.url.path, response.status_code, elapsed,
        )
    return response


def _serialize_transport(result) -> TransportDetailSchema | None:
    """将 TransportResult 数据类转换为 Pydantic 序列化模型"""
    if result is None:
        return None
    return TransportDetailSchema(
        origin=result.origin,
        destination=result.destination,
        drive_options=[
            DriveRouteSchema(
                strategy=d.strategy,
                duration_minutes=d.duration_minutes,
                distance_km=d.distance_km,
                toll_yuan=d.toll_yuan,
                main_highways=d.main_highways,
                tips=d.tips,
            )
            for d in result.drive_options
        ],
        train_options=[
            TrainTypeSchema(
                train_type=t.train_type,
                prefix=t.prefix,
                speed_desc=t.speed_desc,
                from_station=t.from_station,
                to_station=t.to_station,
                duration_desc=t.duration_desc,
                frequency=t.frequency,
                prices=t.prices,
                sample_trains=[
                    TrainScheduleSampleSchema(
                        number=s.number, dep=s.dep, arr=s.arr
                    )
                    for s in t.sample_trains
                ],
                highlights=t.highlights,
                booking_tips=t.booking_tips,
            )
            for t in result.train_options
        ],
        data_source=result.data_source,
        data_note=result.data_note,
    )


@app.post("/api/questionnaire/analyze")
async def analyze_questionnaire(answers: QuestionAnswer):
    """分析问卷答案，立即返回用户旅行画像（纯计算，< 10ms）"""
    t0 = time.perf_counter()
    profile = compute_user_profile(answers)
    ms = int((time.perf_counter() - t0) * 1000)
    logger.info("[Questionnaire] 画像计算完成：%s · %s (%dms)",
                profile.personality_label, profile.budget_level, ms)
    return {"success": True, "user_profile": profile}


@app.post("/api/trip/plan-with-profile", response_model=TripPlanWithProfileResponse)
async def plan_trip_with_profile(request: PlanWithProfileRequest):
    """
    基于问卷画像 + 精细化交通方案生成行程：

    1. 计算用户画像（profiler.py，纯计算）
    2. 预先计算交通方案（transport_client.py）
       - 自驾：高德实时 API（最快 + 少收费），降级内置数据
       - 列车：内置数据库（高铁/动车/普通列车三类）
    3. 将交通方案文字注入 LangGraph Agent 的 User Message
    4. Agent 调用天气和景点工具，生成行程 JSON
    5. 返回：行程 + 画像 + 结构化交通详情（transport_detail）
    """
    t_req = time.perf_counter()
    origin = request.origin.strip() if request.origin else ""
    logger.info("============================================")
    logger.info("[plan-with-profile] %s → %s %dd budget=%s",
                origin or "（无出发地）", request.city, request.travel_days, request.answers.budget_level)

    # ── Step 1：计算用户画像 ─────────────────────────────────────────────────
    t0 = time.perf_counter()
    profile = compute_user_profile(request.answers)
    logger.info("[Step 1] 用户画像：%s · %s (%.0fms)",
                profile.personality_label, profile.budget_level,
                (time.perf_counter() - t0) * 1000)

    # ── Step 2：预先计算交通方案 ─────────────────────────────────────────────
    transport_result = None
    transport_text   = ""
    t0 = time.perf_counter()
    if origin and origin != request.city.strip():
        logger.info("[Step 2] 预算交通方案：%s → %s ...", origin, request.city)
        transport_result = await get_transport_options(origin, request.city)
        if transport_result:
            transport_text = transport_result.to_prompt_text()
            logger.info("[Step 2] 交通预算完成 (%.2fs) → 自驾:%d 列车:%d",
                        time.perf_counter() - t0,
                        len(transport_result.drive_options),
                        len(transport_result.train_options))
        else:
            logger.warning("[Step 2] 交通预算无结果 (%.2fs)", time.perf_counter() - t0)
    else:
        logger.info("[Step 2] 无出发城市，跳过交通预算")

    # ── Step 3：运行 LangGraph ──────────────────────────────────────────────
    trip_req = TripRequest(
        city=request.city,
        origin=request.origin,
        travel_days=request.travel_days,
        budget_level=request.answers.budget_level,
        preferences=[],
        notes=request.notes,
    )

    logger.info("[Step 3] 启动 LangGraph Agent ...")
    plan, agent_log = await run_graph(trip_req, profile.profile_text, transport_text)

    total = time.perf_counter() - t_req
    logger.info("[plan-with-profile] 请求完成 (%.1fs total)", total)
    logger.info("============================================")

    return TripPlanWithProfileResponse(
        success=True,
        message="行程规划完成",
        user_profile=profile,
        data=plan,
        agent_log=agent_log,
        transport_detail=_serialize_transport(transport_result),
    )


@app.post("/api/trip/plan", response_model=TripPlanWithProfileResponse)
async def plan_trip_simple(request: TripRequest):
    """无问卷的快速规划接口"""
    t_req = time.perf_counter()
    origin = request.origin.strip() if request.origin else ""
    logger.info("[plan] %s → %s %dd", origin or "（无出发地）", request.city, request.travel_days)

    transport_result = None
    transport_text   = ""
    if origin and origin != request.city.strip():
        transport_result = await get_transport_options(origin, request.city)
        if transport_result:
            transport_text = transport_result.to_prompt_text()

    plan, agent_log = await run_graph(request, transport_text=transport_text)
    logger.info("[plan] 完成 (%.1fs)", time.perf_counter() - t_req)

    return TripPlanWithProfileResponse(
        success=True,
        message="行程规划完成",
        data=plan,
        agent_log=agent_log,
        transport_detail=_serialize_transport(transport_result),
    )


@app.get("/api/transport")
async def get_transport(origin: str, destination: str):
    """单独查询交通方案（不含行程规划，可用于前端实时预览）"""
    logger.info("[/api/transport] %s → %s", origin, destination)
    result = await get_transport_options(origin, destination)
    if result is None:
        logger.warning("[/api/transport] 无结果：%s → %s", origin, destination)
        return {"success": False, "message": "无效的城市对"}
    return {"success": True, "transport_detail": _serialize_transport(result)}


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
    return {"status": "ok", "version": "0.7.0", "feature": "refined-transport"}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
