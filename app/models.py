from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime, date


class AppSettings(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class TrackedShow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tmdb_id: int = Field(index=True, unique=True)
    name: str
    poster_path: Optional[str] = None
    overview: Optional[str] = None
    status: Optional[str] = None          # "Returning Series", "Ended", etc.
    network: Optional[str] = None         # broadcast network or streaming service
    streaming_services: Optional[str] = None  # JSON list of service names
    air_day: Optional[str] = None         # "Monday", "Friday", etc.
    air_time: Optional[str] = None        # "21:00" in show's local time
    next_episode_date: Optional[datetime] = None
    next_episode_name: Optional[str] = None
    next_episode_number: Optional[str] = None  # "S02E05"
    vote_average: Optional[float] = None
    reminder_hours: int = Field(default=1)
    reminder_enabled: bool = Field(default=True)
    added_at: datetime = Field(default_factory=datetime.utcnow)
    last_refreshed: Optional[datetime] = None


class TrackedMovie(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tmdb_id: int = Field(index=True, unique=True)
    title: str
    poster_path: Optional[str] = None
    overview: Optional[str] = None
    release_date: Optional[datetime] = None
    status: Optional[str] = None          # "Released", "In Production", etc.
    streaming_services: Optional[str] = None  # JSON list of service names
    runtime: Optional[int] = None         # minutes
    vote_average: Optional[float] = None
    watched: bool = Field(default=False)
    added_at: datetime = Field(default_factory=datetime.utcnow)
    last_refreshed: Optional[datetime] = None
