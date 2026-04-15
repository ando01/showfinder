import httpx
from app.config import NTFY_URL, NTFY_TOPIC


async def send_reminder(show_name: str, episode_number: str, episode_name: str, air_time_str: str):
    title = f"New episode: {show_name}"
    body = f"{episode_number}"
    if episode_name:
        body += f' — "{episode_name}"'
    if air_time_str:
        body += f"\nAirs at {air_time_str}"

    url = f"{NTFY_URL.rstrip('/')}/{NTFY_TOPIC}"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                url,
                content=body,
                headers={
                    "Title": title,
                    "Priority": "default",
                    "Tags": "tv,clapper",
                },
            )
        except Exception as e:
            print(f"[ntfy] Failed to send notification: {e}")


async def send_test_notification():
    url = f"{NTFY_URL.rstrip('/')}/{NTFY_TOPIC}"
    async with httpx.AsyncClient() as client:
        await client.post(
            url,
            content="ShowFinder is connected and working!",
            headers={
                "Title": "ShowFinder Test",
                "Tags": "white_check_mark",
            },
        )
