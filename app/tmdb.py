import httpx
import json
from typing import Optional
from app.config import TMDB_API_KEY, TMDB_BASE_URL, TMDB_IMAGE_BASE

HEADERS = {"accept": "application/json"}

# Known release times per network/service (time in network's home timezone).
# Keys are lowercase substrings to match against network or streaming service names.
NETWORK_RELEASE_TIMES = {
    # Premium cable / streaming — drops at 9 PM ET
    "hbo":              ("21:00", "America/New_York"),
    "max":              ("21:00", "America/New_York"),
    "showtime":         ("21:00", "America/New_York"),
    "amc":              ("21:00", "America/New_York"),
    "starz":            ("21:00", "America/New_York"),
    # FX drops at 10 PM ET
    "fx":               ("22:00", "America/New_York"),
    # Streaming — midnight releases
    "netflix":          ("00:00", "America/Los_Angeles"),
    "apple tv":         ("00:00", "America/Los_Angeles"),
    "amazon":           ("00:00", "America/Los_Angeles"),
    "prime video":      ("00:00", "America/Los_Angeles"),
    "disney+":          ("00:00", "America/New_York"),
    "hulu":             ("00:00", "America/New_York"),
    "peacock":          ("00:00", "America/New_York"),
    "paramount+":       ("00:00", "America/New_York"),
    # Broadcast — typical primetime start
    "nbc":              ("20:00", "America/New_York"),
    "cbs":              ("20:00", "America/New_York"),
    "abc":              ("20:00", "America/New_York"),
    "fox":              ("20:00", "America/New_York"),
    "the cw":           ("20:00", "America/New_York"),
    "cw":               ("20:00", "America/New_York"),
}

# US streaming provider IDs we care about
STREAMING_PROVIDER_IDS = {
    8: "Netflix",
    9: "Amazon Prime",
    15: "Hulu",
    337: "Disney+",
    1899: "Max",
    386: "Peacock",
    531: "Paramount+",
    350: "Apple TV+",
    2: "Apple TV",
}


def _auth_params(extra: dict = None) -> dict:
    params = {"api_key": TMDB_API_KEY}
    if extra:
        params.update(extra)
    return params


async def search_shows(query: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/search/tv",
            params=_auth_params({"query": query, "language": "en-US", "page": 1}),
            headers=HEADERS,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [
            {
                "tmdb_id": s["id"],
                "name": s["name"],
                "overview": s.get("overview", ""),
                "poster_path": f"{TMDB_IMAGE_BASE}{s['poster_path']}" if s.get("poster_path") else None,
                "first_air_date": s.get("first_air_date", ""),
            }
            for s in results[:8]
        ]


async def get_show_details(tmdb_id: int) -> Optional[dict]:
    async with httpx.AsyncClient() as client:
        # Fetch main details + watch providers in one call using append_to_response
        r = await client.get(
            f"{TMDB_BASE_URL}/tv/{tmdb_id}",
            params=_auth_params({"language": "en-US", "append_to_response": "watch/providers"}),
            headers=HEADERS,
        )
        if r.status_code != 200:
            return None
        data = r.json()

        # Parse streaming services (US flatrate/subscription providers)
        streaming = []
        providers = data.get("watch/providers", {}).get("results", {}).get("US", {})
        for p in providers.get("flatrate", []):
            name = STREAMING_PROVIDER_IDS.get(p["provider_id"], p["provider_name"])
            if name not in streaming:
                streaming.append(name)

        # Air schedule
        air_day = None
        air_time = None
        air_timezone = None
        networks = [n["name"] for n in data.get("networks", [])]
        if data.get("last_episode_to_air"):
            air_day = _day_from_date(data["last_episode_to_air"].get("air_date"))

        # Determine release time from network / streaming service name
        all_names = [n.lower() for n in networks] + [s.lower() for s in streaming]
        for name in all_names:
            for key, (t, tz) in NETWORK_RELEASE_TIMES.items():
                if key in name:
                    air_time = t
                    air_timezone = tz
                    break
            if air_time:
                break

        # Next episode
        next_ep = data.get("next_episode_to_air")
        next_ep_date = None
        next_ep_name = None
        next_ep_number = None
        if next_ep:
            from datetime import datetime
            try:
                next_ep_date = datetime.strptime(next_ep["air_date"], "%Y-%m-%d")
            except Exception:
                next_ep_date = None
            next_ep_name = next_ep.get("name")
            s = next_ep.get("season_number", 0)
            e = next_ep.get("episode_number", 0)
            next_ep_number = f"S{s:02d}E{e:02d}"
            air_day = _day_from_date(next_ep.get("air_date"))

        import json
        genre_names = [g["name"] for g in data.get("genres", [])]
        season_episode_counts = {
            str(s["season_number"]): s["episode_count"]
            for s in data.get("seasons", [])
            if s["season_number"] > 0 and s.get("episode_count", 0) > 0
        }
        return {
            "tmdb_id": tmdb_id,
            "name": data["name"],
            "poster_path": f"{TMDB_IMAGE_BASE}{data['poster_path']}" if data.get("poster_path") else None,
            "overview": data.get("overview"),
            "status": data.get("status"),
            "network": ", ".join(networks) if networks else None,
            "streaming_services": json.dumps(streaming) if streaming else None,
            "genres": json.dumps(genre_names) if genre_names else None,
            "season_episode_counts": json.dumps(season_episode_counts) if season_episode_counts else None,
            "air_day": air_day,
            "air_time": air_time,
            "air_timezone": air_timezone,
            "next_episode_date": next_ep_date,
            "next_episode_name": next_ep_name,
            "next_episode_number": next_ep_number,
            "vote_average": round(data["vote_average"], 1) if data.get("vote_average") else None,
        }


def _format_discover_results(results: list, media_type: str = "tv") -> list[dict]:
    out = []
    for s in results:
        # TV uses "name", movies use "title"
        name = s.get("name") if media_type == "tv" else s.get("title")
        date = s.get("first_air_date") if media_type == "tv" else s.get("release_date")
        if not name:
            continue
        out.append({
            "tmdb_id": s["id"],
            "name": name,
            "media_type": media_type,
            "poster_path": f"{TMDB_IMAGE_BASE}{s['poster_path']}" if s.get("poster_path") else None,
            "first_air_date": date or "",
            "vote_average": round(s.get("vote_average", 0), 1) if s.get("vote_average") else None,
            "overview": s.get("overview", ""),
            "genre_ids": s.get("genre_ids", []),
        })
    return out


async def get_trending() -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/trending/tv/week",
            params=_auth_params({"language": "en-US"}),
            headers=HEADERS,
        )
        r.raise_for_status()
        return _format_discover_results(r.json().get("results", []))


async def get_recommendations(tmdb_id: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/tv/{tmdb_id}/recommendations",
            params=_auth_params({"language": "en-US", "page": 1}),
            headers=HEADERS,
        )
        if r.status_code != 200:
            return []
        return _format_discover_results(r.json().get("results", []))


async def get_top_by_year(year: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/discover/tv",
            params=_auth_params({
                "language": "en-US",
                "sort_by": "popularity.desc",
                "first_air_date_year": year,
                "vote_count.gte": 50,
                "page": 1,
            }),
            headers=HEADERS,
        )
        r.raise_for_status()
        return _format_discover_results(r.json().get("results", []))


async def search_movies(query: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/search/movie",
            params=_auth_params({"query": query, "language": "en-US", "page": 1}),
            headers=HEADERS,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [
            {
                "tmdb_id": s["id"],
                "title": s["title"],
                "overview": s.get("overview", ""),
                "poster_path": f"{TMDB_IMAGE_BASE}{s['poster_path']}" if s.get("poster_path") else None,
                "release_date": s.get("release_date", ""),
            }
            for s in results[:6]
            if s.get("title")
        ]


async def get_movie_details(tmdb_id: int) -> Optional[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/movie/{tmdb_id}",
            params=_auth_params({"language": "en-US", "append_to_response": "watch/providers"}),
            headers=HEADERS,
        )
        if r.status_code != 200:
            return None
        data = r.json()

    streaming = []
    providers = data.get("watch/providers", {}).get("results", {}).get("US", {})
    for p in providers.get("flatrate", []):
        name = STREAMING_PROVIDER_IDS.get(p["provider_id"], p["provider_name"])
        if name not in streaming:
            streaming.append(name)

    release_date = None
    if data.get("release_date"):
        from datetime import datetime
        try:
            release_date = datetime.strptime(data["release_date"], "%Y-%m-%d")
        except Exception:
            pass

    genre_names = [g["name"] for g in data.get("genres", [])]
    return {
        "tmdb_id": tmdb_id,
        "title": data["title"],
        "poster_path": f"{TMDB_IMAGE_BASE}{data['poster_path']}" if data.get("poster_path") else None,
        "overview": data.get("overview"),
        "status": data.get("status"),
        "streaming_services": json.dumps(streaming) if streaming else None,
        "runtime": data.get("runtime") or None,
        "release_date": release_date,
        "vote_average": round(data["vote_average"], 1) if data.get("vote_average") else None,
        "genres": json.dumps(genre_names) if genre_names else None,
    }


async def get_trending_movies() -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/trending/movie/week",
            params=_auth_params({"language": "en-US"}),
            headers=HEADERS,
        )
        r.raise_for_status()
        return _format_discover_results(r.json().get("results", []), media_type="movie")


async def get_movie_recommendations(tmdb_id: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/movie/{tmdb_id}/recommendations",
            params=_auth_params({"language": "en-US", "page": 1}),
            headers=HEADERS,
        )
        if r.status_code != 200:
            return []
        return _format_discover_results(r.json().get("results", []), media_type="movie")


async def get_top_movies_by_year(year: int) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/discover/movie",
            params=_auth_params({
                "language": "en-US",
                "sort_by": "popularity.desc",
                "primary_release_year": year,
                "vote_count.gte": 50,
                "page": 1,
            }),
            headers=HEADERS,
        )
        r.raise_for_status()
        return _format_discover_results(r.json().get("results", []), media_type="movie")


async def get_trailer_key(tmdb_id: int, media_type: str) -> Optional[str]:
    """Return a YouTube video key for the best available trailer."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}/videos",
            params=_auth_params({"language": "en-US"}),
            headers=HEADERS,
        )
        if r.status_code != 200:
            return None
    videos = r.json().get("results", [])
    yt = [v for v in videos if v.get("site") == "YouTube"]
    # Prefer official trailers, then any trailer, then any video
    for vtype in ("Trailer", "Teaser", None):
        for official in (True, False):
            for v in yt:
                if vtype and v.get("type") != vtype:
                    continue
                if official and not v.get("official"):
                    continue
                return v["key"]
    return None


def _day_from_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%A")
    except Exception:
        return None
