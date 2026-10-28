from urllib.parse import quote

import aiohttp

from ..config import get_settings

API_BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
TIMEOUT = aiohttp.ClientTimeout(total=15)


async def _get_token(session: aiohttp.ClientSession) -> str:
    settings = get_settings()
    body = {
        "client_id": settings.osu_client_id,
        "client_secret": settings.osu_client_secret,
        "grant_type": "client_credentials",
        "scope": "public",
    }
    async with session.post(TOKEN_URL, json=body) as res:
        res.raise_for_status()
        return (await res.json())["access_token"]


async def get_user(username: str) -> dict | None:
    """Look up an osu! user by username. Returns None when the user does not exist."""
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        headers = {
            "Authorization": f"Bearer {await _get_token(session)}",
            "Accept": "application/json",
        }
        url = f"{API_BASE}/users/{quote(username, safe='')}"
        async with session.get(url, headers=headers, params={"key": "username"}) as res:
            if res.status == 404:
                return None
            res.raise_for_status()
            return await res.json()


def avatar_url(osu_id: int) -> str:
    return f"https://a.ppy.sh/{osu_id}"
