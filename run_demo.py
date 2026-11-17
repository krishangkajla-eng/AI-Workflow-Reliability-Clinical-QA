"""Run after starting the API. Uses synthetic data only."""
import os
import time
import httpx

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")
ADMIN = os.getenv("ADMIN_TOKEN", "")
WEBHOOK = os.getenv("WEBHOOK_TOKEN", "")
if not ADMIN or not WEBHOOK:
    raise SystemExit("Set ADMIN_TOKEN and WEBHOOK_TOKEN before running this demo.")

admin_headers = {"X-Admin-Token": ADMIN}
webhook_headers = {"X-Webhook-Token": WEBHOOK}
lead = {"email": "demo@example.com", "full_name": "Alex Demo", "company": "Example Agency", "source_ref": "demo-001"}

with httpx.Client(timeout=10) as client:
    print("1. Valid lead:", client.post(f"{BASE}/webhook/lead", json=lead, headers=webhook_headers).json())
    print("2. Duplicate:", client.post(f"{BASE}/webhook/lead", json=lead, headers=webhook_headers).json())
    bad = client.post(f"{BASE}/webhook/lead", json={"email": "not-an-email", "full_name": "A"}, headers=webhook_headers)
    print("3. Invalid payload:", bad.status_code, bad.text[:200])
    print("4. Health:", client.get(f"{BASE}/health").json())
    print("5. Admin without token:", client.get(f"{BASE}/admin/jobs").status_code)
    print("6. Admin with token:", client.get(f"{BASE}/admin/jobs", headers=admin_headers).status_code)
    print("7. Enable simulated outage:", client.post(f"{BASE}/admin/outage/true", headers=admin_headers).json())
    outage_lead = {**lead, "email": "outage@example.com", "source_ref": "outage-001"}
    client.post(f"{BASE}/webhook/lead", json=outage_lead, headers=webhook_headers)
    print("8. Waiting for bounded retries to exhaust...")
    time.sleep(15)
    print("9. Queue after outage:", client.get(f"{BASE}/health").json())
    print("10. Restore downstream:", client.post(f"{BASE}/admin/outage/false", headers=admin_headers).json())
    print("11. Manual requeue:", client.post(f"{BASE}/admin/requeue-failed", headers=admin_headers).json())
    time.sleep(4)
    print("12. Final health:", client.get(f"{BASE}/health").json())
