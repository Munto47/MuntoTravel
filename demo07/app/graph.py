"""
graph.py —— LangGraph 旅行规划 Agent（demo07 版）

与 demo06 的核心差异：
  1. TOOLS 只剩 get_weather + get_attractions（plan_transport 被移出）
  2. run_graph() 新增 transport_text 参数：
     - 若提供出发城市，main.py 预先调用 transport_client 得到详细数据
     - 数据以文字形式注入 user message，LLM 直接引用
  3. JSON_GENERATION_PROMPT 中 transport_summary 字段简化为一句话摘要，
     不再要求 LLM 重新结构化交通数据（避免 hallucination）

架构对比（学习要点）：
  demo06：plan_transport 是 @tool，LangGraph 决定调用时机
          → 适合：LLM 需要根据上下文动态决定是否查询的场景
  demo07：transport_client 在 main.py 预先计算，文字注入 LLM
          → 适合：确定性、一次性、需要结构化返回给前端的数据
  两种模式各有适用场景，根据数据特性选择
"""

import json
import os
import time
from typing import Annotated, Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from .logger import get_logger
from .schemas import DayPlan, TripPlan, TripRequest
from .tools import TOOLS

load_dotenv()

logger = get_logger(__name__)


# ── State ─────────────────────────────────────────────────────────────────────

class TravelAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    trip_plan: Optional[dict]


# ── Prompts ───────────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """你是一名专业的中文旅行规划师。

你有以下工具可以使用：
- get_weather：获取目的地实时天气预报
- get_attractions：查询目的地各类热门景点和餐厅

工作要求：
1. 调用 get_weather 获取目的地天气，据此调整室内/户外安排
2. 根据用户偏好多次调用 get_attractions，获取不同类别景点数据
3. 若用户消息中已包含详细的交通方案，请直接参考，无需自行查询
4. 在第一天行程中明确写明从出发地出发的具体安排（交通方式、出发时间、到达时间）
5. 在最后一天写明返程安排参考（几点出发，估计几点到家）
6. 收集完信息后，我会另行要求你输出最终行程，无需在工具调用阶段输出"""

JSON_GENERATION_PROMPT = """你已经收集了足够的信息。
请现在根据天气、景点以及交通方案，输出完整的旅行计划，格式严格遵守：

{
  "city": "目的地城市",
  "travel_days": 天数（整数）,
  "summary": "2-3句话的整体概述，体现天气、景点和出行特色",
  "transport_summary": "交通方案一句话总结（有出发城市时必填，如：推荐乘坐沪杭高铁约50分钟直达，第一天09:00从虹桥出发；或：自驾走G92约2小时，注意错峰出行）；无出发城市时填空字符串",
  "profile_applications": [
    "维度名：具体调整说明（有画像时填写，如：行程节奏：每天2个核心景点符合慢游风格）",
    "（其他维度……）"
  ],
  "days": [
    {
      "day": 第几天（整数）,
      "theme": "当天主题",
      "morning": "上午具体安排（第一天必须包含从出发地启程的方式：乘坐什么车次/自驾走哪条路，几点出发，几点预计到达目的地）",
      "afternoon": "下午具体安排",
      "evening": "晚上安排（最后一天必须包含返程安排：几点出发，走什么路线或车次）",
      "meals": ["早餐推荐（具体餐厅或食物）", "午餐推荐", "晚餐推荐"],
      "tips": ["结合天气的实用贴士"],
      "profile_note": "本日哪个具体景点/安排体现了用户的哪条画像偏好（引用真实景点名）；无画像则填空字符串"
    }
  ],
  "packing_tips": ["打包建议（需包含天气相关）"],
  "budget_advice": "预算建议"
}

重要说明：
- profile_applications 每条必须含「维度名：」前缀，没有画像时填 []
- profile_note 必须引用本日真实出现的景点名
- transport_summary 只写一句话，详细的交通卡片由前端另行展示，不要重复列举班次数据
只返回 JSON，不要包含任何其他文字。"""


# ── LLM 工厂 ──────────────────────────────────────────────────────────────────

def _make_llm(temperature: float) -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=temperature,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("OPENAI_API_KEY"),
    )


# ── 节点函数 ──────────────────────────────────────────────────────────────────

async def agent_node(state: TravelAgentState) -> dict:
    t0 = time.perf_counter()
    llm_with_tools = _make_llm(0.3).bind_tools(TOOLS)
    response = await llm_with_tools.ainvoke(state["messages"])
    ms = int((time.perf_counter() - t0) * 1000)

    if isinstance(response, AIMessage) and response.tool_calls:
        calls = [f"{tc['name']}({list(tc.get('args', {}).values())})" for tc in response.tool_calls]
        logger.info("[Agent] 工具调用 → %s (%dms)", " + ".join(calls), ms)
    else:
        logger.info("[Agent] 无工具调用，进入生成节点 (%dms)", ms)
    return {"messages": [response]}


async def generate_node(state: TravelAgentState) -> dict:
    t0 = time.perf_counter()
    logger.info("[Generate] 开始生成行程 JSON ...")
    llm = _make_llm(0.7)
    messages = list(state["messages"]) + [HumanMessage(content=JSON_GENERATION_PROMPT)]
    response = await llm.ainvoke(messages)
    ms = int((time.perf_counter() - t0) * 1000)

    content = response.content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    trip_plan = json.loads(content.strip())
    logger.info("[Generate OK] 行程 JSON 解析成功，%d 天 (%dms)", len(trip_plan.get("days", [])), ms)
    return {"trip_plan": trip_plan}


def should_continue(state: TravelAgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    logger.debug("[Router] → generate（无更多工具调用）")
    return "generate"


# ── 图构建 ────────────────────────────────────────────────────────────────────

def build_graph():
    builder = StateGraph(TravelAgentState)
    builder.add_node("agent",    agent_node)
    builder.add_node("tools",    ToolNode(TOOLS))
    builder.add_node("generate", generate_node)
    builder.set_entry_point("agent")
    builder.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "generate": "generate"},
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("generate", END)
    return builder.compile()


graph = build_graph()


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def extract_agent_log(messages: list) -> list[str]:
    log = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {})
                if name == "get_weather":
                    log.append(f"查询天气：{args.get('city')} · {args.get('days')} 天")
                elif name == "get_attractions":
                    log.append(f"查询景点：{args.get('city')} · {args.get('category')}")
                else:
                    log.append(f"调用工具：{name}")
    return log


def _build_fallback_plan(request: TripRequest) -> TripPlan:
    days = []
    for i in range(request.travel_days):
        days.append(DayPlan(
            day=i + 1,
            theme="城市漫游日",
            morning=f"在{request.city}安排轻松的早餐和周边打卡。",
            afternoon="前往核心景点，按兴趣自由游览。",
            evening="傍晚回到热闹街区，体验当地晚餐。",
            meals=["早餐：当地早餐店", "午餐：景点附近特色餐", "晚餐：步行可达的口碑餐厅"],
            tips=["提前查看天气预报。", "注意保存体力。"],
        ))
    return TripPlan(
        city=request.city,
        travel_days=request.travel_days,
        summary=f"这是一份适合在 {request.city} 进行 {request.travel_days} 天游玩的备用行程。",
        days=days,
        packing_tips=["舒适步行鞋", "充电宝和水杯"],
        budget_advice=f"按 {request.budget_level} 预算规划，建议把主要开销留给交通和门票。",
    )


def _build_user_message(request: TripRequest, transport_text: str = "") -> str:
    """
    构建发给 LLM 的用户消息。
    transport_text 是由 main.py 预先计算的交通方案文字，
    直接追加到消息末尾，LLM 看到后会在行程叙述中引用它。
    """
    origin_line  = f"\n出发城市：{request.origin}" if request.origin else ""
    prefs        = "、".join(request.preferences) if request.preferences else ""
    prefs_line   = f"\n旅行偏好：{prefs}" if prefs else ""
    notes_line   = f"\n额外要求：{request.notes}" if request.notes else ""
    transport_section = f"\n\n{transport_text}" if transport_text else ""

    return (
        f"请为我规划一份 {request.city} {request.travel_days} 天的旅行行程。"
        f"{origin_line}"
        f"{prefs_line}\n"
        f"预算级别：{request.budget_level}"
        f"{notes_line}"
        f"{transport_section}"
    )


async def run_graph(
    request: TripRequest,
    profile_text: str = "",
    transport_text: str = "",
) -> tuple[TripPlan, list[str]]:
    """
    运行 LangGraph Agent。

    profile_text   → 追加至 System Prompt（用户画像）
    transport_text → 追加至 User Message（交通方案，由 main.py 预先计算）

    这两种注入方式的区别：
      System Prompt：影响 Agent 全程的行为策略（"请照顾偏爱慢游的用户"）
      User Message ：提供具体数据（"上海→杭州高铁约50分钟，73元"）
    """
    mode          = "画像" if profile_text else "通用"
    has_transport = "有交通方案" if transport_text else "无出发城市"
    model_name    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    logger.info("────────────────────────────────────────────")
    logger.info("[Graph] 启动 LangGraph Agent")
    logger.info("[Graph] 模式：%s | 交通：%s | 模型：%s", mode, has_transport, model_name)
    logger.info("[Graph] 目的地：%s %d天 | 预算：%s", request.city, request.travel_days, request.budget_level)

    sys_len  = len(BASE_SYSTEM_PROMPT) + (len(profile_text) if profile_text else 0)
    user_msg = _build_user_message(request, transport_text)
    user_len = len(user_msg)
    logger.debug("[Graph] System Prompt：%d chars%s | User Message：%d chars%s",
                 sys_len,
                 f"（含画像 +{len(profile_text)}）" if profile_text else "",
                 user_len,
                 f"（含交通 +{len(transport_text)}）" if transport_text else "")

    system_content = BASE_SYSTEM_PROMPT
    if profile_text:
        system_content = f"{BASE_SYSTEM_PROMPT}\n\n{profile_text}"

    initial_state: TravelAgentState = {
        "messages": [
            SystemMessage(content=system_content),
            HumanMessage(content=user_msg),
        ],
        "trip_plan": None,
    }

    t0 = time.perf_counter()
    try:
        final_state = await graph.ainvoke(initial_state)
        elapsed   = time.perf_counter() - t0
        raw_plan  = final_state["trip_plan"]
        agent_log = extract_agent_log(final_state["messages"])
        plan      = TripPlan(**raw_plan)

        logger.info("[Graph OK] 完成 (%.1fs) | 工具调用 %d 次 | %d 天行程",
                    elapsed, len(agent_log), len(plan.days))
        logger.info("────────────────────────────────────────────")
        return plan, agent_log

    except Exception as e:
        elapsed = time.perf_counter() - t0
        import traceback
        logger.error("[Graph !!] 失败 (%.1fs) %s: %s", elapsed, type(e).__name__, e)
        logger.debug("详细堆栈：\n%s", traceback.format_exc())
        logger.warning("[Graph] 返回 Fallback 行程")
        return _build_fallback_plan(request), []
