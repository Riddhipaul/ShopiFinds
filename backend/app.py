import base64
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, redirect, request, abort, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # whole request (several uploaded images)

# ---- config (set these as environment variables on your host) -------------
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip().strip("\"'") or "change-me"
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")  # e.g. https://yourshop.netlify.app
DATA_FILE = os.environ.get("DATA_FILE", "data.json")            # local-only fallback storage
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()  # Supabase / Neon / any Postgres -> persistent
GOOGLE_SHEET_ID = (os.environ.get("GOOGLE_SHEET_ID") or "").strip()          # Google Sheets as the database
GOOGLE_SA_JSON = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()  # service-account key (JSON or base64)
GOOGLE_SA_FILE = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()  # ...or a path to the key file
SHEETS_REFRESH_SECONDS = int(os.environ.get("SHEETS_REFRESH_SECONDS", "60") or 60)

CORS(app, origins=ALLOWED_ORIGINS.split(","))

STORES = ("meesho", "amazon")
MAX_PRODUCTS = 24
MAX_IMAGE_BYTES = 700_000  # per uploaded image (the admin page shrinks images to ~100 KB)

# ---- storage ----------------------------------------------------------------
# The app keeps one array of reels in memory ("reels") and writes every change to the database layer `db`:
#   * SheetsStore    when GOOGLE_SHEET_ID (+ service-account key) is set -> a Google Sheet
#   * PostgresStore  when DATABASE_URL is set  -> data survives restarts/redeploys (use this on Render)
#   * FileStore      otherwise (local dev)     -> data.json; Render's free disk is wiped on restart!
# reel = {id, store, reelUrl, embedUrl, createdAt,
#         products: [{code, url, title?, image?, price?, imageData?, imageMime?}]}
IG_RE = re.compile(
    r"instagram\.com/(?:[^/?#]+/)?(reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.I
)


def _embed_url(url):
    m = IG_RE.search(url or "")
    if not m:
        return None
    kind = "reel" if m.group(1).lower() == "reels" else m.group(1).lower()
    return f"https://www.instagram.com/{kind}/{m.group(2)}/embed"


def _store_of(reel):
    return reel.get("store", "meesho")  # reels saved before stores existed


class FileStore:
    name = f"JSON file ({DATA_FILE})"

    def __init__(self, path):
        self.path = path

    def load_all(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write(self, data):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def add(self, reel):
        self._write(self.load_all() + [reel])

    def remove(self, reel_id):
        self._write([r for r in self.load_all() if r["id"] != reel_id])


class PostgresStore:
    """One row per reel (JSONB). Opens a short-lived connection per call, which suits poolers
    like Supabase's Supavisor and survives idle disconnects (one automatic retry)."""

    def __init__(self, url):
        import psycopg
        from psycopg.types.json import Jsonb
        self._pg, self._Jsonb, self.url = psycopg, Jsonb, url
        self.name = "PostgreSQL"
        self._run(lambda c: c.execute(
            "create table if not exists public.reels ("
            " id text primary key, created_at bigint not null, data jsonb not null)"))
        # Supabase exposes public tables over its REST API. RLS with no policies blocks that route;
        # this app connects as the table owner, which is not affected.
        self._run(lambda c: c.execute("alter table public.reels enable row level security"))

    def _run(self, fn):
        err = None
        for attempt in (1, 2):
            try:
                # prepare_threshold=None: works with Supabase's transaction pooler as well
                with self._pg.connect(self.url, connect_timeout=10, autocommit=True,
                                      prepare_threshold=None) as conn:
                    return fn(conn)
            except self._pg.OperationalError as e:
                err = e
                if attempt == 1:
                    time.sleep(1)
        raise err

    def load_all(self):
        rows = self._run(lambda c: c.execute("select data from public.reels order by created_at, id").fetchall())
        return [r[0] for r in rows]

    def add(self, reel):
        self._run(lambda c: c.execute(
            "insert into public.reels (id, created_at, data) values (%s, %s, %s)",
            (reel["id"], reel["createdAt"], self._Jsonb(reel))))

    def remove(self, reel_id):
        self._run(lambda c: c.execute("delete from public.reels where id = %s", (reel_id,)))


class SheetsStore:
    """Google Sheets as the database (Sheets REST API v4 through a service account).

    Tab "reels":  one row per PRODUCT -
        reel_id | store | reel_url | embed_url | created_at | product_code | product_url | title | price | image_url | has_upload
    Tab "images": uploaded product photos. A cell holds at most 50,000 characters, so each photo's base64 text
        is split over several rows -  code | mime | idx | data   (<= CHUNK characters per row)
    You can read and edit the "reels" tab by hand; changes go live within SHEETS_REFRESH_SECONDS.
    """

    API = "https://sheets.googleapis.com/v4/spreadsheets"
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
    REEL_COLS = ["reel_id", "store", "reel_url", "embed_url", "created_at",
                 "product_code", "product_url", "title", "price", "image_url", "has_upload"]
    IMG_COLS = ["code", "mime", "idx", "data"]
    CHUNK = 40000
    refreshable = True

    def __init__(self, sheet_id, creds_info=None, session=None):
        self.sheet_id = sheet_id
        self.name = "Google Sheets"
        if session is None:
            from google.oauth2 import service_account
            from google.auth.transport.requests import AuthorizedSession
            creds = service_account.Credentials.from_service_account_info(creds_info, scopes=self.SCOPES)
            session = AuthorizedSession(creds)
            self.service_email = creds_info.get("client_email", "")
        self.http = session
        self._ensure_tabs()

    # -- low-level -------------------------------------------------------------
    def _call(self, method, path="", **kw):
        err = None
        for attempt in (1, 2, 3):
            try:
                r = self.http.request(method, f"{self.API}/{self.sheet_id}{path}", timeout=30, **kw)
            except Exception as e:  # network hiccup
                err = e
            else:
                if r.status_code < 300:
                    return r.json() if r.content else {}
                if r.status_code not in (429, 500, 502, 503):
                    raise RuntimeError(self._explain(r))
                err = RuntimeError(self._explain(r))
            time.sleep(1.5 * attempt)
        raise err

    @staticmethod
    def _explain(r):
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        hint = ""
        if r.status_code == 403:
            hint = " - share the sheet with the service account's e-mail (Editor) and enable the Google Sheets API"
        elif r.status_code == 404:
            hint = " - check GOOGLE_SHEET_ID"
        return f"Google Sheets API {r.status_code}: {msg}{hint}"

    def _ensure_tabs(self):
        meta = self._call("GET", params={"fields": "sheets.properties"})
        self._tab_ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
        missing = [t for t in ("reels", "images") if t not in self._tab_ids]
        if missing:
            res = self._call("POST", ":batchUpdate", json={"requests": [
                {"addSheet": {"properties": {"title": t}}} for t in missing]})
            for rep in res.get("replies", []):
                p = rep["addSheet"]["properties"]
                self._tab_ids[p["title"]] = p["sheetId"]
        first = self._call("GET", "/values:batchGet", params=[("ranges", "reels!A1:K1"), ("ranges", "images!A1:D1")])
        for tab, cols, vr in zip(("reels", "images"), (self.REEL_COLS, self.IMG_COLS), first.get("valueRanges", [])):
            if not vr.get("values"):
                self._call("PUT", f"/values/{tab}!A1", params={"valueInputOption": "RAW"},
                           json={"values": [cols]})

    def _read(self):
        res = self._call("GET", "/values:batchGet", params=[
            ("ranges", "reels!A2:K"), ("ranges", "images!A2:D"), ("majorDimension", "ROWS")])
        vr = res.get("valueRanges", [])
        return (vr[0].get("values", []) if vr else []), (vr[1].get("values", []) if len(vr) > 1 else [])

    @staticmethod
    def _pad(row, n):
        row = [("" if c is None else str(c)) for c in row]
        return row + [""] * (n - len(row))

    # -- interface used by the app -----------------------------------------------
    def load_all(self):
        rrows, irows = self._read()
        chunks = {}
        mimes = {}
        for row in irows:
            code, mime, idx, data = self._pad(row, 4)
            if not code or not idx.strip().isdigit():
                continue
            chunks.setdefault(code, {})[int(idx)] = data
            mimes[code] = mime or "image/jpeg"
        by_id, order = {}, []
        for row in rrows:
            (rid, store, rurl, embed, created, code, purl, title, price, image, up) = self._pad(row, 11)
            rid, code = rid.strip(), code.strip()
            if not rid or not code or not purl.strip():
                continue  # blank or half-filled hand-typed row
            if rid not in by_id:
                try:
                    ts = int(float(created))
                except ValueError:
                    ts = int(time.time())
                by_id[rid] = {"id": rid, "store": store.strip().lower() or "meesho", "reelUrl": rurl,
                              "embedUrl": embed or _embed_url(rurl) or "", "createdAt": ts, "products": []}
                order.append(rid)
            p = {"code": code, "url": purl.strip()}
            if title:
                p["title"] = title
            if price:
                p["price"] = price
            if image:
                p["image"] = image
            if up.strip().upper() in ("TRUE", "1", "YES") and code in chunks:
                parts = chunks[code]
                p["imageData"] = "".join(parts[i] for i in sorted(parts))
                p["imageMime"] = mimes[code]
                p["image"] = f"/api/img/{code}"
            by_id[rid]["products"].append(p)
        return sorted((by_id[i] for i in order), key=lambda r: r["createdAt"])

    def add(self, reel):
        img_rows, reel_rows = [], []
        for p in reel["products"]:
            up = bool(p.get("imageData"))
            if up:
                data = p["imageData"]
                for n, i in enumerate(range(0, len(data), self.CHUNK)):
                    img_rows.append([p["code"], p.get("imageMime", "image/jpeg"), n, data[i:i + self.CHUNK]])
            reel_rows.append([reel["id"], _store_of(reel), reel["reelUrl"], reel["embedUrl"], reel["createdAt"],
                              p["code"], p["url"], p.get("title", ""), p.get("price", ""),
                              p.get("image", ""), "TRUE" if up else "FALSE"])
        opts = {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}
        # photos first: a reel row never points at a photo that is not saved yet
        for i in range(0, len(img_rows), 8):
            self._call("POST", "/values/images!A:D:append", params=opts, json={"values": img_rows[i:i + 8]})
        self._call("POST", "/values/reels!A:K:append", params=opts, json={"values": reel_rows})

    def remove(self, reel_id):
        res = self._call("GET", "/values:batchGet", params=[
            ("ranges", "reels!A:K"), ("ranges", "images!A:A")])
        vr = res.get("valueRanges", [])
        rrows = vr[0].get("values", []) if vr else []
        codes = {self._pad(r, 6)[5] for r in rrows[1:] if r and r[0] == reel_id}
        reel_idx = [i for i, r in enumerate(rrows) if i > 0 and r and r[0] == reel_id]
        irows = vr[1].get("values", []) if len(vr) > 1 else []
        img_idx = [i for i, r in enumerate(irows) if i > 0 and r and r[0] in codes]

        def ranges(idx, tab):
            out, start = [], None
            for i in sorted(idx):
                if start is not None and i == prev + 1:
                    prev = i
                    continue
                if start is not None:
                    out.append((start, prev))
                start = prev = i
            if start is not None:
                out.append((start, prev))
            return [{"deleteDimension": {"range": {
                "sheetId": self._tab_ids[tab], "dimension": "ROWS",
                "startIndex": a, "endIndex": b + 1}}} for a, b in reversed(out)]  # bottom-up keeps indexes valid

        reqs = ranges(reel_idx, "reels") + ranges(img_idx, "images")
        if reqs:
            self._call("POST", ":batchUpdate", json={"requests": reqs})


def _sa_info():
    raw = GOOGLE_SA_JSON
    if not raw and GOOGLE_SA_FILE:
        with open(GOOGLE_SA_FILE) as f:
            raw = f.read()
    raw = raw.strip()
    if raw and not raw.startswith("{"):
        raw = base64.b64decode(raw).decode()
    info = json.loads(raw)
    if isinstance(info.get("private_key"), str):
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    return info


def _open_store():
    if GOOGLE_SHEET_ID:
        try:
            st = SheetsStore(GOOGLE_SHEET_ID, _sa_info())
            data = st.load_all()
        except Exception as e:
            raise RuntimeError(
                f"Could not open the Google Sheet ({type(e).__name__}: {e}). Check GOOGLE_SHEET_ID, "
                "GOOGLE_SERVICE_ACCOUNT_JSON, and that the sheet is shared with the service account's e-mail.") from e
        if not data and os.path.exists(DATA_FILE):
            old = FileStore(DATA_FILE).load_all()
            for r in old:
                st.add(r)
            if old:
                print(f"[reelshop] Imported {len(old)} reel(s) from {DATA_FILE} into the sheet.")
        return st
    if not DATABASE_URL:
        if os.environ.get("RENDER"):
            print("[reelshop] WARNING: DATABASE_URL is not set. Render's disk is wiped on every restart, "
                  "so all data will be LOST. Set DATABASE_URL to a Supabase/Neon Postgres URL.")
        return FileStore(DATA_FILE)
    try:
        st = PostgresStore(DATABASE_URL)
        data = st.load_all()
    except Exception as e:  # fail loudly instead of silently falling back to a disk that gets wiped
        raise RuntimeError(
            f"Could not connect to the database from DATABASE_URL ({type(e).__name__}: {e}). "
            "Check the URL/password (URL-encode special characters) and use Supabase's *pooler* "
            "connection string if your host has no IPv6.") from e
    if not data and os.path.exists(DATA_FILE):  # one-time import of an old data.json
        old = FileStore(DATA_FILE).load_all()
        for r in old:
            st.add(r)
        if old:
            print(f"[reelshop] Imported {len(old)} reel(s) from {DATA_FILE} into the database.")
    return st


_lock = threading.Lock()
db = _open_store()
reels = []


def _load():
    """(Re)load the in-memory array from the database layer."""
    global reels
    reels = db.load_all()


_load()
_last_refresh = time.time()


@app.before_request
def _refresh_from_sheet():
    """Pick up rows edited by hand in the sheet (Sheets only; at most one API read per interval)."""
    global reels, _last_refresh
    if not getattr(db, "refreshable", False) or request.method != "GET" or request.path == "/healthz":
        return
    if time.time() - _last_refresh < SHEETS_REFRESH_SECONDS:
        return
    with _lock:
        if time.time() - _last_refresh < SHEETS_REFRESH_SECONDS:
            return
        _last_refresh = time.time()
        try:
            reels = db.load_all()
        except Exception:
            app.logger.exception("sheet refresh failed; keeping cached data")


print(f"[reelshop] Storage: {db.name} - {len(reels)} reel(s) loaded.")

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _new_code(extra=(), n=6):
    used = {p["code"] for r in reels for p in r["products"]} | set(extra)
    while True:
        c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))
        if c not in used:
            return c


def _new_reel_id():
    taken = {r["id"] for r in reels}
    while True:
        c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if c not in taken:
            return c


def _valid_http_url(u):
    p = urlparse(u or "")
    return p.scheme in ("http", "https") and bool(p.netloc)


def _require_admin():
    token = request.headers.get("X-Admin-Token", "").strip()
    if not hmac.compare_digest(token.encode(), ADMIN_TOKEN.encode()):
        abort(401)


# ---- best-effort product image/title lookup (Amazon often blocks bots) -----
def _is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, TypeError):
        return False
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _valid_http_url(newurl) or not _is_public_host(urlparse(newurl).hostname):
            raise urllib.error.URLError("blocked redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _meta(html, prop):
    pats = (
        r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%s["\']',
    )
    for p in pats:
        m = re.search(p % re.escape(prop), html, re.I)
        if m:
            return unescape(m.group(1)).strip()
    return ""


def _fetch_meta(url):
    """Return {'image':..., 'title':...} when the page allows it, else {}."""
    out = {}
    try:
        if not _is_public_host(urlparse(url).hostname):
            return out
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
        })
        with urllib.request.build_opener(_SafeRedirect).open(req, timeout=4) as r:
            html = r.read(500_000).decode("utf-8", "ignore")
    except Exception:
        return out
    img = _meta(html, "og:image")
    if not img:
        m = re.search(r'id="landingImage"[^>]*data-old-hires="([^"]+)"', html)
        img = unescape(m.group(1)) if m else ""
    title = _meta(html, "og:title")
    if not title:
        m = re.search(r'id="productTitle"[^>]*>\s*([^<]+)', html)
        title = unescape(m.group(1)).strip() if m else ""
    if img and _valid_http_url(img):
        out["image"] = img
    if title:
        out["title"] = title[:140]
    return out


def _fill_metadata(items):
    todo = [it for it in items if not (it["image"] or it.get("imageData")) or not it["title"]]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=6) as ex:
        for it, meta in zip(todo, ex.map(lambda i: _fetch_meta(i["url"]), todo)):
            if not it.get("imageData"):
                it["image"] = it["image"] or meta.get("image", "")
            it["title"] = it["title"] or meta.get("title", "")


_IMG_HEADERS = ("data:image/jpeg;base64", "data:image/png;base64", "data:image/webp;base64")


def _parse_image_data(data_url):
    """Validate an uploaded image sent as a data URL. Returns (mime, base64_text)."""
    header, _, body = (data_url or "").partition(",")
    if header not in _IMG_HEADERS:
        raise ValueError("Uploaded image must be a JPG, PNG or WebP file.")
    try:
        raw = base64.b64decode(body, validate=True)
    except Exception:
        raise ValueError("Uploaded image could not be read.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Uploaded image is too large. Please use a smaller picture.")
    # trust the file's own bytes, not the label the browser sent (blocks SVG/HTML tricks)
    if raw[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Uploaded file is not a valid JPG, PNG or WebP image.")
    return mime, base64.b64encode(raw).decode()


def _strip_reel(reel):
    """Copy of a reel without the raw uploaded image bytes (served from /api/img instead)."""
    drop = ("imageData", "imageMime")
    return {**reel, "products": [{k: v for k, v in p.items() if k not in drop} for p in reel["products"]]}


def _clean_products(store, body):
    raw = body.get("products")
    if raw is None:  # older clients: plain list of URLs
        raw = [{"url": u} for u in (body.get("productUrls") or [])]
    items = []
    for it in raw:
        if isinstance(it, str):
            it = {"url": it}
        url = (it.get("url") or "").strip()
        if not url:
            continue
        if not _valid_http_url(url):
            raise ValueError(f"Invalid product link: {url}")
        item = {"url": url}
        if store == "amazon":
            image = (it.get("image") or "").strip()
            upload = it.get("imageData")
            if upload:  # an uploaded file wins over a pasted link
                item["imageMime"], item["imageData"] = _parse_image_data(upload)
                image = ""
            elif image and not _valid_http_url(image):
                raise ValueError(f"Invalid image link: {image}")
            item.update(
                title=(it.get("title") or "").strip()[:140],
                price=(it.get("price") or "").strip()[:20],
                image=image,
            )
        items.append(item)
    if len(items) > MAX_PRODUCTS:
        raise ValueError(f"Too many products (max {MAX_PRODUCTS}).")
    return items


def _public_product(store, p):
    d = {"code": p["code"]}
    if store == "amazon":
        d.update(title=p.get("title", ""), image=p.get("image", ""), price=p.get("price", ""))
    return d


# ---- public ---------------------------------------------------------------
@app.get("/api/reels")
def public_reels():
    """What customers see. Original product URLs are NOT sent, only short codes."""
    with _lock:
        out = [
            {
                "id": r["id"],
                "store": _store_of(r),
                "embedUrl": r["embedUrl"],
                "products": [{"code": p["code"]} for p in r["products"]],
            }
            for r in reversed(reels)
        ]
    return jsonify(out)


@app.get("/api/reels/<reel_id>")
def public_reel(reel_id):
    """One reel; for Amazon includes each product's title/image/price (collection page)."""
    with _lock:
        for r in reels:
            if r["id"] == reel_id:
                store = _store_of(r)
                return jsonify({
                    "id": r["id"],
                    "store": store,
                    "embedUrl": r["embedUrl"],
                    "products": [_public_product(store, p) for p in r["products"]],
                })
    return jsonify(error="Not found"), 404


@app.get("/api/img/<code>")
def product_image(code):
    """Serve an image uploaded in /admin."""
    with _lock:
        for r in reels:
            for p in r["products"]:
                if p["code"] == code and p.get("imageData"):
                    resp = Response(base64.b64decode(p["imageData"]), mimetype=p.get("imageMime", "image/jpeg"))
                    resp.headers["Cache-Control"] = "public, max-age=86400"
                    resp.headers["X-Content-Type-Options"] = "nosniff"
                    return resp
    abort(404)


@app.get("/go/<code>")
def go(code):
    with _lock:
        for r in reels:
            for p in r["products"]:
                if p["code"] == code:
                    return redirect(p["url"], code=302)
    return "This link is not available.", 404


# ---- admin ----------------------------------------------------------------
@app.get("/api/admin/reels")
def admin_list():
    _require_admin()
    with _lock:
        return jsonify([{**_strip_reel(r), "store": _store_of(r)} for r in reversed(reels)])


@app.post("/api/admin/reels")
def admin_add():
    _require_admin()
    body = request.get_json(silent=True) or {}
    reel_url = (body.get("reelUrl") or "").strip()
    store_name = (body.get("store") or "meesho").strip().lower()

    embed = _embed_url(reel_url)
    if not embed:
        return jsonify(error="Not a valid Instagram reel link."), 400
    if store_name not in STORES:
        return jsonify(error="Store must be Meesho or Amazon."), 400
    try:
        items = _clean_products(store_name, body)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    if store_name == "amazon" and len(items) < 2:
        return jsonify(error="Add at least two Amazon product links to build a collection."), 400
    if not items:
        return jsonify(error="Add at least one product link."), 400

    if store_name == "amazon":
        _fill_metadata(items)  # slow network work happens outside the lock

    with _lock:
        reel = {
            "id": _new_reel_id(),
            "store": store_name,
            "reelUrl": reel_url,
            "embedUrl": embed,
            "products": [],
            "createdAt": int(time.time() * 1000),
        }
        used = set()
        for it in items:
            code = _new_code(used)
            used.add(code)
            prod = {"code": code, **it}
            if it.get("imageData"):
                prod["image"] = f"/api/img/{code}"  # served by product_image()
            reel["products"].append(prod)
        try:
            db.add(reel)  # write to the database first; only then show it to visitors
        except Exception:
            app.logger.exception("db.add failed")
            return jsonify(error="The database is unavailable right now. Nothing was saved - please try again."), 503
        reels.append(reel)
        out = _strip_reel(reel)
    return jsonify(out), 201


@app.delete("/api/admin/reels/<reel_id>")
def admin_delete(reel_id):
    _require_admin()
    global reels
    with _lock:
        if not any(r["id"] == reel_id for r in reels):
            return jsonify(error="Not found"), 404
        try:
            db.remove(reel_id)
        except Exception:
            app.logger.exception("db.remove failed")
            return jsonify(error="The database is unavailable right now. Nothing was deleted - please try again."), 503
        reels = [r for r in reels if r["id"] != reel_id]
    return jsonify(ok=True)


@app.get("/healthz")
def health():
    return "ok"


# ---- optional: serve the built React app (run `npm run build` in frontend/) ----
# Lets http://localhost:5000/admin work with a single server. On Netlify this is unused.
DIST = os.environ.get("FRONTEND_DIST") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "dist")


@app.get("/", defaults={"path": ""})
@app.get("/<path:path>")
def spa(path):
    if path.startswith(("api/", "go/")) or not os.path.isfile(os.path.join(DIST, "index.html")):
        abort(404)
    if path and os.path.isfile(os.path.join(DIST, path)):
        return send_from_directory(DIST, path)
    return send_from_directory(DIST, "index.html")  # client-side routes: /admin, /products, /collection/..


print(f"[reelshop] Admin token loaded: {len(ADMIN_TOKEN)} chars, starts with {ADMIN_TOKEN[:2]!r}"
      + ("  (DEFAULT - set ADMIN_TOKEN!)" if ADMIN_TOKEN == "change-me" else ""))

if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5000)), debug=True)