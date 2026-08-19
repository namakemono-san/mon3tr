import asyncio
import logging
import os
import re
import shutil
import time
from typing import TYPE_CHECKING, Optional, Tuple

import numpy as np
import yt_dlp

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger("Mon3tr").getChild("utils.downloader")

FFMPEG_PATH = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"

DM_FILESIZE_LIMIT = 10 * 1024 * 1024


def filesize_limit(ctx: "commands.Context") -> int:
    return ctx.guild.filesize_limit if ctx.guild else DM_FILESIZE_LIMIT

LOUDNORM_FILTER = "loudnorm=I=-10:LRA=7:TP=-1"

TAIKO_FILTER = (
    "[0:v]split=3[blur][scale][output];"
    "[output]scale=1280:720[output];"
    "[scale]scale=-1:340[scale];"
    "[blur]scale=1280:-1,boxblur=10,crop=1280:340[blur];"
    "[output][1:v]overlay=0:0[output];"
    "[output][blur]overlay=0:387[output];"
    "[output][scale]overlay=(W-w)/2:387[output]"
)


class FileTooLargeError(Exception):
    pass


class ResizeError(Exception):
    pass


class NormalizeError(Exception):
    pass


class SilenceError(Exception):
    pass


def parse_duration_ms(text: str) -> Optional[int]:
    text = text.strip().lower()
    if not text:
        return None
    if text.endswith("ms"):
        value, unit = text[:-2].strip(), "ms"
    elif text.endswith("s"):
        value, unit = text[:-1].strip(), "s"
    else:
        value, unit = text, "ms"
    try:
        number = float(value)
    except ValueError:
        return None
    if number < 0:
        return None
    ms = number * 1000 if unit == "s" else number
    return int(round(ms))


def clean_error(e: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
    lines = [line.strip() for line in msg.splitlines() if line.strip()]
    return lines[-1] if lines else str(e)


def fmt_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "0:00"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02}:{s:02}"
    return f"{m}:{s:02}"


def fmt_size(nbytes: int) -> str:
    if nbytes >= 1024 * 1024:
        return f"{nbytes / 1024 / 1024:.1f}MB"
    return f"{nbytes / 1024:.1f}KB"


def estimate_size(info: dict) -> Optional[int]:
    requested = info.get("requested_formats")
    if requested:
        total = 0
        for fmt in requested:
            size = fmt.get("filesize") or fmt.get("filesize_approx")
            if not size:
                return None
            total += size
        return total
    size = info.get("filesize") or info.get("filesize_approx")
    return size or None


def build_ydl_opts(tmpdir: str, audio: bool, max_bytes: int) -> dict:
    opts: dict = {
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_bytes,
        "ffmpeg_location": FFMPEG_PATH,
        "noplaylist": True,
    }
    if audio:
        opts["format"] = "bestaudio[video=none]/bestaudio/best"
        opts["format_sort"] = ["acodec:opus", "abr"]
    else:
        opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        opts["merge_output_format"] = "mp4"
    return opts


def _get_info(url: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _download(url: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True) or {}


async def fetch_info(url: str, tmpdir: str, audio: bool, max_bytes: int) -> dict:
    opts = build_ydl_opts(tmpdir, audio, max_bytes)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _get_info(url, opts))


async def download_media(
    url: str, tmpdir: str, audio: bool, max_bytes: int
) -> Tuple[dict, str]:
    opts = build_ydl_opts(tmpdir, audio, max_bytes)
    size_exceeded: list = []

    def progress_hook(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        downloaded = d.get("downloaded_bytes", 0)
        if (total and total > max_bytes) or downloaded > max_bytes:
            size_exceeded.append(True)
            raise Exception("File size limit exceeded")

    opts["progress_hooks"] = [progress_hook]
    loop = asyncio.get_running_loop()

    logger.info("download_media: downloading %s (audio=%s)", url, audio)
    t0 = time.perf_counter()
    try:
        info = await loop.run_in_executor(None, lambda: _download(url, opts))
    except Exception:
        if size_exceeded:
            raise FileTooLargeError()
        raise
    logger.info("download_media: done in %.2fs", time.perf_counter() - t0)

    files = [
        f for f in os.listdir(tmpdir)
        if not f.endswith(".part") and not f.endswith(".ytdl")
    ]
    if not files:
        raise FileTooLargeError()

    filepath = os.path.join(tmpdir, files[0])
    if os.path.getsize(filepath) > max_bytes:
        raise FileTooLargeError()

    return info, filepath


async def taiko_resize(input_path: str, output_path: str) -> str:
    logger.info("taiko_resize: %s -> %s", input_path, output_path)
    t0 = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        FFMPEG_PATH,
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_path,
        "-f", "lavfi", "-i", "color=black:s=1280x720:d=1",
        "-filter_complex", TAIKO_FILTER,
        "-map", "[output]",
        "-aspect", "1280:720",
        "-b:v", "800K",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise ResizeError(stderr.decode(errors="replace").strip())
    logger.info("taiko_resize: done in %.2fs", time.perf_counter() - t0)
    return output_path


async def probe_audio_bitrate(input_path: str) -> Optional[int]:
    process = await asyncio.create_subprocess_exec(
        FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=bit_rate:format=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return None
    for line in stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if line.isdigit() and int(line) > 0:
            return int(line)
    return None


async def normalize_loudness(input_path: str, output_path: str) -> str:
    logger.info("normalize_loudness: %s -> %s", input_path, output_path)
    t0 = time.perf_counter()

    bitrate = await probe_audio_bitrate(input_path)
    bitrate_args = ["-b:a", str(bitrate)] if bitrate else []
    logger.info("normalize_loudness: source bitrate=%s", bitrate or "unknown")

    process = await asyncio.create_subprocess_exec(
        FFMPEG_PATH,
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_path,
        "-af", LOUDNORM_FILTER,
        *bitrate_args,
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise NormalizeError(stderr.decode(errors="replace").strip())
    logger.info("normalize_loudness: done in %.2fs", time.perf_counter() - t0)
    return output_path


async def add_silence(input_path: str, output_path: str, delay_ms: int) -> str:
    logger.info("add_silence: %s -> %s (delay=%dms)", input_path, output_path, delay_ms)
    t0 = time.perf_counter()

    bitrate = await probe_audio_bitrate(input_path)
    bitrate_args = ["-b:a", str(bitrate)] if bitrate else []

    process = await asyncio.create_subprocess_exec(
        FFMPEG_PATH,
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_path,
        "-af", f"adelay={delay_ms}:all=1",
        *bitrate_args,
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise SilenceError(stderr.decode(errors="replace").strip())
    logger.info("add_silence: done in %.2fs", time.perf_counter() - t0)
    return output_path


class ConvertError(Exception):
    pass


AUDIO_MAX_BITRATE_KBPS = 192


async def _ffprobe_value(input_path: str, entries: str, select_stream: bool) -> Optional[str]:
    args = [FFPROBE_PATH, "-v", "error"]
    if select_stream:
        args += ["-select_streams", "a:0"]
    args += ["-show_entries", entries, "-of", "default=noprint_wrappers=1:nokey=1", input_path]
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return None
    for line in stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if line and line.lower() != "n/a":
            return line
    return None


async def _extract_pcm_mono(input_path: str, sample_rate: int, seconds: int) -> Optional[np.ndarray]:
    process = await asyncio.create_subprocess_exec(
        FFMPEG_PATH,
        "-v", "error",
        "-t", str(seconds),
        "-i", input_path,
        "-vn", "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0 or not stdout:
        return None
    return np.frombuffer(stdout, dtype="<f4")


def _estimate_cutoff_hz(
    samples: np.ndarray, sample_rate: int,
    threshold_db: float = -94.0, min_occupancy: float = 0.18,
) -> Optional[float]:
    w, h = 2048, 512
    if samples.size < w:
        return None

    hann = np.hanning(w)
    freq_bins = w // 2 + 1
    n_frames = 1 + (samples.size - w) // h

    idx = np.arange(w)[None, :] + (np.arange(n_frames) * h)[:, None]
    frames = samples[idx] * hann[None, :]
    mag = np.abs(np.fft.rfft(frames, axis=1)) / (w / 2.0)
    db = np.clip(20.0 * np.log10(np.maximum(mag, 1e-12)), -120.0, 0.0)
    occupancy = (db > threshold_db).sum(axis=0).astype(np.float64) / n_frames

    smooth = np.empty(freq_bins)
    for b in range(freq_bins):
        b0 = max(b - 3, 0)
        b1 = min(b + 3, freq_bins - 1)
        smooth[b] = occupancy[b0:b1 + 1].mean()

    start_bin = int(round(1000.0 / (sample_rate / 2.0) * (freq_bins - 1)))
    start_bin = min(max(start_bin, 0), freq_bins - 1)

    cutoff_bin = start_bin
    for b in range(start_bin, freq_bins):
        if smooth[b] >= min_occupancy:
            cutoff_bin = b

    refined_bin = cutoff_bin
    for b in range(cutoff_bin, start_bin - 1, -1):
        nxt = min(b + 1, freq_bins - 1)
        if smooth[b] - smooth[nxt] > 0.08:
            refined_bin = b
            break

    return round(refined_bin / (freq_bins - 1) * (sample_rate / 2.0))


def _classify_target_bitrate_kbps(cutoff_hz: float) -> Optional[int]:
    if cutoff_hz <= 16_500.0:
        return 128
    if cutoff_hz <= 18_000.0:
        return 160
    return None


async def convert_audio(src_path: str, output_path: str) -> str:
    logger.info("convert_audio: %s -> %s", src_path, output_path)
    t0 = time.perf_counter()

    sample_rate_raw = await _ffprobe_value(src_path, "stream=sample_rate", True)
    if sample_rate_raw is None or not sample_rate_raw.isdigit():
        raise ConvertError("could not detect sample rate")
    sample_rate = int(sample_rate_raw)
    target_sample_rate = min(sample_rate, 44100)

    cutoff_hz = None
    samples = await _extract_pcm_mono(src_path, target_sample_rate, seconds=30)
    if samples is not None and samples.size >= 2048:
        cutoff_hz = _estimate_cutoff_hz(samples, target_sample_rate)

    if cutoff_hz:
        classified = _classify_target_bitrate_kbps(cutoff_hz)
        target_bitrate = AUDIO_MAX_BITRATE_KBPS if classified is None else min(classified, AUDIO_MAX_BITRATE_KBPS)
        logger.info("convert_audio: cutoff=%.0fHz -> target=%dk", cutoff_hz, target_bitrate)
    else:
        target_bitrate = AUDIO_MAX_BITRATE_KBPS
        logger.info("convert_audio: cutoff=unknown -> target=%dk", target_bitrate)

    process = await asyncio.create_subprocess_exec(
        FFMPEG_PATH,
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-vn",
        "-ar", str(target_sample_rate),
        "-c:a", "libmp3lame",
        "-b:a", f"{target_bitrate}k",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise ConvertError(stderr.decode(errors="replace").strip())
    logger.info("convert_audio: done in %.2fs", time.perf_counter() - t0)
    return output_path
