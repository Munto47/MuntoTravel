import json
import os
from typing import Any

import httpx
from dotenv import load_dotenv

from .schemas import TripRequest


load_dotenv()


def _build_messages(request: TripRequest) -> list[dict[str, str]]:
    preferences = ", ".join(request.preferences) if request.preferences else "general city highlights"
    notes = request.notes or "None"
    return [
        {
            "role": "system",
            "content": (
                "你是一名旅行规划师，注意只返回有效的JSON格式，不要有多于信息\n"
                "The JSON must follow this exact structure and types:\n"
                "{\n"
                '  "city": "string",\n'
                '  "travel_days": integer,\n'
                '  "summary": "string",\n'
                '  "days": [\n'
                "    {\n"
                '      "day": integer,\n'
                '      "theme": "string",\n'
                '      "morning": "string",\n'
                '      "afternoon": "string",\n'
                '      "evening": "string",\n'
                '      "meals": ["string", "string", "string"],\n'
                '      "tips": ["string", "string"]\n'
                "    }\n"
                "  ],\n"
                '  "packing_tips": ["string", "string"],\n'
                '  "budget_advice": "string"\n'
                "}\n"
                "IMPORTANT: meals and tips and packing_tips must be JSON arrays of strings, not objects or single strings.同时，所有的返回的内容使用中文"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Create a {request.travel_days}-day trip plan for {request.city}. "
                f"Preferences: {preferences}. Budget level: {request.budget_level}. "
                f"Extra notes: {notes}."
            ),
        },
    ]


async def generate_trip_plan_json(request: TripRequest) -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model,
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
        "messages": _build_messages(request),
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
