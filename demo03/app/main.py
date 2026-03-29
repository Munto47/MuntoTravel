from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import create_trip_plan
from .schemas import TripPlanResponse, TripRequest


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="MuntoTravel Demo 03", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def read_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "app": "MuntoTravel Demo 03"}


@app.post("/api/trip/plan", response_model=TripPlanResponse)
async def plan_trip(request: TripRequest) -> TripPlanResponse:
    plan, agent_log = await create_trip_plan(request)
    return TripPlanResponse(
        success=True,
        message="行程生成成功",
        data=plan,
        agent_log=agent_log,
    )
