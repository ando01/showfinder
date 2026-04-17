from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select
from datetime import datetime, timedelta

from app.database import engine
from app.models import TrackedShow, TrackedMovie
from app.notifications import send_reminder
from app import tmdb

scheduler = AsyncIOScheduler()


async def check_reminders():
    """Fire reminders for shows whose next episode is coming up within the reminder window."""
    now = datetime.utcnow()
    with Session(engine) as session:
        shows = session.exec(
            select(TrackedShow).where(
                TrackedShow.reminder_enabled == True,
                TrackedShow.next_episode_date != None,
            )
        ).all()
        for show in shows:
            if not show.next_episode_date:
                continue
            hours_until = (show.next_episode_date - now).total_seconds() / 3600
            # Fire if we're within a 15-minute window of the reminder threshold
            if 0 <= (hours_until - show.reminder_hours) <= 0.25:
                await send_reminder(
                    show_name=show.name,
                    episode_number=show.next_episode_number or "",
                    episode_name=show.next_episode_name or "",
                    air_time_str=show.next_episode_date.strftime("%B %d at %I:%M %p") if show.next_episode_date else "",
                )


REFRESH_INTERVAL_HOURS = 6


async def refresh_all():
    """Refresh stale shows and movies from TMDB every 6 hours."""
    stale_cutoff = datetime.utcnow() - timedelta(hours=REFRESH_INTERVAL_HOURS)
    with Session(engine) as session:
        shows = session.exec(select(TrackedShow)).all()
        for show in shows:
            if show.last_refreshed and show.last_refreshed > stale_cutoff:
                continue
            details = await tmdb.get_show_details(show.tmdb_id)
            if not details:
                continue
            for key, val in details.items():
                setattr(show, key, val)
            show.last_refreshed = datetime.utcnow()
            session.add(show)

        movies = session.exec(select(TrackedMovie)).all()
        for movie in movies:
            if movie.last_refreshed and movie.last_refreshed > stale_cutoff:
                continue
            details = await tmdb.get_movie_details(movie.tmdb_id)
            if not details:
                continue
            for key, val in details.items():
                setattr(movie, key, val)
            movie.last_refreshed = datetime.utcnow()
            session.add(movie)

        session.commit()


def start_scheduler():
    # Check reminders every 15 minutes
    scheduler.add_job(check_reminders, CronTrigger(minute="*/15"), id="check_reminders", replace_existing=True)
    # Refresh all shows and movies every 6 hours
    scheduler.add_job(refresh_all, CronTrigger(hour="*/6", minute=0), id="refresh_all", replace_existing=True)
    scheduler.start()
