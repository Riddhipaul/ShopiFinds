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
DATA_FILE = os.environ.get("DATA_FILE", "data.json")

CORS(app, origins=ALLOWED_ORIGINS.split(","))

STORES = ("meesho", "amazon")
MAX_PRODUCTS = 24
MAX_IMAGE_BYTES = 700_000  # per uploaded image (the admin page shrinks images to ~100 KB)

# ---- storage: one array of reels, mirrored to a JSON file -----------------
# reel = {id, store, reelUrl, embedUrl, createdAt,
#         products: [{code, url, title?, image?, price?}]}
# store "meesho": plain redirect links.  store "amazon": a collection with images.
_lock = threading.Lock()
reels = []


def _load():
    global reels
    try:
        with open(DATA_FILE) as f:
            reels = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        reels = []


def _save():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(reels, f)
    os.replace(tmp, DATA_FILE)


_load()

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
IG_RE = re.compile(
    r"instagram\.com/(?:[^/?#]+/)?(reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.I
)


def _new_code(n=6):
    used = {p["code"] for r in reels for p in r["products"]}
    while True:
        c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(n))
        if c not in used:
            return c


def _embed_url(url):
    m = IG_RE.search(url or "")
    if not m:
        return None
    kind = "reel" if m.group(1).lower() == "reels" else m.group(1).lower()
    return f"https://www.instagram.com/{kind}/{m.group(2)}/embed"


def _valid_http_url(u):
    p = urlparse(u or "")
    return p.scheme in ("http", "https") and bool(p.netloc)


def _store_of(reel):
    return reel.get("store", "meesho")  # reels saved before stores existed


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
    store = (body.get("store") or "meesho").strip().lower()

    embed = _embed_url(reel_url)
    if not embed:
        return jsonify(error="Not a valid Instagram reel link."), 400
    if store not in STORES:
        return jsonify(error="Store must be Meesho or Amazon."), 400
    try:
        items = _clean_products(store, body)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    if store == "amazon" and len(items) < 2:
        return jsonify(error="Add at least two Amazon product links to build a collection."), 400
    if not items:
        return jsonify(error="Add at least one product link."), 400

    if store == "amazon":
        _fill_metadata(items)  # slow network work happens outside the lock

    with _lock:
        reel = {
            "id": _new_code(),
            "store": store,
            "reelUrl": reel_url,
            "embedUrl": embed,
            "products": [],
            "createdAt": int(time.time() * 1000),
        }
        reels.append(reel)  # reserve so codes stay unique across this reel
        for it in items:
            code = _new_code()
            prod = {"code": code, **it}
            if it.get("imageData"):
                prod["image"] = f"/api/img/{code}"  # served by product_image() below
            reel["products"].append(prod)
        _save()
        out = _strip_reel(reel)
    return jsonify(out), 201


@app.delete("/api/admin/reels/<reel_id>")
def admin_delete(reel_id):
    _require_admin()
    global reels
    with _lock:
        before = len(reels)
        reels = [r for r in reels if r["id"] != reel_id]
        if len(reels) == before:
            return jsonify(error="Not found"), 404
        _save()
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
