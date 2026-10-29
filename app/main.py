"""The service: one FastAPI app, a health check, each feature's routes, and the dashboard page."""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from app.archetypes.router import router as archetypes_router
from app.config import settings
from app.players.router import router as players_router
from app.projections.router import router as projections_router

# Logs go to stderr (the lab collects them from there), never to a log file.
logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "static" / "index.html"

app = FastAPI(title=settings.app_name)
app.include_router(players_router)
app.include_router(projections_router)
app.include_router(archetypes_router)


# The dashboard reads failures as {"error": "..."}, so every error answers in that shape.
@app.exception_handler(HTTPException)
def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
def bad_input(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "bad input", "detail": exc.errors()}, status_code=422)


@app.exception_handler(Exception)
def crash(request: Request, exc: Exception):
    log.exception("%s %s failed", request.method, request.url.path)
    return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/", include_in_schema=False)
def page():
    """The mobile dashboard: one HTML file, no build step, talking to the /api routes."""
    return FileResponse(PAGE)


@app.get("/health")
def health():
    """For uptime checks (e.g. Uptime Kuma): answers while the service is up."""
    return {"status": "ok"}
