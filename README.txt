
Poison Machine (Web) v4.5.3

- Fix: Switch User is now robust on all browsers.
  Mechanism: first visit to /switch sets a cookie and returns 401 with a unique realm -> forces the login prompt.
  After credentials are entered, the second request validates and redirects home, clearing the cookie.
- Keeps: avatars in results + in the users-dropdown, date-range & filters persistence, CSV/XLSX exports, clean footer, admin/guest visibility rules.

Deploy: upload, clear build cache on Render, hard refresh.
