import httpx
from app.config import NTFY_URL as ENV_NTFY_URL, NTFY_TOPIC as ENV_NTFY_TOPIC


def get_ntfy_config(session=None) -> tuple[str, str]:
    """Return (url, topic), preferring DB settings over .env values."""
    if session:
        from app.database import get_setting
        url = get_setting(session, "ntfy_url") or ENV_NTFY_URL
        topic = get_setting(session, "ntfy_topic") or ENV_NTFY_TOPIC
    else:
        url = ENV_NTFY_URL
        topic = ENV_NTFY_TOPIC
    return url.rstrip("/"), topic


async def send_reminder(show_name: str, episode_number: str, episode_name: str, air_time_str: str, session=None):
    url, topic = get_ntfy_config(session)
    title = f"New episode: {show_name}"
    body = f"{episode_number}"
    if episode_name:
        body += f' — "{episode_name}"'
    if air_time_str:
        body += f"\nAirs at {air_time_str}"

    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{url}/{topic}",
                content=body,
                headers={"Title": title, "Priority": "default", "Tags": "tv,clapper"},
            )
        except Exception as e:
            print(f"[ntfy] Failed to send notification: {e}")


async def send_test_notification(session=None) -> tuple[bool, str]:
    """Send a test notification. Returns (success, message)."""
    url, topic = get_ntfy_config(session)
    if not url or url == "https://ntfy.yourdomain.com":
        return False, "ntfy URL is not configured."
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{url}/{topic}",
                content="ShowFinder is connected and working!",
                headers={"Title": "ShowFinder Test", "Tags": "white_check_mark"},
            )
            if r.status_code in (200, 201, 202):
                return True, f"Notification sent to {url}/{topic}"
            else:
                return False, f"ntfy returned HTTP {r.status_code}"
    except httpx.ConnectError:
        return False, f"Could not connect to {url} — is it reachable from the container?"
    except httpx.TimeoutException:
        return False, f"Connection to {url} timed out."
    except Exception as e:
        return False, str(e)
