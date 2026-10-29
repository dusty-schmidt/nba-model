"""players: HTTP routes. Thin: they check input and call the service."""
from fastapi import APIRouter, HTTPException

from app.players import service

router = APIRouter(prefix="/api", tags=["players"])


@router.get("/state")
def state():
    return service.state()


@router.get("/players")
def players(q: str = ""):
    return service.search(q)


@router.get("/player/{pid}")
def player(pid: int):
    found = service.player(pid)
    if found is None:
        raise HTTPException(404, "unknown player")
    return found


@router.get("/leaders")
def leaders(season: int = 2026, stat: str = "pts", min_games: int = 3):
    return service.leaders(season, stat, min_games)
