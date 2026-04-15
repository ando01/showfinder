# ShowFinder

A self-hosted web app to track your TV shows. Search for any show and ShowFinder will tell you what streaming service it's on, when new episodes air, and send you a push notification reminder so you never miss one.

## Features

- Search for any TV show via TMDB
- See which streaming service it's on (Netflix, Max, Hulu, Disney+, Apple TV+, Peacock, Paramount+, and more)
- View the next episode date, episode number, and title
- Get push notifications via your self-hosted [ntfy](https://ntfy.sh) server
- Set reminder timing per show (1, 2, 3, 6, 12, or 24 hours before air time)
- Show data auto-refreshes daily from TMDB
- Fully self-hosted with Docker

## Requirements

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- A free [TMDB API key](https://www.themoviedb.org/settings/api)
- A self-hosted [ntfy](https://ntfy.sh) server (or use the public ntfy.sh)

## Installation

**1. Clone the repo**

```bash
git clone https://github.com/ando01/showfinder.git
cd showfinder
```

**2. Create your environment file**

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
TMDB_API_KEY=your_tmdb_api_key_here
NTFY_URL=https://ntfy.yourdomain.com
NTFY_TOPIC=showfinder
SECRET_KEY=some-random-string
DEFAULT_REMINDER_HOURS=1
```

**3. Start the app**

```bash
docker compose up -d
```

**4. Open in your browser**

```
http://localhost:8000
```

To verify notifications are working, click the **Test Notification** button in the top right corner.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `TMDB_API_KEY` | Free API key from themoviedb.org | required |
| `NTFY_URL` | Your ntfy server base URL | required |
| `NTFY_TOPIC` | ntfy topic to publish reminders to | `showfinder` |
| `SECRET_KEY` | Random string for session signing | required |
| `DEFAULT_REMINDER_HOURS` | Hours before air time to send reminder | `1` |

## Updating

```bash
git pull
docker compose up -d --build
```

## Data

The SQLite database is stored in `./data/showfinder.db` on your host machine, mounted as a Docker volume. Your watchlist persists across container restarts and updates.
