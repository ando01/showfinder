from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text
from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False)


def _migrate(conn):
    """Add any columns that exist in models but are missing from the live DB."""
    migrations = [
        ("trackedshow",  "vote_average",   "REAL"),
        ("trackedmovie", "vote_average",   "REAL"),
        ("trackedshow",  "genres",         "TEXT"),
        ("trackedshow",  "watch_status",   "TEXT DEFAULT 'watching'"),
    ]
    for table, column, col_type in migrations:
        existing = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
        if existing and column not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


def create_db():
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        _migrate(conn)
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session


def get_setting(session: Session, key: str, default: str = "") -> str:
    from app.models import AppSettings
    row = session.get(AppSettings, key)
    return row.value if row else default


def save_setting(session: Session, key: str, value: str):
    from app.models import AppSettings
    row = session.get(AppSettings, key)
    if row:
        row.value = value
    else:
        row = AppSettings(key=key, value=value)
    session.add(row)
    session.commit()
