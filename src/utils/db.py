import json
import logging
import os
from datetime import UTC, datetime
from typing import Dict, List, Optional

import aiosqlite

logger = logging.getLogger("Mon3tr").getChild("utils.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS osumod_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    notify_bn_queue INTEGER NOT NULL DEFAULT 1,
    notify_modder_queue INTEGER NOT NULL DEFAULT 1,
    notify_status INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(channel_id, mode)
);
CREATE TABLE IF NOT EXISTS osumod_queue_state (
    owner_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    modder_type TEXT NOT NULL,
    modes TEXT NOT NULL,
    open INTEGER NOT NULL,
    last_actioned TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS osumod_request_state (
    request_id TEXT PRIMARY KEY,
    target_username TEXT NOT NULL,
    status TEXT NOT NULL,
    feedback TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS osumod_status_messages (
    request_id TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (request_id, channel_id)
);
CREATE TABLE IF NOT EXISTS osu_links (
    discord_id INTEGER PRIMARY KEY,
    osu_id INTEGER NOT NULL,
    osu_username TEXT NOT NULL,
    notify TEXT NOT NULL DEFAULT 'both',
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self) -> None:
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self, path: str) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()
        logger.info("connected to %s", path)

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA table_info(osumod_queue_state)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "last_actioned" not in columns:
            await self.conn.execute(
                "ALTER TABLE osumod_queue_state ADD COLUMN last_actioned TEXT NOT NULL DEFAULT ''"
            )

        async with self.conn.execute("PRAGMA table_info(osumod_request_state)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "feedback" not in columns:
            await self.conn.execute(
                "ALTER TABLE osumod_request_state ADD COLUMN feedback TEXT NOT NULL DEFAULT ''"
            )
            await self.conn.execute("DELETE FROM osumod_request_state")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        return self._conn

    async def add_osumod_subscription(
        self,
        guild_id: Optional[int],
        channel_id: int,
        mode: str,
        notify_bn_queue: bool,
        notify_modder_queue: bool,
        notify_status: bool,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO osumod_subscriptions
                (guild_id, channel_id, mode, notify_bn_queue, notify_modder_queue, notify_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, mode) DO UPDATE SET
                notify_bn_queue = excluded.notify_bn_queue,
                notify_modder_queue = excluded.notify_modder_queue,
                notify_status = excluded.notify_status
            """,
            (
                guild_id,
                channel_id,
                mode,
                int(notify_bn_queue),
                int(notify_modder_queue),
                int(notify_status),
                datetime.now(UTC).isoformat(),
            ),
        )
        await self.conn.commit()

    async def remove_osumod_subscriptions(
        self, channel_id: int, mode: Optional[str] = None
    ) -> int:
        if mode is None:
            cursor = await self.conn.execute(
                "DELETE FROM osumod_subscriptions WHERE channel_id = ?", (channel_id,)
            )
        else:
            cursor = await self.conn.execute(
                "DELETE FROM osumod_subscriptions WHERE channel_id = ? AND mode = ?",
                (channel_id, mode),
            )
        await self.conn.commit()
        return cursor.rowcount

    async def get_osumod_subscriptions(self) -> List[dict]:
        async with self.conn.execute("SELECT * FROM osumod_subscriptions") as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_osumod_subscriptions_for_guild(self, guild_id: int) -> List[dict]:
        async with self.conn.execute(
            "SELECT * FROM osumod_subscriptions WHERE guild_id = ? ORDER BY channel_id, mode",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def remove_osumod_guild(self, guild_id: int) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM osumod_subscriptions WHERE guild_id = ?", (guild_id,)
        )
        await self.conn.commit()
        return cursor.rowcount

    async def get_osumod_status_message(
        self, request_id: str, channel_id: int
    ) -> Optional[int]:
        async with self.conn.execute(
            "SELECT message_id FROM osumod_status_messages WHERE request_id = ? AND channel_id = ?",
            (request_id, channel_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row["message_id"] if row else None

    async def set_osumod_status_message(
        self, request_id: str, channel_id: int, message_id: int
    ) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO osumod_status_messages VALUES (?, ?, ?)",
            (request_id, channel_id, message_id),
        )
        await self.conn.commit()

    async def cleanup_osumod_status_messages(self) -> None:
        await self.conn.execute(
            "DELETE FROM osumod_status_messages WHERE request_id NOT IN"
            " (SELECT request_id FROM osumod_request_state)"
        )
        await self.conn.commit()

    async def set_osu_link(
        self, discord_id: int, osu_id: int, osu_username: str, notify: str
    ) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO osu_links VALUES (?, ?, ?, ?, ?)",
            (discord_id, osu_id, osu_username, notify, datetime.now(UTC).isoformat()),
        )
        await self.conn.commit()

    async def remove_osu_link(self, discord_id: int) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM osu_links WHERE discord_id = ?", (discord_id,)
        )
        await self.conn.commit()
        return cursor.rowcount

    async def get_osu_link(self, discord_id: int) -> Optional[dict]:
        async with self.conn.execute(
            "SELECT * FROM osu_links WHERE discord_id = ?", (discord_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_osu_links_for_osu_ids(self, osu_ids: List[int]) -> List[dict]:
        if not osu_ids:
            return []
        placeholders = ",".join("?" * len(osu_ids))
        async with self.conn.execute(
            f"SELECT * FROM osu_links WHERE osu_id IN ({placeholders})", osu_ids
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_osumod_queue_state(self) -> Dict[str, dict]:
        async with self.conn.execute("SELECT * FROM osumod_queue_state") as cursor:
            rows = await cursor.fetchall()
        return {
            row["owner_id"]: {
                "username": row["username"],
                "modder_type": row["modder_type"],
                "modes": json.loads(row["modes"]),
                "open": bool(row["open"]),
                "last_actioned": row["last_actioned"],
            }
            for row in rows
        }

    async def replace_osumod_queue_state(self, queues: List[dict]) -> None:
        rows = [
            (
                q["owner"]["_id"],
                q["owner"]["username"],
                q.get("modderType", ""),
                json.dumps(q.get("modes", [])),
                int(bool(q.get("open"))),
                q.get("lastActionedDate", "") or "",
            )
            for q in queues
        ]
        await self.conn.execute("DELETE FROM osumod_queue_state")
        await self.conn.executemany(
            "INSERT OR REPLACE INTO osumod_queue_state VALUES (?, ?, ?, ?, ?, ?)", rows
        )
        await self.conn.commit()

    async def get_osumod_request_state(self) -> Dict[str, dict]:
        async with self.conn.execute("SELECT * FROM osumod_request_state") as cursor:
            rows = await cursor.fetchall()
        return {
            row["request_id"]: {
                "target_username": row["target_username"],
                "status": row["status"],
                "feedback": row["feedback"],
            }
            for row in rows
        }

    async def update_osumod_request_state(
        self, requests_map: Dict[str, List[dict]]
    ) -> None:
        usernames = list(requests_map.keys())
        if not usernames:
            return
        placeholders = ",".join("?" * len(usernames))
        await self.conn.execute(
            f"DELETE FROM osumod_request_state WHERE target_username IN ({placeholders})",
            usernames,
        )
        rows = [
            (r["_id"], username, r.get("status", ""), (r.get("feedback") or "").strip())
            for username, requests in requests_map.items()
            for r in requests
        ]
        await self.conn.executemany(
            "INSERT OR REPLACE INTO osumod_request_state VALUES (?, ?, ?, ?)", rows
        )
        await self.conn.commit()

    async def prune_osumod_request_state(self, keep_usernames: List[str]) -> None:
        if not keep_usernames:
            await self.conn.execute("DELETE FROM osumod_request_state")
        else:
            placeholders = ",".join("?" * len(keep_usernames))
            await self.conn.execute(
                f"DELETE FROM osumod_request_state WHERE target_username NOT IN ({placeholders})",
                keep_usernames,
            )
        await self.conn.commit()
