// Same-origin calls: Netlify (and Vite in dev) proxy /api and /go to Flask.
const TOKEN_KEY = 'reelshop_admin_token';

export const getToken = () => sessionStorage.getItem(TOKEN_KEY) || '';
export const setToken = (t) => sessionStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => sessionStorage.removeItem(TOKEN_KEY);

async function request(path, opts = {}, admin = false) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (admin) headers['X-Admin-Token'] = getToken();
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || (res.status === 401 ? 'Wrong admin token.' : 'Request failed.'));
    err.status = res.status;
    throw err;
  }
  return data;
}

export const fetchPublicReels = () => request('/api/reels');
export const fetchReel = (id) => request(`/api/reels/${encodeURIComponent(id)}`);
export const fetchAdminReels = () => request('/api/admin/reels', {}, true);
// payload: { reelUrl, store: 'meesho' | 'amazon', products: [{ url, title?, image?, price? }] }
export const addReel = (payload) =>
  request('/api/admin/reels', { method: 'POST', body: JSON.stringify(payload) }, true);
export const deleteReel = (id) => request(`/api/admin/reels/${id}`, { method: 'DELETE' }, true);

export const shortLink = (code) => `${window.location.origin}/go/${code}`;

// "388" -> "₹388"; anything already containing a symbol/text is left as typed.
export const formatPrice = (p) => (/^\d[\d,]*(\.\d+)?$/.test((p || '').trim()) ? `₹${p.trim()}` : (p || '').trim());
