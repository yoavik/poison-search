
Poison Machine (Web) v4.6

Features:
1) User avatars + **display names** in the “filter by users” dropdown on the home page.
   - Server resolves names through twitterapi.io (with on-disk cache at DATA_DIR/user_cache.json).
   - Falls back gracefully if the API doesn’t return a name.
2) New search filter: **“רק ציוצים מלפני 7 באוקטובר 2023”**.
   - When checked, the form forces `until_date=2023-10-07` (and disables further edits to that field).

Deploy: upload, clear build cache on Render, hard refresh.
