from fastapi import APIRouter
from app.services.watch_summary import build_watch_summary

router = APIRouter()

@router.get("/watch/summary")
def watch_summary(hours: int = 24):
    return build_watch_summary(hours)
