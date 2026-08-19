import time
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands
from tabulate import tabulate

from ..utils.api import osu as osu_api
from ..utils.views import NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot

ACCENT_COLOR = discord.Color.from_str("#dce26e")


class MapView(discord.ui.LayoutView):
    def __init__(self, data: dict, stars: float, table: str):
        super().__init__(timeout=None)

        beatmapset = data["beatmapset"]
        length = f"{data['total_length'] // 60}:{str(data['total_length'] % 60).zfill(2)}"
        objects = data["count_circles"] + data["count_sliders"] + data["count_spinners"]

        header = discord.ui.Section(
            discord.ui.TextDisplay(
                f"### [{beatmapset['artist']} - {beatmapset['title']}]({data['url']})\n"
                f"**[{data['version']}]** — Mapped by **{beatmapset['creator']}**"
            ),
            accessory=discord.ui.Thumbnail(beatmapset["covers"]["list@2x"]),
        )

        details = discord.ui.TextDisplay(
            f"**Combo:** x{data['max_combo']}　**Stars:** {stars}★\n"
            f"**Length:** {length}　**BPM:** {data['bpm']}　**Objects:** {objects}\n"
            f"**CS:** {data['cs']}　**AR:** {data['ar']}　**OD:** {data['accuracy']}　"
            f"**HP:** {data['drain']}　**Spinners:** {data['count_spinners']}"
        )

        performance = discord.ui.TextDisplay(f"```\n{table}\n```")

        self.add_item(
            discord.ui.Container(
                header,
                discord.ui.Separator(),
                details,
                discord.ui.Separator(),
                performance,
                accent_color=ACCENT_COLOR,
            )
        )


class CalcView(discord.ui.LayoutView):
    def __init__(self, data: dict, accuracy: float, pp: float, stars: float):
        super().__init__(timeout=None)

        beatmapset = data["beatmapset"]

        header = discord.ui.Section(
            discord.ui.TextDisplay(
                f"### [{beatmapset['artist']} - {beatmapset['title']}]({data['url']})\n"
                f"**[{data['version']}]** — Mapped by **{beatmapset['creator']}**"
            ),
            accessory=discord.ui.Thumbnail(beatmapset["covers"]["list@2x"]),
        )

        result = discord.ui.TextDisplay(
            f"**Accuracy:** {accuracy}%　**PP:** {pp}pp　**Stars:** {stars}★"
        )

        self.add_item(
            discord.ui.Container(
                header,
                discord.ui.Separator(),
                result,
                accent_color=ACCENT_COLOR,
            )
        )


class Beatmap(Cog):
    @commands.hybrid_command(
        name="map",
        description="Displays PP values for an osu! beatmap at 95/97/99/100% accuracy.",
    )
    @app_commands.describe(map="osu! beatmap URL or beatmap ID")
    async def osu_map(self, ctx: commands.Context, map: Optional[str] = None) -> None:
        await self.show_map(ctx, map)

    async def show_map(self, ctx: commands.Context, map_arg: Optional[str]) -> None:
        async with ctx.typing():
            beatmap_id = await osu_api.fetch_beatmap_id_from_args(ctx, map_arg)
            if not beatmap_id:
                await ctx.reply(
                    view=NoticeView("error", "No valid osu! map link or ID found.")
                )
                return

            data, beatmap_path = await osu_api.prepare_beatmap(beatmap_id)

            mode = data["mode_int"]
            accs = [0.95, 0.97, 0.99, 1.0]

            self.logger.info("show_map: starting PP calculation (mode=%d, %d accuracies)", mode, len(accs))
            t0 = time.perf_counter()
            result = await osu_api.calculate_pp(
                beatmap_path, mode, data["max_combo"],
                [{"accuracy": acc, **osu_api.compute_hit_counts(mode, acc, data)} for acc in accs],
            )
            self.logger.info("show_map: PP calculation done in %.2fs", time.perf_counter() - t0)

            headers = ["Acc", "95%", "97%", "99%", "100%"]
            data_row = ["PP"]
            stars = data["difficulty_rating"]

            if result.get("status") == "ok":
                stars = round(result["result"]["stars"], 2)
                for score in result["result"]["scores"]:
                    data_row.append(str(round(score["pp"], 2)))
            else:
                self.logger.error("show_map: calculator returned error: %s", result)
                data_row.extend("N/A" for _ in accs)

            table = tabulate([data_row], headers=headers, tablefmt="presto")

            await ctx.reply(view=MapView(data, stars, table))

    @commands.hybrid_command(
        name="calc",
        description="Calculates PP for an osu! beatmap at a specific accuracy.",
    )
    @app_commands.describe(
        map="osu! beatmap URL or beatmap ID",
        accuracy="Accuracy in percent (e.g. 99.5)",
    )
    async def osu_calc(
        self,
        ctx: commands.Context,
        map: Optional[str] = None,
        accuracy: Optional[float] = None,
    ) -> None:
        async with ctx.typing():
            if accuracy is None and map is not None:
                try:
                    accuracy = float(map)
                    map = None
                except ValueError:
                    pass

            if accuracy is None:
                await ctx.reply(view=NoticeView("error", "Usage: calc [map] <accuracy>"))
                return

            if not (0 < accuracy <= 100):
                await ctx.reply(view=NoticeView("error", "Accuracy must be between 0 and 100."))
                return

            beatmap_id = await osu_api.fetch_beatmap_id_from_args(ctx, map)
            if not beatmap_id:
                await ctx.reply(view=NoticeView("error", "No valid osu! map link or ID found."))
                return

            data, beatmap_path = await osu_api.prepare_beatmap(beatmap_id)

            mode = data["mode_int"]
            acc_ratio = accuracy / 100.0

            self.logger.info("osu_calc: calculating PP (mode=%d, acc=%.4f)", mode, acc_ratio)
            t0 = time.perf_counter()
            result = await osu_api.calculate_pp(
                beatmap_path, mode, data["max_combo"],
                [{"accuracy": acc_ratio, **osu_api.compute_hit_counts(mode, acc_ratio, data)}],
            )
            self.logger.info("osu_calc: PP calculation done in %.2fs", time.perf_counter() - t0)

            if result.get("status") != "ok":
                self.logger.error("osu_calc: calculator returned error: %s", result)
                await ctx.reply(view=NoticeView("error", "PP calculation failed."))
                return

            pp = round(result["result"]["scores"][0]["pp"], 2)
            stars = round(result["result"]["stars"], 2)

            await ctx.reply(view=CalcView(data, accuracy, pp, stars))


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Beatmap)
