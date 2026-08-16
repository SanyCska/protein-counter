"""Точка входа HTTP API мини-аппа."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import migrate, today
from .routers import ai, analytics, diary, products, profile, supplements, workouts

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def allowed_origins() -> list[str]:
    """Разрешённые origin'ы фронта. В деве — локальный Vite."""
    raw = (os.environ.get("WEBAPP_ORIGINS") or "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate()
    logger.info("API запущен, база готова")
    yield


app = FastAPI(
    title="Nutrition Mini App API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    profile.router,
    diary.router,
    workouts.router,
    supplements.router,
    products.router,
    ai.router,
    analytics.router,
):
    app.include_router(router, prefix="/api")


@app.get("/api/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "today": today().isoformat()}
