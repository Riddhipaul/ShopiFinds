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
- Data lives in one array in memory and every change is written to the database: a **Google Sheet** (`GOOGLE_SHEET_ID`),
  or Postgres (`DATABASE_URL`). **With neither set it falls back to `data.json`, which Render wipes on every restart.**
- Run with a single gunicorn worker (the array is cached in process memory).
- `/api/reels` (public) never returns the original product URLs, only short codes.

## Stores: Meesho vs Amazon
Pick the store in `/admin`:
- **Meesho** – paste product links (one per line). `/products` shows a "Shop this reel" list; each link redirects straight to the product.
- **Amazon** – add 2+ products (link, optional title / image link / price). `/products` shows a **Get Products** button that opens `/collection/<id>`: a grid of product images with title and price, each tile redirecting to the exact affiliate link.
  For each Amazon product you can **upload a product photo** in `/admin` (shrunk in the browser to max 800px, stored with the
  reel in the database, served from `/api/img/<code>`) or paste an image link. The backend also tries to read missing titles
  from the product page, but Amazon often blocks that.

## One-server local run (optional)
```bash
cd frontend && npm install && npm run build      # builds frontend/dist
cd ../backend && python app.py                   # now http://localhost:5000/admin works
```
Without the build, Flask only serves `/api/*` and `/go/*`; run `npm run dev` and openinstead.


## Persistent data with Supabase (free Postgres) + Render
Render's free disk is erased on every restart/redeploy, so the app stores reels (and uploaded images) in Postgres instead.

1. **Supabase** -> New project. Save the database password (letters/numbers only is easiest; otherwise URL-encode it).
2. Click **Connect** at the top of the project -> *Connection string* -> choose the **Session pooler** URI
   (`postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-...pooler.supabase.com:5432/postgres`) and put your password in.
   The *Direct connection* host is IPv6-only, which many hosts (including Render's free tier) cannot reach.
3. **Render** -> your web service -> Environment -> add `DATABASE_URL` = that URI -> Save (it redeploys).
4. In the Render logs you should see `[reelshop] Storage: PostgreSQL - N reel(s) loaded.`
   The table `public.reels` is created automatically (with Row Level Security on, so Supabase's public REST API cannot read it).

If the URL/password is wrong the app refuses to start and says why - it never silently falls back to the disk.
Any other Postgres works too (Neon, etc.): just use its connection string as `DATABASE_URL`.
If `DATABASE_URL` is set and the table is empty, an existing `data.json` is imported once.

Good to know: free Render services sleep when idle (first visit is slow, data is safe); free Supabase projects can be
paused after a period of inactivity, so open the project now and then or ping `/healthz` from an uptime monitor.


## Persistent data with Google Sheets (alternative to Postgres)
Set `GOOGLE_SHEET_ID` and the app uses a Google Sheet as its database (it takes priority over `DATABASE_URL`).
Both image options keep working: paste an **image link**, or **upload** a photo from your device.

1. **Create the sheet**: sheets.google.com -> blank spreadsheet. Copy the ID from its URL:
   `docs.google.com/spreadsheets/d/<THIS-PART>/edit`. Leave it private.
2. **Google Cloud** (console.cloud.google.com): create a project -> *APIs & Services -> Library* -> enable **Google Sheets API**.
3. *IAM & Admin -> Service Accounts* -> Create service account (any name) -> open it -> *Keys -> Add key -> JSON*. A file downloads.
4. **Share the sheet** with the service account's e-mail (`...@...iam.gserviceaccount.com`, in the JSON as `client_email`) as **Editor**.
5. **Render -> Environment**: `GOOGLE_SHEET_ID` = the ID, `GOOGLE_SERVICE_ACCOUNT_JSON` = the whole contents of the JSON file
   (paste it as is; a base64 version also works). Save. Logs should say `[reelshop] Storage: Google Sheets - N reel(s) loaded.`
   Locally you can use `GOOGLE_SERVICE_ACCOUNT_FILE=/path/key.json` instead.

The app creates two tabs automatically:
- `reels` - one row per product: `reel_id, store, reel_url, embed_url, created_at, product_code, product_url, title, price, image_url, has_upload`.
  You can edit it by hand (fix a link, change a price, delete rows); changes appear within `SHEETS_REFRESH_SECONDS` (default 60).
  A new row needs at least `reel_id`, `product_code` and `product_url` (rows of one reel share `reel_id`, `store`, `reel_url`).
- `images` - uploaded photos. A cell holds at most 50,000 characters, so each photo's base64 text is split across rows
  (`code, mime, idx, data`). Don't edit this tab; delete a reel in `/admin` and its photo rows go too.

Notes: keep the sheet private (it contains your affiliate links). Uploaded photos are shrunk to ~100 KB in the browser, which is
~3 sheet rows each; the sheet allows 10 million cells, so this is plenty. Google allows ~60 reads/min; the app caches in memory,
so normal traffic never touches the API. If the key/ID/sharing is wrong the app refuses to start and says why.
