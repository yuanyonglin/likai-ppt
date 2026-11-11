/** 立铠 PPT brand logo (replaces the original LP monogram). */
export function BrandSymbol() {
  return <span className="brand-symbol" aria-hidden="true" style={{ background: 'none' }}>
    <img src="/static/images/lk-logo.png" alt="" style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }} />
  </span>;
}
