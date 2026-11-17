# AI Workflow Reliability & Clinical AI QA — Demo v1.1

A public, synthetic-data demonstration of two distinct service lines.

## 1. AI Workflow & API Reliability

A small FastAPI webhook pipeline demonstrating:

- Strict request validation with Pydantic
- Token-protected webhook and admin endpoints
- Deterministic idempotency key and duplicate suppression
- SQLite-backed job persistence
- Background processing with bounded retries and backoff
- Expired worker-lease recovery
- Health and queue status endpoints
- Simulated downstream outage, terminal failure, and manual requeue

When `CRM_URL` is empty, the app uses a **demo sink** and logs accepted jobs. It does not connect to a real CRM by default.

## 2. Clinical AI QA & Validation

`clinical_qa.py` demonstrates simple structural and heuristic checks for required HPI, assessment, and plan fields, plus a small set of wording/length warnings.

**Important:** this is not a clinical correctness evaluator, diagnostic tool, comprehensive hallucination detector, or EHR integration. It must not be used to make clinical decisions or process real patient information. Human clinical review remains necessary.

## Architecture

```text
Webhook client
     |
     v
FastAPI endpoint ---- token authentication + Pydantic validation
     |
     v
SQLite job table ---- deterministic idempotency key
     |
     v
Background worker --- retry/backoff + lease recovery
     |
     v
Demo sink OR configured downstream HTTP endpoint
```

## Run locally on Windows PowerShell

Python 3.12 is recommended.

1. Open PowerShell in this project folder.
2. Create and install the environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

3. Open `.env` in Notepad. Replace `ADMIN_TOKEN` and `WEBHOOK_TOKEN` with two different long random values. Do not commit `.env`.
4. Set environment variables in the same PowerShell window:

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
    }
}
```

5. Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

6. In a second PowerShell window, navigate to this same folder, set the same environment variables from `.env` using step 4, then run:

```powershell
.\.venv\Scripts\python.exe run_demo.py
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation. Stop the server with Ctrl+C.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

The test suite covers Clinical QA examples and API pipeline token checks, schema validation, duplicate suppression, retry exhaustion, manual requeue, and invalid tokens. The demo script is a walkthrough; it is not itself the test suite.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Put unique random values in `ADMIN_TOKEN` and `WEBHOOK_TOKEN`.
3. Run:

```bash
docker compose up --build
```

Then visit `http://localhost:8000/docs`. Stop with `docker compose down`. The named Docker volume preserves the demo database between container restarts. Do not use real patient data or production credentials.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/webhook/lead` | Validate and queue a synthetic lead |
| GET | `/health` | App, worker, and queue status |
| GET | `/metrics` | Authenticated queue counts |
| GET | `/admin/jobs` | Authenticated recent job list |
| POST | `/admin/outage/{true\|false}` | Toggle demo outage |
| POST | `/admin/requeue-failed` | Manually replay terminal failures |

Webhook calls require `X-Webhook-Token`. Admin calls require `X-Admin-Token`.

## Demonstration boundaries and production considerations

- This is a single-process, single-host demonstration, not a high-availability service or a 24/7 SLA.
- The worker runs inside the FastAPI process. Do not scale this example to multiple Uvicorn workers without redesigning worker ownership.
- SQLite is suitable for this small demo. Production concurrency and operational needs may require PostgreSQL and a dedicated queue/worker service.
- Retry attempts are deliberately bounded. Exhausted jobs enter `FAILED`; this demo requires an authenticated **manual requeue** after the downstream service is restored.
- Expired processing leases are reclaimed, but this does not provide exactly-once external side effects. A downstream idempotency contract or an outbox/inbox design is needed.
- Alert delivery is best-effort. A configured alert webhook is not monitored for successful delivery.
- The example uses shared-token authentication, not signed webhook payloads/HMAC, full authorization roles, or a complete security perimeter.
- Put HTTPS and appropriate network restrictions in front of any externally exposed service. Do not expose admin endpoints publicly without a security review.
- `CRM_URL` should point only to an approved test endpoint until integration and security review are complete.
- Use synthetic data only. Do not submit real patient information, protected health information, production credentials, or customer data to this repository/demo.

## Service packaging

**Line A — Workflow Reliability:** workflow/API audit, integration hardening, retry and failure-handling design, and scoped implementation pilots.

**Line B — Clinical AI QA:** evaluation rubric design, synthetic test cases, field/schema validation, and human-review workflow design.

Start with a scoped pilot and agreed acceptance criteria. Do not promise absolute uptime, zero failures, or clinical correctness.
