import asyncio
import json
import logging
import os
import re
import time
from typing import Tuple, Union
from urllib.parse import quote

import aiohttp
from discord.ext import commands

logger = logging.getLogger("Mon3tr").getChild("utils.api.osu")

CALCULATOR_PATH = os.path.abspath(os.path.join(
    "calculator", "Calculator", "bin", "Release", "net8.0", "win-x64", "publish", "calculator.exe"
))
BEATMAP_DIR = os.path.abspath("data/beatmaps")
API_BASE = "https://osu.ppy.sh/api/v2/"

OSU_URL_MAP_NEW_MATCHER = r"https:\/\/osu.ppy.sh\/beatmapsets\/(\d+)(?:(?:\/?#(?:osu|mania|taiko|fruits)|<#\d+>)\/(\d+))?"
OSU_URL_MAP_OLD_MATCHER = r"https://osu.ppy.sh/b(?:eatmaps)?/(\d+)"


def get_osu_map_id(text: str) -> Union[re.Match, None]:
    return re.search(OSU_URL_MAP_OLD_MATCHER, text) or re.search(
        OSU_URL_MAP_NEW_MATCHER, text
    )


def resolve_beatmap_id(text: str) -> Union[str, None]:
    text = text.strip()

    if text.isdigit():
        return text

    m = re.match(OSU_URL_MAP_OLD_MATCHER, text)
    if m:
        return m.group(1)

    m = re.match(OSU_URL_MAP_NEW_MATCHER, text)
    if m and m.group(2):
        return m.group(2)

    return None


async def get_osu_token(session: aiohttp.ClientSession) -> dict:
    body = {
        "client_id": os.environ["API_CLIENT_ID_OSU"],
        "client_secret": os.environ["API_CLIENT_SECRET_OSU"],
        "grant_type": "client_credentials",
        "scope": "public",
    }
    async with session.post("https://osu.ppy.sh/oauth/token", json=body) as res:
        return await res.json()


async def get_osu_beatmap(beatmap_id: str) -> dict:
    async with aiohttp.ClientSession() as session:
        token = await get_osu_token(session)
        headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Accept": "application/json",
        }
        async with session.get(
            f"{API_BASE}beatmaps/{beatmap_id}", headers=headers
        ) as response:
            return await response.json()


async def get_osu_user(username: str) -> Union[dict, None]:
    async with aiohttp.ClientSession() as session:
        token = await get_osu_token(session)
        headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Accept": "application/json",
        }
        url = f"{API_BASE}users/{quote(username, safe='')}?key=username"
        async with session.get(url, headers=headers) as response:
            if response.status == 404:
                return None
            response.raise_for_status()
            return await response.json()


async def fetch_beatmap_id_from_args(
    ctx: commands.Context, map_arg: Union[str, None]
) -> Union[str, None]:
    if map_arg:
        return resolve_beatmap_id(map_arg)
    async for msg in ctx.channel.history(limit=50):
        match = get_osu_map_id(msg.content)
        if match:
            return resolve_beatmap_id(match.group(0))
    return None


async def download_beatmap(beatmap_id: int) -> str:
    os.makedirs(BEATMAP_DIR, exist_ok=True)
    beatmap_path = os.path.join(BEATMAP_DIR, f"{beatmap_id}.osu")
    logger.info("download_beatmap: downloading id=%s -> %s", beatmap_id, beatmap_path)
    t0 = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://osu.ppy.sh/osu/{beatmap_id}") as res:
            with open(beatmap_path, "wb") as f:
                async for chunk in res.content.iter_chunked(8192):
                    f.write(chunk)
    logger.info("download_beatmap: done in %.2fs", time.perf_counter() - t0)
    return beatmap_path


async def prepare_beatmap(beatmap_id: str) -> Tuple[dict, str]:
    logger.info("prepare_beatmap: fetching beatmap data (id=%s)", beatmap_id)
    t0 = time.perf_counter()
    data = await get_osu_beatmap(beatmap_id)
    logger.info("prepare_beatmap: beatmap API done in %.2fs", time.perf_counter() - t0)
    beatmap_path = await download_beatmap(data["id"])
    return data, beatmap_path


def compute_hit_counts(mode: int, accuracy: float, data: dict) -> dict:
    n_circles = data["count_circles"]
    n_sliders = data["count_sliders"]
    n_spinners = data["count_spinners"]
    total = n_circles + n_sliders + n_spinners

    if mode == 0:
        n300 = min(total, max(0, round(total * (3 * accuracy - 1) / 2)))
        n100 = total - n300
        logger.debug(
            "compute_hit_counts: mode=0 acc=%.2f total=%d n300=%d n100=%d",
            accuracy, total, n300, n100,
        )
        return {"ngeki": 0, "n300": n300, "nkatu": 0, "n100": n100, "n50": 0, "nmiss": 0}

    elif mode == 1:
        n300 = min(total, max(0, round(total * (2 * accuracy - 1))))
        n100 = total - n300
        logger.debug(
            "compute_hit_counts: mode=1 acc=%.2f total=%d n300=%d n100=%d",
            accuracy, total, n300, n100,
        )
        return {"ngeki": 0, "n300": n300, "nkatu": 0, "n100": n100, "n50": 0, "nmiss": 0}

    elif mode == 2:
        catchable = n_circles + n_sliders
        n300 = n_circles
        n100 = n_sliders
        nkatu = 0 if accuracy >= 1.0 else max(0, round(catchable * (1.0 / accuracy - 1)))
        logger.debug(
            "compute_hit_counts: mode=2 acc=%.2f n300=%d n100=%d nkatu=%d",
            accuracy, n300, n100, nkatu,
        )
        return {"ngeki": 0, "n300": n300, "nkatu": nkatu, "n100": n100, "n50": 0, "nmiss": 0}

    elif mode == 3:
        ngeki = min(total, max(0, round(total * (3 * accuracy - 2))))
        nkatu = total - ngeki
        logger.debug(
            "compute_hit_counts: mode=3 acc=%.2f total=%d ngeki=%d nkatu=%d",
            accuracy, total, ngeki, nkatu,
        )
        return {"ngeki": ngeki, "n300": 0, "nkatu": nkatu, "n100": 0, "n50": 0, "nmiss": 0}

    return {"ngeki": 0, "n300": 0, "nkatu": 0, "n100": 0, "n50": 0, "nmiss": 0}


async def calculate_pp(
    beatmap_path: str, mode: int, combo: int, scores: list
) -> dict:
    args_json = json.dumps({
        "file": beatmap_path,
        "mode": mode,
        "mods": 0,
        "combo": combo,
        "scores": scores,
    })
    logger.debug("calculate_pp: launching calculator (mode=%d, %d score(s), combo=%d)",
                 mode, len(scores), combo)
    process = await asyncio.create_subprocess_exec(
        CALCULATOR_PATH,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(input=args_json.encode())
    if stderr:
        logger.warning("calculator stderr: %s", stderr.decode().strip())
    logger.debug("calculate_pp: done, exit=%d", process.returncode)
    return json.loads(stdout.decode())
