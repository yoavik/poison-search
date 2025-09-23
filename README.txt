Poison Machine (Web) v4.1 - twitterapi.io

- Admin/Guest roles (Basic Auth). Guests cannot access /accounts or /history.
- Dark/Light/System theme toggle (saved to localStorage).
- Import/Export accounts (JSON), bulk edit, add/remove single.
- Search history, filters (min likes, author), highlight, CSV export.

Run locally:
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

On Render:
  Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
  Env: TWITTERAPI_IO_KEY, POISON_* vars, POISON_DATA_DIR=/data
  Disk: mount at /data to persist accounts/history
