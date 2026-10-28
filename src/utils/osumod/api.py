"""HTTP client for the public osumod API (https://osumod.com/api/docs)."""

import asyncio
import logging

import aiohttp

logger = logging.getLogger("Mon3tr").getChild("utils.osumod.api")

API_BASE = "https://osumod.com/api"
TIMEOUT = aiohttp.ClientTimeout(total=30)

MAX_CONCURRENT = 3
MAX_RETRIES = 3
MAX_RETRY_DELAY = 60
# Pause every request once this few remain in the rate limit window, so concurrent
# fetches don't overshoot into 429s.
RATE_LIMIT_BUFFER = 10
MAX_PAGES = 50
# Archived lists can span thousands of requests; the first page holds the recently archived ones.
ARCHIVED_PAGES = 1

# Subscription mode keys as stored in the database (the API's own mode names).
MODE_DISPLAY = {
    "osu": "osu!",
    "taiko": "osu!taiko",
    "fruits": "osu!catch",
    "mania": "osu!mania",
}
MODE_ALIASES = {
    "osu": "osu", "std": "osu", "standard": "osu",
    "taiko": "taiko",
    "catch": "fruits", "ctb": "fruits", "fruits": "fruits", "catch the beat": "fruits",
    "mania": "mania",
}


def normalize_mode(value: str) -> str | None:
    return MODE_ALIASES.get(value.strip().lower())


def is_bn(queue: dict) -> bool:
    return queue.get("kind") == "bn"


class Client:
    """Shares one session and one rate-limit pause across all requests."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._paused_until = 0.0

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    @staticmethod
    def _parse_delay(value: str | None, default: float = 5.0) -> float:
        try:
            return min(max(float(value), 1.0), MAX_RETRY_DELAY)
        except (TypeError, ValueError):
            return default

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        loop = asyncio.get_running_loop()
        for attempt in range(MAX_RETRIES + 1):
            # Re-check after sleeping: a concurrent request may have extended the pause.
            while (wait := self._paused_until - loop.time()) > 0:
                await asyncio.sleep(wait)

            async with self.session.get(f"{API_BASE}{path}", params=params) as res:
                reset = self._parse_delay(
                    res.headers.get("Retry-After") or res.headers.get("X-RateLimit-Reset")
                )
                if res.status == 429 and attempt < MAX_RETRIES:
                    self._paused_until = max(self._paused_until, loop.time() + reset)
                    logger.warning("rate limited on %s, retrying in %.0fs", path, reset)
                    continue

                res.raise_for_status()
                remaining = res.headers.get("X-RateLimit-Remaining", "")
                if remaining.isdigit() and int(remaining) <= RATE_LIMIT_BUFFER:
                    self._paused_until = max(self._paused_until, loop.time() + reset)
                    logger.info(
                        "rate limit nearly exhausted (%s left), pausing %.0fs", remaining, reset
                    )
                return await res.json()
        raise RuntimeError("unreachable")

    async def _get_paginated(
        self, path: str, params: dict | None = None, max_pages: int | None = None
    ) -> list[dict]:
        """Follow `nextCursor`. Raises instead of returning a silently truncated list,
        unless `max_pages` deliberately limits the range."""
        items: list[dict] = []
        cursor = None
        seen_cursors = set()
        for _ in range(min(max_pages or MAX_PAGES, MAX_PAGES)):
            page_params = dict(params or {})
            if cursor:
                page_params["cursor"] = cursor
            data = await self._get_json(path, page_params)
            items.extend(data.get("items", []))
            cursor = data.get("nextCursor")
            if not cursor:
                return items
            if cursor in seen_cursors:
                raise RuntimeError(f"pagination on {path} repeated a cursor")
            seen_cursors.add(cursor)
        if max_pages is None:
            raise RuntimeError(f"pagination on {path} exceeded {MAX_PAGES} pages")
        return items

    async def fetch_queues(self, modes: list[str]) -> list[dict]:
        # Fetch each mode separately: the API's multi-mode matching is undocumented.
        # A queue supporting several subscribed modes must only be processed once.
        queues = {}
        for mode in sorted(set(modes)):
            for queue in await self._get_paginated("/queues", {"mode": mode}):
                queues[queue["id"]] = queue
        return list(queues.values())

    async def fetch_requests(self, osu_id: int) -> list[dict]:
        path = f"/queues/{osu_id}/requests"
        active = await self._get_paginated(path, {"archived": "false"})
        archived = await self._get_paginated(path, {"archived": "true"}, max_pages=ARCHIVED_PAGES)
        return active + archived

    async def fetch_many_requests(self, osu_ids: list[int]) -> dict[int, list[dict]]:
        """Fetch request lists for several queue owners. Failed owners are omitted
        so the caller retries them on the next cycle."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def fetch(osu_id: int) -> tuple[int, list[dict] | None]:
            async with semaphore:
                try:
                    return osu_id, await self.fetch_requests(osu_id)
                except Exception as e:
                    logger.warning("fetch_requests failed (osu_id=%s): %s", osu_id, e)
                    return osu_id, None

        results = await asyncio.gather(*(fetch(osu_id) for osu_id in osu_ids))
        return {osu_id: reqs for osu_id, reqs in results if reqs is not None}
