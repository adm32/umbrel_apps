// Liquid glass: a canvas-generated displacement map feeds an SVG
// feDisplacementMap used as a backdrop-filter, so the wallpaper bends at the
// rim of the glass like a thick lens (same technique as umbrelOS 2.0).
// Chromium renders backdrop-filter: url(); Safari/Firefox get frosted blur.

const REDUCED = matchMedia('(prefers-reduced-transparency: reduce)').matches;
export const CAN_REFRACT =
  !REDUCED &&
  /Chrom(e|ium)/.test(navigator.userAgent) &&
  !/iP(hone|ad|od)/.test(navigator.userAgent) &&
  CSS.supports('backdrop-filter', 'url(#g)');

let defs;
let n = 0;
const lenses = new Map();

function ensureDefs() {
  if (defs) return defs;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.style.position = 'absolute';
  svg.setAttribute('aria-hidden', 'true');
  defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  svg.appendChild(defs);
  document.body.appendChild(svg);
  return defs;
}

// Signed distance to a rounded rectangle centred at the origin.
function sdRoundRect(px, py, hw, hh, r) {
  const qx = Math.abs(px) - (hw - r);
  const qy = Math.abs(py) - (hh - r);
  const ox = Math.max(qx, 0), oy = Math.max(qy, 0);
  return Math.hypot(ox, oy) + Math.min(Math.max(qx, qy), 0) - r;
}

function buildMap(w, h, radius, bevel) {
  const q = 1;
  const W = Math.max(2, Math.round(w * q)), H = Math.max(2, Math.round(h * q));
  const c = document.createElement('canvas');
  c.width = W;
  c.height = H;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(W, H);
  const d = img.data;
  const hw = W / 2, hh = H / 2, r = Math.min(radius * q, hw, hh), b = bevel * q;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const px = x + 0.5 - hw, py = y + 0.5 - hh;
      const dist = -sdRoundRect(px, py, hw, hh, r); // >0 inside
      let dx = 0, dy = 0;
      if (dist > 0 && dist < b) {
        // gradient of the distance field = outward normal
        const e = 0.75;
        const gx = sdRoundRect(px + e, py, hw, hh, r) - sdRoundRect(px - e, py, hw, hh, r);
        const gy = sdRoundRect(px, py + e, hw, hh, r) - sdRoundRect(px, py - e, hw, hh, r);
        const len = Math.hypot(gx, gy) || 1;
        // convex dome: bending grows steeply toward the rim
        const t = 1 - dist / b;
        const mag = t * t * (3 - 2 * t) * Math.pow(t, 1.2);
        dx = (gx / len) * mag;
        dy = (gy / len) * mag;
      }
      const i = (y * W + x) * 4;
      // sample from inside the shape (never past the edge, which is transparent)
      d[i] = 128 - dx * 127;
      d[i + 1] = 128 - dy * 127;
      d[i + 2] = 128;
      d[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL();
}

function filterFor(el, w, h) {
  const cs = getComputedStyle(el);
  const radius = parseFloat(cs.borderTopLeftRadius) || 20;
  const bevel = +(el.dataset.bevel || 18);
  const scale = +(el.dataset.lens || 70);
  const id = el.__lensId || (el.__lensId = `lens${++n}`);
  let f = document.getElementById(id);
  if (!f) {
    f = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
    f.id = id;
    f.setAttribute('color-interpolation-filters', 'sRGB');
    ensureDefs().appendChild(f);
  }
  f.setAttribute('x', '0');
  f.setAttribute('y', '0');
  f.setAttribute('width', String(w));
  f.setAttribute('height', String(h));
  f.setAttribute('filterUnits', 'userSpaceOnUse');
  const href = buildMap(w, h, radius, bevel);
  // R/G/B displaced by slightly different amounts -> faint chromatic fringe
  f.innerHTML = `
    <feImage href="${href}" x="0" y="0" width="${w}" height="${h}" preserveAspectRatio="none" result="map"/>
    <feDisplacementMap in="SourceGraphic" in2="map" scale="${scale}" xChannelSelector="R" yChannelSelector="G" result="dR"/>
    <feDisplacementMap in="SourceGraphic" in2="map" scale="${scale * 1.06}" xChannelSelector="R" yChannelSelector="G" result="dG"/>
    <feDisplacementMap in="SourceGraphic" in2="map" scale="${scale * 1.12}" xChannelSelector="R" yChannelSelector="G" result="dB"/>
    <feColorMatrix in="dR" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r"/>
    <feColorMatrix in="dG" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g"/>
    <feColorMatrix in="dB" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b"/>
    <feBlend in="r" in2="g" mode="screen" result="rg"/>
    <feBlend in="rg" in2="b" mode="screen"/>`;
  return id;
}

function apply(el) {
  const w = Math.round(el.offsetWidth), h = Math.round(el.offsetHeight);
  if (!w || !h) return;
  const key = `${w}x${h}`;
  if (el.__lensKey === key) return;
  el.__lensKey = key;
  const id = filterFor(el, w, h);
  const blur = el.dataset.blur || '3px';
  const light = document.documentElement.dataset.theme !== 'dark';
  const value = `url(#${id}) blur(${blur}) saturate(${light ? 1.6 : 1.8}) brightness(${light ? 1.06 : 0.84})`;
  el.style.backdropFilter = value;
  el.style.webkitBackdropFilter = value;
}

export function lens(el) {
  if (!CAN_REFRACT || document.documentElement.dataset.glass !== 'liquid' || lenses.has(el)) return;
  let t;
  const ro = new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => apply(el), 120); });
  ro.observe(el);
  lenses.set(el, ro);
  apply(el);
}

export function clearLenses() {
  for (const [el, ro] of lenses) {
    ro.disconnect();
    el.style.backdropFilter = '';
    el.style.webkitBackdropFilter = '';
    el.__lensKey = null;
  }
  lenses.clear();
}

export function refreshLenses(root = document) {
  root.querySelectorAll('.lensed').forEach(lens);
  // drop observers for removed elements
  for (const [el, ro] of lenses) if (!el.isConnected) { ro.disconnect(); lenses.delete(el); }
}

// pointer-following specular sheen on cards
document.addEventListener('pointermove', (e) => {
  const card = e.target.closest?.('.glass.sheen');
  if (!card) return;
  const r = card.getBoundingClientRect();
  card.style.setProperty('--mx', `${e.clientX - r.left}px`);
  card.style.setProperty('--my', `${e.clientY - r.top}px`);
}, { passive: true });
