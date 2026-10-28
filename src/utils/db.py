import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger("Mon3tr").getChild("utils.db")

SCHEMA_VERSION = 2

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
-- Last observed open/closed state of every queue in the subscribed modes.
CREATE TABLE IF NOT EXISTS osumod_queue_state (
    owner_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    kind TEXT NOT NULL,
    modes TEXT NOT NULL,
    open INTEGER NOT NULL,
    last_activity TEXT NOT NULL DEFAULT ''
);
-- One row per BN whose requests have been fetched and stored successfully.
-- Its presence marks the BN as seeded; last_activity is the queue activity
-- that was synced, checked_at (unix time) drives periodic re-checks.
CREATE TABLE IF NOT EXISTS osumod_bn_sync (
    osu_id INTEGER PRIMARY KEY,
    last_activity TEXT NOT NULL,
    checked_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS osumod_request_state (
    request_id TEXT PRIMARY KEY,
    bn_osu_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    feedback TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS osumod_request_state_bn ON osumod_request_state (bn_osu_id);
-- Latest notification per request and channel, so updates reply to it.
CREATE TABLE IF NOT EXISTS osumod_status_messages (
    request_id TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (request_id, channel_id)
);
-- Notifications waiting to be delivered. Rows are written together with the state
-- that produced them and deleted once sent, so a failed send is retried.
CREATE TABLE IF NOT EXISTS osumod_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS osu_links (
    discord_id INTEGER PRIMARY KEY,
    osu_id INTEGER NOT NULL,
    osu_username TEXT NOT NULL,
    notify TEXT NOT NULL DEFAULT 'both',
    created_at TEXT NOT NULL
);
"""


def _placeholders(values: list) -> str:
    return ",".join("?" * len(values))


class Database:
    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected.")
        return self._conn

    async def connect(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._migrate()
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info("connected to %s", path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        if version >= SCHEMA_VERSION:
            return

        # Cached osumod state is disposable: dropping it makes the next poll reseed
        # silently instead of reporting every stored request as changed.
        for table in (
            "osumod_queue_state",
            "osumod_request_state",
            "osumod_request_sync",
            "osumod_bn_sync",
            "meta",
        ):
            await self.conn.execute(f"DROP TABLE IF EXISTS {table}")

        if version < 1:
            # osumod API rewrite (2026-09) switched to lowercase mode names.
            await self.conn.executescript(SCHEMA)
            legacy_modes = {
                "Standard": "osu",
                "Taiko": "taiko",
                "Catch the Beat": "fruits",
                "Mania": "mania",
            }
            for old, new in legacy_modes.items():
                await self.conn.execute(
                    "UPDATE osumod_subscriptions SET mode = ? WHERE mode = ?", (new, old)
                )

        await self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        logger.info("migrated database from version %d to %d", version, SCHEMA_VERSION)

    # --- osumod subscriptions -------------------------------------------------

    async def add_subscription(
        self,
        guild_id: int,
        channel_id: int,
        mode: str,
        notify_bn_queue: bool,
        notify_modder_queue: bool,
        notify_status: bool,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO osumod_subscriptions
                (guild_id, channel_id, mode,
                 notify_bn_queue, notify_modder_queue, notify_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, mode) DO UPDATE SET
                notify_bn_queue = excluded.notify_bn_queue,
                notify_modder_queue = excluded.notify_modder_queue,
                notify_status = excluded.notify_status
            """,
            (
                guild_id, channel_id, mode,
                int(notify_bn_queue), int(notify_modder_queue), int(notify_status),
                datetime.now(UTC).isoformat(),
            ),
        )
        await self.conn.commit()

    async def remove_subscriptions(self, channel_id: int, mode: str | None = None) -> int:
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

    async def remove_guild_subscriptions(self, guild_id: int) -> int:
        cursor = await self.conn.execute(
            "DELETE FROM osumod_subscriptions WHERE guild_id = ?", (guild_id,)
        )
        await self.conn.commit()
        return cursor.rowcount

    async def get_subscriptions(self, guild_id: int | None = None) -> list[dict]:
        if guild_id is None:
            query, params = "SELECT * FROM osumod_subscriptions ORDER BY id", ()
        else:
            query = (
                "SELECT * FROM osumod_subscriptions WHERE guild_id = ?"
                " ORDER BY channel_id, mode"
            )
            params = (guild_id,)
        async with self.conn.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # --- osumod polling state -------------------------------------------------

    async def get_queue_state(self) -> dict[str, dict]:
        async with self.conn.execute("SELECT * FROM osumod_queue_state") as cursor:
            rows = await cursor.fetchall()
        return {
            row["owner_id"]: {
                "username": row["username"],
                "kind": row["kind"],
                "modes": json.loads(row["modes"]),
                "open": bool(row["open"]),
                "last_activity": row["last_activity"],
            }
            for row in rows
        }

    async def get_bn_sync(self) -> dict[int, dict]:
        async with self.conn.execute("SELECT * FROM osumod_bn_sync") as cursor:
            rows = await cursor.fetchall()
        return {
            row["osu_id"]: {"last_activity": row["last_activity"], "checked_at": row["checked_at"]}
            for row in rows
        }

    async def get_request_state(self, bn_osu_ids: list[int]) -> dict[str, dict]:
        if not bn_osu_ids:
            return {}
        async with self.conn.execute(
            "SELECT * FROM osumod_request_state"
            f" WHERE bn_osu_id IN ({_placeholders(bn_osu_ids)})",
            bn_osu_ids,
        ) as cursor:
            rows = await cursor.fetchall()
        return {
            row["request_id"]: {"status": row["status"], "feedback": row["feedback"]}
            for row in rows
        }

    async def save_poll(
        self,
        queues: list[dict],
        fetched: dict[int, list[dict]],
        synced_activity: dict[int, str],
        monitored: list[int],
        notifications: list[dict],
    ) -> None:
        """Persist one poll cycle atomically: the observed queues, the fetched requests,
        their sync checkpoints and the notifications they produced."""
        now = time.time()
        try:
            await self.conn.execute("DELETE FROM osumod_queue_state")
            await self.conn.executemany(
                "INSERT OR REPLACE INTO osumod_queue_state VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        q["owner"]["id"],
                        q["owner"]["username"],
                        q.get("kind", ""),
                        json.dumps(q.get("modes", [])),
                        int(bool(q.get("isOpen"))),
                        q.get("lastActivityAt") or "",
                    )
                    for q in queues
                ],
            )

            # Archived requests are fetched only partially, so upsert what was seen
            # instead of treating missing rows as deleted.
            await self.conn.executemany(
                "INSERT OR REPLACE INTO osumod_request_state VALUES (?, ?, ?, ?)",
                [
                    (r["id"], bn_osu_id, r.get("status", ""), (r.get("feedback") or "").strip())
                    for bn_osu_id, requests in fetched.items()
                    for r in requests
                ],
            )
            await self.conn.executemany(
                "INSERT OR REPLACE INTO osumod_bn_sync VALUES (?, ?, ?)",
                [(osu_id, synced_activity.get(osu_id, ""), now) for osu_id in fetched],
            )

            # Forget BNs that are no longer monitored; they are reseeded if they return.
            keep = _placeholders(monitored)
            await self.conn.execute(
                f"DELETE FROM osumod_request_state WHERE bn_osu_id NOT IN ({keep})", monitored
            )
            await self.conn.execute(
                f"DELETE FROM osumod_bn_sync WHERE osu_id NOT IN ({keep})", monitored
            )
            await self.conn.execute(
                "DELETE FROM osumod_status_messages WHERE request_id NOT IN"
                " (SELECT request_id FROM osumod_request_state)"
            )

            await self.conn.executemany(
                "INSERT INTO osumod_outbox (payload, created_at) VALUES (?, ?)",
                [(json.dumps(n), now) for n in notifications],
            )
            await self.conn.commit()
        except BaseException:
            await self.conn.rollback()
            raise

    # --- osumod notification outbox --------------------------------------------

    async def get_outbox(self) -> list[dict]:
        async with self.conn.execute("SELECT * FROM osumod_outbox ORDER BY id") as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "payload": json.loads(row["payload"]),
                "attempts": row["attempts"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def delete_outbox(self, outbox_id: int) -> None:
        await self.conn.execute("DELETE FROM osumod_outbox WHERE id = ?", (outbox_id,))
        await self.conn.commit()

    async def mark_outbox_failed(self, outbox_id: int) -> None:
        await self.conn.execute(
            "UPDATE osumod_outbox SET attempts = attempts + 1 WHERE id = ?", (outbox_id,)
        )
        await self.conn.commit()

    async def get_status_message(self, request_id: str, channel_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT message_id FROM osumod_status_messages"
            " WHERE request_id = ? AND channel_id = ?",
            (request_id, channel_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row["message_id"] if row else None

    async def set_status_message(self, request_id: str, channel_id: int, message_id: int) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO osumod_status_messages VALUES (?, ?, ?)",
            (request_id, channel_id, message_id),
        )
        await self.conn.commit()

    # --- osu! account links -----------------------------------------------------

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

    async def get_osu_link(self, discord_id: int) -> dict | None:
        async with self.conn.execute(
            "SELECT * FROM osu_links WHERE discord_id = ?", (discord_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_osu_links(self, osu_ids: list[int]) -> list[dict]:
        if not osu_ids:
            return []
        async with self.conn.execute(
            f"SELECT * FROM osu_links WHERE osu_id IN ({_placeholders(osu_ids)})", osu_ids
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
