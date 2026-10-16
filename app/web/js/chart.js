// Lightweight SVG charts: time-series area/line with crosshair tooltip,
// grouped/stacked bars with hover, and sparklines.
import { esc } from './ui.js';

let uid = 0;
const NS = 'http://www.w3.org/2000/svg';

function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return step * p;
}

// Monotone cubic (Fritsch–Carlson): smooth without overshooting the data.
function monotonePath(pts) {
  const n = pts.length;
  if (n === 0) return '';
  if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
  const dx = [], dy = [], m = [], t = [];
  for (let i = 0; i < n - 1; i++) {
    dx[i] = pts[i + 1][0] - pts[i][0];
    dy[i] = pts[i + 1][1] - pts[i][1];
    m[i] = dx[i] ? dy[i] / dx[i] : 0;
  }
  t[0] = m[0];
  t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += `C${(pts[i][0] + h).toFixed(1)},${(pts[i][1] + h * t[i]).toFixed(1)} ${(pts[i + 1][0] - h).toFixed(1)},${(pts[i + 1][1] - h * t[i + 1]).toFixed(1)} ${pts[i + 1][0].toFixed(1)},${pts[i + 1][1].toFixed(1)}`;
  }
  return d;
}

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

/**
 * series: [{key, label, color}]; points: [{t, [key]: value}]
 * opts: height, yFmt(v) for axis+tooltip, max (fixed y max), minMax (floor for auto max),
 *       timeFmt(t), empty text, area (default true)
 */
export class TimeChart {
  constructor(host, opts) {
    this.host = host;
    this.o = { height: 180, area: true, yFmt: (v) => String(Math.round(v)), timeFmt: (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), empty: 'Collecting data…', ...opts };
    this.points = [];
    this.id = `c${++uid}`;
    host.classList.add('chart');
    host.innerHTML = '';
    this.svg = el('svg', { height: this.o.height, role: 'img', 'aria-label': this.o.label || 'chart' }, host);
    const defs = el('defs', {}, this.svg);
    for (const s of this.o.series) {
      const g = el('linearGradient', { id: `${this.id}-${s.key}`, x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
      el('stop', { offset: '0%', 'stop-color': s.color, 'stop-opacity': this.o.series.length > 1 ? 0.22 : 0.32 }, g);
      el('stop', { offset: '100%', 'stop-color': s.color, 'stop-opacity': 0 }, g);
    }
    this.gGrid = el('g', { class: 'grid' }, this.svg);
    this.gAxis = el('g', { class: 'axis' }, this.svg);
    this.gData = el('g', {}, this.svg);
    this.paths = this.o.series.map((s) => ({
      area: el('path', { fill: `url(#${this.id}-${s.key})` }, this.gData),
      line: el('path', { class: 'line', stroke: s.color }, this.gData),
    }));
    this.cross = el('line', { class: 'cross', y1: 0, y2: this.o.height - 18, visibility: 'hidden' }, this.svg);
    this.dots = this.o.series.map((s) => el('circle', { class: 'hoverdot', r: 4.5, fill: s.color, visibility: 'hidden' }, this.svg));
    this.emptyText = el('text', { class: 'empty', 'text-anchor': 'middle', visibility: 'hidden' }, this.svg);
    this.emptyText.textContent = this.o.empty;
    this.tip = document.createElement('div');
    this.tip.className = 'tooltip';
    host.appendChild(this.tip);
    this.ro = new ResizeObserver(() => this.render());
    this.ro.observe(host);
    host.addEventListener('pointermove', (e) => this.hover(e));
    host.addEventListener('pointerleave', () => this.unhover());
  }

  set(points) {
    this.points = points || [];
    this.render();
    if (this.hoverX != null) this.hover({ clientX: this.hoverX });
  }

  destroy() { this.ro.disconnect(); }

  render() {
    const W = this.host.clientWidth;
    if (!W) return;
    const H = this.o.height;
    const padL = 44, padR = 6, padT = 8, padB = 20;
    this.svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    this.svg.setAttribute('width', W);
    const pts = this.points;
    this.geom = { W, H, padL, padR, padT, padB };
    const keys = this.o.series.map((s) => s.key);
    let max = 0;
    for (const p of pts) for (const k of keys) if (p[k] != null && p[k] > max) max = p[k];
    max = this.o.max ?? niceMax(Math.max(max * 1.1, this.o.minMax ?? 0));
    let min = this.o.min ?? 0;
    if (this.o.autoMin && pts.length) {
      // e.g. temperatures: zoom to the data band instead of starting at 0
      let lo = Infinity, hi = -Infinity;
      for (const p of pts) for (const k of keys) if (p[k] != null) { lo = Math.min(lo, p[k]); hi = Math.max(hi, p[k]); }
      if (lo !== Infinity) {
        min = Math.max(0, Math.floor((lo - 3) / 10) * 10);
        max = Math.max(min + 20, Math.ceil((hi + 3) / 10) * 10);
      }
    }
    this.scale = { min, max };
    const t0 = pts.length ? pts[0].t : 0;
    const t1 = pts.length ? pts[pts.length - 1].t : 1;
    const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (W - padL - padR);
    const y = (v) => padT + (1 - (v - min) / (max - min || 1)) * (H - padT - padB);
    this.x = x; this.y = y;

    // grid + y labels (3 lines)
    this.gGrid.innerHTML = '';
    this.gAxis.innerHTML = '';
    for (let i = 0; i <= 2; i++) {
      const v = min + ((max - min) * i) / 2;
      const yy = y(v);
      el('line', { x1: padL, x2: W - padR, y1: yy, y2: yy }, this.gGrid);
      const tx = el('text', { x: padL - 8, y: yy + 3.5, 'text-anchor': 'end' }, this.gAxis);
      tx.textContent = this.o.yFmt(v, true);
    }
    if (pts.length > 1) {
      const a = el('text', { x: padL, y: H - 4 }, this.gAxis);
      a.textContent = this.o.timeFmt(t0);
      const b = el('text', { x: W - padR, y: H - 4, 'text-anchor': 'end' }, this.gAxis);
      b.textContent = this.o.endLabel || this.o.timeFmt(t1);
    }

    this.emptyText.setAttribute('x', W / 2);
    this.emptyText.setAttribute('y', H / 2);
    this.emptyText.setAttribute('visibility', pts.length > 1 ? 'hidden' : 'visible');

    this.o.series.forEach((s, i) => {
      // split on gaps (null values)
      const segs = [];
      let cur = [];
      for (const p of pts) {
        if (p[s.key] == null) { if (cur.length) segs.push(cur); cur = []; continue; }
        cur.push([x(p.t), y(Math.min(max, Math.max(min, p[s.key])))]);
      }
      if (cur.length) segs.push(cur);
      const line = segs.map(monotonePath).join('');
      this.paths[i].line.setAttribute('d', line);
      if (this.o.area) {
        const base = y(min);
        const area = segs.filter((sg) => sg.length > 1).map((sg) => `${monotonePath(sg)}L${sg[sg.length - 1][0].toFixed(1)},${base}L${sg[0][0].toFixed(1)},${base}Z`).join('');
        this.paths[i].area.setAttribute('d', area);
      }
    });
    this.cross.setAttribute('y1', padT);
    this.cross.setAttribute('y2', H - padB);
  }

  hover(e) {
    const pts = this.points;
    if (pts.length < 2 || !this.geom) return;
    const r = this.host.getBoundingClientRect();
    const px = e.clientX - r.left;
    this.hoverX = e.clientX;
    const { W, padL, padR } = this.geom;
    if (px < padL - 10 || px > W - padR + 10) return this.unhover();
    // nearest point by x
    let best = 0, bd = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const d = Math.abs(this.x(pts[i].t) - px);
      if (d < bd) { bd = d; best = i; }
    }
    const p = pts[best];
    const cx = this.x(p.t);
    this.cross.setAttribute('x1', cx);
    this.cross.setAttribute('x2', cx);
    this.cross.setAttribute('visibility', 'visible');
    const { min, max } = this.scale;
    this.o.series.forEach((s, i) => {
      const v = p[s.key];
      if (v == null) { this.dots[i].setAttribute('visibility', 'hidden'); return; }
      this.dots[i].setAttribute('cx', cx);
      this.dots[i].setAttribute('cy', this.y(Math.min(max, Math.max(min, v))));
      this.dots[i].setAttribute('visibility', 'visible');
    });
    const rows = this.o.series.map((s) => `<div class="tr"><span>${this.o.series.length > 1 ? `<i style="background:${s.color}"></i>` : ''}${esc(s.label)}</span><b>${p[s.key] == null ? '–' : this.o.yFmt(p[s.key])}</b></div>`).join('');
    this.tip.innerHTML = `<div class="tt">${esc(this.o.tipTime ? this.o.tipTime(p.t) : new Date(p.t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: this.o.seconds === false ? undefined : '2-digit' }))}</div>${rows}`;
    this.tip.classList.add('show');
    const tw = this.tip.offsetWidth;
    let left = cx + 14;
    if (left + tw > W) left = cx - tw - 14;
    this.tip.style.left = `${left}px`;
    this.tip.style.top = `8px`;
  }

  unhover() {
    this.hoverX = null;
    this.cross.setAttribute('visibility', 'hidden');
    this.dots.forEach((d) => d.setAttribute('visibility', 'hidden'));
    this.tip.classList.remove('show');
  }
}

/** Bars over categories. series stacked when opts.stacked. data: [{label, [key]: v}] */
export class BarChart {
  constructor(host, opts) {
    this.host = host;
    this.o = { height: 170, yFmt: (v) => String(v), stacked: true, ...opts };
    host.classList.add('chart');
    host.innerHTML = '';
    this.svg = el('svg', { height: this.o.height, role: 'img', 'aria-label': this.o.label || 'bar chart' }, host);
    this.tip = document.createElement('div');
    this.tip.className = 'tooltip';
    host.appendChild(this.tip);
    this.data = [];
    this.ro = new ResizeObserver(() => this.render());
    this.ro.observe(host);
    host.addEventListener('pointerleave', () => { this.tip.classList.remove('show'); this.svg.querySelectorAll('.barrect').forEach((r) => r.classList.remove('dim')); });
  }

  set(data) { this.data = data || []; this.render(); }
  destroy() { this.ro.disconnect(); }

  render() {
    const W = this.host.clientWidth;
    if (!W) return;
    const H = this.o.height, padL = 44, padR = 4, padT = 8, padB = 20;
    this.svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    this.svg.setAttribute('width', W);
    this.svg.innerHTML = '';
    const d = this.data;
    const keys = this.o.series.map((s) => s.key);
    let max = 0;
    for (const r of d) {
      const v = this.o.stacked ? keys.reduce((a, k) => a + (r[k] || 0), 0) : Math.max(...keys.map((k) => r[k] || 0));
      max = Math.max(max, v);
    }
    max = niceMax(max * 1.05);
    const y = (v) => padT + (1 - v / max) * (H - padT - padB);
    const g = el('g', { class: 'grid' }, this.svg);
    const ax = el('g', { class: 'axis' }, this.svg);
    for (let i = 0; i <= 2; i++) {
      const v = (max * i) / 2;
      el('line', { x1: padL, x2: W - padR, y1: y(v), y2: y(v) }, g);
      const t = el('text', { x: padL - 8, y: y(v) + 3.5, 'text-anchor': 'end' }, ax);
      t.textContent = this.o.yFmt(v, true);
    }
    const n = d.length || 1;
    const slot = (W - padL - padR) / n;
    const bw = Math.max(2, Math.min(28, slot - 3));
    d.forEach((r, i) => {
      const x0 = padL + i * slot + (slot - bw) / 2;
      let acc = 0;
      const rects = [];
      this.o.series.forEach((s, si) => {
        const v = r[s.key] || 0;
        if (v <= 0) return;
        let top, h, x = x0, w = bw;
        if (this.o.stacked) {
          top = y(acc + v);
          h = y(acc) - top - (acc > 0 ? 2 : 0); // 2px surface gap between stacked fills
          acc += v;
        } else {
          w = bw / this.o.series.length - 1;
          x = x0 + si * (w + 1);
          top = y(v);
          h = y(0) - top;
        }
        const rr = Math.min(4, w / 2, Math.max(0, h) / 2);
        rects.push(el('path', { class: 'barrect', fill: s.color, d: roundTop(x, top, w, Math.max(0, h), rr) }, this.svg));
      });
      // generous invisible hit target
      const hit = el('rect', { x: padL + i * slot, y: padT, width: slot, height: H - padT - padB, fill: 'transparent' }, this.svg);
      hit.addEventListener('pointerenter', () => {
        this.svg.querySelectorAll('.barrect').forEach((b) => b.classList.add('dim'));
        rects.forEach((b) => b.classList.remove('dim'));
        const rows = this.o.series.map((s) => `<div class="tr"><span><i style="background:${s.color}"></i>${esc(s.label)}</span><b>${this.o.yFmt(r[s.key] || 0)}</b></div>`).join('');
        this.tip.innerHTML = `<div class="tt">${esc(r.label)}</div>${rows}`;
        this.tip.classList.add('show');
        const tw = this.tip.offsetWidth;
        let left = padL + i * slot + slot + 6;
        if (left + tw > W) left = padL + i * slot - tw - 6;
        this.tip.style.left = `${left}px`;
        this.tip.style.top = '6px';
      });
      if (this.o.xLabel && (i === 0 || i === n - 1 || (n > 12 && i === Math.floor(n / 2)))) {
        const t = el('text', { x: padL + i * slot + slot / 2, y: H - 4, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }, ax);
        t.textContent = this.o.xLabel(r, i);
      }
    });
  }
}

function roundTop(x, y, w, h, r) {
  if (h <= 0) return '';
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Tiny line without axes. */
export function sparkline(host, values, color, { max, height = 34, fill = true } = {}) {
  const W = host.clientWidth || 160;
  const vals = values.filter((v) => v != null);
  if (vals.length < 2) { host.innerHTML = ''; return; }
  const m = max ?? Math.max(...vals, 1e-9) * 1.15;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * W, height - 2 - ((v ?? 0) / m) * (height - 4)]);
  const line = monotonePath(pts);
  const id = `sp${++uid}`;
  host.innerHTML = `<svg width="${W}" height="${height}" viewBox="0 0 ${W} ${height}" style="display:block;overflow:visible">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    ${fill ? `<path d="${line}L${W},${height}L0,${height}Z" fill="url(#${id})"/>` : ''}
    <path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}
