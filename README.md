# ShowFinder

A self-hosted web app to track your TV shows and movies. Search for any title and ShowFinder will tell you what streaming service it's on, track your watch progress, show you what to watch next, and send you push notification reminders so you never miss a new episode.

## Features

### TV Shows
- Search for any TV show via TMDB and add it to your watchlist
- See which streaming service it's on (Netflix, Max, Hulu, Disney+, Apple TV+, Peacock, Paramount+, and more)
- View the next episode date, episode number, and title
- **Up Next queue** — shows what episode to watch next for every show you're actively watching
  - Episodes are held out of Up Next until they're actually available (e.g. HBO shows appear after 9 PM ET, Netflix after midnight PT)
  - Tap **Watched** on any Up Next row to mark that episode done and advance to the next one
  - Shows drop out of Up Next automatically once you're all caught up
  - Season Finale and Series Finale badges highlight milestone episodes
- Track watch progress per show (last watched season/episode)
- Watch status: **Watching**, **On Hold**, **Wishlist**, or **Completed**
- Shows auto-promote from Completed back to Watching when a new season is detected
- Get push notifications via your self-hosted [ntfy](https://ntfy.sh) server
- Set reminder timing per show (1, 2, 3, 6, 12, or 24 hours before air time)

### Movies
- Search for and track movies alongside your TV shows
- See release dates and streaming availability
- **Releasing This Week** section highlights upcoming and newly released movies from your list
- Mark movies as watched
- Sort by release date, name, date added, or rating

### Discover
- Browse trending TV shows and movies
- Get recommendations based on shows you're already tracking
- Browse top-rated shows and movies by year
- Add directly to your watchlist from the Discover page

### General
- Show and movie data auto-refreshes every 6 hours from TMDB
- Filter and sort your watchlist by status, genre, or sort order
- Timezone-aware — dates and times reflect your configured local timezone
- Fully self-hosted with Docker; SQLite database persists across updates

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

To verify notifications are working, click the **Test Notification** button on the Settings page.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `TMDB_API_KEY` | Free API key from themoviedb.org | required |
| `NTFY_URL` | Your ntfy server base URL | required |
| `NTFY_TOPIC` | ntfy topic to publish reminders to | `showfinder` |
| `SECRET_KEY` | Random string for session signing | required |
| `DEFAULT_REMINDER_HOURS` | Hours before air time to send reminder | `1` |

Timezone and ntfy settings can also be configured from the **Settings** page in the app.

## Updating

```bash
git pull
docker compose up -d --build
```

## Data

The SQLite database is stored in `./data/showfinder.db` on your host machine, mounted as a Docker volume. Your watchlist persists across container restarts and updates.
