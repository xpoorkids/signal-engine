from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok", "service": "signal-engine"}


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow:"
