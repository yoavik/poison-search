import os
import json
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import httpx
import csv
import io
import secrets
from datetime import datetime
import time

APP_TITLE = "Poison Machine"
DATA_DIR = os.environ.get("POISON_DATA_DIR", "./data")
ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.json")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
API_KEY = os.environ.get("TWITTERAPI_IO_KEY", "")
API_BASE = "https://api.twitterapi.io"
ADV_ENDPOINT = f"{API_BASE}/twitter/tweet/advanced_search"

# Auth setup
ADMIN_USER = os.environ.get("POISON_ADMIN_USER", os.environ.get("POISON_USERNAME", "poison"))
ADMIN_PASS = os.environ.get("POISON_ADMIN_PASS", os.environ.get("POISON_PASSWORD", ""))
GUEST_USER = os.environ.get("POISON_GUEST_USER", "")
GUEST_PASS = os.environ.get("POISON_GUEST_PASS", "")

security = HTTPBasic()
security_optional = HTTPBasic(auto_error=False)

os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_ACCOUNTS = ["nytimes", "BBCWorld"]

# ---- FastAPI app MUST be created before routes ----
app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---- Helpers ----
def get_role(credentials: HTTPBasicCredentials) -> str:
    if not ADMIN_PASS and not GUEST_PASS:
        return ""
    if ADMIN_PASS and secrets.compare_digest(credentials.username, ADMIN_USER) and secrets.compare_digest(credentials.password, ADMIN_PASS):
        return "ADMIN"
    if GUEST_PASS and secrets.compare_digest(credentials.username, GUEST_USER) and secrets.compare_digest(credentials.password, GUEST_PASS):
        return "GUEST"
    raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": 'Basic realm="PoisonMachine"'})
def require_any(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not ADMIN_PASS and not GUEST_PASS:
        return ""
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
    items.insert(0, entry)
    items = items[:200]
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def build_query(phrase: str, accounts: List[str], since_date: Optional[str], until_date: Optional[str]) -> str:
    phrase = phrase.strip()
    if not (phrase.startswith('"') and phrase.endswith('"')):
        phrase = f'"{phrase}"'
    acct_part = " OR ".join([f"from:{u}" for u in accounts]) if accounts else ""
    date_part = ""
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
    avatar = a.get("profileImageUrl") or a.get("profile_image_url") or a.get("profile_image_url_https")
    username = a.get("userName")
    if not avatar and username:
        avatar = f"https://unavatar.io/twitter/{username}"
    return {
        "id": tweet.get("id"),
        "url": tweet.get("url"),
        "text": tweet.get("text"),
        "createdAt": tweet.get("createdAt"),
        "author_userName": username,
        "author_name": a.get("name"),
        "author_id": a.get("id"),
        "author_avatar": avatar,
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

def role_from_auth(auth) -> str:
    return auth if isinstance(auth, str) else ""

# ---- Routes ----

# Force a fresh Basic Auth prompt with unique realm to avoid cached creds
from fastapi import Cookie
from fastapi.security import HTTPBasic
security_optional = HTTPBasic(auto_error=False)

@app.get("/switch")
async def switch_user(credentials=Depends(security_optional), pm_switch_challenged: str | None = Cookie(default=None)):
    """
    Robust switch flow for browsers that auto-send Authorization:
    1) First hit: always send 401 with UNIQUE realm + set cookie 'pm_switch_challenged=1' → browser shows prompt.
    2) Second hit (after user enters creds): if cookie present and Authorization provided → validate and redirect.
    """
    import time
    def challenge():
        realm = f'PoisonMachine-SWITCH-{int(time.time())}'
        headers = {
            "WWW-Authenticate": f'Basic realm="{realm}", charset="UTF-8"',
            "Set-Cookie": "pm_switch_challenged=1; Path=/; HttpOnly; SameSite=Lax"
        }
        return Response(status_code=401, headers=headers, content=b"")

    # If we haven't challenged yet, *force* a challenge to pop the auth dialog.
    if not pm_switch_challenged:
        return challenge()

    # If we challenged already and now have creds, validate and redirect.
    if credentials is not None:
        try:
            get_role(credentials)  # validates or raises 401
            # Clear the cookie when we're done
            resp = RedirectResponse(url="/", status_code=303)
            resp.headers["Set-Cookie"] = "pm_switch_challenged=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax"
            return resp
        except HTTPException:
            # Wrong creds → challenge again
            return challenge()

    # Edge case: challenged but browser did not send Authorization (user pressed cancel)
    # Re-challenge to try again
    return challenge()

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

@app.post("/export", response_class=Response)
async def export_csv(phrase: str = Form(...), mode: str = Form("Latest"), max_results: int = Form(40),
                     min_likes: int = Form(0), authors: List[str] = Form([]), auth=Depends(require_any)):
    accounts = load_accounts()
    use_accounts = accounts if not authors else [a for a in authors if a in accounts]
    query = build_query(phrase, use_accounts, None, None)
    pages = max(1, int((max_results or 20) // 20))
    raw = await advanced_search(query, mode=mode, max_pages=pages)
    rows = [flatten(t) for t in raw]
    if min_likes:
        rows = [r for r in rows if (r.get("likeCount") or 0) >= int(min_likes)]
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
    accounts = load_accounts()
    use_accounts = accounts if not authors else [a for a in authors if a in accounts]
    query = build_query(phrase, use_accounts, None, None)
    pages = max(1, int((max_results or 20) // 20))
    raw = await advanced_search(query, mode=mode, max_pages=pages)
    rows = [flatten(t) for t in raw]
    if min_likes:
        rows = [r for r in rows if (r.get("likeCount") or 0) >= int(min_likes)]
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

# Admin routes
@app.get("/accounts", response_class=HTMLResponse)
async def accounts_view(request: Request, auth=Depends(require_admin)):
    accounts = load_accounts()
    role = "ADMIN"
    can_edit = True
    return templates.TemplateResponse("accounts.html", {"request": request, "title": APP_TITLE, "accounts": accounts, "can_edit": can_edit, "role": role})

@app.post("/accounts/add", response_class=HTMLResponse)
async def accounts_add(request: Request, username: str = Form(...), auth=Depends(require_admin)):
    username = username.strip().lstrip("@")
    accounts = load_accounts()
    if username and username not in accounts:
        accounts.append(username)
        save_accounts(accounts)
    return RedirectResponse(url="/accounts", status_code=303)

@app.post("/accounts/remove", response_class=HTMLResponse)
async def accounts_remove(request: Request, username: str = Form(...), auth=Depends(require_admin)):
    username = username.strip().lstrip("@")
    accounts = [a for a in load_accounts() if a.lower() != username.lower()]
    save_accounts(accounts)
    return RedirectResponse(url="/accounts", status_code=303)

@app.post("/accounts/bulk_save", response_class=HTMLResponse)
async def accounts_bulk_save(request: Request, bulktext: str = Form(""), auth=Depends(require_admin)):
    items = [line.strip().lstrip("@") for line in bulktext.splitlines() if line.strip()]
    save_accounts(items)
    return RedirectResponse(url="/accounts", status_code=303)

@app.post("/accounts/import", response_class=HTMLResponse)
async def accounts_import(request: Request, file: UploadFile = File(...), auth=Depends(require_admin)):
    try:
        content = await file.read()
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON must be an array of usernames")
        items = [str(x).strip().lstrip("@") for x in data if str(x).strip()]
        save_accounts(items)
        return RedirectResponse(url="/accounts", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

@app.get("/accounts/export", response_class=Response)
async def accounts_export(auth=Depends(require_admin)):
    accounts = load_accounts()
    payload = json.dumps(accounts, ensure_ascii=False, indent=2)
    return Response(content=payload.encode("utf-8"), media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="accounts.json"'})

@app.get("/history", response_class=HTMLResponse)
async def history_view(request: Request, auth=Depends(require_admin)):
    items = load_history()
    role = "ADMIN"
    return templates.TemplateResponse("history.html", {"request": request, "title": APP_TITLE, "items": items, "role": role})
