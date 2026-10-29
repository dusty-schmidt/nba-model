"""projections: HTTP routes. Thin: they check input and call the service."""
from fastapi import APIRouter, HTTPException

from app.projections import service

router = APIRouter(prefix="/api", tags=["projections"])


@router.get("/prob/{pid}")
def prob(pid: int):
    """Next-game projection plus over/under probabilities per stat.

    Strategy is the walk-forward winner from research/projections.py: learned
    mean for pts/reb/ast, plain roll20 for fg3m, per-player shrunk
    spread. Live use may train on full history.
    """
    found = service.next_game(pid)
    if found is None:
        raise HTTPException(404, "insufficient history")
    return found
