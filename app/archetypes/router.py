"""archetypes: HTTP routes. Thin: they check input and call the service."""
from fastapi import APIRouter, HTTPException

from app.archetypes import service
from app.archetypes.schemas import ApplyIn, AssignIn

router = APIRouter(prefix="/api/archetypes", tags=["archetypes"])


@router.get("")
def clusters():
    return service.clusters()


@router.get("/scatter")
def scatter():
    return service.scatter()


@router.post("/scan")
def scan(stab: str = ""):
    """Fit-index tables. ?stab=1 adds bootstrap stability (slow)."""
    return service.fit_tables(stab == "1")


@router.post("/assign")
def assign(body: AssignIn = AssignIn()):
    if not 2 <= body.k <= 12:
        raise HTTPException(400, "k out of range")
    return service.assign_k(body.k)


@router.get("/quality")
def quality():
    """Quality-per-k curve + auto pick, straight from persisted meta."""
    found = service.quality()
    if found is None:
        raise HTTPException(404, "no scan in meta - run optimize first")
    return found


@router.post("/optimize")
def optimize():
    return service.optimize_k()


@router.get("/preview")
def preview(k: int = 10):
    """Fit any k without writing; plain-English cluster descriptions."""
    if not 2 <= k <= 14:
        raise HTTPException(400, "k out of range")
    return service.preview_k(k)


@router.post("/apply")
def apply(body: ApplyIn = ApplyIn()):
    if not 2 <= body.k <= 14:
        raise HTTPException(400, "k out of range")
    return service.apply(body.k)
