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
                "You are a travel planner. Return only valid JSON with keys: "
                "city, travel_days, summary, days, packing_tips, budget_advice. "
                "Each day must contain: day, theme, morning, afternoon, evening, meals, tips. "
                "Keep the plan practical and concise."
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
