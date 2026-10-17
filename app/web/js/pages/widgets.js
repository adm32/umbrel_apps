import { $, api, esc, icon, setHTML, toast } from '../ui.js';

export const WIDGETS = [
  ['system', 'System at a glance', 'CPU, temperature, power and memory.'],
  ['custom', 'Your custom widget', 'Pick any four stats below.'],
  ['network', 'Network', 'Live download & upload, data used today and ping.'],
  ['thermals', 'Temperatures', 'CPU and your hottest drive, as gauges.'],
  ['power', 'Power & cost', 'Watts right now and what it costs per month.'],
  ['drives', 'Drive health', 'Every drive with health, temperature and space.'],
  ['speedtest', 'Last speed test', 'Download, upload and ping from your latest test.'],
  ['apps', 'Busiest apps', 'The apps using the most CPU right now.'],
];

const METRICS = [
  ['cpu', 'CPU usage'], ['cpu_temp', 'CPU temperature'], ['power', 'Power draw'], ['memory', 'Memory'],
  ['net_down', 'Download speed'], ['net_up', 'Upload speed'], ['net_today', 'Data used today'], ['ping', 'Ping'],
  ['storage', 'Storage used'], ['disk_read', 'Disk read'], ['disk_write', 'Disk write'], ['uptime', 'Uptime'],
  ['load', 'Load average'], ['hottest_drive', 'Hottest drive'],
];

const cell = (a, b, c) => `<div class="wcell cell"><div class="a">${esc(a || '')}</div><div class="b">${esc(b ?? '–')}<small>${esc(c || '')}</small></div></div>`;

function ring(p) {
  const r = 15, c = 2 * Math.PI * r;
  return `<svg width="38" height="38" viewBox="0 0 38 38" style="transform:rotate(-90deg)"><circle cx="19" cy="19" r="${r}" fill="none" stroke="rgb(var(--sink) / calc(.25 * var(--sink-k)))" stroke-width="4"/>
    <circle cx="19" cy="19" r="${r}" fill="none" stroke="var(--brand)" stroke-width="4" stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - Math.min(1, p || 0))}"/></svg>`;
}

export function renderWidget(w) {
  if (!w) return '<div class="wprev glass"><div class="skeleton" style="flex:1"></div></div>';
  const it = w.items || [];
  switch (w.type) {
    case 'four-stats':
      return `<div class="wprev glass"><div class="wcells c4">${it.map((x) => cell(x.title, x.text, x.subtext)).join('')}</div></div>`;
    case 'three-stats':
      return `<div class="wprev glass"><div class="wcells c3">${it.map((x) => `<div class="wcell cell" style="align-items:flex-start;gap:6px">${icon(x.icon, 18)}<div class="b" style="font-size:14px">${esc(x.text)}</div><div class="a">${esc(x.subtext)}</div></div>`).join('')}</div></div>`;
    case 'two-stats-with-guage':
      return `<div class="wprev glass"><div class="wcells" style="grid-template-columns:1fr 1fr">${it.map((x) => `<div class="wcell cell" style="flex-direction:row;align-items:center;justify-content:space-between;gap:6px"><div><div class="a">${esc(x.title)}</div><div class="b">${esc(x.text)}<small>${esc(x.subtext)}</small></div></div>${ring(x.progress)}</div>`).join('')}</div></div>`;
    case 'text-with-progress':
      return `<div class="wprev glass"><div class="wt">${esc(w.title)}</div><div class="b" style="font-size:30px;font-weight:650;letter-spacing:-0.04em;flex:1">${esc(w.text)}<small style="font-size:14px;opacity:.5">${esc(w.subtext || '')}</small></div>
        <div class="small muted ellipsis">${esc(w.progressLabel || '')}</div><div class="bar thin"><i class="brand" style="width:${Math.min(100, (w.progress || 0) * 100)}%"></i></div></div>`;
    case 'list':
      return `<div class="wprev glass" style="gap:4px">${it.length ? it.slice(0, 4).map((x) => `<div style="padding:4px 2px;border-bottom:1px solid var(--line)"><div class="ellipsis" style="font-size:13px;font-weight:600">${esc(x.text)}</div><div class="ellipsis tiny muted">${esc(x.subtext)}</div></div>`).join('') : `<div class="muted small">${esc(w.noItemsText || '')}</div>`}</div>`;
    default:
      return '<div class="wprev glass">Unknown widget</div>';
  }
}

export default async function widgets(el, app) {
  const cw = [...(app.settings.custom_widget || ['cpu', 'cpu_temp', 'power', 'net_down'])];
  el.innerHTML = `
    <div class="pagehead"><div><h1>Widgets</h1><p>Add Vitals to your umbrelOS home screen.</p></div></div>
    <div class="glass card pad-l">
      <div class="cardtitle" style="margin-bottom:14px">${icon('layout-grid-add', 17)}How to add a widget</div>
      <div class="steps">
        <div class="step"><div>Go to your umbrelOS <b>home screen</b> and <b>right-click</b> (or long-press on a phone) anywhere on the wallpaper.</div></div>
        <div class="step"><div>Choose <b>Edit widgets</b>.</div></div>
        <div class="step"><div>Scroll to <b>Vitals</b> and tap the widgets you want. umbrelOS shows up to <b>3 widgets</b> at a time, so pick your favourites.</div></div>
      </div>
      <a class="btn sm mt2" style="margin-top:18px" href="${location.protocol}//${location.hostname}/" target="_blank" rel="noopener">${icon('external-link', 14)}Open umbrelOS home</a>
    </div>

    <div class="section-title">Customize your widget</div>
    <div class="glass card pad-l"><div class="row wrap" style="gap:24px;align-items:center">
      <div class="grid g2 grow" style="min-width:260px;gap:10px">${[0, 1, 2, 3].map((i) => `<div class="field"><label>Slot ${i + 1}</label><select class="input" data-slot="${i}">${METRICS.map(([k, l]) => `<option value="${k}" ${cw[i] === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>`).join('')}</div>
      <div id="wg-custom" style="width:300px;max-width:100%"></div></div></div>

    <div class="section-title">All widgets <small>${WIDGETS.length} to choose from</small></div>
    <div class="wgrid">${WIDGETS.map(([id, name, desc]) => `<div class="glass card"><div style="font-weight:620">${esc(name)}</div><div class="small muted" style="margin-bottom:12px">${esc(desc)}</div><div data-w="${id}">${renderWidget(null)}</div></div>`).join('')}</div>`;

  async function refresh() {
    await Promise.all(WIDGETS.map(async ([id]) => {
      try {
        const w = await api.get(`/widgets/${id}`);
        setHTML(el.querySelector(`[data-w="${id}"]`), renderWidget(w));
        if (id === 'custom') setHTML($('#wg-custom', el), renderWidget(w));
      } catch { /* keep skeleton */ }
    }));
  }
  el.addEventListener('change', async (e) => {
    const s = e.target.closest('[data-slot]');
    if (!s) return;
    cw[+s.dataset.slot] = s.value;
    try { await app.saveSettings({ custom_widget: cw }); refresh(); toast('Custom widget updated', 'good', 1600); } catch (err) { toast(err.message, 'error'); }
  });
  await refresh();
  const t = setInterval(refresh, 5000);
  return { destroy() { clearInterval(t); } };
}
