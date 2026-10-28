# Mon3tr

A Discord bot with osu! features related to mapping & modding,
inspired by [AxerBot](https://github.com/Hiviexd/AxerBot).

> [!CAUTION]
> This program was created for educational purposes. Please note that continuous maintenance will not be provided.  
> Additionally, running this on a self-hosted server is not recommended as it may place a heavy load on osumod.

## Commands

Every command works both as a slash command and with the `m.` prefix.

| Command | Description |
|---|---|
| `osumod subscribe <mode> [bn_queue] [modder_queue] [status]` | Subscribe this channel to [osumod](https://osumod.com) notifications for a game mode (Manage Server). All types are enabled by default. |
| `osumod unsubscribe [mode]` | Remove this channel's subscriptions (Manage Server). |
| `osumod list` | List the subscriptions in this server. |
| `link [osu_username] [notify]` | Link your osu! account to be notified (DM and/or mention) when your requests change. |
| `unlink` | Remove your osu! account link. |
| `download video <url> [taiko_resize]` | Download a video with yt-dlp, optionally resized to a 1280x720 osu!taiko background. |
| `download audio <url>` | Download audio as MP3. |
| `addsilence <audio> <duration>` | Add leading silence to an mp3/ogg attachment (e.g. `1000`, `1000ms`, `1s`). |
| `normalize <audio>` | Apply loudness normalization to an mp3/ogg attachment. |

Files larger than Discord's upload limit are hosted on Cloudflare R2 when it is configured.

### osumod notifications

The bot polls osumod every 30 seconds and reports:

- BN and modder queues opening or closing
- request status changes (to pending, accepted or rejected, and pending/rejected → modded)
- feedback added to or edited on a request

Notifications are stored before they are sent, so a temporary Discord outage delays them
instead of losing them. Updates to the same request are posted as replies to its previous
notification.

## Requirements

- [Python](https://www.python.org/) 3.13+ and [Poetry](https://python-poetry.org/)
- [ffmpeg](https://ffmpeg.org/) and ffprobe on `PATH` (or set `FFMPEG_PATH`/`FFPROBE_PATH`)
- A Discord bot application with the **Message Content** and **Server Members** privileged intents enabled
- An osu! OAuth client (client credentials grant) from your [osu! account settings](https://osu.ppy.sh/home/account/edit)

## Setup

1. Install dependencies:

   ```sh
   poetry install
   ```

2. Copy `.env.example` to `.env` and fill in the required values:

   ```sh
   cp .env.example .env
   ```

3. Run the bot:

   ```sh
   poetry run python main.py
   ```

The SQLite database is stored under `data/` and rotating logs under `logs/`, both created on first run.

## License

MIT — see [LICENSE](LICENSE).
