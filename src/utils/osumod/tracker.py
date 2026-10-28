"""Change detection between stored osumod state and freshly fetched data."""

import logging
import math
from datetime import UTC, datetime, timedelta

from .api import is_bn

logger = logging.getLogger("Mon3tr").getChild("utils.osumod.tracker")

NOTIFIED_STATUSES = {"pending", "accepted", "rejected"}
EXTRA_NOTIFIED_TRANSITIONS = {("pending", "modded"), ("rejected", "modded")}
# Feedback on a request first seen with feedback already attached is only reported
# if the request is this recent, so stale history is not announced as news.
UNSEEN_FEEDBACK_MAX_AGE = timedelta(days=3)


def activity(queue: dict) -> str:
    return queue.get("lastActivityAt") or ""


def _is_recent(timestamp: str, now: datetime) -> bool:
    try:
        created = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return now - created <= UNSEEN_FEEDBACK_MAX_AGE


def diff_queue_events(old_state: dict[str, dict], queues: list[dict]) -> list[dict]:
    """Queue open/close transitions. Queues seen for the first time produce no event."""
    events = []
    for queue in queues:
        old = old_state.get(queue["owner"]["id"])
        is_open = bool(queue.get("isOpen"))
        if old is None or old["open"] == is_open:
            continue
        if is_bn(queue):
            kind = "bn_open" if is_open else "bn_close"
        elif queue.get("kind") == "modder":
            kind = "modder_open" if is_open else "modder_close"
        else:
            continue
        events.append({
            "kind": kind,
            "username": queue["owner"]["username"],
            "modes": queue.get("modes", []),
        })
    return events


def select_bns_to_fetch(
    bns: dict[int, dict],
    sync: dict[int, dict],
    now: float,
    poll_interval: float,
    reconcile_interval: float,
) -> list[int]:
    """BNs whose queue activity changed (or were never synced), plus a few of the
    least recently checked ones, so every BN is re-checked within `reconcile_interval`
    without fetching them all in one burst."""
    changed = [
        osu_id for osu_id, queue in bns.items()
        if osu_id not in sync or sync[osu_id]["last_activity"] != activity(queue)
    ]
    changed_set = set(changed)
    stale = sorted(
        (sync[osu_id]["checked_at"], osu_id) for osu_id in bns
        if osu_id not in changed_set and now - sync[osu_id]["checked_at"] >= reconcile_interval
    )
    per_cycle = max(1, math.ceil(len(bns) * poll_interval / reconcile_interval))
    return changed + [osu_id for _, osu_id in stale[:per_cycle]]


def _feedback(data: dict) -> str:
    return (data.get("feedback") or "").strip()


def diff_request_changes(
    old_state: dict[str, dict],
    fetched: dict[int, list[dict]],
    seeded: set[int],
    now: datetime | None = None,
) -> list[dict]:
    """Status changes and new/edited feedback. BNs not in `seeded` are being synced for
    the first time, so their current requests are recorded without notifying."""
    now = now or datetime.now(UTC)
    changes = []
    for bn_osu_id, requests in fetched.items():
        if bn_osu_id not in seeded:
            continue
        for req in requests:
            new_status = req.get("status", "")
            new_feedback = _feedback(req)
            old = old_state.get(req["id"])
            change = {"bn_osu_id": bn_osu_id, "req": req, "new_status": new_status}

            if old is None:
                if (
                    new_feedback
                    and new_status in NOTIFIED_STATUSES
                    and _is_recent(req.get("createdAt", ""), now)
                ):
                    changes.append({
                        **change, "kind": "feedback", "old_status": new_status, "old_feedback": ""
                    })
                continue

            old_status, old_feedback = old["status"], _feedback(old)
            if old_status != new_status and (
                new_status in NOTIFIED_STATUSES
                or (old_status, new_status) in EXTRA_NOTIFIED_TRANSITIONS
            ):
                changes.append({
                    **change, "kind": "status",
                    "old_status": old_status, "old_feedback": old_feedback,
                })
            elif (
                old_status == new_status
                and new_status in NOTIFIED_STATUSES
                and new_feedback
                and new_feedback != old_feedback
            ):
                changes.append({
                    **change, "kind": "feedback",
                    "old_status": old_status, "old_feedback": old_feedback,
                })

    for change in changes:
        logger.info(
            "detected %s: request=%s bn=%s %s -> %s",
            change["kind"], change["req"]["id"], change["bn_osu_id"],
            change["old_status"], change["new_status"],
        )
    return changes
