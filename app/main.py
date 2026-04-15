from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.database import create_db
from app.routes import router
from app.scheduler import start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db()
    start_scheduler()
    yield


app = FastAPI(title="ShowFinder", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)
