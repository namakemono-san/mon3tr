import asyncio
import datetime
from typing import TYPE_CHECKING, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..utils.api import osu as osu_api
from ..utils.api import osumod as osumod_api
from ..utils.views import DetailNoticeView, NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

POLL_INTERVAL = 30
FULL_SYNC_CYCLES = 120
SEND_CONCURRENCY = 8

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
NOTIFY_VALUES = {c.value for c in NOTIFY_CHOICES}
NOTIFY_DISPLAY = {
    "both": "DM + Mention",
    "dm": "DM only",
    "mention": "Mention only",
}


def avatar_url(osu_id: int) -> str:
    return f"https://a.ppy.sh/{osu_id}"


QUEUE_EVENT_TITLES = {
    "bn_open": ("🔓 {username}'s Queue Opened", osumod_api.OPEN_COLOR),
    "bn_close": ("🔒 {username}'s Queue Closed", osumod_api.CLOSE_COLOR),
    "modder_open": ("🔓 {username}'s Modding Queue Opened", osumod_api.OPEN_COLOR),
    "modder_close": ("🔒 {username}'s Modding Queue Closed", osumod_api.CLOSE_COLOR),
}


def build_queue_event_embed(kind: str, username: str) -> discord.Embed:
    title, color = QUEUE_EVENT_TITLES[kind]
    timestamp = int(datetime.datetime.now(datetime.UTC).timestamp())
    return discord.Embed(
        title=f"{title.format(username=username)} (<t:{timestamp}:R>)",
        url=osumod_api.profile_url(username),
        color=color,
    )


def build_status_change_embed(change: dict) -> discord.Embed:
    req = change["req"]
    username = change["username"]
    old_status = change["old_status"]
    new_status = change["new_status"]
    kind = change.get("kind", "status")

    title = req.get("title", "Unknown")
    artist = req.get("artist", "Unknown")
    creator = req.get("creator", "Unknown")
    feedback = (req.get("feedback", "") or "").strip()
    comment = (req.get("comment", "") or "").strip()
    image = req.get("image", "")

    old_emoji = osumod_api.get_status_emoji(old_status)
    new_emoji = osumod_api.get_status_emoji(new_status)
    color = osumod_api.STATUS_COLORS.get(new_status, osumod_api.DEFAULT_STATUS_COLOR)
    timestamp = int(datetime.datetime.now(datetime.UTC).timestamp())

    if kind == "feedback":
        heading = "💬 Feedback Updated" if change.get("old_feedback") else "💬 Feedback Added"
        status_value = f"{new_emoji} {new_status}"
    else:
        heading = f"{new_emoji} Status Changed"
        status_value = f"{old_emoji} {old_status} → {new_emoji} {new_status}"

    embed = discord.Embed(
        title=f"{heading} (<t:{timestamp}:R>)",
        url=osumod_api.profile_url(username),
        color=color,
    )
    embed.add_field(
        name=f"Beatmapset by {creator}",
        value=f"{artist} - {title}"[:1024],
        inline=False,
    )
    embed.add_field(name="Status", value=status_value, inline=True)
    if comment:
        embed.add_field(name="Mapper's Comment", value=comment[:1024], inline=False)
    if feedback:
        embed.add_field(name="Feedback", value=feedback[:1024], inline=False)
    if image:
        embed.set_image(url=image)

    time_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M")
    embed.set_footer(text=f"BN by {username} • {time_str} UTC")
    return embed


class StatusLinkView(discord.ui.View):
    def __init__(self, username: str, mapset_id):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="osumod",
            url=osumod_api.profile_url(username),
        ))
        if mapset_id:
            self.add_item(discord.ui.Button(
                style=discord.ButtonStyle.link,
                label="Beatmap",
                url=f"https://osu.ppy.sh/beatmapsets/{mapset_id}",
            ))


class Osumod(Cog):
    def __init__(self, bot: "Mon3trBot"):
        super().__init__(bot)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cycle_count = 0

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        if self.session is not None:
            await self.session.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        removed = await self.bot.db.remove_osumod_guild(guild.id)
        if removed:
            self.logger.info(
                "left guild %s, removed %d subscription(s)", guild.id, removed
            )

    @commands.hybrid_group(name="osumod")
    @commands.guild_only()
    async def osumod(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(view=NoticeView(
                "info", "Usage: osumod subscribe <mode> / unsubscribe [mode] / list"
            ))

    @osumod.command(
        name="subscribe",
        description="Subscribes this channel to osumod queue notifications.",
    )
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(mode=MODE_CHOICES)
    @app_commands.describe(
        mode="The game mode to subscribe to.",
        bn_queue="Notify when a BN's queue closes.",
        modder_queue="Notify when a modder's queue opens or closes.",
        status="Notify when a request's status changes.",
    )
    async def osumod_subscribe(
        self,
        ctx: commands.Context,
        mode: str,
        bn_queue: bool = False,
        modder_queue: bool = False,
        status: bool = False,
    ) -> None:
        api_mode = osumod_api.normalize_mode(mode)
        if api_mode is None:
            await ctx.reply(view=NoticeView(
                "error", "Invalid mode. Use one of: osu, taiko, catch, mania."
            ), ephemeral=True)
            return

        if not (bn_queue or modder_queue or status):
            await ctx.reply(view=NoticeView(
                "error",
                "At least one notification type must be enabled "
                "(bn_queue / modder_queue / status).",
            ), ephemeral=True)
            return

        await self.bot.db.add_osumod_subscription(
            ctx.guild.id, ctx.channel.id, api_mode, bn_queue, modder_queue, status
        )

        types = [
            name for enabled, name in [
                (bn_queue, "BN queue"),
                (modder_queue, "Modder queue"),
                (status, "Status changes"),
            ] if enabled
        ]
        await ctx.reply(view=NoticeView(
            "success",
            f"Subscribed this channel to {osumod_api.MODE_DISPLAY[api_mode]} "
            f"notifications ({', '.join(types)}).",
        ), ephemeral=True)

    @osumod.command(
        name="unsubscribe",
        description="Unsubscribes this channel from osumod notifications.",
    )
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(mode=MODE_CHOICES)
    @app_commands.describe(mode="The game mode to unsubscribe from. Omit to remove all.")
    async def osumod_unsubscribe(
        self, ctx: commands.Context, mode: Optional[str] = None
    ) -> None:
        api_mode = None
        if mode is not None:
            api_mode = osumod_api.normalize_mode(mode)
            if api_mode is None:
                await ctx.reply(view=NoticeView(
                    "error", "Invalid mode. Use one of: osu, taiko, catch, mania."
                ), ephemeral=True)
                return

        removed = await self.bot.db.remove_osumod_subscriptions(ctx.channel.id, api_mode)
        if removed == 0:
            await ctx.reply(view=NoticeView(
                "warning", "This channel has no matching subscriptions."
            ), ephemeral=True)
        else:
            await ctx.reply(view=NoticeView(
                "success", f"Removed {removed} subscription(s)."
            ), ephemeral=True)

    @osumod.command(
        name="list",
        description="Lists osumod subscriptions in this server.",
    )
    async def osumod_list(self, ctx: commands.Context) -> None:
        subs = await self.bot.db.get_osumod_subscriptions_for_guild(ctx.guild.id)
        if not subs:
            await ctx.reply(view=NoticeView("info", "No subscriptions in this server."))
            return

        lines = ["### osumod subscriptions"]
        for sub in subs:
            types = [
                name for key, name in [
                    ("notify_bn_queue", "BN queue"),
                    ("notify_modder_queue", "Modder queue"),
                    ("notify_status", "Status"),
                ] if sub[key]
            ]
            lines.append(
                f"<#{sub['channel_id']}> — **{osumod_api.MODE_DISPLAY.get(sub['mode'], sub['mode'])}** "
                f"({', '.join(types)})"
            )

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay("\n".join(lines))))
        await ctx.reply(view=view)

    @commands.hybrid_command(
        name="link",
        description="Links your osu! account to receive osumod status notifications.",
    )
    @app_commands.choices(notify=NOTIFY_CHOICES)
    @app_commands.describe(
        osu_username="Your osu! username.",
        notify="How to notify you when your request status changes.",
    )
    async def link(
        self,
        ctx: commands.Context,
        osu_username: Optional[str] = None,
        notify: Optional[str] = None,
    ) -> None:
        if notify is not None and notify not in NOTIFY_VALUES:
            await ctx.reply(view=NoticeView(
                "error", "Invalid notify option. Use one of: both, dm, mention."
            ), ephemeral=True)
            return

        current = await self.bot.db.get_osu_link(ctx.author.id)

        if osu_username is None and notify is None:
            if current is None:
                await ctx.reply(view=NoticeView(
                    "info", "Not linked. Usage: link <osu_username> [notify]"
                ), ephemeral=True)
            else:
                await ctx.reply(view=DetailNoticeView(
                    "info",
                    [
                        f"Linked to `{current['osu_username']}`",
                        f"-# osu! ID: {current['osu_id']} • Notify: {NOTIFY_DISPLAY[current['notify']]}",
                    ],
                    thumbnail=avatar_url(current["osu_id"]),
                ), ephemeral=True)
            return

        if osu_username is None:
            if current is None:
                await ctx.reply(view=NoticeView(
                    "error", "You are not linked yet. Usage: link <osu_username> [notify]"
                ), ephemeral=True)
                return
            await self.bot.db.set_osu_link(
                ctx.author.id, current["osu_id"], current["osu_username"], notify
            )
            await ctx.reply(view=DetailNoticeView(
                "success",
                [
                    f"Linked to `{current['osu_username']}`",
                    f"-# Notify: {NOTIFY_DISPLAY[notify]}",
                ],
                thumbnail=avatar_url(current["osu_id"]),
            ), ephemeral=True)
            return

        async with ctx.typing(ephemeral=True):
            try:
                user = await osu_api.get_osu_user(osu_username)
            except Exception as e:
                self.logger.warning("link: osu! user lookup failed: %s", e)
                await ctx.reply(view=NoticeView(
                    "error", "Failed to look up the osu! user. Try again later."
                ), ephemeral=True)
                return

            if user is None:
                await ctx.reply(view=NoticeView(
                    "error", f"osu! user not found: {osu_username}"
                ), ephemeral=True)
                return

            if notify is None:
                notify = current["notify"] if current else "both"

            await self.bot.db.set_osu_link(
                ctx.author.id, user["id"], user["username"], notify
            )
            await ctx.reply(view=DetailNoticeView(
                "success",
                [
                    f"Linked to `{user['username']}`",
                    f"-# osu! ID: {user['id']} • Notify: {NOTIFY_DISPLAY[notify]}",
                ],
                thumbnail=user.get("avatar_url") or avatar_url(user["id"]),
            ), ephemeral=True)

    @commands.hybrid_command(
        name="unlink",
        description="Unlinks your osu! account.",
    )
    async def unlink(self, ctx: commands.Context) -> None:
        removed = await self.bot.db.remove_osu_link(ctx.author.id)
        if removed:
            await ctx.reply(view=NoticeView("success", "Unlinked."), ephemeral=True)
        else:
            await ctx.reply(view=NoticeView("warning", "You are not linked."), ephemeral=True)

    @tasks.loop(seconds=POLL_INTERVAL)
    async def poll(self) -> None:
        try:
            await self.run_poll_cycle()
        except Exception as e:
            self.logger.exception("poll cycle failed: %s", e)

    @poll.before_loop
    async def before_poll(self) -> None:
        await self.bot.wait_until_ready()

    async def run_poll_cycle(self) -> None:
        subs = await self.bot.db.get_osumod_subscriptions()
        if not subs:
            return

        self.cycle_count += 1
        full_sync = self.cycle_count % FULL_SYNC_CYCLES == 1

        sub_modes = {sub["mode"] for sub in subs}
        queues = await osumod_api.fetch_queues(self.session)

        monitored = set()
        for mode in sub_modes:
            monitored.update(
                q["owner"]["username"] for q in osumod_api.filter_bns(queues, mode)
            )

        old_queue_state = await self.bot.db.get_osumod_queue_state()
        queue_events = osumod_api.diff_queue_events(old_queue_state, queues)

        if full_sync:
            to_fetch = monitored
        else:
            to_fetch = set()
            for q in queues:
                username = q["owner"]["username"]
                if username not in monitored:
                    continue
                old = old_queue_state.get(q["owner"]["_id"])
                if old is None or old["last_actioned"] != (q.get("lastActionedDate", "") or ""):
                    to_fetch.add(username)

        if to_fetch:
            self.logger.info(
                "poll: fetching requests for %d/%d BN(s)%s: %s",
                len(to_fetch), len(monitored),
                " (full sync)" if full_sync else "",
                ", ".join(sorted(to_fetch)),
            )

        new_requests_map = await osumod_api.fetch_all_requests(self.session, sorted(to_fetch))
        old_request_state = await self.bot.db.get_osumod_request_state()
        status_changes = osumod_api.diff_status_changes(old_request_state, new_requests_map)

        username_modes = {
            q["owner"]["username"]: q.get("modes", []) for q in queues
        }

        await self.dispatch_notifications(subs, queue_events, status_changes, username_modes)

        await self.bot.db.replace_osumod_queue_state(queues)
        await self.bot.db.update_osumod_request_state(new_requests_map)
        if full_sync:
            await self.bot.db.prune_osumod_request_state(sorted(monitored))
            await self.bot.db.cleanup_osumod_status_messages()

    async def dispatch_notifications(
        self,
        subs: list,
        queue_events: list,
        status_changes: list,
        username_modes: dict,
    ) -> None:
        if not queue_events and not status_changes:
            return

        osu_ids = list({
            c["req"].get("user") for c in status_changes if c["req"].get("user")
        })
        links_by_osu: dict = {}
        for row in await self.bot.db.get_osu_links_for_osu_ids(osu_ids):
            links_by_osu.setdefault(row["osu_id"], []).append(row)

        jobs = []

        for event in queue_events:
            sent_channels = set()
            for sub in subs:
                if sub["mode"] not in event["modes"]:
                    continue
                if event["kind"] in ("bn_open", "bn_close") and not sub["notify_bn_queue"]:
                    continue
                if event["kind"] in ("modder_open", "modder_close") and not sub["notify_modder_queue"]:
                    continue
                if sub["channel_id"] in sent_channels:
                    continue
                sent_channels.add(sub["channel_id"])
                jobs.append(self.send_queue_event(sub, event))

        for change in status_changes:
            modes = username_modes.get(change["username"], [])
            receiving = []
            seen_channels = set()
            for sub in subs:
                if not sub["notify_status"] or sub["mode"] not in modes:
                    continue
                if sub["channel_id"] in seen_channels:
                    continue
                seen_channels.add(sub["channel_id"])
                receiving.append(sub)

            mentions_by_channel: dict = {}
            dm_targets = []
            for row in links_by_osu.get(change["req"].get("user"), []):
                if row["notify"] in ("dm", "both"):
                    dm_targets.append(row["discord_id"])
                if row["notify"] in ("mention", "both"):
                    channel_id = self.pick_mention_channel(receiving, row["discord_id"])
                    if channel_id is not None:
                        mentions_by_channel.setdefault(channel_id, []).append(row["discord_id"])

            for sub in receiving:
                jobs.append(self.send_status_change(
                    sub, change, mentions_by_channel.get(sub["channel_id"], [])
                ))
            for discord_id in dm_targets:
                jobs.append(self.send_status_dm(discord_id, change))

        if not jobs:
            return

        semaphore = asyncio.Semaphore(SEND_CONCURRENCY)

        async def bounded(coro):
            async with semaphore:
                await coro

        await asyncio.gather(*(bounded(job) for job in jobs))

    async def send_queue_event(self, sub: dict, event: dict) -> None:
        await self.send_message(
            sub["channel_id"],
            embed=build_queue_event_embed(event["kind"], event["username"]),
        )
        self.logger.info(
            "notified %s: %s (%s) -> channel %s",
            event["kind"], event["username"], sub["mode"], sub["channel_id"],
        )

    def pick_mention_channel(self, receiving: list, discord_id: int) -> Optional[int]:
        for sub in receiving:
            guild = self.bot.get_guild(sub["guild_id"]) if sub["guild_id"] else None
            if guild is not None and guild.get_member(discord_id) is not None:
                return sub["channel_id"]
        return None

    async def send_status_change(
        self, sub: dict, change: dict, mention_ids: Optional[list] = None
    ) -> None:
        channel_id = sub["channel_id"]
        request_id = change["req"]["_id"]

        reference = None
        prev_message_id = await self.bot.db.get_osumod_status_message(request_id, channel_id)
        if prev_message_id is not None:
            reference = discord.MessageReference(
                message_id=prev_message_id,
                channel_id=channel_id,
                fail_if_not_exists=False,
            )

        content = None
        allowed_mentions = None
        if mention_ids:
            content = " ".join(f"<@{i}>" for i in mention_ids)
            allowed_mentions = discord.AllowedMentions(
                users=[discord.Object(id=i) for i in mention_ids]
            )

        message = await self.send_message(
            channel_id,
            content=content,
            embed=build_status_change_embed(change),
            view=StatusLinkView(change["username"], change["req"].get("mapsetId")),
            reference=reference,
            allowed_mentions=allowed_mentions,
        )
        if message is not None:
            await self.bot.db.set_osumod_status_message(request_id, channel_id, message.id)

        self.logger.info(
            "notified %s: %s -> %s by %s (%s) -> channel %s%s%s",
            change.get("kind", "status"),
            change["old_status"], change["new_status"],
            change["username"], sub["mode"], channel_id,
            " (reply)" if reference else "",
            f" (mention: {mention_ids})" if mention_ids else "",
        )

    async def send_status_dm(self, discord_id: int, change: dict) -> None:
        try:
            user = self.bot.get_user(discord_id) or await self.bot.fetch_user(discord_id)
            await user.send(
                embed=build_status_change_embed(change),
                view=StatusLinkView(change["username"], change["req"].get("mapsetId")),
            )
            self.logger.info(
                "notified %s via DM: %s -> %s to user %s",
                change.get("kind", "status"),
                change["old_status"], change["new_status"], discord_id,
            )
        except discord.Forbidden:
            self.logger.warning("cannot DM user %s (DMs closed)", discord_id)
        except discord.HTTPException as e:
            self.logger.warning("failed to DM user %s: %s", discord_id, e)

    async def send_message(
        self,
        channel_id: int,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
        reference: Optional[discord.MessageReference] = None,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
    ) -> Optional[discord.Message]:
        kwargs: dict = {"reference": reference}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions

        channel = self.bot.get_channel(channel_id)
        try:
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
            return await channel.send(**kwargs)
        except (discord.NotFound, discord.Forbidden) as e:
            if isinstance(e, discord.NotFound):
                removed = await self.bot.db.remove_osumod_subscriptions(channel_id)
                self.logger.warning(
                    "channel %s not found, removed %d subscription(s)", channel_id, removed
                )
            else:
                self.logger.warning("missing permissions for channel %s", channel_id)
        except discord.HTTPException as e:
            self.logger.warning("failed to send to channel %s: %s", channel_id, e)
        return None


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Osumod)
