"""
Run at 6:30am by launchd to verify the morning briefing completed.
Appends a one-line status entry to data/check.log.
"""
import sys
from datetime import date, datetime
import urllib.request
import json

LOG = "data/check.log"

def check():
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = date.today().isoformat()
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/status", timeout=10) as r:
            data = json.loads(r.read())
        status      = data.get("status")
        event_count = data.get("event_count", 0)
        if status == "complete" and event_count > 0:
            line = f"[{now}] OK — {event_count} events generated for {today}"
        elif status == "pending":
            line = f"[{now}] WARN — pipeline still running at 06:30 for {today}"
        else:
            line = f"[{now}] FAIL — status={status}, events={event_count} for {today}"
    except Exception as exc:
        line = f"[{now}] ERROR — could not reach server: {exc}"

    with open(LOG, "a") as f:
        f.write(line + "\n")
    print(line)

if __name__ == "__main__":
    check()
