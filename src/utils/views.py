
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
        thumbnail: str | None = None,
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


class FileResultView(discord.ui.LayoutView):
    def __init__(
        self,
        filename: str,
        caption: str,
        url: str | None = None,
        gallery: bool = False,
    ):
        super().__init__(timeout=None)

        source = url or f"attachment://{filename}"
        footer = discord.ui.TextDisplay(f"-# {caption}")
        if gallery:
            items = [
                discord.ui.MediaGallery(discord.MediaGalleryItem(source)),
                discord.ui.Separator(),
                footer,
            ]
        elif url:
            items = [discord.ui.TextDisplay(f"{url}\n-# {caption}")]
        else:
            items = [discord.ui.File(source), discord.ui.Separator(), footer]

        self.add_item(discord.ui.Container(*items))
