Poison Machine (Web) v4.3.1 - twitterapi.io

Features:
- Admin/Guest (Basic Auth). Guests cannot access /accounts or /history and don't see the nav.
- Theme: Dark/Light/System with "מצב תצוגה" label.
- Back-to-home button on all pages except index.
- Accounts: Import JSON (bottom), Export JSON, Bulk edit, Add/Remove.
- Search: "max results" selector (20/40/60/100/200), min likes, multi-select authors, highlights.
- Results: CSV export, shows selected authors.
- History: shows authors list, results count, settings snapshot.
- Persistence: set POISON_DATA_DIR (e.g., /data) to persist accounts/history.

Local run:
  pip install -r requirements.txt
  export TWITTERAPI_IO_KEY="YOUR_KEY"
  export POISON_ADMIN_USER="poison"
  export POISON_ADMIN_PASS="StrongPass!"
  # Optional guest:
  export POISON_GUEST_USER="guest"
  export POISON_GUEST_PASS="GuestPass!"
  # Persistence:
  export POISON_DATA_DIR="./data"
  uvicorn main:app --reload --port 8000

Render:
  Start: uvicorn main:app --host 0.0.0.0 --port $PORT
  Env: TWITTERAPI_IO_KEY, POISON_* vars, POISON_DATA_DIR=/data
  Disk: mount at /data
