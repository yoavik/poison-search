import os
import json
import csv
import io
from typing import List, Dict, Any, Optional
from datetime import datetime

import httpx
from fastapi import (
    FastAPI, Request, Depends, Form, HTTPException, Response, Cookie, Body
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# -----------------------------
# Config & constants
# -----------------------------
APP_TITLE = "Poison Machine"
API_BASE = os.getenv("API_BASE", "https://api.twitterapi.io")
API_KEY = os.getenv("TWITTERAPI_IO_KEY", "")

DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", os.path.join(DATA_DIR, "poison_accounts.json"))
HISTORY_FILE = os.getenv("HISTORY_FILE", os.path.join(DATA_DIR, "history.jsonl"))
USER_CACHE_PATH = os.path.join(DATA_DIR, "user_cache.json")

# Basic Auth users
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "adminpass")
GUEST_USER = os.getenv("GUEST_USER", "guest")
GUEST_PASS = os.getenv("GUEST_PASS", "guestpass")

# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

security = HTTPBasic(auto_error=True)
security_optional = HTTPBasic(auto_error=False)

# -----------------------------
# Auth helpers
# -----------------------------
def get_role(credentials: HTTPBasicCredentials) -> str:
    if not credentials:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Auth"'} )
    user = credentials.username
    pwd = credentials.password
    if user == ADMIN_USER and pwd == ADMIN_PASS:
        return "admin"
    if user == GUEST_USER and pwd == GUEST_PASS:
        return "guest"
    raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Auth"'} )

def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    role = get_role(credentials)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    return role

def require_any(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    return get_role(credentials)

def role_from_auth(auth) -> str:
    return auth if isinstance(auth, str) else ""

# -----------------------------
# Data helpers (accounts/history/cache)
# -----------------------------
def load_accounts() -> List[str]:
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # accept either list or {"accounts":[...]}
        if isinstance(data, dict) and "accounts" in data:
            return list(dict.fromkeys([a.strip().lstrip("@") for a in data["accounts"] if a]))
        if isinstance(data, list):
            return list(dict.fromkeys([a.strip().lstrip("@") for a in data if a]))
    except Exception:
        pass
    return []

def save_accounts(accounts: List[str]) -> None:
    out = {"accounts": [a.lstrip("@") for a in accounts if a]}
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

def append_history(entry: Dict[str, Any]) -> None:
    entry["ts"] = datetime.utcnow().isoformat()
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def load_user_cache() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(USER_CACHE_PATH):
        return {}
    try:
        with open(USER_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_user_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    try:
        with open(USER_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# -----------------------------
# Twitter helpers
# -----------------------------
def build_query(phrase: str, authors: List[str], since_date: Optional[str], until_date: Optional[str]) -> str:
    q = phrase.strip()
    if authors:
        # author filter (from:username)
        author_parts = [f'from:{a.lstrip("@")}' for a in authors if a]
        if author_parts:
            q += " " + " (" + " OR ".join(author_parts) + ")"
    if since_date:
        q += f" since:{since_date}"
    if until_date:
        q += f" until:{until_date}"
    return q

async def advanced_search(query: str, mode: str = "Latest", max_pages: int = 1) -> List[Dict[str, Any]]:
    """
    Calls twitterapi.io advanced search endpoint.
    Returns a list of tweet objects (normalized to have 'author' dict when possible).
    """
    headers = {"x-api-key": API_KEY} if API_KEY else {}
    items: List[Dict[str, Any]] = []
    url = f"{API_BASE}/twitter/search/advanced"
    page = 0
    async with httpx.AsyncClient(timeout=30) as client:
        params = {"q": query, "mode": mode, "limit": 20}
        while page < max_pages:
            r = await client.get(url, headers=headers, params=params)
            if r.status_code != 200:
                break
            data = r.json() or {}
            chunk = data.get("data") or data.get("tweets") or data.get("results") or []
            if not chunk:
                break
            # ensure author object if provided in includes/expansions (best effort)
            includes = data.get("includes") or {}
            users_by_id = {}
            for u in includes.get("users", []):
                uid = u.get("id")
                if uid:
                    users_by_id[uid] = u
            for t in chunk:
                author = t.get("author") or {}
                # map via author_id if missing author dict
                if (not author) and "author_id" in t and str(t["author_id"]) in users_by_id:
                    uobj = users_by_id[str(t["author_id"])]
                    author = {
                        "id": uobj.get("id"),
                        "username": uobj.get("username"),
                        "name": uobj.get("name"),
                        "profile_image_url": uobj.get("profile_image_url") or uobj.get("profileImageUrl"),
                    }
                    t["author"] = author
                items.append(t)
            # pagination
            next_token = data.get("meta", {}).get("next_token")
            if not next_token:
                break
            params["next_token"] = next_token
            page += 1
    return items

async def _fallback_name_via_tweet(username: str) -> Optional[str]:
    """
    Fallback: pull the latest tweet by user via advanced_search('from:username')
    and read author.name.
    """
    try:
        q = f"from:{username}"
        tweets = await advanced_search(q, mode="Latest", max_pages=1)
        for t in tweets:
            author = (t or {}).get("author") or {}
            nm = author.get("name")
            if nm and isinstance(nm, str) and nm.strip() and nm.strip().lower() != username.lower():
                return nm.strip()
    except Exception:
        pass
    return None

async def resolve_user_info(usernames: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Return mapping: username -> {'name': display_name, 'avatar': url}
    - Uses on-disk cache first.
    - Tries multiple twitterapi.io schemas.
    - If still missing/equals username, falls back to latest tweet's author.name.
    """
    cache = load_user_cache()
    result: Dict[str, Dict[str, Any]] = {}

    to_fetch: List[str] = []
    for u in usernames:
        info = cache.get(u)
        if info and isinstance(info, dict) and info.get("name") and str(info.get("name")).strip().lower() != u.lower():
            result[u] = info
        else:
            to_fetch.append(u)

    if not to_fetch:
        return result

    headers = {"x-api-key": API_KEY} if API_KEY else {}
    async with httpx.AsyncClient(timeout=20) as client:
        for u in to_fetch:
            name: Optional[str] = None
            avatar: Optional[str] = None

            # 1) by_username
            try:
                url1 = f"{API_BASE}/twitter/user/by_username"
                r1 = await client.get(url1, headers=headers, params={"username": u})
                if r1.status_code == 200:
                    d = r1.json() or {}
                    name = (
                        d.get("name")
                        or (d.get("data") or {}).get("name")
                        or d.get("display_name")
                        or (d.get("user") or {}).get("name")
                        or (((d.get("result") or {}).get("legacy") or {}).get("name"))
                    )
                    avatar = (
                        d.get("profileImageUrl")
                        or d.get("profile_image_url")
                        or d.get("profile_image_url_https")
                        or (d.get("data") or {}).get("profile_image_url")
                        or (((d.get("result") or {}).get("legacy") or {}).get("profile_image_url"))
                    )
            except Exception:
                pass

            # 2) search fallback
            if not name or str(name).strip().lower() == u.lower():
                try:
                    url2 = f"{API_BASE}/twitter/user/search"
                    r2 = await client.get(url2, headers=headers, params={"q": u, "count": 1})
                    if r2.status_code == 200:
                        s = r2.json() or {}
                        candidates = (
                            (s.get("users") or [])
                            or (s.get("data") or [])
                            or (s.get("results") or [])
                        )
                        if candidates:
                            c0 = candidates[0] or {}
                            name = (
                                c0.get("name")
                                or (c0.get("legacy") or {}).get("name")
                                or (c0.get("user") or {}).get("name")
                                or name
                            )
                            avatar = (
                                avatar
                                or c0.get("profileImageUrl")
                                or c0.get("profile_image_url")
                                or (c0.get("legacy") or {}).get("profile_image_url")
                            )
                except Exception:
                    pass

            # 3) final guard via tweet author
            if not name or str(name).strip().lower() == u.lower():
                nm2 = await _fallback_name_via_tweet(u)
                if nm2:
                    name = nm2

            if not avatar:
                avatar = f"https://unavatar.io/twitter/{u}"
            if not name:
                name = u

            cache[u] = {"name": name, "avatar": avatar}
            result[u] = cache[u]

    save_user_cache(cache)
    return result

# -----------------------------
# Routes
# -----------------------------
@app.get("/switch")
async def switch_user(credentials: Optional[HTTPBasicCredentials] = Depends(security_optional),
                      pm_switch_challenged: Optional[str] = Cookie(default=None)):
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

    if not pm_switch_challenged:
        return challenge()

    if credentials is not None:
        try:
            get_role(credentials)  # validates or raises 401
            resp = RedirectResponse(url="/", status_code=303)
            resp.headers["Set-Cookie"] = "pm_switch_challenged=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax"
            return resp
        except HTTPException:
            return challenge()

    return challenge()

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, auth: str = Depends(require_any)):
    role = role_from_auth(auth)
    accounts = load_accounts()

    # build accounts_info (username, name, avatar) for the dropdown
    accounts_info: List[Dict[str, str]] = []
    try:
        info_map = await resolve_user_info(accounts)
        for u in accounts:
            i = info_map.get(u, {"name": u, "avatar": f"https://unavatar.io/twitter/{u}"})
            accounts_info.append({"username": u, "name": i.get("name") or u, "avatar": i.get("avatar")})
    except Exception:
        for u in accounts:
            accounts_info.append({"username": u, "name": u, "avatar": f"https://unavatar.io/twitter/{u}"})

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "accounts": accounts, "accounts_info": accounts_info, "title": APP_TITLE, "role": role}
    )

@app.post("/search", response_class=HTMLResponse)
async def do_search(
    request: Request,
    phrase: str = Form(...),
    mode: str = Form("Latest"),
    max_results: int = Form(40),     # UI מציג 20,40,60...
    min_likes: int = Form(0),
    since_date: Optional[str] = Form(None),
    until_date: Optional[str] = Form(None),
    authors: List[str] = Form([]),
    pre_oct7: Optional[str] = Form(None),
    auth: str = Depends(require_any)
):
    role = role_from_auth(auth)
    use_accounts = [a.lstrip("@") for a in authors if a]  # if none: search all
    if pre_oct7:
        until_date = "2023-10-07"

    query = build_query(phrase, use_accounts, since_date, until_date)

    # figure out max_pages from max_results (20 per page)
    pages = max(1, int(max_results // 20))

    tweets = await advanced_search(query, mode=mode, max_pages=pages)

    # filter min likes if present (best-effort: twitterapi payload may vary)
    if min_likes and min_likes > 0:
        filtered = []
        for t in tweets:
            likes = (
                (t.get("public_metrics") or {}).get("like_count")
                or (t.get("legacy") or {}).get("favorite_count")
                or 0
            )
            if isinstance(likes, int) and likes >= min_likes:
                filtered.append(t)
        tweets = filtered

    # append to history (store minimal info, no PII)
    append_history({
        "phrase": phrase,
        "authors": use_accounts,
        "since": since_date,
        "until": until_date,
        "mode": mode,
        "count": len(tweets)
    })

    # Prepare accounts_info for display on page (for filters re-render)
    accounts = load_accounts()
    accounts_info: List[Dict[str, str]] = []
    try:
        info_map = await resolve_user_info(accounts)
        for u in accounts:
            i = info_map.get(u, {"name": u, "avatar": f"https://unavatar.io/twitter/{u}"})
            accounts_info.append({"username": u, "name": i.get("name") or u, "avatar": i.get("avatar")})
    except Exception:
        for u in accounts:
            accounts_info.append({"username": u, "name": u, "avatar": f"https://unavatar.io/twitter/{u}"})

    return templates.TemplateResponse(
        "results.html",
        {"request": request, "title": APP_TITLE, "role": role, "tweets": tweets,
         "accounts": accounts, "accounts_info": accounts_info,
         "phrase": phrase, "mode": mode, "max_results": max_results,
         "min_likes": min_likes, "since_date": since_date, "until_date": until_date,
         "authors": use_accounts, "pre_oct7": bool(pre_oct7)}
    )

@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, auth: str = Depends(require_admin)):
    role = role_from_auth(auth)
    accounts = load_accounts()
    return templates.TemplateResponse(
        "accounts.html",
        {"request": request, "title": "ניהול קבוצת הרעל", "role": role, "accounts": accounts}
    )

@app.post("/accounts/add", response_class=HTMLResponse)
async def accounts_add(request: Request, username: str = Form(...), auth: str = Depends(require_admin)):
    username = username.strip().lstrip("@")
    accounts = load_accounts()
    if username and username not in accounts:
        accounts.append(username)
        save_accounts(accounts)
    return RedirectResponse(url="/accounts", status_code=303)

@app.post("/accounts/remove", response_class=HTMLResponse)
async def accounts_remove(request: Request, username: str = Form(...), auth: str = Depends(require_admin)):
    username = username.strip().lstrip("@")
    accounts = [a for a in load_accounts() if a.lower() != username.lower()]
    save_accounts(accounts)
    return RedirectResponse(url="/accounts", status_code=303)

@app.post("/accounts/import_json", response_class=HTMLResponse)
async def accounts_import_json(request: Request, payload: str = Form(...), auth: str = Depends(require_admin)):
    try:
        data = json.loads(payload)
        if isinstance(data, dict) and "accounts" in data:
            new_list = [x.strip().lstrip("@") for x in data["accounts"] if x]
        elif isinstance(data, list):
            new_list = [x.strip().lstrip("@") for x in data if x]
        else:
            raise ValueError("bad format")
        save_accounts(list(dict.fromkeys(new_list)))
    except Exception:
        pass
    return RedirectResponse(url="/accounts", status_code=303)

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, auth: str = Depends(require_admin)):
    role = role_from_auth(auth)
    rows: List[Dict[str, Any]] = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    rows.reverse()  # latest first
    return templates.TemplateResponse(
        "history.html",
        {"request": request, "title": "היסטוריית חיפושים", "role": role, "rows": rows}
    )

@app.post("/export/csv")
async def export_csv(
    phrase: str = Form(...),
    mode: str = Form("Latest"),
    max_results: int = Form(40),
    since_date: Optional[str] = Form(None),
    until_date: Optional[str] = Form(None),
    authors: List[str] = Form([]),
    pre_oct7: Optional[str] = Form(None),
    auth: str = Depends(require_any)
):
    use_accounts = [a.lstrip("@") for a in authors if a]
    if pre_oct7:
        until_date = "2023-10-07"
    query = build_query(phrase, use_accounts, since_date, until_date)
    pages = max(1, int(max_results // 20))
    tweets = await advanced_search(query, mode=mode, max_pages=pages)

    def row_of(t):
        author = t.get("author") or {}
        return [
            t.get("id") or "",
            t.get("created_at") or t.get("legacy", {}).get("created_at", ""),
            author.get("username") or "",
            author.get("name") or "",
            t.get("text") or t.get("full_text") or t.get("legacy", {}).get("full_text") or "",
            (t.get("public_metrics") or {}).get("like_count")
            or (t.get("legacy") or {}).get("favorite_count") or 0
        ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "created_at", "username", "name", "text", "likes"])
    for t in tweets:
        writer.writerow(row_of(t))
    output.seek(0)

    headers = {
        "Content-Disposition": 'attachment; filename="poison_export.csv"'
    }
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

@app.post("/export/xlsx")
async def export_xlsx(
    phrase: str = Form(...),
    mode: str = Form("Latest"),
    max_results: int = Form(40),
    since_date: Optional[str] = Form(None),
    until_date: Optional[str] = Form(None),
    authors: List[str] = Form([]),
    pre_oct7: Optional[str] = Form(None),
    auth: str = Depends(require_any)
):
    from openpyxl import Workbook
    use_accounts = [a.lstrip("@") for a in authors if a]
    if pre_oct7:
        until_date = "2023-10-07"
    query = build_query(phrase, use_accounts, since_date, until_date)
    pages = max(1, int(max_results // 20))
    tweets = await advanced_search(query, mode=mode, max_pages=pages)

    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append(["id", "created_at", "username", "name", "text", "likes"])

    for t in tweets:
        author = t.get("author") or {}
        ws.append([
            t.get("id") or "",
            t.get("created_at") or t.get("legacy", {}).get("created_at", ""),
            author.get("username") or "",
            author.get("name") or "",
            t.get("text") or t.get("full_text") or t.get("legacy", {}).get("full_text") or "",
            (t.get("public_metrics") or {}).get("like_count")
            or (t.get("legacy") or {}).get("favorite_count") or 0
        ])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="poison_export.xlsx"'}
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)

# batch user info (for dropdown hydration, if נדרש)
@app.post("/user_info_batch")
async def user_info_batch(payload: dict = Body(...), auth: str = Depends(require_any)):
    try:
        usernames = payload.get("usernames", [])
        if not isinstance(usernames, list):
            return JSONResponse({"error": "bad_request"}, status_code=400)
        info = await resolve_user_info([str(u).lstrip("@") for u in usernames if str(u).strip()])
        out = [
            {
                "username": u,
                "name": info.get(u, {}).get("name") or u,
                "avatar": info.get(u, {}).get("avatar") or f"https://unavatar.io/twitter/{u}",
            }
            for u in usernames
        ]
        return JSONResponse({"data": out})
    except Exception as e:
        return JSONResponse({"error": "server_error", "detail": str(e)}, status_code=500)

# Legal pages (Templates exist as terms.html / privacy.html)
@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request, auth: str = Depends(require_any)):
    role = role_from_auth(auth)
    return templates.TemplateResponse("terms.html", {"request": request, "title": "תנאי שימוש", "role": role})

@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request, auth: str = Depends(require_any)):
    role = role_from_auth(auth)
    return templates.TemplateResponse("privacy.html", {"request": request, "title": "מדיניות פרטיות", "role": role})
