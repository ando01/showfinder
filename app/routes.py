import asyncio
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, desc
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os

from app.database import get_session, get_setting, save_setting
from app.models import TrackedShow, TrackedMovie
from app import tmdb
from app.notifications import send_test_notification
from app.config import DEFAULT_REMINDER_HOURS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


def _build_up_next(shows):
    """Shows in Up Next: watching status, not yet started OR has a next episode to watch."""
    return sorted(
        [
            s for s in shows
            if (s.watch_status or "watching") == "watching"
            and not (s.last_watched_season and s._next_season is None)
        ],
        key=lambda s: s.name.lower(),
    )


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
    today = _local_today(session)

    # TV shows
    shows = session.exec(select(TrackedShow)).all()
    for show in shows:
        _enrich_show(show)
    all_genres = sorted({g for show in shows for g in show._genres})
    up_next_shows = _build_up_next(shows)
    shows = _sort_shows(shows, "next_episode")

    # Movies
    movies = session.exec(select(TrackedMovie)).all()
    for movie in movies:
        movie._services = json.loads(movie.streaming_services) if movie.streaming_services else []
    movies = _sort_movies(movies, "release_date")

    week_start = datetime.combine(today - timedelta(days=3), datetime.min.time())
    week_end = datetime.combine(today + timedelta(days=14), datetime.max.time())
    releasing_this_week = [
        m for m in movies
        if not m.watched and m.release_date and week_start <= m.release_date <= week_end
    ]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "shows": shows,
        "up_next_shows": up_next_shows,
        "movies": movies,
        "releasing_this_week": releasing_this_week,
        "today": today,
        "all_genres": all_genres,
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


# ── Add show ───────────────────────────────────────────────────────────────────

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


@router.post("/shows/add", response_class=HTMLResponse)
async def add_show(request: Request, tmdb_id: int = Form(...), source: str = Form(""), watch_status: str = Form("watching"), session: Session = Depends(get_session)):
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

    shows = session.exec(select(TrackedShow)).all()
    for s in shows:
        _enrich_show(s)
    up_next_shows = _build_up_next(shows)
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
    # default: release_date, watched items last
    return sorted(movies, key=lambda m: (m.watched, m.release_date or datetime.max))


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


@router.get("/movie-list", response_class=HTMLResponse)
async def movie_list_partial(request: Request, sort: str = "release_date", hide_watched: bool = False, session: Session = Depends(get_session)):
    today = _local_today(session)
    movies = session.exec(select(TrackedMovie)).all()
    for movie in movies:
        movie._services = json.loads(movie.streaming_services) if movie.streaming_services else []
    if hide_watched:
        movies = [m for m in movies if not m.watched]
    movies = _sort_movies(movies, sort)
    return templates.TemplateResponse("partials/movie_list.html", {
        "request": request,
        "movies": movies,
        "today": today,
    })


# ── Movies ─────────────────────────────────────────────────────────────────────

@router.post("/movies/add", response_class=HTMLResponse)
async def add_movie(request: Request, tmdb_id: int = Form(...), source: str = Form(""), watched: bool = Form(False), session: Session = Depends(get_session)):
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

    movie = TrackedMovie(**details, watched=watched, last_refreshed=datetime.utcnow())
    session.add(movie)
    session.commit()
    session.refresh(movie)

    movie._services = json.loads(movie.streaming_services) if movie.streaming_services else []
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
    movie.watched = not movie.watched
    session.add(movie)
    session.commit()
    session.refresh(movie)
    movie._services = json.loads(movie.streaming_services) if movie.streaming_services else []
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
    movie._services = json.loads(movie.streaming_services) if movie.streaming_services else []
    today = _local_today(session)
    return templates.TemplateResponse("partials/movie_card.html", {"request": request, "movie": movie, "today": today})


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


@router.get("/discover/similar", response_class=HTMLResponse)
async def discover_similar(request: Request, session: Session = Depends(get_session)):
    tracked = session.exec(select(TrackedShow)).all()
    tracked_ids = {s.tmdb_id for s in tracked}

    async def fetch_recs(show):
        recs = await tmdb.get_recommendations(show.tmdb_id)
        filtered = [r for r in recs if r["tmdb_id"] not in tracked_ids]
        return show.name, filtered

    results = await asyncio.gather(*[fetch_recs(s) for s in tracked[:10]])
    sections = [(name, shows) for name, shows in results if shows]

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
