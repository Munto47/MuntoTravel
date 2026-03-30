"""
graph.py —— LangGraph 旅行规划 Agent

核心概念：StateGraph
  - State  : 流经图的共享数据（TypedDict），每个节点读取并更新 State
  - Node   : 普通 Python 函数，接受 State，返回要更新的字段
  - Edge   : 节点间的转移规则，可以是固定边或条件边
  - Compile: 图编译后成为可调用对象，支持 ainvoke / stream

三节点结构：
  START → [agent] → (tool_calls?) → [tools] → [agent]  (循环直到 LLM 停止调工具)
                 → (no tool_calls) → [generate] → END

与 demo03 的对比：
  demo03：手写 for 循环 + 手动管理 messages 列表
  demo04：LangGraph 自动驱动，状态流转由图引擎管理
"""

import json
import os
from typing import Annotated, Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from .schemas import DayPlan, TripPlan, TripRequest
from .tools import TOOLS

load_dotenv()

# ── State 定义 ────────────────────────────────────────────────────────────────
# TypedDict 定义了整张图的共享状态结构。
# add_messages 是一个 reducer：每个节点返回新 messages，图引擎自动 append（而不是替换）。

class TravelAgentState(TypedDict):
    messages: Annotated[list, add_messages]   # 对话历史，自动累积
    trip_plan: Optional[dict]                 # 最终行程（None 直到 generate 节点完成）


# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一名专业的中文旅行规划师。

你有以下工具可以使用：
- get_weather：获取目的地实时天气预报
- get_attractions：查询目的地各类热门景点和餐厅

工作要求：
1. 收到用户的旅行需求后，先主动调用工具收集信息
2. 至少调用 get_weather 一次，至少调用 get_attractions 一次（可按用户偏好多次调用）
3. 收集完信息后，我会另行要求你输出最终行程，不需要在工具调用阶段输出行程"""

JSON_GENERATION_PROMPT = """你已经收集了足够的信息。
请现在根据以上天气预报和景点数据，输出完整的旅行计划，格式严格遵守：

{
  "city": "目的地城市",
  "travel_days": 天数（整数）,
  "summary": "2-3句话的整体概述，体现天气和景点特色",
  "days": [
    {
      "day": 第几天（整数）,
      "theme": "当天主题",
      "morning": "上午具体安排，引用真实景点名称",
      "afternoon": "下午具体安排",
      "evening": "晚上安排",
      "meals": ["早餐推荐（具体餐厅或食物）", "午餐推荐", "晚餐推荐"],
      "tips": ["结合天气的实用贴士", "其他注意事项"]
    }
  ],
  "packing_tips": ["打包建议，需包含天气相关建议"],
  "budget_advice": "预算建议"
}

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
# 每个节点函数接受 State，返回要更新的字段（dict）。
# 图引擎会把返回值 merge 进 State，触发 reducer（如 add_messages）。

async def agent_node(state: TravelAgentState) -> dict:
    """
    Agent 节点：LLM 决策中枢。
    读取当前对话历史，决定：
      - 还需要哪些工具数据 → 返回带 tool_calls 的 AIMessage
      - 信息已足够 → 返回普通 AIMessage（finish_reason=stop）
    """
    llm_with_tools = _make_llm(0.3).bind_tools(TOOLS)
    response = await llm_with_tools.ainvoke(state["messages"])
    return {"messages": [response]}   # add_messages reducer 会 append


async def generate_node(state: TravelAgentState) -> dict:
    """
    生成节点：结构化输出最终行程。
    LLM 不再有工具访问权，专注于把对话历史里收集到的信息转成 JSON。
    """
    llm = _make_llm(0.7)
    messages = list(state["messages"]) + [HumanMessage(content=JSON_GENERATION_PROMPT)]
    response = await llm.ainvoke(messages)
    trip_plan = json.loads(response.content)
    return {"trip_plan": trip_plan}


# ── 条件边函数 ────────────────────────────────────────────────────────────────
# 条件边决定 agent 节点执行后走哪条路。
# 返回字符串 key，对应 add_conditional_edges 里的路由表。

def should_continue(state: TravelAgentState) -> str:
    """
    检查 agent 节点最后一条消息：
    有 tool_calls → "tools"（继续调工具）
    无 tool_calls → "generate"（信息收集完毕，生成行程）
    """
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "generate"


# ── 构建图 ────────────────────────────────────────────────────────────────────

def build_graph():
    builder = StateGraph(TravelAgentState)

    # 添加节点
    builder.add_node("agent",    agent_node)
    builder.add_node("tools",    ToolNode(TOOLS))   # LangGraph 内置，自动执行 tool_calls
    builder.add_node("generate", generate_node)

    # 设置入口
    builder.set_entry_point("agent")

    # 条件边：agent → tools 或 generate
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "generate": "generate"},
    )

    # 固定边
    builder.add_edge("tools", "agent")      # 工具执行完 → 回到 agent 继续决策
    builder.add_edge("generate", END)       # 生成完毕 → 结束

    return builder.compile()


# 模块级图实例（全局复用，避免每次请求重建）
graph = build_graph()


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def extract_agent_log(messages: list) -> list[str]:
    """从消息历史中提取工具调用记录，供前端展示推理过程。"""
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
    preferences = request.preferences or ["城市漫游", "当地美食"]
    days = []
    for i in range(request.travel_days):
        focus = preferences[i % len(preferences)]
        days.append(DayPlan(
            day=i + 1,
            theme=f"{focus}探索日",
            morning=f"在{request.city}安排轻松的早餐和城市地标打卡，围绕[{focus}]开始当天行程。",
            afternoon=f"挑选1到2个与[{focus}]相关的核心地点，控制节奏，留出拍照和休息时间。",
            evening="傍晚回到热闹街区，体验当地晚餐，并安排一次轻量散步或夜景活动。",
            meals=["早餐：选择口碑稳定的本地早餐店", "午餐：在核心景点附近安排特色餐", "晚餐：优先选择评价高、步行可达的餐厅"],
            tips=["上午尽量安排热门点，避开高峰排队。", "晚间活动控制在住宿点附近，减少返程压力。"],
        ))
    return TripPlan(
        city=request.city,
        travel_days=request.travel_days,
        summary=f"这是一份适合在 {request.city} 进行 {request.travel_days} 天游玩的备用行程。",
        days=days,
        packing_tips=["准备舒适的步行鞋。", "随身带充电宝和水杯。"],
        budget_advice=f"按 {request.budget_level} 预算设计，建议把主要开销留给交通和门票。",
    )


def _build_user_message(request: TripRequest) -> str:
    prefs = "、".join(request.preferences) if request.preferences else "城市漫游、当地美食"
    notes_text = f"\n额外要求：{request.notes}" if request.notes else ""
    return (
        f"请为我规划一份 {request.city} {request.travel_days} 天的旅行行程。\n"
        f"旅行偏好：{prefs}\n"
        f"预算级别：{request.budget_level}"
        f"{notes_text}"
    )


async def run_graph(request: TripRequest) -> tuple[TripPlan, list[str]]:
    """
    运行 LangGraph 图，返回 (TripPlan, agent_log)。
    图引擎自动管理节点调度、状态传递和工具执行循环。
    """
    initial_state: TravelAgentState = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=_build_user_message(request)),
        ],
        "trip_plan": None,
    }

    try:
        final_state = await graph.ainvoke(initial_state)
        raw_plan  = final_state["trip_plan"]
        agent_log = extract_agent_log(final_state["messages"])
        plan = TripPlan(**raw_plan)
        print(f"[Graph] 完成，工具调用 {len(agent_log)} 次，消息 {len(final_state['messages'])} 条")
        return plan, agent_log

    except Exception as e:
        import traceback
        print(f"[Graph ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        return _build_fallback_plan(request), []
