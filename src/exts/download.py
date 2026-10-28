import os
import tempfile
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

from ..utils import downloader, ffmpeg
from ..utils.delivery import reply_with_file
from ..utils.formatting import fmt_duration, fmt_size
from ..utils.views import NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

DOWNLOAD_MAX = 500 * 1024 * 1024


def build_caption(info: dict, path: str, audio: bool, resolution: str | None = None) -> str:
    duration = info.get("duration")
    fps = info.get("fps")

    parts = []
    if not audio:
        width, height = info.get("width"), info.get("height")
        if resolution:
            parts.append(resolution)
        elif width and height:
            parts.append(f"{width}x{height}")
    if fps and duration:
        parts.append(f"{int(fps * duration):,} frames")
    parts.append(fmt_size(os.path.getsize(path)))
    parts.append(fmt_duration(duration))
    return " · ".join(parts)


class Download(Cog):
    @commands.hybrid_group(name="download")
    async def download(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(
                view=NoticeView("info", "Usage: download video <url> / download audio <url>")
            )

    @download.command(
        name="video",
        description="Downloads a video from a URL, optionally resized for osu!taiko backgrounds.",
    )
    @app_commands.describe(
        url="The URL to download from.",
        taiko_resize="Resize the video to 1280x720 for osu!taiko backgrounds.",
    )
    @commands.max_concurrency(1, commands.BucketType.user)
    async def download_video(
        self, ctx: commands.Context, url: str, taiko_resize: bool = False
    ) -> None:
        async with ctx.typing():
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    info = await downloader.fetch_info(url, tmpdir, False, DOWNLOAD_MAX)
                    estimated = downloader.estimate_size(info)
                    if estimated is not None and estimated > DOWNLOAD_MAX:
                        raise downloader.FileTooLargeError()
                    info, path = await downloader.download_media(url, tmpdir, False, DOWNLOAD_MAX)
                except downloader.DownloadError as e:
                    await ctx.reply(view=NoticeView("error", str(e)))
                    return

                resolution = None
                if taiko_resize:
                    stem = os.path.splitext(os.path.basename(path))[0]
                    resized_path = os.path.join(tmpdir, f"{stem} (taiko).mp4")
                    try:
                        await ffmpeg.taiko_resize(path, resized_path)
                    except ffmpeg.FFmpegError as e:
                        self.logger.error("download_video: resize failed: %s", e)
                        await ctx.reply(view=NoticeView("error", "Video resizing failed."))
                        return
                    path = resized_path
                    resolution = "1280x720"

                caption = build_caption(info, path, False, resolution)
                await reply_with_file(ctx, path, caption, gallery=True)

    @download.command(
        name="audio",
        description="Downloads audio from a URL as MP3 using yt-dlp.",
    )
    @app_commands.describe(url="The URL to download from.")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def download_audio(self, ctx: commands.Context, url: str) -> None:
        async with ctx.typing():
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    info, raw_path = await downloader.download_media(
                        url, tmpdir, True, DOWNLOAD_MAX
                    )
                except downloader.DownloadError as e:
                    await ctx.reply(view=NoticeView("error", str(e)))
                    return

                stem = os.path.splitext(os.path.basename(raw_path))[0]
                mp3_path = os.path.join(tmpdir, f"{stem}.mp3")
                try:
                    await ffmpeg.convert_to_mp3(raw_path, mp3_path)
                except ffmpeg.FFmpegError as e:
                    self.logger.error("download_audio: convert failed: %s", e)
                    await ctx.reply(view=NoticeView("error", "Audio conversion failed."))
                    return

                await reply_with_file(ctx, mp3_path, build_caption(info, mp3_path, True))


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Download)
