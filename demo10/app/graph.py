"""
graph.py —— demo10：两阶段 Multi-Agent 图 + 用户画像

架构对比：
  demo08（一阶段）:
    [weather/poi/transport 并行] → planner_node → END

  demo09（两阶段）:
    Phase 1 (并行)：[weather/poi/transport]  ← 数据采集，互不依赖，同时跑
    Phase 2 (顺序)：planner_node → route_node → END
                    ↑ LLM 决定去哪里         ↑ 工具计算怎么走

新 LangGraph 知识点：
  ① planner_node → route_node 是普通顺序边（add_edge），
     与 Fan-out/Fan-in 的 Send() / 并行边完全不同
  ② route_node 读取 state["trip_plan"]，修改其 route_segments 字段，
     再将整个 trip_plan 写回 state —— 这是"状态读-改-写"模式
  ③ 两阶段的分工：
       - Phase 1 专家：无 LLM，快速并行，产出数据
       - planner：有 LLM，综合数据，产出结构（决定去哪里、顺序是什么）
       - route_node：无 LLM，利用 planner 输出，补充路线细节（怎么走）

这体现了"LLM 做决策，工具做执行"的核心原则：
  LLM 最擅长理解语义、安排顺序、权衡取舍，
  API/工具最擅长精确计算、实时数据，
  两者分工才能发挥各自优势。
"""

import json
import operator
import os
import time
from typing import Annotated, Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from langgraph.types import Send
from typing_extensions import TypedDict

from .agents import poi_agent, transport_agent, weather_agent
from .logger import get_logger
from .route_client import plan_day_routes
from .schemas import PlanRequest, RouteSegment, TripPlan

load_dotenv()
logger = get_logger(__name__)

_DEFAULT_PREFERENCES = ["历史文化", "自然风景", "美食探索"]


# ── 共享状态 ──────────────────────────────────────────────────────────────────

class TravelState(TypedDict):
    """
    demo10 扩展：新增 profile_note 字段（来自问卷的用户画像描述）。

    pre-step 保留：
      poi_coords: poi_agent 搜索结果的坐标字典（name → "lng,lat"）
    """
    city: str
    origin: str
    hotel: str          # 住宿地点，每日路线的起点（可空）
    travel_days: int
    preferences: list[str]
    budget_level: str
    notes: str
    profile_note: str   # 来自问卷的用户画像文本（可为空字符串）

    # Phase 1 Fan-in 汇聚点
    context_pieces: Annotated[list[str], operator.add]
    transport_result: Optional[dict]
    agent_logs: Annotated[list[dict], operator.add]

    # poi_agent 搜索结果坐标（供 route_node 预加载缓存用）
    poi_coords: Optional[dict]
    rich_poi_catalog: Optional[list]
    district_hint: Optional[str]
    static_map_url: Optional[str]

    # Phase 2 中间产物（planner 写入，route 读取并更新）
    trip_plan: Optional[dict]


# ── Phase 1 Fan-out ───────────────────────────────────────────────────────────

def dispatch_agents(state: TravelState) -> list[Send]:
    prefs = state.get("preferences") or _DEFAULT_PREFERENCES
    notes = state.get("notes", "")

    sends = [
        Send("weather_agent", {"city": state["city"], "days": state["travel_days"], "notes": notes}),
        Send("poi_agent", {
            "city": state["city"],
            "preferences": prefs,
            "notes": notes,
            "budget_level": state.get("budget_level") or "medium",
            "profile_note": (state.get("profile_note") or "").strip(),
        }),
    ]

    origin = (state.get("origin") or "").strip()
    city   = (state.get("city") or "").strip()
    if origin and origin != city:
        sends.append(Send("transport_agent", {"origin": origin, "city": city}))
        logger.info("[Graph] Phase1 调度 3 Agent（含交通 %s→%s）%s",
                    origin, city, f"· 备注:{notes}" if notes else "")
    else:
        logger.info("[Graph] Phase1 调度 2 Agent（天气+景点）%s",
                    f"· 备注:{notes}" if notes else "")
    return sends


# ── Phase 2a：行程规划师（LLM）────────────────────────────────────────────────

_PLAN_SYSTEM_PROMPT = """\
你是一名专业的中文旅行规划师。
你将收到由各专家 Agent 收集的城市信息（天气、景点餐厅、交通），请制定 JSON 格式的完整行程。

JSON 结构（字段名严格遵循，不可更改）：
{
  "city": "目的地",
  "travel_days": 天数（整数）,
  "summary": "吸引人的旅行概述（2-3句话）",
  "transport_summary": "城际交通一句话：若专家数据含高德驾车则概括耗时/距离；若仅提示12306查火车则写明「长途建议查询12306」；京沪线可提及高铁参考示例（非实时）",
  "days": [
    {
      "day": 1,
      "theme": "当天主题",
      "weather_note": "天气状况 · 最低~最高°C（有降水加 · 建议带伞）",
      "locations": ["住宿/出发点", "早餐地点名称", "上午景点名称", "午餐地点名称", "下午景点名称", "晚餐地点名称"],
      "breakfast": "早餐推荐（餐厅名 + 招牌菜 + 一句推荐理由）",
      "morning": "上午行程（具体景点+活动，2-3句话，注明游览时长）",
      "lunch": "午餐推荐（餐厅名 + 招牌菜 + 一句推荐理由）",
      "afternoon": "下午行程（具体景点+活动，2-3句话）",
      "dinner": "晚餐推荐（餐厅名 + 招牌菜 + 一句推荐理由）",
      "evening": "晚上活动（2句话）",
      "tips": ["小贴士1（结合天气）", "小贴士2"]
    }
  ],
  "packing_tips": ["建议1", "建议2", "建议3"],
  "budget_advice": "预算建议（1-2句话）"
}

重要要求：
1. 必须以有效 JSON 响应，不含 markdown 代码块或额外文字
2. locations 字段：按时间顺序列出当天所有地点的实际名称（用于路线规划），
   第一个元素为住宿地点或城市中心，后续依次是早餐/景点/午餐/景点/晚餐
3. breakfast/lunch/dinner 是独立字符串字段，不是数组
4. 严格遵守 context 中的「⚠️ 备注限制条件」
5. 根据预算等级调整档次：low=平价/经济型，medium=均衡，high=品质/奢华优先；**不得**在 low 预算下全日推荐高端餐厅，也不得在 high 预算下全日只用路边摊敷衍
6. 餐厅推荐要有具体名称，**优先**从专家 POI 列表里选择与本次「预算/风格/偏好」一致的名称写入 locations；专家上下文若含「POI 检索条件」或「检索画像」，必须与之一致
7. 专家上下文若说明「火车请查12306」且未给列车时刻，不要在行程中编造具体车次/时刻
8. **避免万能模板**：summary、各日 theme、活动描述须体现用户本次的旅行偏好与备注/画像（如奢华/简约/亲子/美食等），不要每期使用相同套话
9. 若用户强调某种体验风格，餐饮与住宿的价位、环境描述须与该风格匹配
"""


async def planner_node(state: TravelState) -> dict:
    """
    Phase 2a：LLM 综合所有专家数据，生成带 locations 列表的行程草稿。

    关键：locations 字段是 route_node 的输入——
    LLM 决定"去哪里、什么顺序"，route_node 计算"怎么走、花多久"。
    """
    t0 = time.perf_counter()
    logger.info("[Planner] 开始综合规划 · %d 段上下文", len(state.get("context_pieces", [])))

    city         = state["city"]
    travel_days  = state["travel_days"]
    district_extra = (state.get("district_hint") or "").strip()
    hotel        = (state.get("hotel") or "").strip()
    budget_cn    = {"low": "节省", "medium": "均衡", "high": "体验/奢华"}.get(
                     state.get("budget_level", "medium"), "均衡")
    prefs_str    = "、".join(state.get("preferences") or _DEFAULT_PREFERENCES)
    notes        = state.get("notes", "")
    profile_note = (state.get("profile_note") or "").strip()
    context_str  = "\n\n".join(state.get("context_pieces", []))

    hotel_line   = f"住宿/出发点：{hotel}（请将此作为每天 locations 列表的第一项）" if hotel else ""

    user_msg = f"""\
目的地：{city}
行程天数：{travel_days} 天
旅行偏好：{prefs_str}
预算档位：{budget_cn}
【规划约束】住宿与餐饮的档次必须与上述预算档位一致；若专家上下文中含「POI 检索画像」或「检索词」，所选地点名称与描述须与该画像一致，不得套用与本次需求无关的万能模板。
{hotel_line}
{'额外备注：' + notes if notes else ''}
{profile_note if profile_note else ''}
{f'{district_extra}\n' if district_extra else ''}
以下是各专家 Agent 收集的数据：
{context_str}

请生成 {travel_days} 天 {city} 旅行行程（JSON 格式）。
"""

    model = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.85,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )

    try:
        response = await model.ainvoke([
            {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ])
        raw = response.content.strip()
        logger.debug("[Planner] LLM 原始（前300字）: %s", raw[:300])

        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        plan_dict = json.loads(raw)
        plan      = TripPlan.model_validate(plan_dict)
        ms        = int((time.perf_counter() - t0) * 1000)
        logger.info("[Planner] 完成 (%dms) · %s %d天 · locations总数:%d",
                    ms, city, len(plan.days),
                    sum(len(d.locations) for d in plan.days))

        planner_log = {
            "agent": "planner", "label": "行程规划师", "icon": "✈️",
            "status": "ok", "duration_ms": ms,
            "detail": f"{city} {travel_days}天已生成，移交路线专员",
            "source": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        }
        return {"trip_plan": plan.model_dump(), "agent_logs": [planner_log]}

    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        err_type = type(e).__name__
        logger.error("[Planner] 失败 (%dms) %s: %s", ms, err_type, e)
        return {"trip_plan": None, "agent_logs": [{
            "agent": "planner", "label": "行程规划师", "icon": "✈️",
            "status": "fail", "duration_ms": ms,
            "detail": f"{err_type}: {e}", "source": "",
        }]}


# ── Phase 2b：路线专员（无 LLM，工具计算）────────────────────────────────────

async def route_node(state: TravelState) -> dict:
    """
    Phase 2b：在 planner_node 之后顺序执行，逐日计算景点间实际路线。

    执行流程：
      1. 读取 state["trip_plan"]["days"][i]["locations"]（LLM 已排好顺序）
      2. 对每一对相邻地点，调用高德步行/公交 API
      3. 将 route_segments 写入每天的行程，更新 trip_plan
      4. 写回 state（覆盖 trip_plan）

    为什么不用 LLM 做路线？
      - 路线是精确计算，不是语义推理——API 比 LLM 更可信
      - 避免 LLM "幻想"路程时间（LLM 不知道实时交通）
      - 节省 token，路线计算直接用坐标
    """
    trip_plan = state.get("trip_plan")
    if not trip_plan:
        logger.warning("[Route] trip_plan 为空，跳过路线规划")
        return {"agent_logs": [{
            "agent": "route", "label": "路线专员", "icon": "🗺️",
            "status": "skip", "duration_ms": 0,
            "detail": "无行程数据，跳过", "source": "",
        }]}

    city       = state["city"]
    api_key    = os.getenv("AMAP_API_KEY", "").strip()
    poi_coords = state.get("poi_coords") or {}   # poi_agent 预先搜集的坐标
    t0         = time.perf_counter()
    total_segs = 0

    if not api_key:
        logger.warning("[Route] 未配置 AMAP_API_KEY，路线使用距离估算")
    elif poi_coords:
        logger.info("[Route] 接收到 %d 个 POI 坐标，将预加载到 geocode 缓存", len(poi_coords))

    updated_days = []
    for day in trip_plan.get("days", []):
        locs: list[str] = day.get("locations", [])

        if len(locs) >= 2:
            logger.info("[Route] Day%d 计算 %d 个地点的 %d 段路线",
                        day["day"], len(locs), len(locs) - 1)
            citycode = ""
            for r in (state.get("rich_poi_catalog") or []):
                if isinstance(r, dict) and r.get("citycode"):
                    citycode = str(r["citycode"]).strip()
                    break
            segs: list[RouteSegment] = await plan_day_routes(
                locs, city, api_key, poi_coords, citycode=citycode or None,
            )
            day["route_segments"] = [s.model_dump() for s in segs]
            total_segs += len(segs)
        else:
            day["route_segments"] = []
            logger.debug("[Route] Day%d locations 不足（%d），跳过", day["day"], len(locs))

        updated_days.append(day)

    trip_plan["days"] = updated_days
    ms = int((time.perf_counter() - t0) * 1000)
    source = "高德地图实时" if api_key else "距离估算"
    logger.info("[Route] 全部完成 (%dms) · %d段路线 · %s", ms, total_segs, source)

    route_log = {
        "agent": "route", "label": "路线专员", "icon": "🗺️",
        "status": "ok" if api_key else "warn",
        "duration_ms": ms,
        "detail": f"{total_segs}段路线 · {source}",
        "source": source,
    }
    return {"trip_plan": trip_plan, "agent_logs": [route_log]}


# ── 构建图 ────────────────────────────────────────────────────────────────────

def build_graph():
    """
    两阶段图结构：

    START
      │ dispatch_agents (conditional)
      ├──→ weather_agent  ─┐
      ├──→ poi_agent       ├─ 并行 Fan-out (Phase 1)
      └──→ transport_agent─┘
                          │ Fan-in（自动等待全部完成）
                    planner_node  ← Phase 2a：LLM 决策
                          │ 普通顺序边（add_edge）
                     route_node   ← Phase 2b：工具执行
                          │
                         END

    对比 demo08：
      新增了 route_node 这一"后处理"顺序节点。
      Phase 1 并行结构完全不变，仅在 planner 之后多了一步。
    """
    builder = StateGraph(TravelState)

    builder.add_node("weather_agent",   weather_agent)
    builder.add_node("poi_agent",       poi_agent)
    builder.add_node("transport_agent", transport_agent)
    builder.add_node("planner_node",    planner_node)
    builder.add_node("route_node",      route_node)   # ← 新增

    # Phase 1 Fan-out
    builder.add_conditional_edges(START, dispatch_agents)

    # Phase 1 Fan-in → Phase 2a
    builder.add_edge("weather_agent",   "planner_node")
    builder.add_edge("poi_agent",       "planner_node")
    builder.add_edge("transport_agent", "planner_node")

    # Phase 2a → 2b（顺序边）
    builder.add_edge("planner_node", "route_node")
    builder.add_edge("route_node",   END)

    return builder.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
        logger.info("[Graph] demo10 两阶段图编译完成")
    return _graph


# ── 公共入口 ─────────────────────────────────────────────────────────────────

async def run_graph(
    request: PlanRequest,
) -> tuple[Optional[TripPlan], list[dict], Optional[dict], list[dict], str]:
    t0 = time.perf_counter()
    logger.info("[Graph] 启动 · %s %dd · hotel=%s",
                request.city, request.travel_days, request.hotel or "未填")

    initial_state: TravelState = {
        "city":             request.city,
        "origin":           request.origin or "",
        "hotel":            request.hotel or "",
        "travel_days":      request.travel_days,
        "preferences":      request.preferences or _DEFAULT_PREFERENCES,
        "budget_level":     request.budget_level or "medium",
        "notes":            request.notes or "",
        "profile_note":     getattr(request, "profile_note", "") or "",
        "context_pieces":   [],
        "agent_logs":       [],
        "transport_result": None,
        "poi_coords":       None,
        "rich_poi_catalog": None,
        "district_hint":    None,
        "static_map_url":   None,
        "trip_plan":        None,
    }

    final_state = await get_graph().ainvoke(initial_state)

    elapsed = time.perf_counter() - t0
    logs    = final_state.get("agent_logs", [])
    logger.info("[Graph] 完成 (%.1fs) · %d 个 Agent", elapsed, len(logs))

    plan_dict = final_state.get("trip_plan")
    plan      = TripPlan.model_validate(plan_dict) if plan_dict else None
    catalog   = final_state.get("rich_poi_catalog") or []
    smap      = (final_state.get("static_map_url") or "").strip()
    return plan, logs, final_state.get("transport_result"), catalog, smap
