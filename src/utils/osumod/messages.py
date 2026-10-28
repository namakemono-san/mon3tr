"""Discord message rendering for osumod notifications."""

from datetime import UTC, datetime
from urllib.parse import quote

import discord

STATUS_LABELS = {
    "pending": "Pending",
    "inConsideration": "In Consideration",
    "waitingOnOtherBn": "Waiting on Other BN",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "modded": "Modded",
    "nominated": "Nominated",
    "ranked": "Ranked",
    "needsRecheck": "Needs Recheck",
    "rechecked": "Rechecked",
    "dropped": "Dropped",
    "canceled": "Canceled",
}
STATUS_EMOJI = {
    "accepted": "🟢",
    "rejected": "🔴",
    "nominated": "🔵",
    "modded": "🟣",
    "pending": "🟡",
}
DEFAULT_STATUS_EMOJI = "⚪"
STATUS_COLORS = {
    "accepted": 0x2ECC71,
    "rejected": 0xE74C3C,
    "nominated": 0x3498DB,
    "modded": 0x9B59B6,
}
DEFAULT_STATUS_COLOR = 0x95A5A6
OPEN_COLOR = 0x2ECC71
CLOSE_COLOR = 0xE74C3C

QUEUE_EVENT_TITLES = {
    "bn_open": ("🔓 {username}'s Queue Opened", OPEN_COLOR),
    "bn_close": ("🔒 {username}'s Queue Closed", CLOSE_COLOR),
    "modder_open": ("🔓 {username}'s Modding Queue Opened", OPEN_COLOR),
    "modder_close": ("🔒 {username}'s Modding Queue Closed", CLOSE_COLOR),
}


def profile_url(username: str) -> str:
    return f"https://osumod.com/{quote(username, safe='')}"


def _emoji(status: str) -> str:
    return STATUS_EMOJI.get(status, DEFAULT_STATUS_EMOJI)


def _status_text(status: str) -> str:
    return f"{_emoji(status)} {STATUS_LABELS.get(status, status)}"


def queue_event_embed(event: dict) -> discord.Embed:
    title, color = QUEUE_EVENT_TITLES[event["kind"]]
    return discord.Embed(
        title=title.format(username=event["username"]),
        url=profile_url(event["username"]),
        color=color,
        timestamp=datetime.now(UTC),
    )


def request_change_embed(change: dict, bn_username: str) -> discord.Embed:
    req = change["req"]
    old_status, new_status = change["old_status"], change["new_status"]

    if change["kind"] == "feedback":
        title = "💬 Feedback Updated" if change["old_feedback"] else "💬 Feedback Added"
        status_value = _status_text(new_status)
    else:
        title = f"{_emoji(new_status)} Status Changed"
        status_value = f"{_status_text(old_status)} → {_status_text(new_status)}"

    embed = discord.Embed(
        title=title,
        url=profile_url(bn_username),
        color=STATUS_COLORS.get(new_status, DEFAULT_STATUS_COLOR),
        timestamp=datetime.now(UTC),
    )
    embed.add_field(
        name=f"Beatmapset by {req.get('creator') or 'Unknown'}"[:256],
        value=f"{req.get('artist') or 'Unknown'} - {req.get('title') or 'Unknown'}"[:1024],
        inline=False,
    )
    embed.add_field(name="Status", value=status_value, inline=True)
    if comment := (req.get("comment") or "").strip():
        embed.add_field(name="Mapper's Comment", value=comment[:1024], inline=False)
    if feedback := (req.get("feedback") or "").strip():
        embed.add_field(name="Feedback", value=feedback[:1024], inline=False)
    if cover := req.get("coverUrl"):
        embed.set_image(url=cover)
    embed.set_footer(text=f"BN: {bn_username}")
    return embed


def link_view(bn_username: str, beatmapset_id: int | None) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="osumod", url=profile_url(bn_username)))
    if beatmapset_id:
        view.add_item(discord.ui.Button(
            label="Beatmap", url=f"https://osu.ppy.sh/beatmapsets/{beatmapset_id}"
        ))
    return view
