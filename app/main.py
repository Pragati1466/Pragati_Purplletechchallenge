"""
Store Intelligence API - Main Application
Built for Purplle Tech Challenge 2026
"""

from __future__ import annotations
import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.database import init_db, close_db
from app.ingestion import router as ingestion_router
from app.metrics import router as metrics_router
from app.funnel import router as funnel_router
from app.heatmap import router as heatmap_router
from app.anomalies import router as anomalies_router
from app.health import router as health_router
from app.seed import router as seed_router
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

# ── Structured logging ────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


# ── Lifecycle ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("api_starting", version="1.0.0")
    await init_db()
    yield
    logger.info("api_stopping")
    await close_db()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail analytics from CCTV footage — Purplle Tech Challenge 2026",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request logging middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    start = time.time()

    # Buffer the response so we can read event_count from ingest responses
    response = await call_next(request)

    latency_ms = int((time.time() - start) * 1000)

    # Extract store_id from path: /stores/{store_id}/...
    path_parts = request.url.path.strip("/").split("/")
    store_id = None
    if len(path_parts) >= 2 and path_parts[0] == "stores":
        store_id = path_parts[1]

    # Derive a clean endpoint label (e.g. /stores/{id}/metrics)
    endpoint = request.url.path

    # event_count is set on request.state by the ingest handler
    event_count = getattr(request.state, "event_count", None)

    log_fields = dict(
        trace_id=trace_id,
        store_id=store_id,
        endpoint=endpoint,
        method=request.method,
        latency_ms=latency_ms,
        status_code=response.status_code,
    )
    if event_count is not None:
        log_fields["event_count"] = event_count

    logger.info("request", **log_fields)
    response.headers["X-Trace-ID"] = trace_id
    return response


# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error("unhandled_exception", trace_id=trace_id, error=str(exc),
                 path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred",
            "trace_id": trace_id,
        },
    )


# ── Database unavailable handler ──────────────────────────────────────────────
from sqlalchemy.exc import OperationalError, InterfaceError

@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
async def db_error_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error("database_error", trace_id=trace_id, error=str(exc))
    return JSONResponse(
        status_code=503,
        content={
            "error": "service_unavailable",
            "message": "Database temporarily unavailable. Please retry.",
            "retry_after": 30,
            "trace_id": trace_id,
        },
    )


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(ingestion_router, prefix="/events",  tags=["Events"])
app.include_router(metrics_router,   prefix="/stores",  tags=["Metrics"])
app.include_router(funnel_router,    prefix="/stores",  tags=["Funnel"])
app.include_router(heatmap_router,   prefix="/stores",  tags=["Heatmap"])
app.include_router(anomalies_router, prefix="/stores",  tags=["Anomalies"])
app.include_router(health_router,                       tags=["Health"])
app.include_router(seed_router,                         tags=["Demo"])


@app.get("/", tags=["Root"])
async def root():
    return {
        "name": "Store Intelligence API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "dashboard": "/dashboard",
    }


@app.get("/dashboard", tags=["Dashboard"], include_in_schema=False)
async def dashboard():
    """Serve the live dashboard HTML."""
    dashboard_path = Path(__file__).parent.parent / "dashboard" / "index.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    return {"error": "Dashboard not found. Run from repo root."}
