from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from datetime import datetime
import json

from app.database import get_session
from app.models import TrackedShow
from app import tmdb
from app.notifications import send_test_notification
from app.config import DEFAULT_REMINDER_HOURS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ── Dashboard ──────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: Session = Depends(get_session)):
    shows = session.exec(select(TrackedShow).order_by(TrackedShow.next_episode_date)).all()
    # Parse streaming_services JSON for display
    for show in shows:
        show._services = json.loads(show.streaming_services) if show.streaming_services else []
    return templates.TemplateResponse("index.html", {"request": request, "shows": shows})


# ── Search ─────────────────────────────────────────────────────────────────────

@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    results = []
    if q:
        results = await tmdb.search_shows(q)
    return templates.TemplateResponse("partials/search_results.html", {"request": request, "results": results, "q": q})


# ── Add show ───────────────────────────────────────────────────────────────────

@router.post("/shows/add", response_class=HTMLResponse)
async def add_show(request: Request, tmdb_id: int = Form(...), session: Session = Depends(get_session)):
    # Check not already tracked
    existing = session.exec(select(TrackedShow).where(TrackedShow.tmdb_id == tmdb_id)).first()
    if existing:
        return HTMLResponse('<p class="text-yellow-400">Already in your watchlist.</p>')

    details = await tmdb.get_show_details(tmdb_id)
    if not details:
        return HTMLResponse('<p class="text-red-400">Could not fetch show details.</p>')

    show = TrackedShow(
        **details,
        reminder_hours=DEFAULT_REMINDER_HOURS,
        last_refreshed=datetime.utcnow(),
    )
    session.add(show)
    session.commit()
    session.refresh(show)

    show._services = json.loads(show.streaming_services) if show.streaming_services else []
    return templates.TemplateResponse("partials/show_card.html", {"request": request, "show": show})


# ── Remove show ────────────────────────────────────────────────────────────────

@router.delete("/shows/{show_id}", response_class=HTMLResponse)
async def remove_show(show_id: int, session: Session = Depends(get_session)):
    show = session.get(TrackedShow, show_id)
    if not show:
        raise HTTPException(status_code=404)
    session.delete(show)
    session.commit()
    return HTMLResponse("")


# ── Update reminder settings ───────────────────────────────────────────────────

@router.post("/shows/{show_id}/reminder", response_class=HTMLResponse)
async def update_reminder(
    request: Request,
    show_id: int,
    reminder_hours: int = Form(...),
    reminder_enabled: bool = Form(False),
    session: Session = Depends(get_session),
):
    show = session.get(TrackedShow, show_id)
    if not show:
        raise HTTPException(status_code=404)
    show.reminder_hours = reminder_hours
    show.reminder_enabled = reminder_enabled
    session.add(show)
    session.commit()
    return HTMLResponse('<span class="text-green-400 text-sm">Saved</span>')


# ── Manual refresh ─────────────────────────────────────────────────────────────

@router.post("/shows/{show_id}/refresh", response_class=HTMLResponse)
async def refresh_show(request: Request, show_id: int, session: Session = Depends(get_session)):
    show = session.get(TrackedShow, show_id)
    if not show:
        raise HTTPException(status_code=404)
    details = await tmdb.get_show_details(show.tmdb_id)
    if details:
        for key, val in details.items():
            setattr(show, key, val)
        show.last_refreshed = datetime.utcnow()
        session.add(show)
        session.commit()
        session.refresh(show)
    show._services = json.loads(show.streaming_services) if show.streaming_services else []
    return templates.TemplateResponse("partials/show_card.html", {"request": request, "show": show})


# ── Test notification ──────────────────────────────────────────────────────────

@router.post("/test-notification", response_class=HTMLResponse)
async def test_notification():
    await send_test_notification()
    return HTMLResponse('<span class="text-green-400 text-sm">Test notification sent!</span>')
