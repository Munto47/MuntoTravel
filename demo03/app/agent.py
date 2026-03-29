"""
agent.py —— 两阶段 Agent 循环

demo02 的模式（编排式）：
    我们的代码决定 → 先取天气 → 注入 prompt → 调 LLM

demo03 的模式（Agent 式）：
    LLM 自己决定 → 需要什么工具 → 我们执行工具 → 结果还给 LLM → LLM 再决定...

两阶段设计：
    Phase 1：工具收集阶段（tools enabled，LLM 自由调用工具）
    Phase 2：结构化生成阶段（response_format: json_object，输出最终行程 JSON）

为什么分两阶段？
    因为 OpenAI 的 tool_calls 和 response_format: json_object 不能同时使用。
    先让 LLM 自由探索，信息收集完毕后再切换到严格 JSON 输出模式。
"""

import json
import os

import httpx
from dotenv import load_dotenv

from .schemas import DayPlan, TripPlan, TripRequest
from .tools import TOOL_DEFINITIONS, execute_tool


load_dotenv()

MAX_TOOL_TURNS = 6  # 最多允许 6 轮工具调用，防止死循环

SYSTEM_PROMPT = """你是一名专业的中文旅行规划师。

你有以下工具可以使用：
- get_weather：获取目的地实时天气预报
- get_attractions：查询目的地各类热门景点和餐厅

工作要求：
1. 收到用户的旅行需求后，先主动调用工具收集信息
2. 至少调用 get_weather 一次，至少调用 get_attractions 一次（可按用户偏好调用多次）
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


def _build_user_message(request: TripRequest) -> str:
    prefs = "、".join(request.preferences) if request.preferences else "城市漫游、当地美食"
    notes_text = f"\n额外要求：{request.notes}" if request.notes else ""
    return (
        f"请为我规划一份 {request.city} {request.travel_days} 天的旅行行程。\n"
        f"旅行偏好：{prefs}\n"
        f"预算级别：{request.budget_level}"
        f"{notes_text}"
    )


def _build_fallback_plan(request: TripRequest) -> TripPlan:
    preferences = request.preferences or ["城市漫游", "当地美食"]
    days = []
    for i in range(request.travel_days):
        focus = preferences[i % len(preferences)]
        days.append(DayPlan(
            day=i + 1,
            theme=f"{focus}探索日",
            morning=f"在{request.city}安排轻松的早餐和城市地标打卡，围绕{focus}开始当天行程。",
            afternoon=f"挑选1到2个与{focus}相关的核心地点，控制节奏，留出拍照和休息时间。",
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


async def run_agent(request: TripRequest) -> tuple[TripPlan, list[str]]:
    """
    运行两阶段 Agent 循环。

    返回：
        (TripPlan, agent_log)
        agent_log 是工具调用记录列表，供前端展示 Agent 的推理过程
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未配置")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url}/chat/completions"

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(request)},
    ]
    agent_log: list[str] = []

    async with httpx.AsyncClient(timeout=90) as client:

        # ── Phase 1：工具收集阶段 ──────────────────────────────────────────────
        # LLM 可以自由决定调用哪些工具、调用几次
        for turn in range(MAX_TOOL_TURNS):
            resp = await client.post(url, headers=headers, json={
                "model": model,
                "temperature": 0.3,   # 工具调用阶段用低温，保证参数准确
                "messages": messages,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",  # LLM 自行决定是否调工具
            })
            resp.raise_for_status()
            choice = resp.json()["choices"][0]

            if choice["finish_reason"] == "tool_calls":
                # LLM 决定调工具：执行它，把结果加回 messages
                assistant_msg = choice["message"]
                messages.append(assistant_msg)

                for tc in assistant_msg["tool_calls"]:
                    func_name = tc["function"]["name"]
                    func_args = tc["function"]["arguments"]

                    print(f"[Agent] 调用工具：{func_name}  参数：{func_args}")
                    result = await execute_tool(func_name, func_args)
                    print(f"[Agent] 工具结果：{result[:100]}...")

                    # 记录到 agent_log（供前端展示）
                    args_dict = json.loads(func_args)
                    if func_name == "get_weather":
                        log_msg = f"查询天气：{args_dict.get('city')} · {args_dict.get('days')} 天"
                    elif func_name == "get_attractions":
                        log_msg = f"查询景点：{args_dict.get('city')} · {args_dict.get('category')}"
                    else:
                        log_msg = f"调用工具：{func_name}"
                    agent_log.append(log_msg)

                    # 把工具结果返回给 LLM（role: tool）
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })

            else:
                # finish_reason == "stop"：LLM 认为信息收集完毕
                print(f"[Agent] 工具收集完成，共 {turn + 1} 轮，调用 {len(agent_log)} 次工具")
                break

        # ── Phase 2：结构化生成阶段 ────────────────────────────────────────────
        # 带着完整的上下文（包含所有工具结果），切换到 JSON 模式输出最终行程
        messages.append({"role": "user", "content": JSON_GENERATION_PROMPT})

        final_resp = await client.post(url, headers=headers, json={
            "model": model,
            "temperature": 0.7,   # 生成阶段恢复正常温度
            "messages": messages,
            "response_format": {"type": "json_object"},
            # 注意：这里不传 tools，LLM 只能输出内容，不会再调工具
        })
        final_resp.raise_for_status()
        raw_json = final_resp.json()["choices"][0]["message"]["content"]

    raw_plan = json.loads(raw_json)
    return TripPlan(**raw_plan), agent_log


async def create_trip_plan(request: TripRequest) -> tuple[TripPlan, list[str]]:
    """供 main.py 调用的入口，包含 fallback 处理。"""
    try:
        return await run_agent(request)
    except Exception as e:
        import traceback
        print(f"[Agent ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        return _build_fallback_plan(request), []
