// App shell: navigation, routing, live polling, wallpaper and glass.
import { $, $$, api, duration, esc, icon, prefs, toast } from './ui.js';
import { clearLenses, refreshLenses } from './glass.js';

export const PAGES = [
  { id: 'overview', title: 'Overview', icon: 'layout-dashboard', group: 'Monitor' },
  { id: 'performance', title: 'CPU & Memory', icon: 'cpu', group: 'Monitor' },
  { id: 'sensors', title: 'Temperature & Power', short: 'Sensors', icon: 'temperature', group: 'Monitor' },
  { id: 'storage', title: 'Storage', icon: 'database', group: 'Monitor' },
  { id: 'network', title: 'Network', icon: 'world', group: 'Monitor' },
  { id: 'activity', title: 'Activity', icon: 'activity', group: 'Monitor' },
  { id: 'cleaner', title: 'Cleaner', icon: 'brush', group: 'Tools' },
  { id: 'netlab', title: 'Network Lab', icon: 'flask', group: 'Tools' },
  { id: 'studio', title: 'Studio', icon: 'music', group: 'Tools' },
  { id: 'widgets', title: 'Widgets', icon: 'layout-grid-add', group: 'Vitals' },
  { id: 'settings', title: 'Settings', icon: 'settings', group: 'Vitals' },
];
const TABBAR = ['overview', 'storage', 'network', 'activity'];

// umbrelOS wallpapers and the accent colour umbrelOS pairs with each
export const WALLPAPERS = {
  1: '259 100% 59%', 2: '6 56% 54%', 3: '22 88% 40%', 4: '198 100% 31%', 5: '202 100% 33%', 6: '160 100% 27%',
  7: '79 100% 25%', 8: '185 100% 29%', 9: '359 64% 62%', 10: '18 75% 52%', 11: '185 100% 29%', 12: '332 84% 47%',
  13: '194 81% 39%', 14: '328 87% 49%', 15: '32 100% 36%', 16: '265 100% 42%', 17: '184 100% 25%', 18: '259 100% 59%',
  19: '204 100% 41%', 20: '259 100% 59%', 21: '12 78% 50%', 22: '92 52% 41%', 23: '24 90% 50%', 24: '209 85% 42%', 25: '174 75% 32%',
};
export const BUILTIN = {
  white: ['White', '20 92% 44%', 'light'], homekit: ['Home', '35 100% 50%', 'home'], apricot: ['Apricot', '20 92% 44%', 'light'], sand: ['Sand', '24 90% 42%', 'light'], sunset: ['Sunset', '16 88% 44%', 'light'],
  ember: ['Ember', '20 95% 55%', 'dark'], midnight: ['Midnight', '24 95% 55%', 'dark'],
};
export const ORANGE = '20 92% 44%';
export const umbrelAsset = (path) => `${location.protocol}//${location.hostname}${path}`;

export const app = {
  settings: {},
  system: {},
  live: null,
  hist: [],
  page: null,
  pageId: null,
  online: true,
  _subs: new Set(),
  onLive(fn) { this._subs.add(fn); return () => this._subs.delete(fn); },
  async saveSettings(patch) {
    this.settings = await api.put('/api/settings', patch);
    applySettings();
    return this.settings;
  },
  go(id) { location.hash = id; },
};
window.vitals = app;

// ------------------------------------------------------------------ shell

function navHTML() {
  let html = '';
  let group = '';
  for (const p of PAGES) {
    if (p.group !== group) { group = p.group; html += `<div class="navgroup">${group}</div>`; }
    html += `<a class="navitem" href="#${p.id}" data-page="${p.id}">${icon(p.icon, 19)}<span>${p.title}</span><span class="badge hide" data-badge="${p.id}"></span></a>`;
  }
  return html;
}

function renderShell() {
  $('#app').innerHTML = `
    <aside class="sidebar glass lensed" data-lens="60" data-bevel="22" aria-label="Main navigation">
      <div class="brandrow"><img src="img/icon.svg" alt=""><div><div class="t">Vitals</div><div class="s" id="statusline"><span class="dot pulse"></span>Connecting…</div></div></div>
      <button class="searchbtn" data-palette>${icon('search', 16)}Search<kbd>${/Mac/.test(navigator.platform) ? '⌘' : 'Ctrl'} K</kbd></button>
      <nav class="nav">${navHTML()}</nav>
      <div class="sidefoot" id="sidefoot"></div>
    </aside>
    <header class="topbar glass lensed" data-lens="40">
      <img src="img/icon.svg" alt=""><div class="t" id="toptitle">Vitals</div><span id="topstatus"></span>
      <button class="btn ghost icon" data-palette aria-label="Search">${icon('search', 19)}</button>
    </header>
    <main class="main" id="main"><div id="page"></div></main>
    <nav class="tabbar glass lensed" data-lens="50" data-bevel="20" aria-label="Sections">
      ${TABBAR.map((id) => { const p = PAGES.find((x) => x.id === id); return `<a href="#${id}" data-page="${id}">${icon(p.icon, 22)}<span>${p.short || p.title}</span></a>`; }).join('')}
      <a href="#" data-more>${icon('apps', 22)}<span>More</span></a>
    </nav>`;
  $('[data-more]').addEventListener('click', (e) => { e.preventDefault(); toggleMore(); });
  document.querySelectorAll('[data-palette]').forEach((b) => b.addEventListener('click', openPalette));
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  });
}

function toggleMore(force) {
  const existing = $('.moresheet');
  if (existing || force === false) { existing?.remove(); return; }
  const sheet = document.createElement('div');
  sheet.className = 'moresheet glass lensed';
  sheet.innerHTML = PAGES.filter((p) => !TABBAR.includes(p.id)).map((p) =>
    `<a href="#${p.id}" class="${app.pageId === p.id ? 'active' : ''}">${icon(p.icon, 24)}<span>${p.short || p.title}</span></a>`).join('');
  sheet.addEventListener('click', () => sheet.remove());
  document.body.appendChild(sheet);
  refreshLenses(document);
}

// ------------------------------------------------------------------ settings → look

function applySettings() {
  const s = app.settings;
  prefs.temp_unit = s.temp_unit || 'C';
  prefs.currency = s.currency || '€';
  const root = document.documentElement;
  const glassMode = s.glass || 'liquid';
  const dark = s.theme === 'dark' || (s.theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  const home = s.theme === 'home';
  const themeName = home ? 'home' : dark ? 'dark' : 'light';
  if (root.dataset.glass !== glassMode || root.dataset.theme !== themeName) {
    root.dataset.glass = glassMode;
    root.dataset.theme = themeName;
    clearLenses();
    refreshLenses(document);
  }
  root.style.setProperty('--wp-blur', `${s.wallpaper_blur ?? 0}px`);
  let wp = String(s.wallpaper || 'white');
  if (dark && BUILTIN[wp]?.[2] !== 'dark' && BUILTIN[wp]) wp = 'ember'; // keep dark mode dark
  if (home && BUILTIN[wp]) wp = 'homekit'; // Home look: warm blurred wallpaper
  if (!home && !dark && wp === 'homekit') wp = 'white';
  const wall = $('.wallpaper');
  const hsl = home ? '35 100% 50%' : s.accent === 'wallpaper' ? (BUILTIN[wp]?.[1] || WALLPAPERS[wp] || ORANGE) : ORANGE;
  root.style.setProperty('--brand-hsl', hsl);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', `hsl(${hsl.replace(/ /g, ', ')})`);
  if (wall.dataset.current === wp) return;
  wall.dataset.current = wp;
  wall.querySelector('img')?.remove();
  if (BUILTIN[wp]) { wall.dataset.builtin = wp; return; }
  wall.dataset.builtin = home ? 'homekit' : dark ? 'ember' : 'white';
  const img = new Image();
  img.alt = '';
  img.decoding = 'async';
  const sources = [
    umbrelAsset(`/assets/wallpapers/generated-avif/large/${wp}.avif`),
    umbrelAsset(`/assets/wallpapers/${wp}.jpg`),
  ];
  let i = 0;
  img.onload = () => img.classList.add('loaded');
  img.onerror = () => { if (++i < sources.length) img.src = sources[i]; else img.remove(); };
  img.src = sources[0];
  wall.appendChild(img);
}

// ------------------------------------------------------------------ routing

async function route() {
  const [id, sub = ''] = location.hash.replace('#', '').split('/');
  const meta = PAGES.find((p) => p.id === (id || 'overview')) || PAGES[0];
  app.sub = sub;
  toggleMore(false);
  if (app.pageId === meta.id && app.page) { app.page.onRoute?.(sub); return; }
  try { app.page?.destroy?.(); } catch (e) { console.error(e); }
  app.page = null;
  app.pageId = meta.id;
  $$('[data-page]').forEach((a) => a.classList.toggle('active', a.dataset.page === meta.id));
  $('#toptitle').textContent = meta.title;
  document.title = `${meta.title} · Vitals`;
  const host = $('#page');
  host.innerHTML = '<div class="page"><div class="skeleton" style="height:40px;width:260px;margin:8px 4px 24px"></div><div class="grid g4"><div class="skeleton" style="height:150px"></div><div class="skeleton" style="height:150px"></div><div class="skeleton" style="height:150px"></div><div class="skeleton" style="height:150px"></div></div></div>';
  $('#main').scrollTop = 0;
  try {
    const mod = await import(`./pages/${meta.id}.js`);
    if (app.pageId !== meta.id) return;
    host.innerHTML = '<div class="page"></div>';
    const pageEl = host.firstChild;
    app.page = await mod.default(pageEl, app);
    pageEl.querySelectorAll('.glass:not(.lensed)').forEach((g) => g.classList.add('sheen'));
    refreshLenses(document);
    if (app.live) app.page?.update?.(app.live);
  } catch (e) {
    console.error(e);
    host.innerHTML = `<div class="page"><div class="glass card">${icon('alert-circle', 22)} Couldn't open this page: ${esc(e.message)}</div></div>`;
  }
}

// ------------------------------------------------------------------ live data

function statusSummary(live) {
  const alerts = live.alerts || [];
  const crit = alerts.filter((a) => a.level === 'critical').length;
  if (crit) return ['critical', `${alerts.length} issue${alerts.length > 1 ? 's' : ''} need attention`];
  if (alerts.length) return ['warning', `${alerts.length} thing${alerts.length > 1 ? 's' : ''} to check`];
  return ['good', 'All systems normal'];
}

function updateChrome(live) {
  const [lvl, text] = statusSummary(live);
  $('#statusline').innerHTML = `<span class="dot ${lvl}"></span>${esc(text)}`;
  $('#topstatus').innerHTML = `<span class="dot ${lvl}" title="${esc(text)}"></span>`;
  const n = (live.alerts || []).length;
  const badge = $('[data-badge="overview"]');
  badge.textContent = n;
  badge.classList.toggle('hide', !n);
  badge.classList.toggle('warning', !(live.alerts || []).some((a) => a.level === 'critical'));
  const sys = app.system;
  $('#sidefoot').innerHTML = `<div><b>${esc(sys.hostname || 'umbrel')}</b>${sys.demo ? ' · <span class="pill sm brand">Demo</span>' : ''}</div>
    <div>${esc(sys.device || sys.cpuModel || '')}</div><div>Up ${esc(duration(live.uptime, 2))}</div>`;
}

let failures = 0;
async function poll() {
  if (document.hidden) return;
  try {
    const live = await api.get('/api/live');
    failures = 0;
    if (!app.online) { app.online = true; toast('Reconnected', 'good', 2000); }
    app.live = live;
    if (live?.t) {
      app.hist.push({
        t: Math.round(live.t), cpu: live.cpu.total, mem: live.memory.percent, temp: live.cpu.temp, power: live.power?.wall,
        rx: live.net.rx, tx: live.net.tx,
        dr: Object.values(live.disks || {}).reduce((a, d) => a + d.read, 0),
        dw: Object.values(live.disks || {}).reduce((a, d) => a + d.write, 0),
      });
      if (app.hist.length > 180) app.hist.splice(0, app.hist.length - 180);
    }
    updateChrome(live);
    app.page?.update?.(live);
    app._subs.forEach((fn) => fn(live));
  } catch (e) {
    if (++failures === 3) {
      app.online = false;
      $('#statusline').innerHTML = '<span class="dot critical"></span>Can\'t reach Vitals';
      toast('Lost connection to Vitals — retrying…', 'error', 4000);
    }
  }
}

async function boot() {
  renderShell();
  try {
    [app.settings, app.system] = await Promise.all([api.get('/api/settings'), api.get('/api/system')]);
    const h = await api.get('/api/history?range=5m');
    app.hist = (h.points || []).slice(-180);
  } catch (e) {
    console.error(e);
  }
  applySettings();
  refreshLenses(document);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (app.settings.theme === 'auto') applySettings(); });
  window.addEventListener('hashchange', route);
  await route();
  poll();
  setInterval(poll, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
}

// ------------------------------------------------------------------ command palette (⌘K)

const ACTIONS = [
  { t: 'Run a speed test', ic: 'brand-speedtest', go: 'network/speed-run' },
  { t: 'Scan for leftover files', ic: 'brush', go: 'cleaner/scan' },
  { t: 'Find devices on my network', ic: 'radar-2', go: 'netlab/devices' },
  { t: 'Ping a host', ic: 'activity-heartbeat', go: 'netlab/ping' },
  { t: 'Traceroute', ic: 'route', go: 'netlab/trace' },
  { t: 'DNS lookup', ic: 'list-search', go: 'netlab/dns' },
  { t: 'Check open ports', ic: 'lock-open', go: 'netlab/ports' },
  { t: 'Subnet calculator', ic: 'binary', go: 'netlab/subnet' },
  { t: 'Subnetting practice quiz', ic: 'school', go: 'netlab/quiz' },
  { t: 'LAN speed test', ic: 'transfer', go: 'netlab/lan' },
  { t: 'Speed test a drive', ic: 'gauge', go: 'storage/bench' },
  { t: 'Drive health', ic: 'stethoscope', go: 'storage' },
  { t: 'Scan my sample library', ic: 'music', go: 'studio/library' },
  { t: 'Tap tempo & delay times', ic: 'hand-click', go: 'studio/tools' },
  { t: 'Data usage', ic: 'chart-bar', go: 'network/usage' },
  { t: 'Set up notifications', ic: 'bell', go: 'settings/notify' },
  { t: 'Switch theme: Light', ic: 'palette', theme: 'light' },
  { t: 'Switch theme: Home', ic: 'home', theme: 'home' },
  { t: 'Switch theme: Dark', ic: 'palette', theme: 'dark' },
];

function openPalette() {
  if ($('.palette')) return;
  const items = [...PAGES.map((p) => ({ t: p.title, ic: p.icon, go: p.id, kind: 'Page' })), ...ACTIONS.map((a) => ({ ...a, kind: a.theme ? 'Theme' : 'Action' }))];
  const el = document.createElement('div');
  el.className = 'palette';
  el.innerHTML = `<div class="box glass" role="dialog" aria-label="Search"><input placeholder="Search pages and actions…" aria-label="Search" autocomplete="off"><div class="res" role="listbox"></div></div>`;
  document.body.appendChild(el);
  const input = el.querySelector('input');
  const res = el.querySelector('.res');
  let list = items;
  let sel = 0;
  const close = () => el.remove();
  const draw = () => {
    res.innerHTML = list.length ? list.map((x, i) => `<div class="it ${i === sel ? 'sel' : ''}" role="option" data-i="${i}">${icon(x.ic, 18)}${esc(x.t)}<small>${x.kind}</small></div>`).join('')
      : '<div class="it faint">Nothing found</div>';
    res.querySelector('.sel')?.scrollIntoView({ block: 'nearest' });
  };
  const pick = async (x) => {
    close();
    if (!x) return;
    if (x.theme) { await app.saveSettings({ theme: x.theme }); toast(`Theme: ${x.t.split(': ')[1]}`, 'good', 1500); return; }
    location.hash = x.go;
  };
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase().trim();
    list = !q ? items : items.filter((x) => q.split(/\s+/).every((w) => `${x.t} ${x.kind}`.toLowerCase().includes(w)));
    sel = 0;
    draw();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { sel = Math.min(list.length - 1, sel + 1); draw(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
    if (e.key === 'Enter') pick(list[sel]);
    if (e.key === 'Escape') close();
  });
  res.addEventListener('click', (e) => { const it = e.target.closest('[data-i]'); if (it) pick(list[+it.dataset.i]); });
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  draw();
  input.focus();
}

window.__vitalsGlass = (root) => refreshLenses(root);
boot();
