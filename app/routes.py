import asyncio
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, desc
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os
import re

from app.database import get_session, get_setting, save_setting
from app.models import TrackedShow, TrackedMovie
from app import tmdb
from app.notifications import send_test_notification
from app.config import DEFAULT_REMINDER_HOURS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Register tojson filter (not in Jinja2 stdlib)
import json as _json
templates.env.filters["tojson"] = lambda v: _json.dumps(v)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _compute_next_episode(show):
    """Return (next_season, next_episode_num) after the last watched episode, or (None, None) if all caught up."""
    s = show.last_watched_season
    e = show.last_watched_episode_num
    if not s or not e:
        return None, None
    if not show.season_episode_counts:
        return s, e + 1  # fallback: no season data
    season_map = json.loads(show.season_episode_counts)
    ep_count = season_map.get(str(s), 0)
    if e < ep_count:
        return s, e + 1
    # End of season — find the next season
    max_season = max(int(k) for k in season_map) if season_map else s
    next_s = s + 1
    while next_s <= max_season and str(next_s) not in season_map:
        next_s += 1
    if next_s <= max_season:
        return next_s, 1
    return None, None  # all seasons complete


def _enrich_show(show):
    show._services = json.loads(show.streaming_services) if show.streaming_services else []
    show._genres = json.loads(show.genres) if show.genres else []
    show._next_season, show._next_episode_num = _compute_next_episode(show)

    show._is_season_finale = False
    show._is_series_finale = False
    if show._next_season and show._next_episode_num and show.season_episode_counts:
        season_map = json.loads(show.season_episode_counts)
        ep_count = season_map.get(str(show._next_season), 0)
        if ep_count and show._next_episode_num == ep_count:
            show._is_season_finale = True
            max_season = max(int(k) for k in season_map) if season_map else 0
            if show._next_season == max_season and show.status in ("Ended", "Canceled", "Cancelled"):
                show._is_series_finale = True

    return show


def _enrich_movie(movie):
    movie._services = json.loads(movie.streaming_services) if movie.streaming_services else []
    movie._genres = json.loads(movie.genres) if movie.genres else []
    movie._watched = (movie.watch_status or "want_to_watch") == "completed"
    return movie


def _parse_ep(ep_str):
    """Parse 'S02E08' into (2, 8), or None."""
    if not ep_str:
        return None
    m = re.match(r'S(\d+)E(\d+)', ep_str)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _next_episode_available(show, today, now_utc):
    """Return True if show._next_season/episode is an episode that is available to watch right now."""
    if not show._next_season or not show._next_episode_num:
        return False
    # No future episode scheduled — all episodes have aired
    if not show.next_episode_number or not show.next_episode_date:
        return True
    next_ep_date = show.next_episode_date.date()
    # Future air date — only show if user's next-to-watch is an already-aired earlier episode
    if next_ep_date > today:
        unaired = _parse_ep(show.next_episode_number)
        if not unaired:
            return True
        return (show._next_season, show._next_episode_num) < unaired
    # Past air date — definitely available
    if next_ep_date < today:
        return True
    # Airs today — check if user needs an earlier (already aired) episode first
    unaired = _parse_ep(show.next_episode_number)
    if unaired and (show._next_season, show._next_episode_num) < unaired:
        return True
    # User's next episode IS the one releasing today — gate on the release time
    if show.air_time and show.air_timezone:
        try:
            tz = ZoneInfo(show.air_timezone)
            h, m = map(int, show.air_time.split(":"))
            release_dt = datetime(
                next_ep_date.year, next_ep_date.month, next_ep_date.day, h, m, tzinfo=tz
            )
            if now_utc < release_dt.astimezone(ZoneInfo("UTC")):
                return False
        except Exception:
            pass
    return True


def _build_up_next(shows, today, now_utc):
    """Shows in Up Next: watching status, with at least one available episode to watch."""
    result = []
    for s in shows:
        if (s.watch_status or "watching") != "watching":
            continue
        if s.last_watched_season is None:
            result.append(s)  # not started — always show
        elif s._next_season is not None and _next_episode_available(s, today, now_utc):
            result.append(s)  # has progress and next episode is available
    return sorted(result, key=lambda s: s.name.lower())


# ── Dashboard ──────────────────────────────────────────────────────────────────

def _local_today(session: Session) -> date:
    tz_name = get_setting(session, "timezone") or os.getenv("TZ", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    return datetime.now(tz=tz).date()


def _schedule_label(d: date, today: date) -> str:
    delta = (d - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return d.strftime("%A · %b %d")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: Session = Depends(get_session)):
    today = _local_today(session)
    now_utc = datetime.now(tz=ZoneInfo("UTC"))

    shows = session.exec(select(TrackedShow)).all()
    for show in shows:
        _enrich_show(show)
    up_next_shows = _build_up_next(shows, today, now_utc)

    # Coming Up: watching shows that are NOT in Watch Now but have a known future air date
    up_next_ids = {s.id for s in up_next_shows}
    from collections import defaultdict
    schedule_dict = defaultdict(list)
    for show in shows:
        if (show.watch_status or "watching") != "watching":
            continue
        if show.id in up_next_ids:
            continue
        if show.next_episode_date and show.next_episode_date.date() >= today:
            schedule_dict[show.next_episode_date.date()].append(show)

    # Sort shows within each day alphabetically, then sort days
    coming_up_schedule = [
        (_schedule_label(d, today), sorted(day_shows, key=lambda s: s.name.lower()))
        for d, day_shows in sorted(schedule_dict.items())
    ]

    # Movies to watch — want_to_watch, released or releasing in next 30 days
    movies = session.exec(select(TrackedMovie)).all()
    for movie in movies:
        _enrich_movie(movie)

    cutoff_future = datetime.combine(today + timedelta(days=30), datetime.max.time())
    movies_to_watch = sorted(
        [m for m in movies
         if (m.watch_status or "want_to_watch") == "want_to_watch"
         and m.release_date and m.release_date <= cutoff_future],
        key=lambda m: m.release_date or datetime.max
    )

    return templates.TemplateResponse("index.html", {
        "request": request,
        "up_next_shows": up_next_shows,
        "coming_up_schedule": coming_up_schedule,
        "movies_to_watch": movies_to_watch,
        "today": today,
    })


# ── Search ─────────────────────────────────────────────────────────────────────

@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    show_results, movie_results = [], []
    if q:
        show_results, movie_results = await asyncio.gather(
            tmdb.search_shows(q),
            tmdb.search_movies(q),
        )
    return templates.TemplateResponse("partials/search_results.html", {
        "request": request,
        "show_results": show_results,
        "movie_results": movie_results,
        "q": q,
    })


# ── Trailer ────────────────────────────────────────────────────────────────────

@router.get("/trailer/{media_type}/{tmdb_id}", response_class=HTMLResponse)
async def trailer(media_type: str, tmdb_id: int):
    if media_type not in ("tv", "movie"):
        raise HTTPException(status_code=400)
    key = await tmdb.get_trailer_key(tmdb_id, media_type)
    if key:
        return HTMLResponse(
            f'<iframe src="https://www.youtube.com/embed/{key}?autoplay=1" '
            f'class="w-full aspect-video" frameborder="0" '
            f'allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>'
        )
    return HTMLResponse('<p class="text-gray-400 text-sm text-center py-10">No trailer available.</p>')


# ── TV Shows page ──────────────────────────────────────────────────────────────

@router.get("/tv", response_class=HTMLResponse)
async def tv_page(request: Request, session: Session = Depends(get_session)):
    shows = session.exec(select(TrackedShow)).all()
    for show in shows:
        _enrich_show(show)
    all_genres = sorted({g for show in shows for g in show._genres})
    return templates.TemplateResponse("tv.html", {
        "request": request,
        "all_genres": all_genres,
    })


@router.get("/tv/list", response_class=HTMLResponse)
async def tv_list(
    request: Request,
    status: str = "watching",
    genres: str = "",
    sort: str = "next_episode",
    session: Session = Depends(get_session),
):
    shows = session.exec(select(TrackedShow)).all()
    for show in shows:
        _enrich_show(show)
    shows = [s for s in shows if (s.watch_status or "watching") == status]
    if genres:
        genre_list = [g.strip() for g in genres.split(",") if g.strip()]
        shows = [s for s in shows if any(g in s._genres for g in genre_list)]
    shows = _sort_shows(shows, sort)
    return templates.TemplateResponse("partials/tv_list.html", {
        "request": request,
        "shows": shows,
        "status": status,
    })


# ── Movies page ────────────────────────────────────────────────────────────────

@router.get("/movies", response_class=HTMLResponse)
async def movies_page(request: Request, session: Session = Depends(get_session)):
    movies = session.exec(select(TrackedMovie)).all()
    for movie in movies:
        _enrich_movie(movie)
    all_genres = sorted({g for movie in movies for g in movie._genres})
    return templates.TemplateResponse("movies.html", {
        "request": request,
        "all_genres": all_genres,
    })


@router.get("/movies/list", response_class=HTMLResponse)
async def movies_list(
    request: Request,
    status: str = "want_to_watch",
    genres: str = "",
    sort: str = "release_date",
    session: Session = Depends(get_session),
):
    today = _local_today(session)
    movies = session.exec(select(TrackedMovie)).all()
    for movie in movies:
        _enrich_movie(movie)
    movies = [m for m in movies if (m.watch_status or "want_to_watch") == status]
    if genres:
        genre_list = [g.strip() for g in genres.split(",") if g.strip()]
        movies = [m for m in movies if any(g in m._genres for g in genre_list)]
    movies = _sort_movies(movies, sort)
    return templates.TemplateResponse("partials/movie_status_list.html", {
        "request": request,
        "movies": movies,
        "today": today,
    })


# ── Home suggestions ───────────────────────────────────────────────────────────

@router.get("/home/suggestions", response_class=HTMLResponse)
async def home_suggestions(request: Request, session: Session = Depends(get_session)):
    all_shows = session.exec(select(TrackedShow)).all()
    tracked_show_ids = {s.tmdb_id for s in all_shows}
    tracked_movie_ids = {m.tmdb_id for m in session.exec(select(TrackedMovie)).all()}
    seeds = _recommendation_seeds(all_shows, limit=4)

    async def fetch_recs(show):
        recs = await tmdb.get_recommendations(show.tmdb_id)
        filtered = [r for r in recs if r["tmdb_id"] not in tracked_show_ids][:5]
        return show.name, filtered

    results = await asyncio.gather(*[fetch_recs(s) for s in seeds])
    sections = [(name, recs) for name, recs in results if recs]

    return templates.TemplateResponse("partials/suggestions.html", {
        "request": request,
        "sections": sections,
        "tracked_show_ids": tracked_show_ids,
        "tracked_movie_ids": tracked_movie_ids,
    })


# ── Add show ───────────────────────────────────────────────────────────────────

@router.post("/shows/add", response_class=HTMLResponse)
async def add_show(
    request: Request,
    tmdb_id: int = Form(...),
    source: str = Form(""),
    watch_status: str = Form("watching"),
    session: Session = Depends(get_session),
):
    existing = session.exec(select(TrackedShow).where(TrackedShow.tmdb_id == tmdb_id)).first()
    if existing:
        if source == "discover":
            return HTMLResponse('<span class="text-xs text-gray-500">✓ Added</span>')
        if source == "search":
            return HTMLResponse('<span class="text-sm text-yellow-400 flex-shrink-0 px-3 py-1.5">Already added</span>')
        return HTMLResponse('<p class="text-yellow-400">Already in your watchlist.</p>')

    details = await tmdb.get_show_details(tmdb_id)
    if not details:
        return HTMLResponse('<p class="text-red-400">Could not fetch show details.</p>')

    show = TrackedShow(
        **details,
        watch_status=watch_status,
        reminder_hours=DEFAULT_REMINDER_HOURS,
        last_refreshed=datetime.utcnow(),
    )
    session.add(show)
    session.commit()
    session.refresh(show)

    _enrich_show(show)
    if source == "discover":
        return HTMLResponse('<span class="text-xs text-green-400">✓ Added</span>')
    if source == "search":
        card_html = templates.get_template("partials/show_card.html").render(show=show)
        return HTMLResponse(
            '<span class="text-sm text-green-400 flex-shrink-0 px-3 py-1.5">✓ Added</span>'
            f'<div class="contents" hx-swap-oob="beforeend:#watchlist-inner">{card_html}</div>'
        )
    return templates.TemplateResponse("partials/show_card.html", {"request": request, "show": show})


# ── Rate show ──────────────────────────────────────────────────────────────────

@router.post("/shows/{show_id}/rating", response_class=HTMLResponse)
async def rate_show(
    request: Request,
    show_id: int,
    rating: int = Form(...),
    session: Session = Depends(get_session),
):
    show = session.get(TrackedShow, show_id)
    if not show:
        raise HTTPException(status_code=404)
    show.user_rating = rating if rating > 0 else None
    session.add(show)
    session.commit()
    return templates.TemplateResponse("partials/show_rating.html", {"request": request, "show": show})


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


# ── Update watch status ────────────────────────────────────────────────────────

@router.post("/shows/{show_id}/status", response_class=HTMLResponse)
async def update_show_status(
    request: Request,
    show_id: int,
    watch_status: str = Form(...),
    session: Session = Depends(get_session),
):
    show = session.get(TrackedShow, show_id)
    if not show:
        raise HTTPException(status_code=404)
    show.watch_status = watch_status
    session.add(show)
    session.commit()
    session.refresh(show)
    _enrich_show(show)
    return templates.TemplateResponse("partials/show_card.html", {"request": request, "show": show})


# ── Update watch progress ──────────────────────────────────────────────────────

@router.post("/shows/{show_id}/progress", response_class=HTMLResponse)
async def update_progress(
    request: Request,
    show_id: int,
    last_watched_season: int = Form(...),
    last_watched_episode_num: int = Form(...),
    session: Session = Depends(get_session),
):
    show = session.get(TrackedShow, show_id)
    if not show:
        raise HTTPException(status_code=404)
    show.last_watched_season = last_watched_season
    show.last_watched_episode_num = last_watched_episode_num
    session.add(show)
    session.commit()
    session.refresh(show)
    _enrich_show(show)
    return templates.TemplateResponse("partials/show_card.html", {"request": request, "show": show})


# ── Mark next episode watched (Up Next queue) ──────────────────────────────────

@router.post("/shows/{show_id}/watched-next", response_class=HTMLResponse)
async def watched_next(
    request: Request,
    show_id: int,
    session: Session = Depends(get_session),
):
    show = session.get(TrackedShow, show_id)
    if not show:
        raise HTTPException(status_code=404)
    _enrich_show(show)
    if show._next_season:
        show.last_watched_season = show._next_season
        show.last_watched_episode_num = show._next_episode_num
        session.add(show)
        session.commit()

    today = _local_today(session)
    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    shows = session.exec(select(TrackedShow)).all()
    for s in shows:
        _enrich_show(s)
    up_next_shows = _build_up_next(shows, today, now_utc)
    return templates.TemplateResponse("partials/up_next.html", {
        "request": request,
        "up_next_shows": up_next_shows,
    })


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
    _enrich_show(show)
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


# ── Watchlist filter/sort ──────────────────────────────────────────────────────

def _sort_shows(shows, sort: str):
    if sort == "name":
        return sorted(shows, key=lambda s: s.name.lower())
    if sort == "date_added":
        return sorted(shows, key=lambda s: s.added_at, reverse=True)
    if sort == "rating":
        return sorted(shows, key=lambda s: s.vote_average or 0, reverse=True)
    # default: next_episode
    return sorted(shows, key=lambda s: s.next_episode_date or datetime.max)


def _sort_movies(movies, sort: str):
    if sort == "name":
        return sorted(movies, key=lambda m: m.title.lower())
    if sort == "date_added":
        return sorted(movies, key=lambda m: m.added_at, reverse=True)
    if sort == "rating":
        return sorted(movies, key=lambda m: m.vote_average or 0, reverse=True)
    # default: release_date
    return sorted(movies, key=lambda m: m.release_date or datetime.max)


# ── Legacy watchlist route (backward compat) ───────────────────────────────────

@router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_partial(
    request: Request,
    sort: str = "next_episode",
    status: str = "all",
    genre: str = "all",
    session: Session = Depends(get_session),
):
    shows = session.exec(select(TrackedShow)).all()
    for show in shows:
        _enrich_show(show)
    if status != "all":
        shows = [s for s in shows if (s.watch_status or "watching") == status]
    if genre != "all":
        shows = [s for s in shows if genre in s._genres]
    shows = _sort_shows(shows, sort)
    return templates.TemplateResponse("partials/tv_watchlist.html", {
        "request": request,
        "shows": shows,
    })


# ── Movies ─────────────────────────────────────────────────────────────────────

@router.post("/movies/add", response_class=HTMLResponse)
async def add_movie(
    request: Request,
    tmdb_id: int = Form(...),
    source: str = Form(""),
    watch_status: str = Form("want_to_watch"),
    session: Session = Depends(get_session),
):
    existing = session.exec(select(TrackedMovie).where(TrackedMovie.tmdb_id == tmdb_id)).first()
    if existing:
        if source == "discover":
            return HTMLResponse('<span class="text-xs text-gray-500">✓ Added</span>')
        if source == "search":
            return HTMLResponse('<span class="text-sm text-yellow-400 flex-shrink-0 px-3 py-1.5">Already added</span>')
        return HTMLResponse('<p class="text-yellow-400 text-sm">Already in your movies.</p>')

    details = await tmdb.get_movie_details(tmdb_id)
    if not details:
        return HTMLResponse('<p class="text-red-400 text-sm">Could not fetch movie details.</p>')

    watched = watch_status == "completed"
    movie = TrackedMovie(**details, watch_status=watch_status, watched=watched, last_refreshed=datetime.utcnow())
    session.add(movie)
    session.commit()
    session.refresh(movie)

    _enrich_movie(movie)
    if source == "discover":
        return HTMLResponse('<span class="text-xs text-green-400">✓ Added</span>')
    today = _local_today(session)
    if source == "search":
        card_html = templates.get_template("partials/movie_card.html").render(movie=movie, today=today)
        return HTMLResponse(
            '<span class="text-sm text-green-400 flex-shrink-0 px-3 py-1.5">✓ Added</span>'
            f'<div class="contents" hx-swap-oob="beforeend:#movie-list-inner">{card_html}</div>'
        )
    return templates.TemplateResponse("partials/movie_card.html", {"request": request, "movie": movie, "today": today})


# ── Rate movie ─────────────────────────────────────────────────────────────────

@router.post("/movies/{movie_id}/rating", response_class=HTMLResponse)
async def rate_movie(
    request: Request,
    movie_id: int,
    rating: int = Form(...),
    session: Session = Depends(get_session),
):
    movie = session.get(TrackedMovie, movie_id)
    if not movie:
        raise HTTPException(status_code=404)
    movie.user_rating = rating if rating > 0 else None
    session.add(movie)
    session.commit()
    return templates.TemplateResponse("partials/movie_rating.html", {"request": request, "movie": movie})


@router.delete("/movies/{movie_id}", response_class=HTMLResponse)
async def remove_movie(movie_id: int, session: Session = Depends(get_session)):
    movie = session.get(TrackedMovie, movie_id)
    if not movie:
        raise HTTPException(status_code=404)
    session.delete(movie)
    session.commit()
    return HTMLResponse("")


@router.post("/movies/{movie_id}/watched", response_class=HTMLResponse)
async def toggle_watched(request: Request, movie_id: int, session: Session = Depends(get_session)):
    movie = session.get(TrackedMovie, movie_id)
    if not movie:
        raise HTTPException(status_code=404)
    new_status = "want_to_watch" if (movie.watch_status or "want_to_watch") == "completed" else "completed"
    movie.watch_status = new_status
    movie.watched = new_status == "completed"
    session.add(movie)
    session.commit()
    session.refresh(movie)
    _enrich_movie(movie)
    today = _local_today(session)
    return templates.TemplateResponse("partials/movie_card.html", {"request": request, "movie": movie, "today": today})


@router.post("/movies/{movie_id}/refresh", response_class=HTMLResponse)
async def refresh_movie(request: Request, movie_id: int, session: Session = Depends(get_session)):
    movie = session.get(TrackedMovie, movie_id)
    if not movie:
        raise HTTPException(status_code=404)
    details = await tmdb.get_movie_details(movie.tmdb_id)
    if details:
        for key, val in details.items():
            setattr(movie, key, val)
        movie.last_refreshed = datetime.utcnow()
        session.add(movie)
        session.commit()
        session.refresh(movie)
    _enrich_movie(movie)
    today = _local_today(session)
    return templates.TemplateResponse("partials/movie_card.html", {"request": request, "movie": movie, "today": today})


# ── Legacy movie-list route (backward compat) ──────────────────────────────────

@router.get("/movie-list", response_class=HTMLResponse)
async def movie_list_partial(
    request: Request,
    sort: str = "release_date",
    hide_watched: bool = False,
    session: Session = Depends(get_session),
):
    today = _local_today(session)
    movies = session.exec(select(TrackedMovie)).all()
    for movie in movies:
        _enrich_movie(movie)
    if hide_watched:
        movies = [m for m in movies if not m._watched]
    movies = _sort_movies(movies, sort)
    return templates.TemplateResponse("partials/movie_list.html", {
        "request": request,
        "movies": movies,
        "today": today,
    })


# ── Discover ───────────────────────────────────────────────────────────────────

@router.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request):
    return templates.TemplateResponse("discover.html", {"request": request})


@router.get("/discover/trending", response_class=HTMLResponse)
async def discover_trending(request: Request, session: Session = Depends(get_session)):
    shows = await tmdb.get_trending()
    tracked_ids = {s.tmdb_id for s in session.exec(select(TrackedShow)).all()}
    return templates.TemplateResponse("partials/discover_trending.html", {
        "request": request,
        "shows": shows,
        "tracked_ids": tracked_ids,
    })


def _recommendation_seeds(shows, limit: int = 10) -> list:
    """Order shows for recommendation seeding: 4-5 star first, then unrated completed, then unrated watching."""
    high_rated  = [s for s in shows if s.user_rating and s.user_rating >= 4]
    unrated_done = [s for s in shows if not s.user_rating and (s.watch_status or "watching") == "completed"]
    unrated_watching = [s for s in shows if not s.user_rating and (s.watch_status or "watching") == "watching"]
    return (high_rated + unrated_done + unrated_watching)[:limit]


@router.get("/discover/similar", response_class=HTMLResponse)
async def discover_similar(request: Request, session: Session = Depends(get_session)):
    all_shows = session.exec(select(TrackedShow)).all()
    tracked_ids = {s.tmdb_id for s in all_shows}
    seeds = _recommendation_seeds(all_shows)

    async def fetch_recs(show):
        recs = await tmdb.get_recommendations(show.tmdb_id)
        filtered = [r for r in recs if r["tmdb_id"] not in tracked_ids]
        return show.name, filtered, show.user_rating

    results = await asyncio.gather(*[fetch_recs(s) for s in seeds])
    sections = [(name, recs, rating) for name, recs, rating in results if recs]

    return templates.TemplateResponse("partials/discover_similar.html", {
        "request": request,
        "sections": sections,
        "tracked_ids": tracked_ids,
    })


@router.get("/discover/top", response_class=HTMLResponse)
async def discover_top(request: Request, year: int = None, session: Session = Depends(get_session)):
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    shows = await tmdb.get_top_by_year(year)
    tracked_ids = {s.tmdb_id for s in session.exec(select(TrackedShow)).all()}
    current_year = _date.today().year
    return templates.TemplateResponse("partials/discover_top.html", {
        "request": request,
        "shows": shows,
        "tracked_ids": tracked_ids,
        "selected_year": year,
        "years": list(range(current_year, 1999, -1)),
    })


# ── Discover — Movies ──────────────────────────────────────────────────────────

@router.get("/discover/movies/trending", response_class=HTMLResponse)
async def discover_movies_trending(request: Request, session: Session = Depends(get_session)):
    shows = await tmdb.get_trending_movies()
    tracked_ids = {m.tmdb_id for m in session.exec(select(TrackedMovie)).all()}
    return templates.TemplateResponse("partials/discover_trending_movies.html", {
        "request": request,
        "shows": shows,
        "tracked_ids": tracked_ids,
    })


@router.get("/discover/movies/similar", response_class=HTMLResponse)
async def discover_movies_similar(request: Request, session: Session = Depends(get_session)):
    tracked = session.exec(select(TrackedMovie)).all()
    tracked_ids = {m.tmdb_id for m in tracked}

    async def fetch_recs(movie):
        recs = await tmdb.get_movie_recommendations(movie.tmdb_id)
        filtered = [r for r in recs if r["tmdb_id"] not in tracked_ids]
        return movie.title, filtered

    results = await asyncio.gather(*[fetch_recs(m) for m in tracked[:10]])
    sections = [(title, recs) for title, recs in results if recs]

    return templates.TemplateResponse("partials/discover_similar_movies.html", {
        "request": request,
        "sections": sections,
        "tracked_ids": tracked_ids,
    })


@router.get("/discover/movies/top", response_class=HTMLResponse)
async def discover_movies_top(request: Request, year: int = None, session: Session = Depends(get_session)):
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    shows = await tmdb.get_top_movies_by_year(year)
    tracked_ids = {m.tmdb_id for m in session.exec(select(TrackedMovie)).all()}
    current_year = _date.today().year
    return templates.TemplateResponse("partials/discover_top_movies.html", {
        "request": request,
        "shows": shows,
        "tracked_ids": tracked_ids,
        "selected_year": year,
        "years": list(range(current_year, 1999, -1)),
    })


# ── Test notification ──────────────────────────────────────────────────────────

@router.post("/test-notification", response_class=HTMLResponse)
async def test_notification(session: Session = Depends(get_session)):
    success, message = await send_test_notification(session=session)
    color = "text-green-400" if success else "text-red-400"
    return HTMLResponse(f'<span class="{color} text-sm">{message}</span>')
