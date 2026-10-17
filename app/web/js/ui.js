// Shared helpers: DOM, formatting, API, and small components.
import { icon } from './icons.js';

export { icon };
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function setHTML(el, html) {
  if (el && el.__html !== html) { el.innerHTML = html; el.__html = html; }
}
export function setText(el, text) {
  if (el && el.textContent !== String(text)) el.textContent = text;
}

// ------------------------------------------------------------------ settings-aware formatting

export const prefs = { temp_unit: 'C', currency: '€' };

export function splitBytes(n, digits) {
  if (n == null || isNaN(n)) return ['–', ''];
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0;
  let v = Math.abs(n);
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  const d = digits ?? (i === 0 ? 0 : v >= 100 ? 0 : v >= 10 ? 1 : 2);
  return [(n < 0 ? '-' : '') + v.toFixed(d), units[i]];
}
export const bytes = (n, d) => splitBytes(n, d).join(' ');
export const bytesHTML = (n, d) => { const [v, u] = splitBytes(n, d); return `${v}<small>${u}</small>`; };

export function splitRate(bps) { // bytes/s -> bits
  if (bps == null || isNaN(bps)) return ['–', ''];
  const b = bps * 8;
  if (b >= 1e9) return [(b / 1e9).toFixed(2), 'Gb/s'];
  if (b >= 1e6) return [(b / 1e6).toFixed(b >= 1e8 ? 0 : 1), 'Mb/s'];
  if (b >= 1e3) return [(b / 1e3).toFixed(0), 'kb/s'];
  return [b.toFixed(0), 'b/s'];
}
export const rate = (bps) => splitRate(bps).join(' ');
export const rateHTML = (bps) => { const [v, u] = splitRate(bps); return `${v}<small>${u}</small>`; };
export const diskRate = (bps) => { const [v, u] = splitBytes(bps); return u ? `${v} ${u}/s` : v; };
export const diskRateHTML = (bps) => { const [v, u] = splitBytes(bps); return `${v}<small>${u}/s</small>`; };
export function mbps(v) {
  if (v == null) return '–';
  return v >= 1000 ? `${(v / 1000).toFixed(2)} Gb/s` : `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} Mb/s`;
}

export function temp(c, withUnit = true) {
  if (c == null) return '–';
  const v = prefs.temp_unit === 'F' ? c * 9 / 5 + 32 : c;
  return `${v.toFixed(0)}${withUnit ? (prefs.temp_unit === 'F' ? '°F' : '°C') : ''}`;
}
export const tempHTML = (c) => c == null ? '–' : `${temp(c, false)}<small>${prefs.temp_unit === 'F' ? '°F' : '°C'}</small>`;

export function tempLevel(c, high = 80, crit = 100) {
  if (c == null) return '';
  if (c >= (crit || 100) - 5) return 'critical';
  if (c >= (high || 80)) return 'serious';
  if (c >= (high || 80) - 15) return 'warning';
  return 'good';
}
export const tempWord = (lvl) => ({ good: 'Normal', warning: 'Warm', serious: 'Hot', critical: 'Too hot' }[lvl] || '');

export function money(v) {
  if (v == null) return '–';
  return `${prefs.currency}${v.toFixed(2)}`;
}

export function duration(sec, parts = 2) {
  if (sec == null) return '–';
  sec = Math.max(0, sec);
  const units = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['min', 60]];
  const out = [];
  for (const [n, s] of units) {
    const q = Math.floor(sec / s);
    if (q > 0 && out.length < parts) { out.push(`${q} ${n}${q > 1 && n !== 'min' ? 's' : ''}`); sec -= q * s; }
    else if (out.length) break;
  }
  return out.join(', ') || 'just now';
}
export const hoursText = (h) => h == null ? '–' : duration(h * 3600);

export function ago(ts) {
  if (!ts) return 'never';
  const s = Date.now() / 1000 - ts;
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} days ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
export const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
export function shortCpu(model) {
  if (!model) return '';
  const m = model.match(/\b(i[3579]-\w+|Ryzen \d+ \w+|Celeron \w+|Pentium \w+|N\d{3,4}|J\d{4}|Xeon \S+|Core Ultra \d \w+)/i);
  return m ? m[1] : model.replace(/\(R\)|\(TM\)|CPU|@.*$/g, '').replace(/\s+/g, ' ').trim();
}
export const pct = (v, d = 0) => v == null ? '–' : `${v.toFixed(d)}%`;
export const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));

// ------------------------------------------------------------------ API

async function call(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error((data && data.error) || `${res.status} ${res.statusText}`);
  return data;
}
export const api = {
  get: (u) => call('GET', u),
  post: (u, b) => call('POST', u, b || {}),
  put: (u, b) => call('PUT', u, b || {}),
};

/** Start a background job and poll it until it ends. onUpdate gets the job each tick. */
export async function runJob(startUrl, body, onUpdate, interval = 350) {
  let job = await api.post(startUrl, body);
  let since = 0;
  let misses = 0;
  const lines = [];
  for (;;) {
    lines.push(...(job.lines || []));
    since = job.lineCount ?? lines.length;
    onUpdate?.({ ...job, allLines: lines });
    if (job.status !== 'running') return { ...job, allLines: lines };
    await new Promise((r) => setTimeout(r, interval));
    try {
      job = await api.get(`/api/jobs/${job.id}?since=${since}`);
      misses = 0;
    } catch (e) {
      // a Wi-Fi blip shouldn't abandon a job that's still running on the server
      if (++misses > 10) throw e;
      job = { ...job, lines: [] };
    }
  }
}
export const cancelJob = (id) => api.post(`/api/jobs/${id}/cancel`).catch(() => {});

// ------------------------------------------------------------------ components

export function gauge(frac, { cls = '', value = '', label = '', color = 'var(--brand)', id = '' } = {}) {
  const r = 46;
  const c = 2 * Math.PI * r;
  const arc = c * 0.75;
  const off = arc * (1 - clamp(frac || 0));
  return `<div class="gauge ${cls}" ${id ? `id="${id}"` : ''}>
    <svg viewBox="0 0 110 110"><circle class="track" cx="55" cy="55" r="${r}" fill="none" stroke-width="9" stroke-linecap="round" stroke-dasharray="${arc} ${c}"/>
    <circle class="val" cx="55" cy="55" r="${r}" fill="none" stroke-width="9" stroke-linecap="round" stroke="${color}" style="--glow:${color}"
      stroke-dasharray="${arc} ${c}" stroke-dashoffset="${off}"/></svg>
    <div class="center"><div class="v">${value}</div><div class="l">${label}</div></div></div>`;
}
export function updateGauge(el, frac, { value, label, color } = {}) {
  if (!el) return;
  const v = el.querySelector('.val');
  const r = 46;
  const c = 2 * Math.PI * r;
  const arc = c * 0.75;
  v.setAttribute('stroke-dashoffset', String(arc * (1 - clamp(frac || 0))));
  if (color) { v.setAttribute('stroke', color); v.style.setProperty('--glow', color); }
  if (value != null) setHTML(el.querySelector('.v'), value);
  if (label != null) setHTML(el.querySelector('.l'), label);
}

export const bar = (frac, cls = 'brand', extra = '') =>
  `<div class="bar ${extra}"><i class="${cls}" style="width:${(clamp(frac || 0) * 100).toFixed(1)}%"></i></div>`;
export function setBar(el, frac, cls) {
  if (!el) return;
  const i = el.querySelector('i');
  i.style.width = `${(clamp(frac || 0) * 100).toFixed(1)}%`;
  if (cls) i.className = cls;
}

export const statusColor = (lvl) => ({ good: 'var(--good)', warning: 'var(--warning)', serious: 'var(--serious)', critical: 'var(--critical)' }[lvl] || 'var(--brand)');

const HEALTH = {
  good: ['good', 'circle-check', 'Healthy'],
  warning: ['warning', 'alert-triangle', 'Needs attention'],
  critical: ['critical', 'alert-circle', 'Failing'],
  standby: ['', 'bed-flat', 'Asleep'],
  unknown: ['', 'info-circle', 'No health data'],
};
export function healthPill(sm) {
  let key = 'unknown';
  if (sm?.standby && !sm?.health) key = 'standby';
  else if (sm?.health) key = sm.health;
  const [cls, ic, text] = HEALTH[key];
  return `<span class="pill ${cls}">${icon(ic, 14)}${text}</span>`;
}

export function appIcon(url, name) {
  const letter = esc((name || '?').trim()[0] || '?');
  if (!url) return `<span class="appicon">${icon('server', 18)}</span>`;
  return `<span class="appicon"><img src="${esc(url)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('b'),{textContent:'${letter}'}))"></span>`;
}

export function empty(ic, title, text, action = '') {
  return `<div class="empty">${icon(ic, 34)}<h3>${esc(title)}</h3><p>${text}</p>${action}</div>`;
}

// modal ---------------------------------------------------------------------

export function modal({ title, sub = '', body = '', actions = [], wide = false, onClose } = {}) {
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  scrim.innerHTML = `<div class="modal glass lensed ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <button class="btn ghost icon x" data-close aria-label="Close">${icon('x', 18)}</button>
    <h2>${esc(title)}</h2>${sub ? `<p class="sub">${sub}</p>` : ''}<div class="mbody">${body}</div>
    ${actions.length ? `<div class="foot">${actions.map((a, i) => `<button class="btn ${a.cls || ''}" data-act="${i}">${a.icon ? icon(a.icon, 16) : ''}${esc(a.label)}</button>`).join('')}</div>` : ''}
  </div>`;
  const close = () => { scrim.remove(); document.removeEventListener('keydown', onKey); onClose?.(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  scrim.addEventListener('click', (e) => {
    if (e.target === scrim || e.target.closest('[data-close]')) close();
    const b = e.target.closest('[data-act]');
    if (b) {
      const a = actions[+b.dataset.act];
      const r = a.onClick?.({ close, el: scrim, button: b });
      if (r !== false && !a.keepOpen) close();
    }
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(scrim);
  window.__vitalsGlass?.(scrim);
  setTimeout(() => scrim.querySelector('.foot .btn.primary, .foot .btn.danger, input')?.focus(), 50);
  return { el: scrim, close, body: scrim.querySelector('.mbody') };
}

export function confirmDialog({ title, text, confirm = 'Continue', danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    modal({
      title, sub: text,
      actions: [
        { label: 'Cancel', cls: 'ghost', onClick: () => { done = true; resolve(false); } },
        { label: confirm, cls: danger ? 'danger' : 'primary', onClick: () => { done = true; resolve(true); } },
      ],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}

export function toast(text, type = 'good', ms = 3800) {
  let root = $('.toasts');
  if (!root) { root = document.createElement('div'); root.className = 'toasts'; document.body.appendChild(root); }
  const t = document.createElement('div');
  t.className = `toast glass ${type}`;
  t.innerHTML = `${icon(type === 'error' ? 'alert-circle' : type === 'info' ? 'info-circle' : 'circle-check', 18)}<span>${esc(text)}</span>`;
  root.appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, ms);
}

export function seg(name, options, current) {
  return `<div class="seg" role="tablist" data-seg="${name}">${options.map(([v, l]) =>
    `<button role="tab" data-v="${v}" class="${v === current ? 'on' : ''}" aria-selected="${v === current}">${l}</button>`).join('')}</div>`;
}
export function bindSeg(root, name, onChange) {
  const el = root.querySelector(`[data-seg="${name}"]`);
  el?.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    el.querySelectorAll('button').forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); });
    onChange(b.dataset.v);
  });
}

export const store = {
  get(k, d) { try { const v = localStorage.getItem('vitals:' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('vitals:' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

/** Small ⓘ button that reveals an explanation, so pages stay calm by default. */
export function info(html, label = 'More info') {
  return `<button type="button" class="infobtn" aria-label="${esc(label)}" data-info="${esc(html)}">${icon('info-circle', 15)}</button>`;
}

/** Collapsible "How it works" section (closed by default). */
export function learn(html, title = 'How it works') {
  return `<details class="learn"><summary>${icon('school', 15)}${esc(title)}</summary><div class="explain">${html}</div></details>`;
}

let tipEl = null;
document.addEventListener('click', (e) => {
  const b = e.target.closest?.('.infobtn');
  if (!b) { if (tipEl && !e.target.closest('.tip')) { tipEl.remove(); tipEl = null; } return; }
  e.preventDefault();
  e.stopPropagation();
  if (tipEl && tipEl.__for === b) { tipEl.remove(); tipEl = null; return; }
  tipEl?.remove();
  tipEl = document.createElement('div');
  tipEl.className = 'tip glass';
  tipEl.__for = b;
  tipEl.innerHTML = b.dataset.info;
  document.body.appendChild(tipEl);
  const r = b.getBoundingClientRect();
  const w = Math.min(320, window.innerWidth - 24);
  tipEl.style.width = `${w}px`;
  tipEl.style.left = `${Math.max(12, Math.min(window.innerWidth - w - 12, r.left + r.width / 2 - w / 2))}px`;
  const below = r.bottom + 8 + tipEl.offsetHeight < window.innerHeight;
  tipEl.style.top = `${below ? r.bottom + 8 : r.top - tipEl.offsetHeight - 8}px`;
}, true);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && tipEl) { tipEl.remove(); tipEl = null; } });
document.addEventListener('scroll', () => { if (tipEl) { tipEl.remove(); tipEl = null; } }, true);

export function downloadLink(href, label = 'Export CSV') {
  return `<a class="btn sm ghost" href="${esc(href)}" download title="${esc(label)}">${icon('cloud-download', 15)}<span class="hide-sm-text">${esc(label)}</span></a>`;
}
