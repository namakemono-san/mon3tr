# Mon3tr

A general-purpose and powerful Discord bot with osu! features related to mapping & modding  
inspired by [AxerBot](https://github.com/Hiviexd/AxerBot).

> [!CAUTION]
> This program was created for educational purposes. Please note that continuous maintenance will not be provided.
> Additionally, running this on a self-hosted server is not recommended as it may place a heavy load on osumod.

## Features

- **osu!** — `map`/`calc` show PP for a beatmap using a bundled C# PP calculator (osu!, taiko, catch, mania).
- **Media download** — `download video`/`download audio` fetch media via yt-dlp, with an osu!taiko background resize option.
- **Tools** — `addsilence`/`normalize` post-process mp3/ogg attachments with ffmpeg.
- **osumod** — polls [osumod.com](https://osumod.com) and notifies subscribed channels (and linked users) about BN/modder queue changes and request status updates.
- **link/unlink** — link a Discord account to an osu! username to receive personal osumod notifications.

Run `m.help` (or `/help`) in Discord for the full command list.

## Requirements

- [Python](https://www.python.org/) 3.13+ and [Poetry](https://python-poetry.org/)
- [.NET SDK](https://dotnet.microsoft.com/) 8.0 (to build the PP calculator)
- [ffmpeg](https://ffmpeg.org/) and ffprobe on `PATH` (or set `FFMPEG_PATH`/`FFPROBE_PATH`)
- A Discord bot application and token
- An osu! OAuth client (client credentials grant) from your [osu! account settings](https://osu.ppy.sh/home/account/edit)

## Setup

1. Install Python dependencies:

   ```sh
   poetry install
   ```

2. Build the PP calculator:

   ```sh
   dotnet publish calculator/Calculator -c Release -r win-x64
   ```

   This produces `calculator/Calculator/bin/Release/net8.0/win-x64/publish/calculator.exe`, which the bot invokes directly.

3. Copy `.env.example` to `.env` and fill in the required values:

   ```sh
   cp .env.example .env
   ```

4. Run the bot:

   ```sh
   poetry run python main.py
   ```

The SQLite database and downloaded beatmaps are stored under `data/`, created automatically on first run.

## License

MIT — see [LICENSE](LICENSE).
