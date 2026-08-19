import os
import tempfile
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..utils import downloader, r2
from ..utils.views import NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

DOWNLOAD_MAX = 500 * 1024 * 1024


class DownloadResultView(discord.ui.LayoutView):
    def __init__(
        self,
        info: dict,
        filesize: int,
        filename: str,
        audio: bool,
        r2_url: Optional[str] = None,
        resolution: Optional[str] = None,
    ):
        super().__init__(timeout=None)

        duration = info.get("duration")
        width = info.get("width")
        height = info.get("height")
        fps = info.get("fps")
        frames = int(fps * duration) if fps and duration else None

        meta_parts = []
        if not audio:
            if resolution:
                meta_parts.append(resolution)
            elif width and height:
                meta_parts.append(f"{width}x{height}")
        if frames:
            meta_parts.append(f"{frames:,} frames")
        meta_parts.append(downloader.fmt_size(filesize))
        meta_parts.append(downloader.fmt_duration(duration))

        meta_line = " · ".join(meta_parts)
        media_url = r2_url or f"attachment://{filename}"

        if audio:
            if r2_url:
                self.add_item(discord.ui.Container(
                    discord.ui.TextDisplay(f"{r2_url}\n-# {meta_line}"),
                ))
            else:
                self.add_item(discord.ui.Container(
                    discord.ui.File(media_url),
                    discord.ui.Separator(),
                    discord.ui.TextDisplay(f"-# {meta_line}"),
                ))
        else:
            self.add_item(discord.ui.Container(
                discord.ui.MediaGallery(discord.MediaGalleryItem(media_url)),
                discord.ui.Separator(),
                discord.ui.TextDisplay(f"-# {meta_line}"),
            ))


class Download(Cog):
    async def deliver(
        self,
        ctx: commands.Context,
        info: dict,
        filepath: str,
        audio: bool,
        resolution: Optional[str] = None,
    ) -> None:
        filesize = os.path.getsize(filepath)
        filename = os.path.basename(filepath)

        if filesize > downloader.filesize_limit(ctx):
            if not r2.r2_configured():
                await ctx.reply(view=NoticeView("error", "The file is too large."))
                return
            try:
                r2_url = await r2.upload(filepath, filename)
            except Exception as e:
                self.logger.error("deliver: R2 upload failed: %s", e)
                await ctx.reply(view=NoticeView(
                    "error", f"Upload failed: {downloader.clean_error(e)}"
                ))
                return
            await ctx.reply(view=DownloadResultView(
                info, filesize, filename, audio, r2_url=r2_url, resolution=resolution
            ))
        else:
            await ctx.reply(
                view=DownloadResultView(
                    info, filesize, filename, audio, resolution=resolution
                ),
                file=discord.File(filepath, filename=filename),
            )

    @commands.hybrid_group(name="download")
    async def download(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.reply(
                view=NoticeView("info", "Usage: download video <url> / download audio <url>")
            )

    @download.command(
        name="video",
        description="Downloads a video from a URL. Optionally resizes it for osu!taiko backgrounds.",
    )
    @app_commands.describe(
        url="The URL to download from.",
        taiko_resize="Resize the video to 1280x720 for osu!taiko backgrounds.",
    )
    async def download_video(
        self, ctx: commands.Context, url: str, taiko_resize: bool = False
    ) -> None:
        async with ctx.typing():
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    info = await downloader.fetch_info(url, tmpdir, False, DOWNLOAD_MAX)
                except Exception as e:
                    await ctx.reply(view=NoticeView(
                        "error", f"Failed to fetch video info: {downloader.clean_error(e)}"
                    ))
                    return

                estimated = downloader.estimate_size(info)
                if estimated is not None and estimated > DOWNLOAD_MAX:
                    await ctx.reply(view=NoticeView("error", "The file is too large."))
                    return

                try:
                    info, filepath = await downloader.download_media(
                        url, tmpdir, False, DOWNLOAD_MAX
                    )
                except downloader.FileTooLargeError:
                    await ctx.reply(view=NoticeView("error", "The file is too large."))
                    return
                except Exception as e:
                    await ctx.reply(view=NoticeView(
                        "error", f"Download failed: {downloader.clean_error(e)}"
                    ))
                    return

                resolution = None
                if taiko_resize:
                    stem = os.path.splitext(os.path.basename(filepath))[0]
                    output_path = os.path.join(tmpdir, f"{stem} (taiko).mp4")
                    try:
                        filepath = await downloader.taiko_resize(filepath, output_path)
                    except downloader.ResizeError as e:
                        self.logger.error("download_video: resize failed: %s", e)
                        await ctx.reply(view=NoticeView("error", "Video resizing failed."))
                        return
                    resolution = "1280x720"

                await self.deliver(ctx, info, filepath, False, resolution)

    @download.command(
        name="audio",
        description="Downloads audio from a URL as MP3 using yt-dlp.",
    )
    @app_commands.describe(url="The URL to download from.")
    async def download_audio(self, ctx: commands.Context, url: str) -> None:
        async with ctx.typing():
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    info, raw_path = await downloader.download_media(
                        url, tmpdir, True, DOWNLOAD_MAX
                    )
                except downloader.FileTooLargeError:
                    await ctx.reply(view=NoticeView("error", "The file is too large."))
                    return
                except Exception as e:
                    await ctx.reply(view=NoticeView(
                        "error", f"Download failed: {downloader.clean_error(e)}"
                    ))
                    return

                stem = os.path.splitext(os.path.basename(raw_path))[0]
                mp3_path = os.path.join(tmpdir, f"{stem}.mp3")
                try:
                    await downloader.convert_audio(raw_path, mp3_path)
                except downloader.ConvertError as e:
                    self.logger.error("download_audio: convert failed: %s", e)
                    await ctx.reply(view=NoticeView("error", "Audio conversion failed."))
                    return

                await self.deliver(ctx, info, mp3_path, True)


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Download)
