
import os
import json
from typing import List, Dict, Any
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import httpx
import csv
import io
import secrets

APP_TITLE = "Poison Machine"
DATA_DIR = os.environ.get("POISON_DATA_DIR", "./data")
ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.json")
API_KEY = os.environ.get("TWITTERAPI_IO_KEY", "")
API_BASE = "https://api.twitterapi.io"
ADV_ENDPOINT = f"{API_BASE}/twitter/tweet/advanced_search"

# Basic Auth (optional). Set POISON_PASSWORD (and optionally POISON_USERNAME) to enable.
POISON_USERNAME = os.environ.get("POISON_USERNAME", "poison")
POISON_PASSWORD = os.environ.get("POISON_PASSWORD", "")
security = HTTPBasic()

os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_ACCOUNTS = ["nytimes", "BBCWorld"]  # change via UI

def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not POISON_PASSWORD:
        return  # auth disabled
    correct_username = secrets.compare_digest(credentials.username, POISON_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, POISON_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})

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

def build_query(phrase: str, accounts: List[str]) -> str:
    phrase = phrase.strip()
    if not (phrase.startswith('"') and phrase.endswith('"')):
        phrase = f'"{phrase}"'
    acct_part = " OR ".join([f"from:{u}" for u in accounts]) if accounts else ""
    return f'{phrase} ({acct_part})' if acct_part else phrase

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

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, auth=Depends(require_auth)):
    accounts = load_accounts()
    return templates.TemplateResponse("index.html", {"request": request, "accounts": accounts, "title": APP_TITLE})

@app.post("/search", response_class=HTMLResponse)
async def do_search(request: Request, phrase: str = Form(...), mode: str = Form("Latest"), max_pages: int = Form(2), auth=Depends(require_auth)):
    accounts = load_accounts()
    query = build_query(phrase, accounts)
    try:
        raw = await advanced_search(query, mode=mode, max_pages=max_pages)
    except HTTPException as e:
        return templates.TemplateResponse("error.html", {"request": request, "title": APP_TITLE, "error": f"{e.status_code} {e.detail}", "query": query})
    flat = [flatten(t) for t in raw]
    return templates.TemplateResponse("results.html", {"request": request, "title": APP_TITLE, "query": query, "count": len(flat), "items": flat, "accounts": accounts, "phrase": phrase, "mode": mode, "max_pages": max_pages})

@app.post("/export", response_class=Response)
async def export_csv(phrase: str = Form(...), mode: str = Form("Latest"), max_pages: int = Form(2), auth=Depends(require_auth)):
    accounts = load_accounts()
    query = build_query(phrase, accounts)
    raw = await advanced_search(query, mode=mode, max_pages=max_pages)
    rows = [flatten(t) for t in raw]
    output = io.StringIO()
    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = ["id","url","text","createdAt","author_userName","author_name","author_id","likeCount","retweetCount","replyCount","quoteCount","viewCount","lang"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    csv_bytes = output.getvalue().encode("utf-8")
    return Response(content=csv_bytes, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="poison_results.csv"'})

@app.get("/accounts", response_class=HTMLResponse)
async def accounts_view(request: Request, auth=Depends(require_auth)):
    accounts = load_accounts()
    return templates.TemplateResponse("accounts.html", {"request": request, "title": APP_TITLE, "accounts": accounts})

@app.post("/accounts/add", response_class=HTMLResponse)
async def accounts_add(request: Request, username: str = Form(...), auth=Depends(require_auth)):
    username = username.strip().lstrip("@")
    accounts = load_accounts()
    if username and username not in accounts:
        accounts.append(username)
        save_accounts(accounts)
    return RedirectResponse(url="/accounts", status_code=303)

@app.post("/accounts/remove", response_class=HTMLResponse)
async def accounts_remove(request: Request, username: str = Form(...), auth=Depends(require_auth)):
    username = username.strip().lstrip("@")
    accounts = [a for a in load_accounts() if a.lower() != username.lower()]
    save_accounts(accounts)
    return RedirectResponse(url="/accounts", status_code=303)

@app.post("/accounts/bulk_save", response_class=HTMLResponse)
async def accounts_bulk_save(request: Request, bulktext: str = Form(""), auth=Depends(require_auth)):
    items = [line.strip().lstrip("@") for line in bulktext.splitlines() if line.strip()]
    save_accounts(items)
    return RedirectResponse(url="/accounts", status_code=303)
