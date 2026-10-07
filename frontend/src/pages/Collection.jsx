import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { fetchReel, formatPrice } from '../api.js';
import Shell from '../components/Shell.jsx';

export default function Collection() {
  const { id } = useParams();
  const [reel, setReel] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setReel(null); setError('');
    fetchReel(id).then(setReel).catch((e) => setError(e.status === 404 ? 'This collection is not available.' : e.message));
  }, [id]);

  return (
    <Shell hero={{ small: true, title: 'Collection',
                   crumbs: [{ label: 'Shop', to: '/products' }, { label: 'Reels', to: '/products' }, { label: 'Collection' }] }}>
      {error && <div className="empty">{error}</div>}
      {!reel && !error && (
        <div className="coll-grid">{[0, 1, 2, 3].map((i) => <div className="tile-skel" key={i} />)}</div>
      )}

      {reel && (
        <div className="coll-grid">
          {reel.products.map((p, i) => (
            <a className="tile" key={p.code} href={`/go/${p.code}`} rel="nofollow noopener">
              <div className="tile-img">
                {p.image ? (
                  <img src={p.image} alt={p.title || `Product ${i + 1}`} loading="lazy" referrerPolicy="no-referrer"
                       onError={(e) => { e.currentTarget.style.display = 'none'; }} />
                ) : null}
                <span className="ph">No image</span>
              </div>
              <div className="tile-body">
                <h3 title={p.title}>{p.title || `Product ${i + 1}`}</h3>
                {p.price && <div className="price">{formatPrice(p.price)}</div>}
                <span className="tile-cta">Buy on Amazon →</span>
              </div>
            </a>
          ))}
        </div>
      )}
    </Shell>
  );
}
