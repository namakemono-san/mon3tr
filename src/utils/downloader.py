import asyncio
import logging
import os
import time

import yt_dlp

from ..config import get_settings

logger = logging.getLogger("Mon3tr").getChild("utils.downloader")


class DownloadError(Exception):
    """A download failure whose message is safe to show to users."""


class FileTooLargeError(DownloadError):
    def __init__(self) -> None:
        super().__init__("The file is too large.")


def _describe(e: Exception) -> str:
    # yt-dlp messages embed URLs and local paths, so map them to fixed messages.
    text = str(e)
    if "Unsupported URL" in text:
        return "This URL is not supported."
    if "Private video" in text or "is private" in text:
        return "This media is private."
    if "Sign in" in text or "login" in text.lower():
        return "This media requires login."
    if "unavailable" in text.lower() or "not available" in text.lower():
        return "This media is unavailable."
    return "Could not download from this URL."


class _YtdlpLogger:
    """Route yt-dlp output to debug logs instead of stderr; failures are raised anyway."""

    def debug(self, msg: str) -> None:
        logger.debug(msg)

    info = warning = error = debug


def estimate_size(info: dict) -> int | None:
    requested = info.get("requested_formats")
    if requested:
        total = 0
        for fmt in requested:
            size = fmt.get("filesize") or fmt.get("filesize_approx")
            if not size:
                return None
            total += size
        return total
    return info.get("filesize") or info.get("filesize_approx") or None


def _build_opts(outdir: str, audio: bool, max_bytes: int) -> dict:
    opts: dict = {
        "outtmpl": os.path.join(outdir, "%(title).150B.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YtdlpLogger(),
        "max_filesize": max_bytes,
        "ffmpeg_location": get_settings().ffmpeg_path,
        "noplaylist": True,
    }
    if audio:
        opts["format"] = "bestaudio[video=none]/bestaudio/best"
        opts["format_sort"] = ["acodec:opus", "abr"]
    else:
        opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        opts["merge_output_format"] = "mp4"
    return opts


def _extract(url: str, opts: dict, download: bool) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=download) or {}


async def fetch_info(url: str, workdir: str, audio: bool, max_bytes: int) -> dict:
    opts = _build_opts(workdir, audio, max_bytes)
    try:
        return await asyncio.to_thread(_extract, url, opts, False)
    except yt_dlp.utils.YoutubeDLError as e:
        raise DownloadError(_describe(e)) from e


async def download_media(url: str, workdir: str, audio: bool, max_bytes: int) -> tuple[dict, str]:
    """Download into `<workdir>/source/` so outputs written to `workdir` never collide."""
    outdir = os.path.join(workdir, "source")
    os.makedirs(outdir, exist_ok=True)
    opts = _build_opts(outdir, audio, max_bytes)
    size_exceeded = False

    def progress_hook(d: dict) -> None:
        nonlocal size_exceeded
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if total > max_bytes or (d.get("downloaded_bytes") or 0) > max_bytes:
            size_exceeded = True
            raise FileTooLargeError()

    opts["progress_hooks"] = [progress_hook]

    t0 = time.perf_counter()
    try:
        info = await asyncio.to_thread(_extract, url, opts, True)
    except Exception as e:
        if size_exceeded:
            raise FileTooLargeError() from None
        if isinstance(e, yt_dlp.utils.YoutubeDLError):
            raise DownloadError(_describe(e)) from e
        raise
    logger.info("download_media: done in %.2fs (audio=%s)", time.perf_counter() - t0, audio)

    downloads = info.get("requested_downloads") or []
    path = downloads[0].get("filepath") if downloads else None
    if not path or not os.path.exists(path):
        # yt-dlp silently skips files over max_filesize, leaving nothing behind.
        files = [f for f in os.listdir(outdir) if not f.endswith((".part", ".ytdl"))]
        if not files:
            raise FileTooLargeError()
        path = os.path.join(outdir, files[0])

    if os.path.getsize(path) > max_bytes:
        raise FileTooLargeError()
    return info, path
