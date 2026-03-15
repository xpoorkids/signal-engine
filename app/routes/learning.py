from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.services.signal_learning_service import (
    get_engine_health_digest,
    get_diagnostics_summary,
    get_learning_digest,
    get_latest_learning_report,
    get_learning_report,
    render_engine_health_html,
    render_diagnostics_html,
    render_learning_digest_html,
    render_learning_report_html,
)


router = APIRouter()


@router.get("/learning/report/latest")
def learning_report_latest():
    report = get_latest_learning_report()
    if report is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return report


@router.get("/learning/report/latest/dashboard")
def learning_report_latest_dashboard():
    try:
        return HTMLResponse(content=render_learning_report_html())
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/report/latest/digest")
def learning_report_latest_digest():
    digest = get_learning_digest()
    if digest is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return digest


@router.get("/learning/report/latest/digest/dashboard")
def learning_report_latest_digest_dashboard():
    try:
        return HTMLResponse(content=render_learning_digest_html())
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/report/{report_date}")
def learning_report_by_date(report_date: str):
    report = get_learning_report(report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return report


@router.get("/learning/report/{report_date}/dashboard")
def learning_report_dashboard(report_date: str):
    try:
        return HTMLResponse(content=render_learning_report_html(report_date))
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/report/{report_date}/digest")
def learning_report_digest(report_date: str):
    digest = get_learning_digest(report_date)
    if digest is None:
        raise HTTPException(status_code=404, detail="learning_report_not_found")
    return digest


@router.get("/learning/report/{report_date}/digest/dashboard")
def learning_report_digest_dashboard(report_date: str):
    try:
        return HTMLResponse(content=render_learning_digest_html(report_date))
    except KeyError:
        raise HTTPException(status_code=404, detail="learning_report_not_found")


@router.get("/learning/diagnostics/summary")
def learning_diagnostics_summary(hours: int = 24):
    return get_diagnostics_summary(hours=max(1, hours))


@router.get("/learning/health")
def learning_engine_health(hours: int = 6):
    return get_engine_health_digest(hours=max(1, hours))


@router.get("/learning/health/dashboard")
def learning_engine_health_dashboard(hours: int = 6):
    return HTMLResponse(content=render_engine_health_html(hours=max(1, hours)))


@router.get("/learning/diagnostics/dashboard")
def learning_diagnostics_dashboard(hours: int = 24):
    return HTMLResponse(content=render_diagnostics_html(hours=max(1, hours)))
