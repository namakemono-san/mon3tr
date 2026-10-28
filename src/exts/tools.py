# Copyright (c) 2026 namakemono-san
#
# This software is released under the MIT License.
# https://opensource.org/licenses/MIT

import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..utils import ffmpeg
from ..utils.delivery import reply_with_file
from ..utils.formatting import clean_error, fmt_size, parse_duration_ms
from ..utils.views import NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

ALLOWED_EXTS = (".mp3", ".ogg")

MAX_DELAY_MS = 60 * 60 * 1000


class Tools(Cog):
    async def process_audio(
        self,
        ctx: commands.Context,
        audio: discord.Attachment,
        suffix: str,
        process: Callable[[str, str], Awaitable[None]],
        caption: str,
    ) -> None:
        ext = os.path.splitext(audio.filename)[1].lower()
        stem = os.path.splitext(os.path.basename(audio.filename))[0]

        async with ctx.typing():
            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = os.path.join(tmpdir, f"input{ext}")
                output_path = os.path.join(tmpdir, f"{stem} ({suffix}){ext}")

                try:
                    await audio.save(input_path)
                except discord.HTTPException as e:
                    await ctx.reply(view=NoticeView(
                        "error", f"Failed to download the attachment: {clean_error(e)}"
                    ))
                    return

                try:
                    await process(input_path, output_path)
                except ffmpeg.FFmpegError as e:
                    self.logger.error("%s: ffmpeg failed: %s", ctx.command, e)
                    await ctx.reply(view=NoticeView("error", "Audio processing failed."))
                    return

                size = fmt_size(os.path.getsize(output_path))
                await reply_with_file(ctx, output_path, f"{caption} · {size}")

    @commands.hybrid_command(
        name="addsilence",
        description="Adds leading silence to an audio file (mp3 or ogg).",
    )
    @app_commands.describe(
        audio="The audio file (mp3 or ogg).",
        duration="Silence length, e.g. 1000, 1000ms, or 1s (defaults to ms).",
    )
    @commands.max_concurrency(1, commands.BucketType.user)
    async def addsilence(
        self, ctx: commands.Context, audio: discord.Attachment, duration: str
    ) -> None:
        if not audio.filename.lower().endswith(ALLOWED_EXTS):
            await ctx.reply(view=NoticeView("error", "Only mp3 or ogg files are supported."))
            return

        delay_ms = parse_duration_ms(duration)
        if delay_ms is None:
            await ctx.reply(view=NoticeView(
                "error", "Invalid duration. Use e.g. 1000, 1000ms, or 1s."
            ))
            return
        if not (0 < delay_ms <= MAX_DELAY_MS):
            await ctx.reply(view=NoticeView("error", "Duration must be between 1ms and 1 hour."))
            return

        await self.process_audio(
            ctx, audio, "silence",
            lambda src, dst: ffmpeg.add_silence(src, dst, delay_ms),
            f"Silence added: {delay_ms}ms",
        )

    @commands.hybrid_command(
        name="normalize",
        description="Applies loudness normalization to an audio file (mp3 or ogg).",
    )
    @app_commands.describe(audio="The audio file to normalize (mp3 or ogg).")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def normalize(self, ctx: commands.Context, audio: discord.Attachment) -> None:
        if not audio.filename.lower().endswith(ALLOWED_EXTS):
            await ctx.reply(view=NoticeView("error", "Only mp3 or ogg files are supported."))
            return

        await self.process_audio(
            ctx, audio, "normalized", ffmpeg.normalize_loudness, "Loudness normalized"
        )


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Tools)
