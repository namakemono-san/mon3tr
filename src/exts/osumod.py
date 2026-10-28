import asyncio
import time
from collections import defaultdict
from enum import Enum
from typing import TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..utils import osu
from ..utils.osumod import api as osumod_api
from ..utils.osumod import messages, tracker
from ..utils.views import DetailNoticeView, NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

POLL_INTERVAL = 30
# Every monitored BN is re-fetched at least this often, even without queue activity.
RECONCILE_INTERVAL = 60 * 60
SEND_CONCURRENCY = 8
# Undeliverable notifications are retried each cycle until either limit is hit.
OUTBOX_MAX_ATTEMPTS = 20
OUTBOX_MAX_AGE = 24 * 60 * 60

MODE_CHOICES = [
    app_commands.Choice(name="osu!", value="osu"),
    app_commands.Choice(name="osu!taiko", value="taiko"),
    app_commands.Choice(name="osu!catch", value="catch"),
    app_commands.Choice(name="osu!mania", value="mania"),
]

NOTIFY_CHOICES = [
    app_commands.Choice(name="DM + Mention", value="both"),
    app_commands.Choice(name="DM only", value="dm"),
    app_commands.Choice(name="Mention only", value="mention"),
]
NOTIFY_DISPLAY = {choice.value: choice.name for choice in NOTIFY_CHOICES}

NOTIFY_TYPES = [
    ("notify_bn_queue", "BN queue"),
    ("notify_modder_queue", "Modder queue"),
    ("notify_status", "Request status"),
]


class Delivery(Enum):
    SENT = "sent"
    DROPPED = "dropped"
    RETRY = "retry"


def requester_osu_id(req: dict) -> int | None:
    return (req.get("requester") or {}).get("osuId")


def enabled_types(sub: dict) -> str:
    return ", ".join(name for key, name in NOTIFY_TYPES if sub[key])


class Osumod(Cog):
    def __init__(self, bot: "Mon3trBot"):
        super().__init__(bot)
        self.api = osumod_api.Client()

    async def cog_load(self) -> None:
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        await self.api.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        removed = await self.bot.db.remove_guild_subscriptions(guild.id)
        if removed:
            self.logger.info("left guild %s, removed %d subscription(s)", guild.id, removed)

    # --- commands ---------------------------------------------------------------

    @commands.hybrid_group(name="osumod")
    @commands.guild_only()
    async def osumod(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(view=NoticeView(
                "info", "Usage: osumod subscribe <mode> / unsubscribe [mode] / list"
            ))

    @osumod.command(name="list", description="Lists osumod subscriptions in this server.")
    async def osumod_list(self, ctx: commands.Context) -> None:
        subs = await self.bot.db.get_subscriptions(ctx.guild.id)
        if not subs:
            await ctx.reply(view=NoticeView(
                "info", "No subscriptions in this server. Use `osumod subscribe <mode>`."
            ))
            return

        lines = ["### osumod subscriptions"]
        for sub in subs:
            mode = osumod_api.MODE_DISPLAY.get(sub["mode"], sub["mode"])
            lines.append(f"<#{sub['channel_id']}> — **{mode}** ({enabled_types(sub)})")

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay("\n".join(lines))))
        await ctx.reply(view=view)

    @osumod.command(
        name="subscribe",
        description="Subscribes this channel to osumod notifications for a game mode.",
    )
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(mode=MODE_CHOICES)
    @app_commands.describe(
        mode="The game mode to subscribe to.",
        bn_queue="Notify when a BN's queue opens or closes.",
        modder_queue="Notify when a modder's queue opens or closes.",
        status="Notify when a request's status or feedback changes.",
    )
    async def osumod_subscribe(
        self,
        ctx: commands.Context,
        mode: str,
        bn_queue: bool = True,
        modder_queue: bool = True,
        status: bool = True,
    ) -> None:
        api_mode = osumod_api.normalize_mode(mode)
        if api_mode is None:
            await ctx.reply(view=NoticeView(
                "error", "Invalid mode. Use one of: osu, taiko, catch, mania."
            ), ephemeral=True)
            return
        if not (bn_queue or modder_queue or status):
            await ctx.reply(view=NoticeView(
                "error", "Enable at least one of bn_queue, modder_queue or status."
            ), ephemeral=True)
            return

        await self.bot.db.add_subscription(
            ctx.guild.id, ctx.channel.id, api_mode, bn_queue, modder_queue, status
        )
        sub = {"notify_bn_queue": bn_queue, "notify_modder_queue": modder_queue,
               "notify_status": status}
        await ctx.reply(view=NoticeView(
            "success",
            f"Subscribed this channel to {osumod_api.MODE_DISPLAY[api_mode]} "
            f"notifications ({enabled_types(sub)}).",
        ), ephemeral=True)

    @osumod.command(
        name="unsubscribe",
        description="Unsubscribes this channel from osumod notifications.",
    )
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(mode=MODE_CHOICES)
    @app_commands.describe(mode="The game mode to unsubscribe from. Omit to remove all.")
    async def osumod_unsubscribe(self, ctx: commands.Context, mode: str | None = None) -> None:
        api_mode = None
        if mode is not None:
            api_mode = osumod_api.normalize_mode(mode)
            if api_mode is None:
                await ctx.reply(view=NoticeView(
                    "error", "Invalid mode. Use one of: osu, taiko, catch, mania."
                ), ephemeral=True)
                return

        removed = await self.bot.db.remove_subscriptions(ctx.channel.id, api_mode)
        if removed:
            await ctx.reply(view=NoticeView(
                "success", f"Removed {removed} subscription(s)."
            ), ephemeral=True)
        else:
            await ctx.reply(view=NoticeView(
                "warning", "This channel has no matching subscriptions."
            ), ephemeral=True)

    @commands.hybrid_command(
        name="link",
        description="Links your osu! account to receive osumod request notifications.",
    )
    @app_commands.choices(notify=NOTIFY_CHOICES)
    @app_commands.describe(
        osu_username="Your osu! username. Omit to show or update the current link.",
        notify="How to notify you when your request status changes.",
    )
    async def link(
        self,
        ctx: commands.Context,
        osu_username: str | None = None,
        notify: str | None = None,
    ) -> None:
        if notify is not None and notify not in NOTIFY_DISPLAY:
            await ctx.reply(view=NoticeView(
                "error", "Invalid notify option. Use one of: both, dm, mention."
            ), ephemeral=True)
            return

        current = await self.bot.db.get_osu_link(ctx.author.id)

        if osu_username is None:
            if current is None:
                await ctx.reply(view=NoticeView(
                    "info", "Not linked. Usage: link <osu_username> [notify]"
                ), ephemeral=True)
                return
            if notify is not None:
                await self.bot.db.set_osu_link(
                    ctx.author.id, current["osu_id"], current["osu_username"], notify
                )
            await ctx.reply(view=DetailNoticeView(
                "success" if notify else "info",
                [
                    f"Linked to `{current['osu_username']}`",
                    f"-# osu! ID: {current['osu_id']} • "
                    f"Notify: {NOTIFY_DISPLAY[notify or current['notify']]}",
                ],
                thumbnail=osu.avatar_url(current["osu_id"]),
            ), ephemeral=True)
            return

        async with ctx.typing(ephemeral=True):
            try:
                user = await osu.get_user(osu_username)
            except (aiohttp.ClientError, TimeoutError) as e:
                self.logger.warning("link: osu! user lookup failed: %s", e)
                await ctx.reply(view=NoticeView(
                    "error", "Failed to look up the osu! user. Try again later."
                ), ephemeral=True)
                return

            if user is None:
                await ctx.reply(view=NoticeView(
                    "error", f"osu! user not found: {discord.utils.escape_markdown(osu_username)}"
                ), ephemeral=True)
                return

            notify = notify or (current["notify"] if current else "both")
            await self.bot.db.set_osu_link(ctx.author.id, user["id"], user["username"], notify)
            await ctx.reply(view=DetailNoticeView(
                "success",
                [
                    f"Linked to `{user['username']}`",
                    f"-# osu! ID: {user['id']} • Notify: {NOTIFY_DISPLAY[notify]}",
                ],
                thumbnail=user.get("avatar_url") or osu.avatar_url(user["id"]),
            ), ephemeral=True)

    @commands.hybrid_command(name="unlink", description="Unlinks your osu! account.")
    async def unlink(self, ctx: commands.Context) -> None:
        if await self.bot.db.remove_osu_link(ctx.author.id):
            await ctx.reply(view=NoticeView("success", "Unlinked."), ephemeral=True)
        else:
            await ctx.reply(view=NoticeView("warning", "You are not linked."), ephemeral=True)

    # --- polling ----------------------------------------------------------------

    @tasks.loop(seconds=POLL_INTERVAL)
    async def poll(self) -> None:
        try:
            await self.run_poll_cycle()
        except Exception:
            self.logger.exception("poll cycle failed")
        try:
            await self.flush_outbox()
        except Exception:
            self.logger.exception("delivering notifications failed")

    @poll.before_loop
    async def before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def run_poll_cycle(self) -> None:
        subs = await self.bot.db.get_subscriptions()
        if not subs:
            return

        status_modes = {sub["mode"] for sub in subs if sub["notify_status"]}
        queues = await self.api.fetch_queues([sub["mode"] for sub in subs])
        bns = {
            q["owner"]["osuId"]: q for q in queues
            if osumod_api.is_bn(q) and status_modes.intersection(q.get("modes", []))
        }

        queue_events = tracker.diff_queue_events(await self.bot.db.get_queue_state(), queues)

        sync = await self.bot.db.get_bn_sync()
        to_fetch = tracker.select_bns_to_fetch(
            bns, sync, time.time(), POLL_INTERVAL, RECONCILE_INTERVAL
        )
        fetched: dict[int, list[dict]] = {}
        if to_fetch:
            self.logger.info(
                "poll: fetching requests for %d/%d BN(s): %s",
                len(to_fetch), len(bns),
                ", ".join(bns[osu_id]["owner"]["username"] for osu_id in to_fetch),
            )
            fetched = await self.api.fetch_many_requests(to_fetch)

        old_requests = await self.bot.db.get_request_state(list(fetched))
        changes = tracker.diff_request_changes(old_requests, fetched, seeded=set(sync))

        notifications = await self.plan_notifications(subs, queue_events, changes, bns)
        await self.bot.db.save_poll(
            queues,
            fetched,
            {osu_id: tracker.activity(bns[osu_id]) for osu_id in fetched},
            sorted(bns),
            notifications,
        )

    @staticmethod
    def receiving_channels(subs: list[dict], modes: list[str], flag: str) -> list[dict]:
        """Subscriptions that want `flag` for any of `modes`, one per channel."""
        seen, receiving = set(), []
        for sub in subs:
            if sub[flag] and sub["mode"] in modes and sub["channel_id"] not in seen:
                seen.add(sub["channel_id"])
                receiving.append(sub)
        return receiving

    def pick_mention_channel(self, receiving: list[dict], discord_id: int) -> int | None:
        """The first receiving channel whose server the user is a member of."""
        for sub in receiving:
            guild = self.bot.get_guild(sub["guild_id"]) if sub["guild_id"] else None
            if guild is not None and guild.get_member(discord_id) is not None:
                return sub["channel_id"]
        return None

    async def plan_notifications(
        self,
        subs: list[dict],
        queue_events: list[dict],
        changes: list[dict],
        bns: dict[int, dict],
    ) -> list[dict]:
        notifications = []

        for event in queue_events:
            flag = "notify_bn_queue" if event["kind"].startswith("bn_") else "notify_modder_queue"
            embed = messages.queue_event_embed(event).to_dict()
            for sub in self.receiving_channels(subs, event["modes"], flag):
                notifications.append(
                    {"type": "channel", "channel_id": sub["channel_id"], "embed": embed}
                )

        requester_ids = {requester_osu_id(c["req"]) for c in changes} - {None}
        links: dict[int, list[dict]] = defaultdict(list)
        for row in await self.bot.db.get_osu_links(sorted(requester_ids)):
            links[row["osu_id"]].append(row)

        for change in changes:
            queue = bns[change["bn_osu_id"]]
            bn_username = queue["owner"]["username"]
            embed = messages.request_change_embed(change, bn_username).to_dict()
            link = {
                "bn_username": bn_username,
                "beatmapset_id": change["req"].get("beatmapsetOsuId"),
            }
            receiving = self.receiving_channels(subs, queue.get("modes", []), "notify_status")

            mentions: dict[int, list[int]] = defaultdict(list)
            for row in links.get(requester_osu_id(change["req"]), []):
                if row["notify"] in ("dm", "both"):
                    notifications.append(
                        {"type": "dm", "user_id": row["discord_id"], "embed": embed, "link": link}
                    )
                if row["notify"] in ("mention", "both"):
                    channel_id = self.pick_mention_channel(receiving, row["discord_id"])
                    if channel_id is not None:
                        mentions[channel_id].append(row["discord_id"])

            for sub in receiving:
                notifications.append({
                    "type": "channel",
                    "channel_id": sub["channel_id"],
                    "embed": embed,
                    "link": link,
                    "request_id": change["req"]["id"],
                    "mentions": mentions.get(sub["channel_id"], []),
                })

        return notifications

    # --- delivery ---------------------------------------------------------------

    async def flush_outbox(self) -> None:
        """Send pending notifications in order per destination. A destination whose
        send fails transiently is retried next cycle, keeping its later messages queued."""
        rows = await self.bot.db.get_outbox()
        if not rows:
            return

        groups: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            payload = row["payload"]
            target = payload.get("channel_id") or payload.get("user_id")
            groups[(payload["type"], target)].append(row)

        semaphore = asyncio.Semaphore(SEND_CONCURRENCY)

        async def flush_group(group: list[dict]) -> None:
            async with semaphore:
                for row in group:
                    result = await self.deliver(row["payload"])
                    if result is not Delivery.RETRY:
                        await self.bot.db.delete_outbox(row["id"])
                        continue
                    expired = (
                        row["attempts"] + 1 >= OUTBOX_MAX_ATTEMPTS
                        or time.time() - row["created_at"] > OUTBOX_MAX_AGE
                    )
                    if expired:
                        self.logger.warning("giving up on notification %s", row["id"])
                        await self.bot.db.delete_outbox(row["id"])
                    else:
                        await self.bot.db.mark_outbox_failed(row["id"])
                    return

        await asyncio.gather(*(flush_group(group) for group in groups.values()))

    async def deliver(self, payload: dict) -> Delivery:
        target = payload.get("channel_id") or payload.get("user_id")
        try:
            if payload["type"] == "dm":
                await self.send_dm(payload)
            else:
                await self.send_to_channel(payload)
            return Delivery.SENT
        except discord.Forbidden:
            self.logger.warning("missing permission to notify %s %s", payload["type"], target)
            return Delivery.DROPPED
        except discord.NotFound:
            self.logger.warning("%s %s no longer exists", payload["type"], target)
            return Delivery.DROPPED
        except discord.HTTPException as e:
            if e.status >= 500 or e.status == 429:
                return Delivery.RETRY
            self.logger.warning("failed to notify %s %s: %s", payload["type"], target, e)
            return Delivery.DROPPED
        except (aiohttp.ClientError, TimeoutError, OSError) as e:
            self.logger.warning("network error notifying %s %s: %s", payload["type"], target, e)
            return Delivery.RETRY

    async def send_to_channel(self, payload: dict) -> None:
        channel_id = payload["channel_id"]
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.NotFound:
                removed = await self.bot.db.remove_subscriptions(channel_id)
                self.logger.warning(
                    "channel %s was deleted, removed %d subscription(s)", channel_id, removed
                )
                raise

        kwargs: dict = {"embed": discord.Embed.from_dict(payload["embed"])}
        if link := payload.get("link"):
            kwargs["view"] = messages.link_view(**link)
        if mentions := payload.get("mentions"):
            kwargs["content"] = " ".join(f"<@{user_id}>" for user_id in mentions)
            kwargs["allowed_mentions"] = discord.AllowedMentions(
                users=[discord.Object(id=user_id) for user_id in mentions]
            )

        request_id = payload.get("request_id")
        if request_id:
            # Thread updates of the same request as replies to its previous notification.
            previous = await self.bot.db.get_status_message(request_id, channel_id)
            if previous is not None:
                kwargs["reference"] = discord.MessageReference(
                    message_id=previous, channel_id=channel_id, fail_if_not_exists=False
                )

        message = await channel.send(**kwargs)
        if request_id:
            await self.bot.db.set_status_message(request_id, channel_id, message.id)

    async def send_dm(self, payload: dict) -> None:
        user_id = payload["user_id"]
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        await user.send(
            embed=discord.Embed.from_dict(payload["embed"]),
            view=messages.link_view(**payload["link"]),
        )


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Osumod)
