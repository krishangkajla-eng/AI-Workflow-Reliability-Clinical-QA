import asyncio
import hashlib
import json
import logging
import os
import random
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field

DB_PATH = os.getenv("DB_PATH", "pipeline.db")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
CRM_URL = os.getenv("CRM_URL", "")
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
POLL_SECONDS = float(os.getenv("POLL_SECONDS", "2"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "4"))
LEASE_SECONDS = int(os.getenv("LEASE_SECONDS", "90"))
OUTAGE_MODE = False

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ai-reliability-demo")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=20000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            idem_key TEXT PRIMARY KEY, email TEXT NOT NULL, full_name TEXT NOT NULL,
            company TEXT NOT NULL, source_ref TEXT, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING', attempts INTEGER NOT NULL DEFAULT 0,
            next_run REAL NOT NULL DEFAULT 0, lease_until REAL, worker_id TEXT,
            last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_next ON jobs(status, next_run)")


class Lead(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=100)
    company: str = Field(min_length=2, max_length=100)
    source_ref: str | None = Field(default=None, max_length=120)


def idem_key(lead: Lead) -> str:
    raw = "|".join((str(lead.email).lower(), lead.company.lower(), lead.source_ref or "default"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require_webhook(x_webhook_token: str | None = Header(default=None)):
    if not WEBHOOK_TOKEN or not secrets.compare_digest((x_webhook_token or "").encode(), WEBHOOK_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid webhook token")


def require_admin(x_admin_token: str | None = Header(default=None)):
    if not ADMIN_TOKEN or not secrets.compare_digest((x_admin_token or "").encode(), ADMIN_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Admin authentication required")


async def alert(message: str):
    log.error("PIPELINE ALERT: %s", message)
    if ALERT_WEBHOOK_URL:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(ALERT_WEBHOOK_URL, json={"text": message})
                response.raise_for_status()
        except Exception:
            log.exception("Alert delivery failed")


class RetryableHTTP(Exception):
    def __init__(self, status: int, retry_after: str | None = None):
        self.status, self.retry_after = status, retry_after
        super().__init__(f"Downstream HTTP {status}")


class PermanentHTTP(Exception):
    pass


async def downstream_send(record: dict[str, Any]):
    if OUTAGE_MODE:
        raise httpx.ConnectError("Simulated downstream outage")
    if not CRM_URL:
        log.info("DEMO_SINK_ACCEPTED key=%s", record["idem_key"][:12])
        return
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post(CRM_URL, json=json.loads(record["payload"]))
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableHTTP(response.status_code, response.headers.get("Retry-After"))
        if response.status_code >= 400:
            raise PermanentHTTP(f"Non-retryable downstream HTTP {response.status_code}")
        response.raise_for_status()


def retry_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(300, max(1, float(retry_after)))
        except ValueError:
            try:
                dt = parsedate_to_datetime(retry_after)
                return min(300, max(1, (dt - datetime.now(timezone.utc)).total_seconds()))
            except Exception:
                pass
    return min(120, (2 ** max(0, attempt - 1)) + random.uniform(0.1, 0.8))


def claim_one(worker_id: str):
    now = time.time()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""UPDATE jobs SET status='PENDING', worker_id=NULL, lease_until=NULL, updated_at=?
            WHERE status='PROCESSING' AND lease_until < ?""", (utcnow(), now))
        row = conn.execute("""SELECT * FROM jobs WHERE status='PENDING' AND next_run <= ?
            ORDER BY created_at LIMIT 1""", (now,)).fetchone()
        if row is None:
            conn.commit()
            return None
        changed = conn.execute("""UPDATE jobs SET status='PROCESSING', worker_id=?, lease_until=?, updated_at=?
            WHERE idem_key=? AND status='PENDING'""",
            (worker_id, now + LEASE_SECONDS, utcnow(), row["idem_key"])).rowcount
        conn.commit()
        return dict(row) if changed == 1 else None


def finish_job(key: str):
    with db() as conn:
        conn.execute("""UPDATE jobs SET status='PROCESSED', worker_id=NULL, lease_until=NULL,
            attempts=attempts+1, last_error=NULL, updated_at=? WHERE idem_key=?""", (utcnow(), key))


def fail_or_retry(row, exc):
    attempt = row["attempts"] + 1
    retry_after = exc.retry_after if isinstance(exc, RetryableHTTP) else None
    terminal = attempt >= MAX_ATTEMPTS or isinstance(exc, PermanentHTTP)
    status = "FAILED" if terminal else "PENDING"
    with db() as conn:
        conn.execute("""UPDATE jobs SET status=?, attempts=?, next_run=?, worker_id=NULL,
            lease_until=NULL, last_error=?, updated_at=? WHERE idem_key=?""",
            (status, attempt, time.time() + retry_delay(attempt, retry_after),
             str(exc)[:1000], utcnow(), row["idem_key"]))
    return terminal


async def worker_loop():
    worker_id = f"worker-{os.getpid()}"
    log.info("WORKER_STARTED id=%s", worker_id)
    while True:
        try:
            row = await asyncio.to_thread(claim_one, worker_id)
            if row is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            try:
                await downstream_send(row)
                await asyncio.to_thread(finish_job, row["idem_key"])
            except Exception as exc:
                terminal = await asyncio.to_thread(fail_or_retry, row, exc)
                log.warning("JOB_FAILURE key=%s error=%s terminal=%s", row["idem_key"][:12], exc, terminal)
                if terminal:
                    await alert(f"Job {row['idem_key'][:12]} exhausted retries or encountered a permanent error: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Worker loop exception")
            await alert("Worker loop exception; inspect service logs")
            await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(worker_loop())
    app.state.worker_task = task
    log.info("APPLICATION_STARTED")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log.info("APPLICATION_STOPPED")


app = FastAPI(title="AI Workflow Reliability Demo", version="1.1.0", lifespan=lifespan)


@app.post("/webhook/lead", dependencies=[Depends(require_webhook)])
def receive_lead(lead: Lead):
    key, now = idem_key(lead), utcnow()
    try:
        with db() as conn:
            conn.execute("""INSERT INTO jobs (idem_key, email, full_name, company, source_ref,
                payload, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)""",
                (key, str(lead.email).lower(), lead.full_name, lead.company,
                 lead.source_ref, lead.model_dump_json(), now, now))
        log.info("LEAD_ACCEPTED key=%s", key[:12])
        return {"status": "queued", "idempotency_key": key}
    except sqlite3.IntegrityError:
        log.warning("DUPLICATE_BLOCKED key=%s", key[:12])
        return {"status": "duplicate_ignored", "idempotency_key": key}


def queue_counts():
    with db() as conn:
        return {row["status"]: row["n"] for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()}


@app.get("/health")
def health():
    task = getattr(app.state, "worker_task", None)
    return {"app": "ok", "worker_running": bool(task and not task.done()), "queue": queue_counts()}


@app.get("/metrics", dependencies=[Depends(require_admin)])
def metrics():
    return health()


@app.post("/admin/outage/{enabled}", dependencies=[Depends(require_admin)])
def set_outage(enabled: bool):
    global OUTAGE_MODE
    OUTAGE_MODE = enabled
    log.warning("OUTAGE_MODE_CHANGED enabled=%s", OUTAGE_MODE)
    return {"outage_mode": OUTAGE_MODE}


@app.post("/admin/requeue-failed", dependencies=[Depends(require_admin)])
def requeue_failed():
    with db() as conn:
        cur = conn.execute("""UPDATE jobs SET status='PENDING', attempts=0, next_run=0,
            last_error=NULL, updated_at=? WHERE status='FAILED'""", (utcnow(),))
    log.info("FAILED_JOBS_REQUEUED count=%s", cur.rowcount)
    return {"requeued_count": cur.rowcount}


@app.get("/admin/jobs", dependencies=[Depends(require_admin)])
def list_jobs():
    with db() as conn:
        rows = conn.execute("""SELECT idem_key, status, attempts, last_error, created_at, updated_at
            FROM jobs ORDER BY created_at DESC LIMIT 100""").fetchall()
    return [dict(row) for row in rows]
