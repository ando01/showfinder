from dotenv import load_dotenv
import os

load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w300"

NTFY_URL = os.getenv("NTFY_URL", "https://ntfy.sh")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "showfinder")

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
DEFAULT_REMINDER_HOURS = int(os.getenv("DEFAULT_REMINDER_HOURS", "1"))

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./showfinder.db")
