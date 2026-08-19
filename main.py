import logging
import os
from typing import TYPE_CHECKING, Type

import discord
from colorama import init
from discord.ext import commands, tasks
from dotenv import load_dotenv

from src.console import ColoredFormatter
from src.utils.db import Database

if TYPE_CHECKING:
    from src.exts._common import Cog


class Mon3trBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix="m.",
            strip_after_prefix=True,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions.none(),
            intents=discord.Intents.all(),
            help_command=None,
            owner_id=int(os.environ["OWNER_ID"]),
        )

        self.version = "1.0.0"

        self.prev_update = {}

        self.logger = logging.getLogger("Mon3tr")
        self.db = Database()

    async def on_ready(self) -> None:
        self.logger.info("Mon3tr is ready.")

    async def setup_hook(self) -> None:
        await self.db.connect("data/mon3tr.db")

        await self.load_extension("jishaku")

        for file in os.listdir("src/exts/"):
            if not file.endswith(".py") or file.startswith("_"):
                continue
            name = file[:-3]
            await self.load_extension(f"src.exts.{name}")

        await self.tree.sync()

        self.watch_files.start()

    @tasks.loop(seconds=1)
    async def watch_files(self) -> None:
        try:
            for file in os.listdir("src/exts/"):
                if not file.endswith(".py") or file.startswith("_"):
                    continue
                update_time = os.path.getmtime(f"src/exts/{file}")
                if update_time != self.prev_update.get(file):
                    if self.prev_update.get(file) is not None:
                        self.logger.info("Reloading %s by auto reloading", file)
                        try:
                            await self.reload_extension(f"src.exts.{file[:-3]}")
                        except commands.errors.ExtensionNotLoaded:
                            await self.load_extension(f"src.exts.{file[:-3]}")
                    self.prev_update[file] = update_time
        except Exception as e:
            self.logger.exception(e)

    async def close(self) -> None:
        await super().close()
        await self.db.close()

    def run(self) -> None:
        self.logger.info("Starting Mon3tr...")
        super().run(os.environ["DISCORD_TOKEN"], log_handler=None)

    async def load_cog(self, cog: Type["Cog"]) -> None:
        self.logger.info("Loading cog: %s", cog)
        await self.add_cog(cog(self), override=True)


def setup_logging() -> None:
    file_handler = logging.FileHandler(
        filename="mon3tr.log", encoding="utf-8", mode="w"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(ColoredFormatter(True))

    logging.root.setLevel(logging.DEBUG)
    logging.root.addHandler(console_handler)
    logging.root.addHandler(file_handler)


def main() -> None:
    init(autoreset=True)
    load_dotenv()
    bot = Mon3trBot()

    setup_logging()
    bot.run()


if __name__ == "__main__":
    main()
