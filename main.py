import logging
import logging.handlers
import shutil
import sys
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from src.config import DATA_DIR, LOG_DIR, ROOT, ConfigError, Settings, get_settings
from src.utils.db import Database

if TYPE_CHECKING:
    from src.exts._common import Cog

EXTENSIONS_DIR = ROOT / "src" / "exts"

logger = logging.getLogger("Mon3tr")


class Mon3trBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # prefix commands
        intents.members = True  # mention routing for osumod notifications

        super().__init__(
            command_prefix="m.",
            strip_after_prefix=True,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions.none(),
            intents=intents,
            help_command=None,
            owner_id=settings.owner_id,
        )
        self.settings = settings
        self.db = Database()

    async def setup_hook(self) -> None:
        await self.db.connect(DATA_DIR / "mon3tr.db")

        await self.load_extension("jishaku")
        for path in sorted(EXTENSIONS_DIR.glob("*.py")):
            if not path.name.startswith("_"):
                await self.load_extension(f"src.exts.{path.stem}")

        synced = await self.tree.sync()
        logger.info("synced %d application command(s)", len(synced))

    async def on_ready(self) -> None:
        logger.info("logged in as %s (%s)", self.user, self.user.id)

    async def close(self) -> None:
        await super().close()
        await self.db.close()

    async def load_cog(self, cog: type["Cog"]) -> None:
        await self.add_cog(cog(self))
        logger.info("loaded cog %s", cog.__name__)


def setup_logging() -> None:
    discord.utils.setup_logging(level=logging.INFO)

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "mon3tr.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"
    ))
    logging.getLogger().addHandler(file_handler)

    # Transfer logs include local paths, object keys and signed requests.
    for name in ("boto3", "botocore", "s3transfer"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    try:
        settings = get_settings()
    except ConfigError as e:
        logger.critical("%s (see .env.example)", e)
        sys.exit(1)

    for tool in (settings.ffmpeg_path, settings.ffprobe_path):
        if shutil.which(tool) is None:
            logger.warning("%s not found; download and audio tools will fail", tool)

    Mon3trBot(settings).run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
