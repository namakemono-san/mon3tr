from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

ACCENT_COLOR = discord.Color.from_str("#dce26e")

SECTIONS = [
    ("osu!", [
        ("map [map]", "Show PP for 95/97/99/100% accuracy."),
        ("calc [map] <accuracy>", "Calculate PP at a specific accuracy."),
    ]),
    ("Media Download", [
        ("download video <url> [taiko_resize]", "Download a video (optional osu!taiko resize)."),
        ("download audio <url>", "Download audio as MP3."),
    ]),
    ("Tools", [
        ("addsilence <audio> <duration>", "Add leading silence to an mp3/ogg file."),
        ("normalize <audio>", "Apply loudness normalization to an mp3/ogg file."),
    ]),
    ("osumod", [
        ("osumod subscribe <mode> [types...]", "Subscribe this channel (needs Manage Server)."),
        ("osumod unsubscribe [mode]", "Unsubscribe this channel."),
        ("osumod list", "List subscriptions in this server."),
        ("link [osu_username] [notify]", "Link your osu! account for status alerts."),
        ("unlink", "Unlink your osu! account."),
    ]),
]


class Help(Cog):
    @commands.hybrid_command(
        name="help",
        description="Shows the list of available commands.",
    )
    async def help(self, ctx: commands.Context) -> None:
        prefix = ctx.clean_prefix if ctx.prefix else "m."

        blocks = [f"## Mon3tr Commands\n-# Prefix: `{prefix}` · also usable as slash commands"]
        for title, cmds in SECTIONS:
            lines = [f"### {title}"]
            lines += [f"`{prefix}{sig}`\n-# {desc}" for sig, desc in cmds]
            blocks.append("\n".join(lines))

        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(accent_color=ACCENT_COLOR)
        for i, block in enumerate(blocks):
            container.add_item(discord.ui.TextDisplay(block))
            if i < len(blocks) - 1:
                container.add_item(discord.ui.Separator())
        view.add_item(container)

        await ctx.reply(view=view)


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Help)
