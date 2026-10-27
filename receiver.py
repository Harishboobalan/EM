"""
DCD event receiver - called by the CPI iFlow's HTTP receiver adapter.

    Event Mesh queue -> CPI (AMQP sender) -> Request Reply -> HTTP POST /dcd/events -> this app

Status codes are what drive CPI's retry behaviour:
    200  processed (or a duplicate that was already processed) -> CPI completes, message leaves the queue
    401  bad credentials
    500  processing failed -> CPI fails the run, AMQP adapter releases the message for retry

Modes (env ASYNC_PROCESSING):
    false (default)  process inside the request, answer 200/500 based on the result
    true             store the event in SQLite, answer 200 at once, a background worker processes it.
                     After the 200 the local store is responsible for not losing the event.

Locally:   python receiver.py
On CF:     cf push   (see manifest.yml)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request

try:  # local runs read .env; on CF the env vars come from cf set-env / manifest
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

USER = os.getenv("RECEIVER_USER", "")
PASSWORD = os.getenv("RECEIVER_PASSWORD", "")
API_KEY = os.getenv("RECEIVER_API_KEY", "")
# false only if the existing iFlow calls without authentication (endpoint is then open to anyone)
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() != "false"
ASYNC = os.getenv("ASYNC_PROCESSING", "false").lower() == "true"
DB_PATH = os.getenv("EVENTS_DB", "events.db")
WORKER_INTERVAL = float(os.getenv("WORKER_INTERVAL", "2"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dcd-receiver")

app = Flask(__name__)
db_lock = threading.Lock()


# ---------------------------------------------------------------- agent logic

def process_event(event_id, payload):
    """Hand the event to the agent. Raise an exception to signal failure (-> 500 -> CPI retries)."""
    body = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    log.info("Processing event %s:\n%s", event_id, body)
    # TODO: call your agent here


# ---------------------------------------------------------------- storage (dedup + async queue)

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_lock, db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                   id TEXT PRIMARY KEY,
                   payload TEXT NOT NULL,
                   status TEXT NOT NULL,          -- pending | done | failed
                   attempts INTEGER NOT NULL DEFAULT 0,
                   last_error TEXT,
                   received_at TEXT NOT NULL,
                   processed_at TEXT
               )"""
        )


def get_status(event_id):
    with db_lock, db() as conn:
        row = conn.execute("SELECT status FROM events WHERE id = ?", (event_id,)).fetchone()
    return row["status"] if row else None


def save_event(event_id, payload, status):
    with db_lock, db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events (id, payload, status, received_at) VALUES (?, ?, ?, ?)",
            (event_id, json.dumps(payload), status, now()),
        )


def mark(event_id, status, error=None):
    with db_lock, db() as conn:
        conn.execute(
            "UPDATE events SET status = ?, attempts = attempts + 1, last_error = ?, processed_at = ? WHERE id = ?",
            (status, error, now() if status == "done" else None, event_id),
        )


def now():
    return datetime.now(timezone.utc).isoformat()


def worker():
    """Async mode: process stored events until they succeed (pending/failed rows are retried)."""
    while True:
        with db_lock, db() as conn:
            rows = conn.execute(
                "SELECT id, payload FROM events WHERE status IN ('pending', 'failed') ORDER BY received_at LIMIT 10"
            ).fetchall()
        for row in rows:
            try:
                process_event(row["id"], json.loads(row["payload"]))
                mark(row["id"], "done")
            except Exception as e:
                log.exception("Background processing of %s failed, will retry", row["id"])
                mark(row["id"], "failed", str(e))
        time.sleep(WORKER_INTERVAL)


# ---------------------------------------------------------------- HTTP

def authorized():
    if not REQUIRE_AUTH:
        return True
    if API_KEY and hmac.compare_digest(request.headers.get("X-API-Key", ""), API_KEY):
        return True
    auth = request.authorization
    return bool(
        USER and PASSWORD and auth and auth.username is not None
        and hmac.compare_digest(auth.username, USER)
        and hmac.compare_digest(auth.password or "", PASSWORD)
    )


def event_id_of(payload, raw):
    """Event ID header set by the iFlow, else the CloudEvents 'id', else a hash of the body."""
    return (
        request.headers.get("X-Event-Id")
        or (payload.get("id") if isinstance(payload, dict) else None)
        or "sha256:" + hashlib.sha256(raw).hexdigest()
    )


@app.post("/")  # existing iFlow posts to the app root
@app.post("/dcd/events")
def dcd_events():
    if not authorized():
        return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="dcd"'})

    raw = request.get_data()
    try:
        payload = json.loads(raw)
    except ValueError:  # plain-text (or other non-JSON) message: pass it on as a string
        payload = raw.decode("utf-8", errors="replace")

    event_id = str(event_id_of(payload, raw))
    status = get_status(event_id)

    if status == "done":
        log.info("Duplicate event %s - already processed, skipping", event_id)
        return jsonify(status="duplicate", id=event_id), 200

    if ASYNC:
        save_event(event_id, payload, "pending")  # no-op if already stored; the worker owns it now
        log.info("Stored event %s for background processing", event_id)
        return jsonify(status="accepted", id=event_id), 200

    save_event(event_id, payload, "pending")
    try:
        process_event(event_id, payload)
    except Exception as e:
        log.exception("Processing of %s failed - returning 500 so CPI retries", event_id)
        mark(event_id, "failed", str(e))
        return jsonify(status="failed", id=event_id, error=str(e)), 500
    mark(event_id, "done")
    return jsonify(status="processed", id=event_id), 200


@app.get("/")
def health():
    return "DCD receiver is running. CPI should POST events to /dcd/events.\n"


init_db()
if ASYNC:
    threading.Thread(target=worker, daemon=True, name="event-worker").start()
if not REQUIRE_AUTH:
    log.warning("REQUIRE_AUTH=false - /dcd/events accepts unauthenticated requests")
elif not (USER and PASSWORD) and not API_KEY:
    log.warning("No RECEIVER_USER/RECEIVER_PASSWORD or RECEIVER_API_KEY set - every request will get 401")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT") or os.getenv("RECEIVER_PORT") or 8000))
