import logging
from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from main import Mon3trBot


class Cog(commands.Cog):
    def __init__(self, bot: "Mon3trBot"):
        self.bot = bot
        self.logger = logging.getLogger("Mon3tr").getChild(
            "exts." + self.__class__.__name__
        )
