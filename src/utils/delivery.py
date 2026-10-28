import logging
import os

import discord
from discord.ext import commands

from . import r2
from .formatting import clean_error
from .views import FileResultView, NoticeView

logger = logging.getLogger("Mon3tr").getChild("utils.delivery")

DM_FILESIZE_LIMIT = 10 * 1024 * 1024


def filesize_limit(ctx: commands.Context) -> int:
    return ctx.guild.filesize_limit if ctx.guild else DM_FILESIZE_LIMIT


async def reply_with_file(
    ctx: commands.Context, path: str, caption: str, *, gallery: bool = False
) -> None:
    """Attach the file, or host it on R2 when it exceeds Discord's upload limit."""
    filename = os.path.basename(path)
    if os.path.getsize(path) <= filesize_limit(ctx):
        await ctx.reply(
            view=FileResultView(filename, caption, gallery=gallery),
            file=discord.File(path, filename=filename),
        )
        return

    if not r2.is_configured():
        await ctx.reply(view=NoticeView("error", "The file is too large to upload."))
        return

    try:
        url = await r2.upload(path, filename)
    except Exception as e:
        logger.error("R2 upload failed: %s", clean_error(e))
        await ctx.reply(view=NoticeView("error", f"Upload failed: {clean_error(e)}"))
        return
    await ctx.reply(view=FileResultView(filename, caption, url=url, gallery=gallery))
