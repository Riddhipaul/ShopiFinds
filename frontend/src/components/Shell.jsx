import { Link } from 'react-router-dom';
import { SITE_NAME, INSTAGRAM_HANDLE, CONTACT_EMAIL, HERO_IMAGE } from '../config.js';

const InstagramIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="5" /><circle cx="12" cy="12" r="4" /><circle cx="17.3" cy="6.7" r=".8" fill="currentColor" />
  </svg>
);

/** hero = { crumbs: [{label, to?}], title, small? } */
export default function Shell({ hero, children }) {
  return (
    <div className="page">
      <header className="topbar">
        <nav className="topbar-left"><Link to="/products">Shop</Link></nav>
        <Link className="brand" to="/products">{SITE_NAME}</Link>
        <nav className="topbar-right">
          {INSTAGRAM_HANDLE && (
            <a href={`https://www.instagram.com/${INSTAGRAM_HANDLE}/`} target="_blank" rel="noopener noreferrer"
               aria-label="Instagram"><InstagramIcon /></a>
          )}
        </nav>
      </header>

      {hero && (
        <section className={`hero ${hero.small ? 'hero--sm' : ''}`}
                 style={HERO_IMAGE ? { backgroundImage: `url(${HERO_IMAGE})` } : undefined}>
          <div className="hero-inner">
            <div className="crumbs">
              {hero.crumbs.map((c, i) => (
                <span key={i}>{i > 0 && <i>/</i>}{c.to ? <Link to={c.to}>{c.label}</Link> : c.label}</span>
              ))}
            </div>
            <h1 className="hero-title coll-title">{hero.title}</h1>
          </div>
        </section>
      )}

      <main className="site-main">{children}</main>

      <footer className="site-footer">
        <strong>Thanks for Visiting</strong>
        {(INSTAGRAM_HANDLE || CONTACT_EMAIL) && (
          <div className="contact">
            <span>Contact Us:</span>
            {INSTAGRAM_HANDLE && (
              <a href={`https://www.instagram.com/${INSTAGRAM_HANDLE}/`} target="_blank" rel="noopener noreferrer">
                @{INSTAGRAM_HANDLE}
              </a>
            )}
            {CONTACT_EMAIL && <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>}
          </div>
        )}
        <small>Made with love ❤️ . By TheClassyFinds</small>
      </footer>
    </div>
  );
}
