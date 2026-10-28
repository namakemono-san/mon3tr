import asyncio
import logging
import os
import time

import numpy as np

from ..config import get_settings

logger = logging.getLogger("Mon3tr").getChild("utils.ffmpeg")

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

DOWNLOAD_MAX_BITRATE_KBPS = 192
DOWNLOAD_MAX_SAMPLE_RATE = 44100


class FFmpegError(Exception):
    pass


async def _run(label: str, *args: str) -> bytes:
    t0 = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        get_settings().ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise FFmpegError(stderr.decode(errors="replace").strip())
    logger.info("%s: done in %.2fs", label, time.perf_counter() - t0)
    return stdout


async def _probe_sample_rate(path: str) -> int:
    process = await asyncio.create_subprocess_exec(
        get_settings().ffprobe_path, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    value = next(iter(stdout.decode(errors="replace").split()), "")
    if process.returncode != 0 or not value.isdigit() or int(value) <= 0:
        raise FFmpegError("could not detect audio sample rate")
    return int(value)


def _encoder_args(output_path: str) -> list[str]:
    # Highest-quality VBR so re-encoding after a filter loses as little as possible.
    ext = os.path.splitext(output_path)[1].lower()
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-q:a", "0", "-compression_level", "0"]
    if ext == ".ogg":
        return ["-c:a", "libvorbis", "-q:a", "10"]
    raise FFmpegError(f"unsupported audio format: {ext}")


async def _filter_audio(label: str, input_path: str, output_path: str, audio_filter: str) -> None:
    # loudnorm resamples to 192 kHz internally, so restore the input rate explicitly.
    sample_rate = await _probe_sample_rate(input_path)
    await _run(
        label,
        "-i", input_path,
        "-map", "0:a:0", "-vn",
        "-af", audio_filter,
        *_encoder_args(output_path),
        "-ar", str(sample_rate),
        output_path,
    )


async def normalize_loudness(input_path: str, output_path: str) -> None:
    await _filter_audio("normalize_loudness", input_path, output_path, LOUDNORM_FILTER)


async def add_silence(input_path: str, output_path: str, delay_ms: int) -> None:
    await _filter_audio("add_silence", input_path, output_path, f"adelay={delay_ms}:all=1")


async def taiko_resize(input_path: str, output_path: str) -> None:
    await _run(
        "taiko_resize",
        "-i", input_path,
        "-f", "lavfi", "-i", "color=black:s=1280x720:d=1",
        "-filter_complex", TAIKO_FILTER,
        "-map", "[output]",
        "-aspect", "1280:720",
        "-b:v", "800K",
        output_path,
    )


async def _read_pcm_mono(path: str, sample_rate: int, seconds: int) -> np.ndarray | None:
    try:
        stdout = await _run(
            "read_pcm_mono",
            "-t", str(seconds),
            "-i", path,
            "-vn", "-ac", "1",
            "-ar", str(sample_rate),
            "-f", "f32le", "pipe:1",
        )
    except FFmpegError:
        return None
    return np.frombuffer(stdout, dtype="<f4") if stdout else None


def _estimate_cutoff_hz(
    samples: np.ndarray, sample_rate: int,
    threshold_db: float = -94.0, min_occupancy: float = 0.18,
) -> float | None:
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


def _target_bitrate_kbps(cutoff_hz: float | None) -> int:
    # Sources whose spectrum is already band-limited gain nothing from a high bitrate.
    if cutoff_hz is not None and cutoff_hz <= 16_500.0:
        return 128
    if cutoff_hz is not None and cutoff_hz <= 18_000.0:
        return 160
    return DOWNLOAD_MAX_BITRATE_KBPS


async def convert_to_mp3(input_path: str, output_path: str) -> None:
    sample_rate = min(await _probe_sample_rate(input_path), DOWNLOAD_MAX_SAMPLE_RATE)

    cutoff_hz = None
    samples = await _read_pcm_mono(input_path, sample_rate, seconds=30)
    if samples is not None:
        cutoff_hz = _estimate_cutoff_hz(samples, sample_rate)

    bitrate = _target_bitrate_kbps(cutoff_hz)
    logger.info("convert_to_mp3: cutoff=%sHz -> %dk", cutoff_hz or "unknown", bitrate)

    await _run(
        "convert_to_mp3",
        "-i", input_path,
        "-vn",
        "-ar", str(sample_rate),
        "-c:a", "libmp3lame",
        "-b:a", f"{bitrate}k",
        output_path,
    )
