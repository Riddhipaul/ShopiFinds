import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchPublicReels } from '../api.js';
import { HERO_TITLE } from '../config.js';
import Shell from '../components/Shell.jsx';

const TABS = [
  { key: 'all', label: 'All reels' },
  { key: 'meesho', label: 'Meesho' },
  { key: 'amazon', label: 'Amazon' },
];

export default function Products() {
  const [reels, setReels] = useState(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState({});
  const [tab, setTab] = useState('all');

  useEffect(() => {
    fetchPublicReels().then(setReels).catch((e) => setError(e.message));
  }, []);

  const toggle = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));
  const label = (n) => `${n} ${n === 1 ? 'item' : 'items'}`;
  const shown = reels ? reels.filter((r) => tab === 'all' || r.store === tab) : [];

  return (
    <Shell hero={{ crumbs: [{ label: 'Shop', to: '/products' }, { label: 'Reels' }], title: HERO_TITLE }}>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key}
                  className={`tab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
        {reels && <span className="tabs-count">{shown.length} {shown.length === 1 ? 'reel' : 'reels'}</span>}
      </div>

      {error && <div className="err" style={{ textAlign: 'center' }}>{error}</div>}
      {reels && reels.length === 0 && <div className="empty">No reels yet. Check back soon.</div>}
      {reels && reels.length > 0 && shown.length === 0 && <div className="empty">No reels in this category yet.</div>}

      {reels === null && !error && (
        <div className="grid">{[0, 1, 2].map((i) => <div className="skeleton" key={i} />)}</div>
      )}

      {shown.length > 0 && (
        <div className="grid">
          {shown.map((r) => (
            <article className="card" key={r.id}>
              <div className="reel-frame">
                <iframe src={r.embedUrl} title="Instagram reel" loading="lazy" scrolling="no" allowFullScreen />
              </div>

              <div className="reel-meta">
                <span className="store-tag">{r.store === 'amazon' ? 'Amazon' : 'Meesho'}</span>
                <span>{label(r.products.length)}</span>
              </div>

              {r.store === 'amazon' ? (
                /* Amazon: several products -> opens the collection page */
                <Link className="shop-btn" to={`/collection/${r.id}`}>
                  <span>Get Products · {label(r.products.length)}</span>
                  <span>→</span>
                </Link>
              ) : (
                /* Meesho: list of links that redirect straight to the product */
                <>
                  <button className="shop-btn" aria-expanded={!!open[r.id]} onClick={() => toggle(r.id)}>
                    <span>Shop this reel · {label(r.products.length)}</span>
                    <span className="chev">▾</span>
                  </button>
                  <div className={`products ${open[r.id] ? 'open' : ''}`}>
                    {r.products.map((p, i) => (
                      <a className="product" key={p.code} href={`/go/${p.code}`} rel="nofollow noopener">
                        <span>Product {i + 1}</span>
                        <span className="tag">Buy now →</span>
                      </a>
                    ))}
                  </div>
                </>
              )}
            </article>
          ))}
        </div>
      )}
    </Shell>
  );
}
