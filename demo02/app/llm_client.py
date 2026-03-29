import json
import os
from typing import Any

import httpx
from dotenv import load_dotenv

from .schemas import TripRequest, WeatherInfo


load_dotenv()

SYSTEM_PROMPT = """你是一名专业的中文旅行规划师。请严格按照以下 JSON 结构返回行程，不要包含任何 JSON 之外的文字。

{
  "city": "字符串，目的地城市名",
  "travel_days": 整数，行程天数,
  "summary": "字符串，整体行程概述（2-3句话）",
  "days": [
    {
      "day": 整数，第几天,
      "theme": "字符串，当天主题",
      "morning": "字符串，上午安排（1-2句话）",
      "afternoon": "字符串，下午安排（1-2句话）",
      "evening": "字符串，晚上安排（1-2句话）",
      "meals": ["字符串，早餐建议", "字符串，午餐建议", "字符串，晚餐建议"],
      "tips": ["字符串，小贴士1", "字符串，小贴士2"]
    }
  ],
  "packing_tips": ["字符串，打包建议1", "字符串，打包建议2"],
  "budget_advice": "字符串，预算建议（1-2句话）"
}

重要约束：
- meals、tips、packing_tips 必须是字符串数组，不能是对象或单个字符串
- 所有内容使用中文
- 只返回 JSON，不要有任何前缀或后缀"""


def _build_messages(
    request: TripRequest,
    weather: WeatherInfo | None = None,
) -> list[dict[str, str]]:
    prefs = "、".join(request.preferences) if request.preferences else "城市漫游、当地美食"
    notes_text = f"\n额外要求：{request.notes}" if request.notes else ""
    weather_text = f"\n\n{weather.to_prompt_text()}" if weather else ""
    weather_instruction = (
        "\n\n请根据以上天气预报合理调整行程安排，"
        "例如：雨天减少户外活动、安排室内景点；高温天气建议早出晚归、中午休整；"
        "天气好的日子优先安排户外和自然景观。"
        "同时在 packing_tips 中体现天气相关的打包建议。"
        if weather else ""
    )

    user_content = (
        f"请为我规划一份{request.city} {request.travel_days} 天的旅行行程。\n"
        f"旅行偏好：{prefs}\n"
        f"预算级别：{request.budget_level}"
        f"{notes_text}"
        f"{weather_text}"
        f"{weather_instruction}"
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


async def generate_trip_plan_json(
    request: TripRequest,
    weather: WeatherInfo | None = None,
) -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未配置")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "messages": _build_messages(request, weather),
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
