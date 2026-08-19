from typing import Optional

import discord

NOTICE_STYLES = {
    "error": ("Error", discord.Color.red()),
    "success": ("Success", discord.Color.green()),
    "info": ("Info", discord.Color.blue()),
    "warning": ("Warning", discord.Color.yellow()),
}


class DetailNoticeView(discord.ui.LayoutView):
    def __init__(
        self,
        tag: str,
        lines: list,
        thumbnail: Optional[str] = None,
    ):
        super().__init__(timeout=None)

        if tag not in NOTICE_STYLES:
            raise KeyError("A non-existent tag was specified.")

        label, color = NOTICE_STYLES[tag]
        text = "\n".join([f"### {label}", *lines])

        if thumbnail:
            body = discord.ui.Section(
                discord.ui.TextDisplay(text),
                accessory=discord.ui.Thumbnail(thumbnail),
            )
        else:
            body = discord.ui.TextDisplay(text)

        self.add_item(discord.ui.Container(body, accent_color=color))


class NoticeView(discord.ui.LayoutView):
    def __init__(self, tag: str, content: str):
        super().__init__(timeout=None)

        if tag not in NOTICE_STYLES:
            raise KeyError("A non-existent tag was specified.")

        label, color = NOTICE_STYLES[tag]
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"**{label}** — {content}"),
                accent_color=color,
            )
        )
