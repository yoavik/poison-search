Poison Machine (Web) - twitterapi.io

1) Create a virtualenv and install:
   pip install -r requirements.txt

2) Set the API key (replace XXX):
   export TWITTERAPI_IO_KEY="XXX"

3) (Optional) choose data directory for accounts.json:
   export POISON_DATA_DIR="./data"

4) Run the server:
   uvicorn main:app --reload --port 8000

5) Open:
   http://127.0.0.1:8000/

Routes:
- GET /            -> search form
- POST /search     -> run query via twitterapi.io advanced_search
- GET /accounts    -> manage the "poison" account list
- POST /accounts/add, /accounts/remove

Notes:
- The query auto-adds quotes for exact phrase if you didn't include them.
- Accounts are stored in accounts.json; you can add/remove via the UI.


## Optional password protection (HTTP Basic)
Set these environment variables before running:
  export POISON_USERNAME="poison"
  export POISON_PASSWORD="your-strong-pass"
Then run uvicorn as usual. Your browser will prompt for user/pass.
