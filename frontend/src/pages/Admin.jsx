import { useEffect, useState } from 'react';
import {
  addReel, deleteReel, fetchAdminReels, shortLink,
  getToken, setToken, clearToken,
} from '../api.js';

const emptyRow = () => ({ url: '', title: '', image: '', price: '', imageData: '' });

// Shrink a chosen photo in the browser (max 800px, JPEG) so uploads stay small and fast.
async function fileToDataUrl(file, max = 800) {
  if (!file.type.startsWith('image/')) throw new Error('not an image');
  const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
  const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(bmp.width * scale));
  c.height = Math.max(1, Math.round(bmp.height * scale));
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.drawImage(bmp, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.85);
}

export default function Admin() {
  const [authed, setAuthed] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [reels, setReels] = useState([]);

  const [store, setStore] = useState('meesho');
  const [reelUrl, setReelUrl] = useState('');
  const [prods, setProds] = useState('');                 // meesho: one link per line
  const [rows, setRows] = useState([emptyRow(), emptyRow()]); // amazon: product rows

  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState('');

  const load = async () => {
    try {
      setReels(await fetchAdminReels());
      setAuthed(true);
      setError('');
    } catch (e) {
      setAuthed(false);
      if (e.status === 401 && getToken()) { clearToken(); setError('Wrong admin token.'); }
      else if (e.status !== 401) setError(e.message);
    }
  };

  useEffect(() => { if (getToken()) load(); }, []);

  const login = (e) => { e.preventDefault(); setToken(tokenInput.trim()); load(); };

  const setRow = (i, k, v) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const addRow = () => setRows((rs) => [...rs, emptyRow()]);
  const pickImage = async (i, file) => {
    if (!file) return;
    setError('');
    try { setRow(i, 'imageData', await fileToDataUrl(file)); }
    catch { setError('Could not read that image. Please choose a JPG, PNG or WebP file.'); }
  };
  const removeRow = (i) => setRows((rs) => (rs.length > 2 ? rs.filter((_, j) => j !== i) : rs));

  const submit = async (e) => {
    e.preventDefault();
    setError(''); setNotice(''); setBusy(true);
    try {
      const products = store === 'amazon'
        ? rows.filter((r) => r.url.trim())
        : prods.split('\n').map((u) => ({ url: u.trim() })).filter((p) => p.url);
      const reel = await addReel({ reelUrl, store, products });
      const noImg = store === 'amazon' ? reel.products.filter((p) => !p.image).length : 0;
      setNotice(noImg
        ? `Added. ${noImg} product(s) have no image (Amazon blocked auto-fetch). Delete and re-add with an image link to fix.`
        : 'Reel added.');
      setReelUrl(''); setProds(''); setRows([emptyRow(), emptyRow()]);
      await load();
    } catch (ex) { setError(ex.message); }
    finally { setBusy(false); }
  };

  const remove = async (id) => {
    if (!window.confirm('Delete this reel and its links?')) return;
    try { await deleteReel(id); await load(); } catch (ex) { setError(ex.message); }
  };

  const copy = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(''), 1200);
  };

  if (!authed) {
    return (
      <div className="wrap">
        <h1>Admin</h1>
        <form className="card" onSubmit={login}>
          <label htmlFor="tok">Admin token</label>
          <input id="tok" type="password" value={tokenInput}
                 onChange={(e) => setTokenInput(e.target.value)} required />
          <div className="err">{error}</div>
          <button type="submit">Sign in</button>
        </form>
      </div>
    );
  }

  return (
    <div className="wrap">
      <h1>Admin</h1>
      <form className="card" onSubmit={submit}>
        <label htmlFor="store">Store</label>
        <select id="store" value={store} onChange={(e) => setStore(e.target.value)}>
          <option value="meesho">Meesho — links redirect straight to the product</option>
          <option value="amazon">Amazon — multiple products shown as a collection</option>
        </select>

        <label htmlFor="reel">Reel link</label>
        <input id="reel" type="url" required value={reelUrl}
               onChange={(e) => setReelUrl(e.target.value)}
               placeholder="https://www.instagram.com/reel/XXXXXXXXXXX/" />

        {store === 'meesho' ? (
          <>
            <label htmlFor="prods">Product links <span className="hint">(one per line)</span></label>
            <textarea id="prods" required value={prods} onChange={(e) => setProds(e.target.value)}
                      placeholder={'https://www.meesho.com/...\nhttps://www.meesho.com/...'} />
          </>
        ) : (
          <>
            <label>Amazon products <span className="hint">(at least 2)</span></label>
            <p className="hint tip">
              Upload a product photo (or paste an image link). The title is optional; we try to read missing
              titles from the link, but Amazon often blocks that.
            </p>
            {rows.map((r, i) => (
              <div className="prow" key={i}>
                <div className="prow-head">
                  <strong>Product {i + 1}</strong>
                  {rows.length > 2 && (
                    <button type="button" className="ghost small" onClick={() => removeRow(i)}>Remove</button>
                  )}
                </div>
                <input type="url" placeholder="Product link (your affiliate link)" required={i < 2}
                       value={r.url} onChange={(e) => setRow(i, 'url', e.target.value)} />
                <input placeholder="Title (optional)" value={r.title} maxLength={140}
                       onChange={(e) => setRow(i, 'title', e.target.value)} />
                <div className="prow-2">
                  <input type="url" placeholder="or paste an image link" value={r.image}
                         onChange={(e) => setRow(i, 'image', e.target.value)} />
                  <input placeholder="Price" value={r.price} maxLength={20}
                         onChange={(e) => setRow(i, 'price', e.target.value)} />
                </div>
                <div className="upload">
                  {r.imageData ? (
                    <>
                      <img className="thumb lg" src={r.imageData} alt="Uploaded preview" />
                      <span className="hint">Uploaded image will be used</span>
                      <button type="button" className="ghost small" onClick={() => setRow(i, 'imageData', '')}>Remove image</button>
                    </>
                  ) : (
                    <label className="upload-btn">
                      Upload image
                      <input type="file" accept="image/*" hidden
                             onChange={(e) => { pickImage(i, e.target.files[0]); e.target.value = ''; }} />
                    </label>
                  )}
                </div>
              </div>
            ))}
            <button type="button" className="ghost small" onClick={addRow}>+ Add another product</button>
          </>
        )}

        <div className="err">{error}</div>
        {notice && <div className="ok">{notice}</div>}
        <button type="submit" disabled={busy}>{busy ? 'Adding…' : 'Add reel'}</button>
      </form>

      <h1>Saved reels</h1>
      {reels.length === 0 && <div className="empty">Nothing added yet.</div>}
      {reels.map((r) => (
        <div className="card" key={r.id}>
          <div className="reel-head">
            <span className={`badge ${r.store}`}>{r.store === 'amazon' ? 'Amazon' : 'Meesho'}</span>
            <a href={r.reelUrl} target="_blank" rel="noopener noreferrer">{r.reelUrl}</a>
            <button className="ghost small" onClick={() => remove(r.id)}>Delete</button>
          </div>
          {r.store === 'amazon' && (
            <div className="row">
              <code>{`${window.location.origin}/collection/${r.id}`}</code>
              <button className="small" onClick={() => copy(`${window.location.origin}/collection/${r.id}`)}>
                {copied.endsWith(`/collection/${r.id}`) ? 'Copied' : 'Copy page'}
              </button>
            </div>
          )}
          {r.products.map((p) => (
            <div key={p.code}>
              <div className="row">
                {r.store === 'amazon' && (
                  p.image
                    ? <img className="thumb" src={p.image} alt="" referrerPolicy="no-referrer" />
                    : <span className="thumb none">No img</span>
                )}
                <code>{shortLink(p.code)}</code>
                <button className="small" onClick={() => copy(shortLink(p.code))}>
                  {copied === shortLink(p.code) ? 'Copied' : 'Copy'}
                </button>
              </div>
              {p.title && <div className="orig"><b>{p.title}</b>{p.price ? ` · ${p.price}` : ''}</div>}
              <div className="orig">→ {p.url}</div>
            </div>
          ))}
        </div>
      ))}
      <button className="ghost small" onClick={() => { clearToken(); setAuthed(false); }}>Sign out</button>
    </div>
  );
}
