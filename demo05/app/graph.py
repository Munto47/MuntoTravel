"""
graph.py —— LangGraph 旅行规划 Agent（demo05 版）

与 demo04 的唯一关键差异：
  run_graph() 接受 profile_text 参数，在 System Prompt 末尾注入用户画像。
  这让同一张图可以在"无画像"和"有画像"两种模式下工作，
  行为由 System Prompt 的内容驱动，图结构完全不变。
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
1. 收到用户的旅行需求后，先主动调用工具收集信息
2. 至少调用 get_weather 一次，至少调用 get_attractions 一次（可按用户偏好多次调用）
3. 收集完信息后，我会另行要求你输出最终行程，不需要在工具调用阶段输出行程"""

JSON_GENERATION_PROMPT = """你已经收集了足够的信息。
请现在根据以上天气预报和景点数据，输出完整的旅行计划，格式严格遵守：

{
  "city": "目的地城市",
  "travel_days": 天数（整数）,
  "summary": "2-3句话的整体概述，体现天气和景点特色",
  "profile_applications": [
    "行程节奏：（说明景点密度如何体现用户的节奏偏好，如：每天安排2个核心景点符合慢游风格）",
    "美食探索：（说明餐厅选择如何体现用户的美食偏好）",
    "（其他有画像依据的维度，每条一句，格式：维度名：具体调整说明）"
  ],
  "days": [
    {
      "day": 第几天（整数）,
      "theme": "当天主题",
      "morning": "上午具体安排，引用真实景点名称",
      "afternoon": "下午具体安排",
      "evening": "晚上安排",
      "meals": ["早餐推荐（具体餐厅或食物）", "午餐推荐", "晚餐推荐"],
      "tips": ["结合天气的实用贴士", "其他注意事项"],
      "profile_note": "1-2句话，指名道姓说明本日哪个具体景点/餐厅/安排体现了用户的哪条画像偏好（如：武侯祠+锦里2个景点贴合慢游节奏；晚餐推荐陈麻婆豆腐是基于用户的深度探店偏好）；若无画像信息则留空字符串"
    }
  ],
  "packing_tips": ["打包建议，需包含天气相关建议"],
  "budget_advice": "预算建议"
}

重要说明：
- profile_applications 每条必须包含「维度名：」前缀，内容要具体，不能泛泛而谈
- profile_note 必须引用本日真实出现的景点或餐厅名称，不能写通用模板
- 没有用户画像时，profile_applications 填 []，profile_note 填 ""
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
    llm_with_tools = _make_llm(0.3).bind_tools(TOOLS)
    response = await llm_with_tools.ainvoke(state["messages"])
    return {"messages": [response]}


async def generate_node(state: TravelAgentState) -> dict:
    llm = _make_llm(0.7)
    messages = list(state["messages"]) + [HumanMessage(content=JSON_GENERATION_PROMPT)]
    response = await llm.ainvoke(messages)
    trip_plan = json.loads(response.content)
    return {"trip_plan": trip_plan}


def should_continue(state: TravelAgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
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


def _build_user_message(request: TripRequest) -> str:
    prefs = "、".join(request.preferences) if request.preferences else ""
    prefs_text = f"\n旅行偏好：{prefs}" if prefs else ""
    notes_text = f"\n额外要求：{request.notes}" if request.notes else ""
    return (
        f"请为我规划一份 {request.city} {request.travel_days} 天的旅行行程。"
        f"{prefs_text}\n"
        f"预算级别：{request.budget_level}"
        f"{notes_text}"
    )


async def run_graph(
    request: TripRequest,
    profile_text: str = "",
) -> tuple[TripPlan, list[str]]:
    """
    运行 LangGraph Agent。
    profile_text 非空时，将用户画像追加至 System Prompt，
    使 LLM 在规划时参照用户偏好调整景点密度、餐厅档次等。
    """
    system_content = BASE_SYSTEM_PROMPT
    if profile_text:
        system_content = f"{BASE_SYSTEM_PROMPT}\n\n{profile_text}"

    initial_state: TravelAgentState = {
        "messages": [
            SystemMessage(content=system_content),
            HumanMessage(content=_build_user_message(request)),
        ],
        "trip_plan": None,
    }

    try:
        final_state = await graph.ainvoke(initial_state)
        raw_plan  = final_state["trip_plan"]
        agent_log = extract_agent_log(final_state["messages"])
        plan = TripPlan(**raw_plan)
        mode = "画像模式" if profile_text else "通用模式"
        print(f"[Graph] {mode} 完成，工具调用 {len(agent_log)} 次")
        return plan, agent_log

    except Exception as e:
        import traceback
        print(f"[Graph ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        return _build_fallback_plan(request), []
