# Reel Shop (React + Flask)

```
reelshop-app/
├── backend/    Flask API  -> deploy to Render / Railway / PythonAnywhere
└── frontend/   React app  -> deploy to Netlify
```

Netlify only hosts the static React build, so Flask runs on a Python host and
Netlify proxies `/api/*` and `/go/*` to it (see `frontend/netlify.toml`).
Customers only ever see `yoursite.com/go/k7x2a9`.

## 1. Run locally

```bash
# terminal 1
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
 python app.py          # http://localhost:5000

# terminal 2
cd frontend
npm install
npm run dev                                   # http://localhost:5173
```
Open `/admin`, sign in with the token, add a reel. Then open `/products`.

## 2. Deploy backend (Render example)
1. Push the repo to GitHub -> Render -> New Web Service -> root dir `backend`.
2. Build: `pip install -r requirements.txt`  Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1`
3. Env vars: `ADMIN_TOKEN` (long random string), `ALLOWED_ORIGINS` (your Netlify URL).
4. Note the URL, e.g. `https://reelshop-api.onrender.com`.

## 3. Deploy frontend (Netlify)
1. In `frontend/netlify.toml`, replace both `https://YOUR-BACKEND.onrender.com` with your backend URL.
2. Netlify -> Add new site -> import repo -> base directory `frontend`
   (build `npm run build`, publish `dist` are read from netlify.toml).

## Notes
- Data is an array in memory, mirrored to `data.json`. Free hosts have ephemeral disks,
  so data resets on redeploy; swap `_load/_save` in `app.py` for Postgres/Supabase when ready.
- Run with a single gunicorn worker (the array lives in process memory).
- `/api/reels` (public) never returns the original product URLs, only short codes.

## Stores: Meesho vs Amazon
Pick the store in `/admin`:
- **Meesho** – paste product links (one per line). `/products` shows a "Shop this reel" list; each link redirects straight to the product.
- **Amazon** – add 2+ products (link, optional title / image link / price). `/products` shows a **Get Products** button that opens `/collection/<id>`: a grid of product images with title and price, each tile redirecting to the exact affiliate link.
  For each Amazon product you can **upload a product photo** in `/admin` (shrunk in the browser to max 800px, stored with the
  reel in `data.json`, served from `/api/img/<code>`) or paste an image link. The backend also tries to read missing titles
  from the product page, but Amazon often blocks that.

## One-server local run (optional)
```bash
cd frontend && npm install && npm run build      # builds frontend/dist
cd ../backend && python app.py                   # now http://localhost:5000/admin works
```
Without the build, Flask only serves `/api/*` and `/go/*`; run `npm run dev` and open http://localhost:5173/admin instead.
