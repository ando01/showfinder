from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from datetime import datetime, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os

from app.database import get_session, get_setting, save_setting
from app.models import TrackedShow
from app import tmdb
from app.notifications import send_test_notification
from app.config import DEFAULT_REMINDER_HOURS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ── Dashboard ──────────────────────────────────────────────────────────────────

def _local_today(session: Session) -> date:
    tz_name = get_setting(session, "timezone") or os.getenv("TZ", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    return datetime.now(tz=tz).date()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: Session = Depends(get_session)):
    shows = session.exec(select(TrackedShow).order_by(TrackedShow.next_episode_date)).all()
    today = _local_today(session)
    today_shows = []
    for show in shows:
        show._services = json.loads(show.streaming_services) if show.streaming_services else []
        if show.next_episode_date and show.next_episode_date.date() == today:
            today_shows.append(show)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "shows": shows,
        "today_shows": today_shows,
        "today_label": today.strftime("%A, %B %d"),
    })


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


# ── Settings page ──────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: Session = Depends(get_session)):
    ntfy_url = get_setting(session, "ntfy_url")
    ntfy_topic = get_setting(session, "ntfy_topic")
    timezone = get_setting(session, "timezone") or os.getenv("TZ", "UTC")
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "ntfy_url": ntfy_url,
        "ntfy_topic": ntfy_topic,
        "timezone": timezone,
    })


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    ntfy_url: str = Form(...),
    ntfy_topic: str = Form(...),
    timezone: str = Form(...),
    session: Session = Depends(get_session),
):
    save_setting(session, "ntfy_url", ntfy_url.strip())
    save_setting(session, "ntfy_topic", ntfy_topic.strip())
    save_setting(session, "timezone", timezone.strip())
    return HTMLResponse('<span class="text-green-400 text-sm">Settings saved.</span>')


# ── Test notification ──────────────────────────────────────────────────────────

@router.post("/test-notification", response_class=HTMLResponse)
async def test_notification(session: Session = Depends(get_session)):
    success, message = await send_test_notification(session=session)
    color = "text-green-400" if success else "text-red-400"
    return HTMLResponse(f'<span class="{color} text-sm">{message}</span>')
