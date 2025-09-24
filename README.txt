Poison Machine (Web) v4.5.1

Fix:
- Render deploy crash (NameError: app is not defined) fixed by ensuring FastAPI `app` is defined before any route decorators.
- Keeps: avatars in results, dropdown authors selector (closed by default), remembers authors & date range, CSV/XLSX export, improved styling, clean footer, working /switch prompt with unique realm.

Deploy steps: push → Clear build cache on Render → Deploy → hard refresh.
