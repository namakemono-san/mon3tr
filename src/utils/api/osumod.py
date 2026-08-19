import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import aiohttp

logger = logging.getLogger("Mon3tr").getChild("utils.api.osumod")

API_URL = "https://osumod.com/api/queues"
REQUEST_API = "https://osumod.com/api/requests?archived={archived}&target={username}"

UNSEEN_FEEDBACK_MAX_AGE = timedelta(days=3)


def _request_is_recent(request_date: str, max_age: timedelta) -> bool:
    if not request_date:
        return False
    try:
        dt = datetime.fromisoformat(request_date.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(UTC) - dt <= max_age

STATUS_COLORS = {
    "Accepted": 0x2ECC71,
    "Rejected": 0xE74C3C,
    "Nominated": 0x3498DB,
    "Modded": 0x9B59B6,
}
DEFAULT_STATUS_COLOR = 0x95A5A6
CLOSE_COLOR = 0xE74C3C
OPEN_COLOR = 0x2ECC71

MAX_CONCURRENT = 5

MODES = {
    "osu": "Standard",
    "taiko": "Taiko",
    "catch": "Catch the Beat",
    "mania": "Mania",
}
MODE_ALIASES = {
    "osu": "osu", "std": "osu", "standard": "osu",
    "taiko": "taiko",
    "catch": "catch", "ctb": "catch", "fruits": "catch", "catch the beat": "catch",
    "mania": "mania",
}
MODE_DISPLAY = {
    "Standard": "osu!",
    "Taiko": "osu!taiko",
    "Catch the Beat": "osu!catch",
    "Mania": "osu!mania",
}


def normalize_mode(value: str) -> Optional[str]:
    key = MODE_ALIASES.get(value.strip().lower())
    return MODES.get(key) if key else None


def is_bn(queue: dict) -> bool:
    return queue.get("modderType") in ["full", "probation"]


def filter_bns(queues: List[dict], mode: str) -> List[dict]:
    return [q for q in queues if mode in q.get("modes", []) and is_bn(q)]


def filter_modders(queues: List[dict], mode: str) -> List[dict]:
    return [
        q
        for q in queues
        if mode in q.get("modes", []) and q.get("modderType") == "modder"
    ]


def profile_url(username: str) -> str:
    return f"https://osumod.com/{quote(username, safe='')}"


def get_status_emoji(status: str) -> str:
    return {
        "Accepted": "🟢",
        "Rejected": "🔴",
        "Nominated": "🔵",
        "Modded": "🟣",
        "Pending": "🟡",
    }.get(status, "⚪")


async def fetch_queues(session: aiohttp.ClientSession) -> List[dict]:
    async with session.get(API_URL) as res:
        res.raise_for_status()
        return await res.json()


async def fetch_requests(
    session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, username: str
) -> Tuple[str, Optional[list]]:
    async with semaphore:
        try:
            results = []
            for archived in ("false", "true"):
                url = REQUEST_API.format(archived=archived, username=quote(username, safe=""))
                async with session.get(url) as res:
                    res.raise_for_status()
                    data = await res.json()
                    results.extend(data)
            return username, results
        except Exception as e:
            logger.warning("fetch_requests failed (%s): %s", username, e)
            return username, None


async def fetch_all_requests(
    session: aiohttp.ClientSession, usernames: List[str]
) -> Dict[str, list]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [fetch_requests(session, semaphore, u) for u in usernames]
    results = await asyncio.gather(*tasks)
    return {u: r for u, r in results if r is not None}


def diff_queue_events(old_state: Dict[str, dict], new_queues: List[dict]) -> List[dict]:
    events = []
    for queue in new_queues:
        owner_id = queue["owner"]["_id"]
        old = old_state.get(owner_id)
        if old is None:
            continue

        old_open = old["open"]
        new_open = bool(queue.get("open"))
        if old_open == new_open:
            continue

        username = queue["owner"]["username"]
        modes = queue.get("modes", [])

        if is_bn(queue):
            kind = "bn_open" if new_open else "bn_close"
            events.append({"kind": kind, "username": username, "modes": modes})
        elif queue.get("modderType") == "modder":
            kind = "modder_open" if new_open else "modder_close"
            events.append({"kind": kind, "username": username, "modes": modes})

    return events


NOTIFIED_STATUSES = {"Pending", "Accepted", "Rejected"}
EXTRA_NOTIFIED_TRANSITIONS = {
    ("Pending", "Modded"),
    ("Rejected", "Modded"),
}


def diff_status_changes(
    old_state: Dict[str, dict], new_requests_map: Dict[str, list]
) -> List[dict]:
    seeded_usernames = {row["target_username"] for row in old_state.values()}

    changes = []
    for username, requests in new_requests_map.items():
        for req in requests:
            old = old_state.get(req["_id"])
            if old is None:
                new_status = req.get("status", "")
                new_feedback = (req.get("feedback") or "").strip()
                if (
                    username in seeded_usernames
                    and new_feedback
                    and new_status in NOTIFIED_STATUSES
                ):
                    request_date = req.get("requestDate", "")
                    if not _request_is_recent(request_date, UNSEEN_FEEDBACK_MAX_AGE):
                        logger.info(
                            "feedback[unseen] skipped (stale): id=%s user=%s requestDate=%s",
                            req.get("_id"), username, request_date,
                        )
                        continue
                    logger.info(
                        "feedback[unseen]: id=%s user=%s status=%s requestDate=%s feedback=%r",
                        req.get("_id"), username, new_status,
                        request_date, new_feedback[:80],
                    )
                    changes.append({
                        "kind": "feedback",
                        "username": username,
                        "req": req,
                        "old_status": new_status,
                        "new_status": new_status,
                        "old_feedback": "",
                    })
                continue

            old_status = old["status"]
            new_status = req.get("status", "")
            status_changed = old_status != new_status and (
                new_status in NOTIFIED_STATUSES
                or (old_status, new_status) in EXTRA_NOTIFIED_TRANSITIONS
            )

            if status_changed:
                changes.append({
                    "kind": "status",
                    "username": username,
                    "req": req,
                    "old_status": old_status,
                    "new_status": new_status,
                    "old_feedback": (old.get("feedback") or "").strip(),
                })
                continue

            if old_status == new_status and new_status in NOTIFIED_STATUSES:
                old_feedback = (old.get("feedback") or "").strip()
                new_feedback = (req.get("feedback") or "").strip()
                if new_feedback and new_feedback != old_feedback:
                    logger.info(
                        "feedback[changed]: id=%s user=%s status=%s requestDate=%s old=%r new=%r",
                        req.get("_id"), username, new_status,
                        req.get("requestDate"), old_feedback[:80], new_feedback[:80],
                    )
                    changes.append({
                        "kind": "feedback",
                        "username": username,
                        "req": req,
                        "old_status": old_status,
                        "new_status": new_status,
                        "old_feedback": old_feedback,
                    })

    return changes
