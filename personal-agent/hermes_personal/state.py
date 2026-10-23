"""Small durable task and delivery ledger; independent of Hermes' own schema."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager


def sanitize(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(s in k.lower() for s in ("key", "token", "password", "secret", "content", "text")) else sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value[:100]]
    if isinstance(value, str):
        return value[:4000]
    return value


class TaskStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, objective TEXT NOT NULL,
                status TEXT NOT NULL, update_id TEXT UNIQUE, created REAL NOT NULL,
                updated REAL NOT NULL, summary TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, ts REAL NOT NULL,
                tool TEXT NOT NULL, args TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries (
                id TEXT PRIMARY KEY, task TEXT NOT NULL, fingerprint TEXT NOT NULL,
                mode TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT,
                error TEXT, updated REAL NOT NULL, UNIQUE(task, fingerprint, mode));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            for name, definition in (("pid", "INTEGER NOT NULL DEFAULT 0"), ("process_started", "REAL NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, session, objective, update_id=None):
        task = uuid.uuid4().hex[:16]
        now = time.time()
        import psutil
        process_started = psutil.Process().create_time()
        try:
            with self.connect() as db:
                db.execute("INSERT INTO tasks(id,session,objective,status,update_id,created,updated,summary,pid,process_started) VALUES(?,?,?,?,?,?,?,?,?,?)", (task, session, objective[:4000], "running", update_id, now, now, "", os.getpid(), process_started))
            return task
        except sqlite3.IntegrityError:
            return None

    def record(self, task, tool, args, result):
        with self.connect() as db:
            db.execute("INSERT INTO events(task,ts,tool,args,result) VALUES(?,?,?,?,?)", (task, time.time(), tool, json.dumps(sanitize(args), ensure_ascii=False), json.dumps(sanitize(result), ensure_ascii=False)))
            db.execute("UPDATE tasks SET updated=? WHERE id=?", (time.time(), task))

    def finish(self, task, status, summary=""):
        if status not in {"completed", "failed", "interrupted", "cancelled", "needs_attention", "running"}:
            raise ValueError("Invalid task state")
        with self.connect() as db:
            db.execute("UPDATE tasks SET status=?,summary=?,updated=? WHERE id=?", (status, summary[:4000], time.time(), task))

    def get(self, task):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["events"] = [dict(e) for e in db.execute("SELECT * FROM events WHERE task=? ORDER BY id", (task,))]
            return result

    def recent(self, session=None, limit=10):
        with self.connect() as db:
            if session:
                rows = db.execute("SELECT * FROM tasks WHERE session=? ORDER BY updated DESC LIMIT ?", (session, limit))
            else:
                rows = db.execute("SELECT * FROM tasks ORDER BY updated DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def recover_interrupted(self, *, only_dead=False):
        with self.connect() as db:
            ids = []
            for row in db.execute("SELECT id,pid,process_started FROM tasks WHERE status='running'"):
                alive = False
                if only_dead and row["pid"]:
                    import psutil
                    try:
                        alive = abs(psutil.Process(row["pid"]).create_time() - row["process_started"]) < .01
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.AccessDenied:
                        alive = True
                if not alive:
                    ids.append(row["id"])
            count = 0
            for task in ids:
                count += db.execute("UPDATE tasks SET status='interrupted',summary='Runtime restarted; inspect results before resuming',updated=? WHERE id=?", (time.time(), task)).rowcount
                db.execute("UPDATE deliveries SET status='unknown',error='Runtime restarted during upload',updated=? WHERE status='pending' AND task=?", (time.time(), task))
            return count

    def begin_delivery(self, task, fingerprint, mode):
        token = uuid.uuid4().hex
        try:
            with self.connect() as db:
                db.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)", (token, task, fingerprint, mode, "pending", None, None, time.time()))
            return token
        except sqlite3.IntegrityError:
            return None

    def finish_delivery(self, token, status, message_id=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET status=?,message_id=?,error=?,updated=? WHERE id=?", (status, message_id, error, time.time(), token))

    def delivery(self, task, fingerprint, mode):
        with self.connect() as db:
            row = db.execute("SELECT * FROM deliveries WHERE task=? AND fingerprint=? AND mode=?", (task, fingerprint, mode)).fetchone()
            return dict(row) if row else None
