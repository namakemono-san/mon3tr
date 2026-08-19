# Copyright (c) 2026 namakemono-san
# 
# This software is released under the MIT License.
# https://opensource.org/licenses/MIT

import os
import logging
import tempfile
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..utils import downloader
from ..utils.views import NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

ALLOWED_EXTS = (".mp3", ".ogg")

MAX_DELAY_MS = 60 * 60 * 1000


class SilenceResultView(discord.ui.LayoutView):
    def __init__(self, filename: str, filesize: int, delay_ms: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(
            discord.ui.File(f"attachment://{filename}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"-# Silence added: {delay_ms}ms · {downloader.fmt_size(filesize)}"
            ),
        ))


class NormalizeResultView(discord.ui.LayoutView):
    def __init__(self, filename: str, filesize: int):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(
            discord.ui.File(f"attachment://{filename}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# Loudness normalized · {downloader.fmt_size(filesize)}"),
        ))


class Tools(Cog):
    @commands.hybrid_command(
        name="addsilence",
        description="Adds leading silence to an audio file (mp3 or ogg).",
    )
    @app_commands.describe(
        audio="The audio file (mp3 or ogg).",
        duration="Silence length, e.g. 1000, 1000ms, or 1s (defaults to ms).",
    )
    async def addsilence(
        self, ctx: commands.Context, audio: discord.Attachment, duration: str
    ) -> None:
        ext = os.path.splitext(audio.filename)[1].lower()
        if ext not in ALLOWED_EXTS:
            await ctx.reply(view=NoticeView("error", "Only mp3 or ogg files are supported."))
            return

        delay_ms = downloader.parse_duration_ms(duration)
        if delay_ms is None:
            await ctx.reply(view=NoticeView(
                "error", "Invalid duration. Use e.g. 1000, 1000ms, or 1s."
            ))
            return
        if not (0 < delay_ms <= MAX_DELAY_MS):
            await ctx.reply(view=NoticeView(
                "error", "Duration must be between 1ms and 1 hour."
            ))
            return

        async with ctx.typing():
            with tempfile.TemporaryDirectory() as tmpdir:
                stem = os.path.splitext(os.path.basename(audio.filename))[0]
                input_path = os.path.join(tmpdir, f"input{ext}")
                output_path = os.path.join(tmpdir, f"{stem} (silence){ext}")

                try:
                    await audio.save(input_path)
                except discord.HTTPException as e:
                    await ctx.reply(view=NoticeView(
                        "error", f"Failed to download the attachment: {downloader.clean_error(e)}"
                    ))
                    return

                try:
                    await downloader.add_silence(input_path, output_path, delay_ms)
                except downloader.SilenceError as e:
                    self.logger.error("addsilence: ffmpeg failed: %s", e)
                    await ctx.reply(view=NoticeView("error", "Adding silence failed."))
                    return

                filesize = os.path.getsize(output_path)
                if filesize > downloader.filesize_limit(ctx):
                    await ctx.reply(view=NoticeView("error", "The result is too large to upload."))
                    return

                filename = os.path.basename(output_path)
                await ctx.reply(
                    view=SilenceResultView(filename, filesize, delay_ms),
                    file=discord.File(output_path, filename=filename),
                )

    @commands.hybrid_command(
        name="normalize",
        description="Applies loudness normalization to an audio file (mp3 or ogg) without changing its bitrate.",
    )
    @app_commands.describe(audio="The audio file to normalize (mp3 or ogg).")
    async def normalize(self, ctx: commands.Context, audio: discord.Attachment) -> None:
        ext = os.path.splitext(audio.filename)[1].lower()
        if ext not in ALLOWED_EXTS:
            await ctx.reply(view=NoticeView(
                "error", "Only mp3 or ogg files are supported."
            ))
            return

        async with ctx.typing():
            with tempfile.TemporaryDirectory() as tmpdir:
                stem = os.path.splitext(os.path.basename(audio.filename))[0]
                input_path = os.path.join(tmpdir, f"input{ext}")
                output_path = os.path.join(tmpdir, f"{stem} (normalized){ext}")

                try:
                    await audio.save(input_path)
                except discord.HTTPException as e:
                    await ctx.reply(view=NoticeView(
                        "error", f"Failed to download the attachment: {downloader.clean_error(e)}"
                    ))
                    return

                try:
                    await downloader.normalize_loudness(input_path, output_path)
                except downloader.NormalizeError as e:
                    self.logger.error("normalize: ffmpeg failed: %s", e)
                    await ctx.reply(view=NoticeView("error", "Loudness normalization failed."))
                    return

                filesize = os.path.getsize(output_path)
                if filesize > downloader.filesize_limit(ctx):
                    await ctx.reply(view=NoticeView("error", "The result is too large to upload."))
                    return

                filename = os.path.basename(output_path)
                await ctx.reply(
                    view=NormalizeResultView(filename, filesize),
                    file=discord.File(output_path, filename=filename),
                )


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Tools)
