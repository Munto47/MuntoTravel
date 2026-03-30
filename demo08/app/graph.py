"""
graph.py —— Multi-Agent 并行图（LangGraph Fan-out / Fan-in）

架构一览：
                         ┌─────────────────────────────┐
                         │       dispatch_agents        │
                         │  (conditional edge from START) │
                         └──┬────────────┬──────────────┘
                            │            │       │ (仅当 origin != city)
                            ▼            ▼       ▼
                     weather_agent  poi_agent  transport_agent
                     （天气专员）   （景点专员）  （交通专员）
                            │            │       │
                            └────────────┴───────┘
                                         │  Fan-in：所有并行 Agent 完成后汇聚
                                         ▼
                                  planner_node
                                  （行程规划师 · LLM）
                                         │
                                        END

关键 LangGraph 知识点：
  1. Send()：向特定节点发送独立的 payload，实现并行 Fan-out
  2. Annotated + operator.add：list 类型字段的 reducer，自动合并来自多个并行分支的结果
  3. Fan-in 自动同步：当多个节点都有边指向同一目标节点时，
     LangGraph 等待所有当前 superstep 的节点完成后才执行目标节点
  4. planner_node 只在所有专家 Agent 完成后才执行，确保 context 完整

对比 demo07：
  demo07：LLM 在 agentic loop 中主动调用工具（工具是 LLM 的手）
  demo08：LLM 只做最后的综合规划（LLM 是乐团指挥，专家 Agent 是各声部）
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

from .agents import (
    poi_agent,
    transport_agent,
    weather_agent,
)
from .logger import get_logger
from .schemas import PlanRequest, TripPlan

load_dotenv()
logger = get_logger(__name__)

_DEFAULT_PREFERENCES = ["历史文化", "自然风景", "美食探索"]


# ── 共享状态定义 ──────────────────────────────────────────────────────────────

class TravelState(TypedDict):
    """
    所有 Agent 共享的状态对象。

    list 类型字段使用 Annotated[list, operator.add] 作为 reducer：
      当多个并行节点同时更新同一字段时，LangGraph 自动将各自的 list 拼接，
      而不是互相覆盖（这是实现 Fan-in 数据聚合的关键）。

    非 list 字段（transport_result）由单一 Agent 写入，使用默认覆盖行为。
    """
    # 请求信息
    city: str
    origin: str
    travel_days: int
    preferences: list[str]
    budget_level: str
    notes: str

    # 并行 Agent 数据汇聚点（Fan-in 核心）
    context_pieces: Annotated[list[str], operator.add]

    # 结构化交通数据（仅 transport_agent 写入，直接传给前端）
    transport_result: Optional[dict]

    # 执行日志（每个 Agent 写一条，Fan-in 自动合并）
    agent_logs: Annotated[list[dict], operator.add]

    # planner_node 写入的最终行程（必须在 TypedDict 中声明，否则 LangGraph 静默丢弃）
    trip_plan: Optional[dict]


# ── Fan-out：调度专家 Agent ───────────────────────────────────────────────────

def dispatch_agents(state: TravelState) -> list[Send]:
    """
    条件边函数：决定调度哪些专家 Agent，返回 Send 对象列表。

    Send(node_name, payload) 的作用：
      - 向 node_name 发送自定义 payload（不必是完整 State）
      - 被发送的节点在同一 superstep 并行执行
      - 节点的返回值作为 State 更新，经过 reducer 处理

    transport_agent 只在有出发城市（且不同于目的地）时才调度：
      → 这体现了 Multi-Agent 的灵活性：按需激活专家，节省资源
    """
    prefs = state.get("preferences") or _DEFAULT_PREFERENCES

    notes = state.get("notes", "")

    sends = [
        Send("weather_agent", {"city": state["city"], "days": state["travel_days"], "notes": notes}),
        Send("poi_agent",     {"city": state["city"], "preferences": prefs, "notes": notes}),
    ]

    origin = (state.get("origin") or "").strip()
    city   = (state.get("city") or "").strip()
    if origin and origin != city:
        sends.append(Send("transport_agent", {"origin": origin, "city": city}))
        logger.info("[Graph] 调度 3 个专家 Agent（含交通专员 %s→%s）%s",
                    origin, city, f" · 备注:{notes}" if notes else "")
    else:
        logger.info("[Graph] 调度 2 个专家 Agent（天气+景点）%s",
                    f" · 备注:{notes}" if notes else "")

    return sends


# ── Fan-in：行程规划师（LLM）────────────────────────────────────────────────────

_PLAN_SYSTEM_PROMPT = """\
你是一名专业的中文旅行规划师。
你将收到由各专家 Agent 收集的城市信息（天气、景点餐厅、交通），请据此制定一份 JSON 格式的完整行程。

JSON 结构要求（严格遵循，字段名不可更改）：
{
  "city": "目的地",
  "travel_days": 天数（整数）,
  "summary": "一段吸引人的旅行概述（2-3句话）",
  "transport_summary": "交通方案一句话建议（无交通数据时填空字符串）",
    "days": [
    {
      "day": 1,
      "theme": "当天主题（例：西湖畔的历史诗意之旅）",
      "weather_note": "根据天气数据填写，格式：「天气状况 · 最低~最高°C」，若有降水风险加「· 建议带伞」，例：晴 · 15~25°C 或 小雨 · 12~18°C · 建议带伞",
      "breakfast": "早餐推荐（餐厅名 + 招牌菜 + 一句推荐理由，紧扣当日景区位置）",
      "morning": "上午行程（具体景点+活动，2-3句话，注明大概游览时长）",
      "lunch": "午餐推荐（餐厅名 + 招牌菜 + 一句推荐理由，建议在上午景区附近）",
      "afternoon": "下午行程（具体景点+活动，2-3句话）",
      "dinner": "晚餐推荐（餐厅名 + 招牌菜 + 一句推荐理由，结合晚上活动位置）",
      "evening": "晚上活动（夜游/夜市/休息，2句话）",
      "tips": ["当天实用小贴士（结合天气/体力/交通）", "小贴士2"]
    }
  ],
  "packing_tips": ["打包建议1", "打包建议2", "打包建议3"],
  "budget_advice": "整体预算规划建议（1-2句话）"
}

要求：
1. 必须以有效的 JSON 格式响应，不要包含 markdown 代码块或任何额外文字
2. breakfast/lunch/dinner 是独立字段，不是数组，每个填一段话
3. 行程必须结合天气数据（如多雨天气请调整户外活动，在 tips 中提醒带伞）
4. 如果专家数据中包含「⚠️ 备注限制条件」，必须在全程规划中严格遵守（如不爬山、素食等）
5. 若有交通信息，transport_summary 要点出最推荐的方案
6. 根据预算等级调整推荐档次：low=平价体验，medium=均衡之选，high=品质优先
7. 餐厅推荐要具体（有名称），尽量使用专家提供的景点/餐厅，而非泛泛而谈
"""


async def planner_node(state: TravelState) -> dict:
    """
    行程规划师节点：汇聚所有专家数据，一次性调用 LLM 生成完整行程。

    核心设计决策：
      - 此 LLM 不使用任何工具（bind_tools / ToolNode 均不需要）
      - 所有数据已由专家 Agent 收集完毕，存入 context_pieces
      - LLM 只负责"理解 + 写作"，而非"探索 + 决策"
      - 这使 LLM 输出更稳定、延迟更可预测
    """
    t0 = time.perf_counter()
    logger.info("[Planner] 开始综合规划 · 已获得 %d 段上下文", len(state.get("context_pieces", [])))

    city        = state["city"]
    travel_days = state["travel_days"]
    budget      = state.get("budget_level", "medium")
    prefs       = state.get("preferences") or _DEFAULT_PREFERENCES
    notes       = state.get("notes", "")

    budget_cn = {"low": "节省", "medium": "均衡", "high": "体验/奢华"}.get(budget, "均衡")
    prefs_str = "、".join(prefs)
    context_str = "\n\n".join(state.get("context_pieces", []))

    user_msg = f"""\
目的地：{city}
行程天数：{travel_days} 天
旅行偏好：{prefs_str}
预算档位：{budget_cn}
{'额外备注：' + notes if notes else ''}

以下是各专家 Agent 为你收集的数据：

{context_str}

请根据以上信息，生成一份完整的 {travel_days} 天 {city} 旅行行程（JSON 格式）。
"""

    model = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0.7,
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )

    try:
        response = await model.ainvoke([
            {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ])
        raw_text = response.content.strip()
        logger.debug("[Planner] LLM 原始输出（前500字）: %s", raw_text[:500])

        # 去除可能的 markdown 包装
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        plan_dict = json.loads(raw_text)
        plan = TripPlan.model_validate(plan_dict)

        ms = int((time.perf_counter() - t0) * 1000)
        logger.info("[Planner] 完成 (%dms) · %s %d天行程", ms, city, len(plan.days))

        planner_log = {
            "agent": "planner", "label": "行程规划师", "icon": "✈️",
            "status": "ok", "duration_ms": ms,
            "detail": f"{city} {travel_days}天 · {len(plan.days)}天行程已生成",
            "source": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        }
        return {"trip_plan": plan.model_dump(), "agent_logs": [planner_log]}

    except json.JSONDecodeError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        logger.error("[Planner] JSON 解析失败 (%dms): %s", ms, e)
        planner_log = {
            "agent": "planner", "label": "行程规划师", "icon": "✈️",
            "status": "fail", "duration_ms": ms,
            "detail": f"JSON 解析失败: {e}", "source": "",
        }
        return {"trip_plan": None, "agent_logs": [planner_log]}

    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        logger.error("[Planner] 调用失败 (%dms) %s: %s", ms, type(e).__name__, e)
        planner_log = {
            "agent": "planner", "label": "行程规划师", "icon": "✈️",
            "status": "fail", "duration_ms": ms,
            "detail": f"{type(e).__name__}: {e}", "source": "",
        }
        return {"trip_plan": None, "agent_logs": [planner_log]}


# ── 构建图 ────────────────────────────────────────────────────────────────────

def build_graph():
    """
    构建并编译 Multi-Agent 并行图。

    图结构：
      START
        │  conditional_edges (dispatch_agents)
        ├──→ weather_agent
        ├──→ poi_agent
        └──→ transport_agent  (可选)
              三者并行执行，结果通过 operator.add reducer 汇入共享状态
        ↓  Fan-in：等待所有并行节点完成
      planner_node
        │
       END

    边的绑定方式决定了 Fan-in 时机：
      当 weather_agent、poi_agent、transport_agent 都有边指向 planner_node，
      LangGraph 会在同一 superstep 内等待全部完成后才进入 planner_node。
    """
    builder = StateGraph(TravelState)

    # 注册节点（专家 Agent + Planner）
    builder.add_node("weather_agent",   weather_agent)
    builder.add_node("poi_agent",       poi_agent)
    builder.add_node("transport_agent", transport_agent)
    builder.add_node("planner_node",    planner_node)

    # Fan-out：START → 条件调度 → 并行专家
    builder.add_conditional_edges(START, dispatch_agents)

    # Fan-in：所有专家 → planner（LangGraph 自动等待当前 superstep 所有节点）
    builder.add_edge("weather_agent",   "planner_node")
    builder.add_edge("poi_agent",       "planner_node")
    builder.add_edge("transport_agent", "planner_node")
    builder.add_edge("planner_node",    END)

    return builder.compile()


# 模块级单例，避免重复编译
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
        logger.info("[Graph] Multi-Agent 图编译完成")
    return _graph


# ── 公共入口 ───────────────────────────────────────────────────────────────────

async def run_graph(request: PlanRequest) -> tuple[Optional[TripPlan], list[dict], Optional[dict]]:
    """
    执行 Multi-Agent 图，返回 (trip_plan, agent_logs, transport_detail)。

    Returns:
        trip_plan:        LLM 生成的行程规划
        agent_logs:       所有专家 Agent 的执行记录（用于前端时间线）
        transport_detail: 结构化交通数据（用于前端交通卡片，不经 LLM 处理）
    """
    t0 = time.perf_counter()
    logger.info("[Graph] 启动 Multi-Agent 图 · %s %dd · 偏好: %s",
                request.city, request.travel_days, " / ".join(request.preferences or _DEFAULT_PREFERENCES))

    initial_state: TravelState = {
        "city":             request.city,
        "origin":           request.origin or "",
        "travel_days":      request.travel_days,
        "preferences":      request.preferences or _DEFAULT_PREFERENCES,
        "budget_level":     request.budget_level or "medium",
        "notes":            request.notes or "",
        "context_pieces":   [],
        "agent_logs":       [],
        "transport_result": None,
        "trip_plan":        None,
    }

    graph = get_graph()
    final_state = await graph.ainvoke(initial_state)

    elapsed = time.perf_counter() - t0
    logs = final_state.get("agent_logs", [])
    logger.info("[Graph] 全流程完成 (%.1fs) · %d 个 Agent 执行记录", elapsed, len(logs))

    plan_dict = final_state.get("trip_plan")
    plan = TripPlan.model_validate(plan_dict) if plan_dict else None

    return plan, logs, final_state.get("transport_result")
