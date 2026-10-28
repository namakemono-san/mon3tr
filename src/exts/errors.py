import contextlib
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..utils.views import NoticeView
from ._common import Cog

if TYPE_CHECKING:
    from main import Mon3trBot


def _unwrap(error: Exception) -> Exception:
    # Hybrid and invoke errors wrap the original exception, sometimes twice.
    wrappers = (
        commands.CommandInvokeError,
        commands.HybridCommandError,
        app_commands.CommandInvokeError,
    )
    while isinstance(error, wrappers):
        error = error.original
    return error


def _usage(ctx: commands.Context) -> str:
    parts = [f"{ctx.clean_prefix}{ctx.command.qualified_name}", ctx.command.signature]
    return f"`{' '.join(part for part in parts if part)}`"


def _permissions(names: list[str]) -> str:
    return ", ".join(n.replace("_", " ").title().replace("Guild", "Server") for n in names)


def describe(ctx: commands.Context, error: Exception) -> str | None:
    """A user-facing message for expected errors, or None for unexpected ones."""
    if isinstance(error, commands.MissingRequiredArgument | commands.MissingRequiredAttachment):
        return f"Missing argument `{error.param.name}`. Usage: {_usage(ctx)}"
    if isinstance(error, commands.BadArgument | commands.BadUnionArgument):
        return f"Invalid argument. Usage: {_usage(ctx)}"
    if isinstance(error, commands.TooManyArguments):
        return f"Too many arguments. Usage: {_usage(ctx)}"
    if isinstance(error, commands.NoPrivateMessage | app_commands.NoPrivateMessage):
        return "This command can only be used in a server."
    if isinstance(error, commands.MissingPermissions | app_commands.MissingPermissions):
        return f"You need the following permission(s): {_permissions(error.missing_permissions)}."
    if isinstance(error, commands.BotMissingPermissions | app_commands.BotMissingPermissions):
        return f"I need the following permission(s): {_permissions(error.missing_permissions)}."
    if isinstance(error, commands.MaxConcurrencyReached):
        return "You already have this command running. Wait for it to finish."
    if isinstance(error, commands.CommandOnCooldown | app_commands.CommandOnCooldown):
        return f"This command is on cooldown. Try again in {error.retry_after:.0f}s."
    if isinstance(error, commands.CheckFailure | app_commands.CheckFailure):
        return "You can't use this command."
    if isinstance(error, discord.Forbidden):
        return "I don't have permission to do that here."
    return None


class Errors(Cog):
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.CommandNotFound | commands.DisabledCommand):
            return

        error = _unwrap(error)
        message = describe(ctx, error)
        if message is None:
            self.logger.error(
                "unhandled error in %s", ctx.command,
                exc_info=(type(error), error, error.__traceback__),
            )
            message = "An unexpected error occurred."

        with contextlib.suppress(discord.HTTPException):
            await ctx.reply(view=NoticeView("error", message), ephemeral=True)


async def setup(bot: "Mon3trBot") -> None:
    await bot.load_cog(Errors)
