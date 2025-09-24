import os
import json
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import httpx
import csv
import io
import secrets
from datetime import datetime

APP_TITLE = "Poison Machine"
DATA_DIR = os.environ.get("POISON_DATA_DIR", "./data")
ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
API_KEY = os.environ.get("TWITTERAPI_IO_KEY", "")
API_BASE = "https://api.twitterapi.io"
ADV_ENDPOINT = f"{API_BASE}/twitter/tweet/advanced_search"

# Roles: Admin & Guest (Basic Auth). If none set -> open access.
ADMIN_USER = os.environ.get("POISON_ADMIN_USER", os.environ.get("POISON_USERNAME", "poison"))
ADMIN_PASS = os.environ.get("POISON_ADMIN_PASS", os.environ.get("POISON_PASSWORD", ""))
GUEST_USER = os.environ.get("POISON_GUEST_USER", "")
GUEST_PASS = os.environ.get("POISON_GUEST_PASS", "")

security = HTTPBasic()

os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_ACCOUNTS = ["nytimes", "BBCWorld"]  # change via UI

def get_role(credentials: HTTPBasicCredentials) -> str:
    # Return "ADMIN" / "GUEST" / "" (no auth configured)
    if not ADMIN_PASS and not GUEST_PASS:
        return ""  # auth disabled
    if ADMIN_PASS and secrets.compare_digest(credentials.username, ADMIN_USER) and secrets.compare_digest(credentials.password, ADMIN_PASS):
        return "ADMIN"
    if GUEST_PASS and secrets.compare_digest(credentials.username, GUEST_USER) and secrets.compare_digest(credentials.password, GUEST_PASS):
        return "GUEST"
    raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})

def require_any(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not ADMIN_PASS and not GUEST_PASS:
        return ""  # open
    return get_role(credentials)

def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    role = get_role(credentials)
    if role != "ADMIN":
        raise HTTPException(status_code=403, detail="Admins only")
    return role

def load_accounts() -> List[str]:
    if not os.path.exists(ACCOUNTS_PATH):
        save_accounts(DEFAULT_ACCOUNTS)
        return DEFAULT_ACCOUNTS[:]
    try:
        with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [a.strip() for a in data if a.strip()]
    except Exception:
        return DEFAULT_ACCOUNTS[:]

def save_accounts(accounts: List[str]) -> None:
    with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(set([a.strip().lstrip('@') for a in accounts if a.strip()])), f, ensure_ascii=False, indent=2)

def load_history() -> List[Dict[str, Any]]:
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def append_history(entry: Dict[str, Any]) -> None:
    items = load_history()
    items.insert(0, entry)  # newest first
    items = items[:200]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def build_query(phrase: str, accounts: List[str], since_date: Optional[str], until_date: Optional[str]) -> str:
    phrase = phrase.strip()
    if not (phrase.startswith('"') and phrase.endswith('"')):
        phrase = f'"{phrase}"'
    acct_part = " OR ".join([f"from:{u}" for u in accounts]) if accounts else ""
    date_part = ""
    # Twitter search operators: since:YYYY-MM-DD until:YYYY-MM-DD
    if since_date:
        date_part += f" since:{since_date}"
    if until_date:
        date_part += f" until:{until_date}"
    base = f'{phrase} ({acct_part})' if acct_part else phrase
    return (base + date_part).strip()

async def advanced_search(query: str, mode: str = "Latest", max_pages: int = 2) -> List[Dict[str, Any]]:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="TWITTERAPI_IO_KEY is not set in environment.")
    headers = {"x-api-key": API_KEY}
    all_items: List[Dict[str, Any]] = []
    cursor = ""
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(max_pages):
            params = {"query": query, "queryType": mode}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(ADV_ENDPOINT, headers=headers, params=params)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            data = resp.json()
            tweets = data.get("tweets", []) or []
            all_items.extend(tweets)
            if not data.get("has_next_page") or not data.get("next_cursor"):
                break
            cursor = data.get("next_cursor")
    return all_items

def flatten(tweet: Dict[str, Any]) -> Dict[str, Any]:
    a = tweet.get("author", {}) or {}
    return {
        "id": tweet.get("id"),
        "url": tweet.get("url"),
        "text": tweet.get("text"),
        "createdAt": tweet.get("createdAt"),
        "author_userName": a.get("userName"),
        "author_name": a.get("name"),
        "author_id": a.get("id"),
        "likeCount": tweet.get("likeCount"),
        "retweetCount": tweet.get("retweetCount"),
        "replyCount": tweet.get("replyCount"),
        "quoteCount": tweet.get("quoteCount"),
        "viewCount": tweet.get("viewCount"),
        "lang": tweet.get("lang"),
    }

def highlight_text(text: str, phrase: str) -> str:
    if not phrase:
        return text
    p = phrase.strip('"')
    if not p:
        return text
    try:
        import re
        def repl(m):
            return f"<mark>{m.group(0)}</mark>"
        return re.sub(re.escape(p), repl, text, flags=re.IGNORECASE)
    except Exception:
        return text

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def role_from_auth(auth) -> str:
    # auth is '', 'ADMIN', or 'GUEST'
    return auth if isinstance(auth, str) else ""

@app.get("/switch", response_class=HTMLResponse)
async def switch_user():
    # Force the browser to prompt for credentials again (switch user)
    return Response(status_code=401, headers={"WWW-Authenticate": "Basic realm=PoisonMachine"}, content="")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, auth=Depends(require_any)):
    accounts = load_accounts()
    role = role_from_auth(auth)
    return templates.TemplateResponse("index.html", {"request": request, "accounts": accounts, "title": APP_TITLE, "role": role})

@app.post("/search", response_class=HTMLResponse)
async def do_search(request: Request,
                    phrase: str = Form(...),
                    mode: str = Form("Latest"),
                    max_results: int = Form(40),
                    min_likes: int = Form(0),
                    since_date: Optional[str] = Form(None),
                    until_date: Optional[str] = Form(None),
                    authors: List[str] = Form([]),
                    auth=Depends(require_any)):
    accounts = load_accounts()
    use_accounts = accounts
    if authors:
        selected = [a for a in authors if a in accounts]
        if selected:
            use_accounts = selected
    query = build_query(phrase, use_accounts, since_date, until_date)
    try:
        pages = max(1, int((max_results or 20) // 20))
        raw = await advanced_search(query, mode=mode, max_pages=pages)
    except HTTPException as e:
        role = role_from_auth(auth)
        return templates.TemplateResponse("error.html", {"request": request, "title": APP_TITLE, "error": f"{e.status_code} {e.detail}", "query": query, "role": role})
    flat = [flatten(t) for t in raw]
    if min_likes and isinstance(min_likes, int):
        flat = [t for t in flat if (t.get("likeCount") or 0) >= min_likes]
    for t in flat:
        t["text_highlight"] = highlight_text(t.get("text") or "", phrase)
    try:
        append_history({
            "ts": datetime.utcnow().isoformat() + "Z",
            "phrase": phrase,
            "mode": mode,
            "max_results": max_results,
            "min_likes": min_likes,
            "authors": use_accounts,
            "since_date": since_date,
            "until_date": until_date,
            "accounts_snapshot": use_accounts,
            "results": len(flat)
        })
    except Exception:
        pass
    role = role_from_auth(auth)
    return templates.TemplateResponse("results.html", {"request": request, "title": APP_TITLE, "query": query, "count": len(flat),
                                                      "items": flat, "accounts": accounts, "phrase": phrase, "mode": mode,
                                                      "max_results": max_results, "min_likes": min_likes, "authors": use_accounts,
                                                      "since_date": since_date, "until_date": until_date, "role": role})

def _collect_rows_for_export(phrase: str, mode: str, max_results: int, min_likes: int, authors: List[str]):
    # helper used by export endpoints (we let browser handle file download)
    accounts = load_accounts()
    use_accounts = accounts if not authors else [a for a in authors if a in accounts]
    # When exporting we don't re-apply date range to keep it simple; could be added similarly.
    query = build_query(phrase, use_accounts, None, None)
    pages = max(1, int((max_results or 20) // 20))
    import anyio
    async def _get():
        return await advanced_search(query, mode=mode, max_pages=pages)
    rows = anyio.run(_get)
    flat = [flatten(t) for t in rows]
    if min_likes:
        flat = [r for r in flat if (r.get("likeCount") or 0) >= int(min_likes)]
    return flat

@app.post("/export", response_class=Response)
async def export_csv(phrase: str = Form(...), mode: str = Form("Latest"), max_results: int = Form(40),
                     min_likes: int = Form(0), authors: List[str] = Form([]), auth=Depends(require_any)):
    rows = _collect_rows_for_export(phrase, mode, max_results, min_likes, authors)
    output = io.StringIO()
    fieldnames = list(rows[0].keys()) if rows else ["id","url","text","createdAt","author_userName","author_name","author_id","likeCount","retweetCount","replyCount","quoteCount","viewCount","lang"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    csv_bytes = output.getvalue().encode("utf-8")
    return Response(content=csv_bytes, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="poison_results.csv"'})

@app.post("/export_xlsx", response_class=Response)
async def export_xlsx(phrase: str = Form(...), mode: str = Form("Latest"), max_results: int = Form(40),
                      min_likes: int = Form(0), authors: List[str] = Form([]), auth=Depends(require_any)):
    rows = _collect_rows_for_export(phrase, mode, max_results, min_likes, authors)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    headers = list(rows[0].keys()) if rows else ["id","url","text","createdAt","author_userName","author_name","author_id","likeCount","retweetCount","replyCount","quoteCount","viewCount","lang"]
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h) for h in headers])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return Response(content=bio.read(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="poison_results.xlsx"'})
