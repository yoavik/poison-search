Poison Machine (Web) v4.4

What's new:
1) Date range filter ("since" / "until") with native date pickers, added to query using Twitter operators.
2) "Switch User" button – forces Basic Auth prompt to log in as Admin/Guest.
3) One-click Excel export (XLSX) in addition to CSV – works with Google Sheets too.
4) Visual refresh – subtle accents, elevated cards, primary buttons, still clean & elegant.

Deploy:
- pip install -r requirements.txt
- Set env: TWITTERAPI_IO_KEY, POISON_* vars, POISON_DATA_DIR
- Start: uvicorn main:app --host 0.0.0.0 --port $PORT
- Render: clear build cache on deploy. Mount disk at /data.
