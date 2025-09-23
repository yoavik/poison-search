Poison Machine (Web) v3 - twitterapi.io

1) Install:
   pip install -r requirements.txt

2) Set env vars:
   export TWITTERAPI_IO_KEY="YOUR_KEY"
   export POISON_ADMIN_USER="poison"
   export POISON_ADMIN_PASS="StrongPass!"
   # optional guest
   export POISON_GUEST_USER="guest"
   export POISON_GUEST_PASS="GuestPass!"
   # optional persistence for accounts/history
   export POISON_DATA_DIR="./data"

3) Run:
   uvicorn main:app --reload --port 8000

Routes:
- GET /            -> search form (with filters)
- POST /search     -> run query via twitterapi.io
- POST /export     -> CSV export of current query
- GET /accounts    -> list accounts (admin can edit; guest read-only)
- POST /accounts/add, /remove, /bulk_save
- GET /accounts/export -> download current accounts as JSON
- GET /history     -> search history (stored in data/history.json if POISON_DATA_DIR set)
