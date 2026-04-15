from sqlmodel import SQLModel, create_engine, Session
from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False)


def create_db():
    SQLModel.metadata.create_all(engine)


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
