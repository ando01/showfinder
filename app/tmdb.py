import httpx
from typing import Optional
from app.config import TMDB_API_KEY, TMDB_BASE_URL, TMDB_IMAGE_BASE

HEADERS = {"accept": "application/json"}
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
        if data.get("next_episode_to_air"):
            pass  # air day comes from networks/last_episode pattern below
        networks = [n["name"] for n in data.get("networks", [])]
        if data.get("last_episode_to_air"):
            air_day = _day_from_date(data["last_episode_to_air"].get("air_date"))

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
        return {
            "tmdb_id": tmdb_id,
            "name": data["name"],
            "poster_path": f"{TMDB_IMAGE_BASE}{data['poster_path']}" if data.get("poster_path") else None,
            "overview": data.get("overview"),
            "status": data.get("status"),
            "network": ", ".join(networks) if networks else None,
            "streaming_services": json.dumps(streaming) if streaming else None,
            "air_day": air_day,
            "air_time": air_time,
            "next_episode_date": next_ep_date,
            "next_episode_name": next_ep_name,
            "next_episode_number": next_ep_number,
        }


def _day_from_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%A")
    except Exception:
        return None
