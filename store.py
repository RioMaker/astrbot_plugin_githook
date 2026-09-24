"""Single-process SQLite state and a durable, per-group notification outbox."""

import json
import sqlite3
import time
from pathlib import Path

from .query import chunks


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS subscriptions(
                umo TEXT PRIMARY KEY, enabled_by TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS plugins(
                name TEXT PRIMARY KEY, info TEXT NOT NULL, present INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery TEXT NOT NULL REFERENCES deliveries(id) ON DELETE CASCADE,
                umo TEXT NOT NULL, part INTEGER NOT NULL, message TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(delivery, umo, part));
            CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(status, next_try);
            CREATE TABLE IF NOT EXISTS commits(
                repo TEXT NOT NULL, branch TEXT NOT NULL, sha TEXT NOT NULL,
                title TEXT NOT NULL, time TEXT NOT NULL,
                PRIMARY KEY(repo, branch, sha));
            CREATE TABLE IF NOT EXISTS api_cache(
                key TEXT PRIMARY KEY, body TEXT NOT NULL, saved REAL NOT NULL);
        """)

    def close(self):
        self.db.close()

    def enabled(self, umo):
        return (
            self.db.execute("SELECT 1 FROM subscriptions WHERE umo=?", (umo,)).fetchone()
            is not None
        )

    def set_enabled(self, umo, enabled, user=""):
        with self.db:
            if enabled:
                self.db.execute(
                    "INSERT OR IGNORE INTO subscriptions VALUES (?,?,?)", (umo, user, time.time())
                )
            else:
                self.db.execute("DELETE FROM subscriptions WHERE umo=?", (umo,))
                self.db.execute(
                    "UPDATE outbox SET status='cancelled' WHERE umo=? AND status='pending'", (umo,)
                )

    def targets(self):
        return [r[0] for r in self.db.execute("SELECT umo FROM subscriptions ORDER BY umo")]

    def _enqueue(self, delivery, digest, message):
        prior = self.db.execute("SELECT digest FROM deliveries WHERE id=?", (delivery,)).fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("同一个 Delivery ID 对应不同内容")
            return False
        self.db.execute("INSERT INTO deliveries VALUES (?,?,?)", (delivery, digest, time.time()))
        for umo in self.targets():
            for part, text in enumerate(chunks(message)):
                self.db.execute(
                    "INSERT INTO outbox(delivery,umo,part,message) VALUES (?,?,?,?)",
                    (delivery, umo, part, text),
                )
        return True

    def record_push(self, delivery, digest, message, commits):
        with self.db:
            created = self._enqueue(delivery, digest, message)
            if created:
                self._cache_commits(commits)
        return created

    def _cache_commits(self, commits):
        self.db.executemany(
            "INSERT OR REPLACE INTO commits VALUES (?,?,?,?,?)",
            [
                (c["repo"].casefold(), c.get("branch", ""), c["sha"], c["title"], c["time"])
                for c in commits
            ],
        )

    def cache_commits(self, commits):
        with self.db:
            self._cache_commits(commits)

    def sync_plugins(self, plugins, owner_key):
        """Snapshot + new-install notifications in one transaction; first scan is silent."""
        row = self.db.execute("SELECT value FROM settings WHERE key='owners'").fetchone()
        baseline = row is None or row[0] != owner_key
        previous = {
            r["name"]: bool(r["present"])
            for r in self.db.execute("SELECT name,present FROM plugins")
        }
        # A reload/update may temporarily remove its directory. Only first discovery
        # is a new plugin; a previously known name does not become new on reinstall.
        added = [p for p in plugins if p.name not in previous]
        with self.db:
            self.db.execute("UPDATE plugins SET present=0")
            for p in plugins:
                self.db.execute(
                    "INSERT OR REPLACE INTO plugins VALUES (?,?,1)",
                    (p.name, json.dumps(p.to_dict(), ensure_ascii=False)),
                )
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('owners',?)", (owner_key,))
            if not baseline:
                for plugin in added:
                    # Include monotonic ns to support reinstall after an observed removal.
                    identity = f"install:{plugin.name}:{time.time_ns()}"
                    message = f"[githook 新增插件]\n{plugin.display_name}\n版本：{plugin.version}\n状态：{plugin.status}\n仓库：https://github.com/{plugin.repo}"
                    self._enqueue(identity, identity, message)
        return [] if baseline else added

    def pending(self, limit=20):
        return [
            dict(r)
            for r in self.db.execute(
                """
            SELECT o.* FROM outbox o JOIN subscriptions s ON s.umo=o.umo
            WHERE o.status='pending' AND o.next_try<=?
            AND NOT EXISTS (SELECT 1 FROM outbox earlier
                WHERE earlier.delivery=o.delivery AND earlier.umo=o.umo
                AND earlier.part<o.part AND earlier.status!='sent')
            ORDER BY o.id LIMIT ?
        """,
                (time.time(), limit),
            )
        ]

    def sent(self, item):
        with self.db:
            self.db.execute(
                "UPDATE outbox SET status='sent' WHERE id=? AND status='pending'", (item["id"],)
            )

    def failed(self, item):
        attempts = item["attempts"] + 1
        with self.db:
            self.db.execute(
                "UPDATE outbox SET attempts=?,next_try=?,status=? WHERE id=? AND status='pending'",
                (
                    attempts,
                    time.time() + min(3600, 15 * 2 ** min(attempts - 1, 8)),
                    "failed" if attempts >= 8 else "pending",
                    item["id"],
                ),
            )

            if attempts >= 8:
                self.db.execute(
                    "UPDATE outbox SET status='failed' WHERE delivery=? AND umo=? "
                    "AND part>? AND status='pending'",
                    (item["delivery"], item["umo"], item["part"]),
                )

    def delivery_counts(self):
        return dict(
            self.db.execute("SELECT status,count(*) FROM outbox GROUP BY status").fetchall()
        )

    def cache_get(self, key):
        row = self.db.execute("SELECT body,saved FROM api_cache WHERE key=?", (key,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def cache_put(self, key, body):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO api_cache VALUES (?,?,?)",
                (key, json.dumps(body, ensure_ascii=False), time.time()),
            )
            self.db.execute(
                "DELETE FROM api_cache WHERE key NOT IN (SELECT key FROM api_cache ORDER BY saved DESC LIMIT 512)"
            )

    def prune(self):
        """Keep bounded cache and 90 days of delivery IDs; never prune unsent work."""
        with self.db:
            self.db.execute("DELETE FROM api_cache WHERE saved<?", (time.time() - 7 * 86400,))
            self.db.execute(
                "DELETE FROM deliveries WHERE created<? AND id NOT IN (SELECT delivery FROM outbox WHERE status IN ('pending','failed'))",
                (time.time() - 90 * 86400,),
            )
            self.db.execute(
                "DELETE FROM commits WHERE rowid NOT IN (SELECT rowid FROM commits ORDER BY time DESC LIMIT 50000)"
            )
