import {
  $, ago, api, bindSeg, bytes, clamp, downloadLink, duration, esc, gauge, healthPill, icon, mbps,
  modal, money, pct, rate, rateHTML, seg, setBar, setHTML, shortCpu, splitBytes, statusColor, temp, tempHTML,
  tempLevel, tempWord, updateGauge,
} from '../ui.js';
import { TimeChart, sparkline } from '../chart.js';

const KIND_ICON = { 'NVMe SSD': 'cpu-2', SSD: 'device-floppy', 'Hard drive': 'disc', 'USB drive': 'usb', 'USB hard drive': 'usb', 'SD card': 'device-floppy', eMMC: 'cpu-2' };
export const driveIcon = (k) => KIND_ICON[k] || 'database';

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

export function timelineHTML(events, limit = 6) {
  if (!events?.length) return '<div class="small muted" style="padding:8px 0">Nothing yet — outages, restarts, new devices and drive changes will show up here.</div>';
  return `<div class="timeline">${events.slice(0, limit).map((e) => `<div class="tl"><span class="ico ${e.level}">${icon(e.icon, 15)}</span>
    <span class="ellipsis" title="${esc(e.text)}">${esc(e.text)}</span><span class="when">${esc(ago(e.at))}</span></div>`).join('')}</div>`;
}

const HIST = {
  cpu: { label: 'CPU', series: [{ key: 'cpu', label: 'CPU', color: 'var(--brand)' }], max: 100, yFmt: (v) => `${v.toFixed(0)}%` },
  temp: { label: 'Temperature', series: [{ key: 'temp', label: 'CPU temp', color: 'var(--s2)' }], autoMin: true, yFmt: (v) => temp(v) },
  power: { label: 'Power', series: [{ key: 'power', label: 'Power', color: 'var(--s3)' }], minMax: 10, yFmt: (v) => `${v.toFixed(v < 10 ? 1 : 0)} W` },
  net: { label: 'Network', series: [{ key: 'rx', label: 'Download', color: 'var(--s1)' }, { key: 'tx', label: 'Upload', color: 'var(--s2)' }], minMax: 1250, yFmt: (v) => rate(v) },
};

export default async function overview(el, app) {
  el.innerHTML = `
    <div class="pagehead"><div><h1>${greeting()}</h1><p id="ov-sub">Checking on your Umbrel…</p></div></div>
    <div id="ov-alerts"></div>
    <div class="grid g4">
      ${['cpu', 'mem', 'temp', 'pow'].map((k) => `
      <a class="glass card tile" href="#${k === 'temp' || k === 'pow' ? 'sensors' : 'performance'}">
        ${gauge(0, { id: `g-${k}`, label: { cpu: 'CPU', mem: 'Memory', temp: 'CPU temp', pow: 'Power' }[k], color: { cpu: 'var(--brand)', mem: 'var(--s4)', temp: 'var(--good)', pow: 'var(--s3)' }[k] })}
        <div class="info"><div class="k">${icon({ cpu: 'cpu', mem: 'stack-2', temp: 'temperature', pow: 'bolt' }[k], 16)}${{ cpu: 'Processor', mem: 'Memory', temp: 'Temperature', pow: 'Power' }[k]}</div>
          <div class="sub" id="ov-${k}">–</div><div class="spark" id="sp-${k}"></div></div></a>`).join('')}
    </div>

    <div class="grid g-main mt">
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('world', 17)}Network</div><a class="cardlink" href="#network">Details ${icon('chevron-right', 14)}</a></div>
        <div class="row" style="gap:28px;margin-bottom:4px">
          <div><div class="legend" style="margin:0"><span><i style="background:var(--s1)"></i>Download</span></div><div class="big" id="ov-rx">–</div></div>
          <div><div class="legend" style="margin:0"><span><i style="background:var(--s2)"></i>Upload</span></div><div class="big" id="ov-tx">–</div></div>
        </div>
        <div id="ov-netchart"></div>
      </div>
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('database', 17)}Storage</div><a class="cardlink" href="#storage">Drives ${icon('chevron-right', 14)}</a></div>
        <div class="row between"><div class="big" id="ov-st-used">–</div><div class="small muted" id="ov-st-free"></div></div>
        <div class="bar thick" id="ov-st-bar" style="margin:10px 0 4px"><i class="brand" style="width:0"></i></div>
        <div class="list" id="ov-drives"><div class="skeleton" style="height:110px;margin-top:12px"></div></div>
      </div>
    </div>

    <div class="grid g2 mt">
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('clock', 17)}Recent activity</div><button class="btn sm ghost" id="ov-allev">See all</button></div>
        <div id="ov-events"><div class="skeleton" style="height:160px"></div></div>
      </div>
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('sparkles', 17)}At a glance</div></div>
        <div class="glance" id="ov-glance"></div>
      </div>
    </div>

    <div class="glass card mt">
      <div class="cardhead"><div class="cardtitle">${icon('chart-area-line', 17)}Last 24 hours</div>
        <div class="row" style="gap:8px">${seg('hist', Object.entries(HIST).map(([k, v]) => [k, v.label]), 'cpu')}${downloadLink('/api/export/history.csv?range=24h', 'CSV')}</div></div>
      <div id="ov-hist"></div>
    </div>`;

  const netChart = new TimeChart($('#ov-netchart', el), {
    height: 150, series: HIST.net.series, yFmt: HIST.net.yFmt, minMax: 1250, endLabel: 'now', label: 'Network throughput, last 5 minutes',
  });
  let histKey = 'cpu';
  let histPts = [];
  let hist = null;
  const drawHist = () => {
    hist?.destroy();
    const h = HIST[histKey];
    hist = new TimeChart($('#ov-hist', el), { height: 190, seconds: false, endLabel: 'now', empty: 'History builds up over the first minutes',
      timeFmt: (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), ...h });
    hist.set(histPts);
  };
  bindSeg(el, 'hist', (k) => { histKey = k; drawHist(); });
  drawHist();

  let storage = null, net = null, procs = null, evs = null;
  const loadHist = () => api.get('/api/history?range=24h').then((h) => {
    histPts = h.points;
    hist.set(histPts);
    const pts = h.points.filter((p) => p.power != null);
    el.__kwh = pts.length > 30 ? pts.reduce((a, p) => a + p.power * 60, 0) / 3.6e6 : null;
  }).catch(() => {});
  const loadStorage = () => api.get('/api/storage').then((s) => { storage = s; renderDrives(); }).catch(() => {});
  const loadNet = () => api.get('/api/network').then((n) => { net = n; renderGlance(); }).catch(() => {});
  const loadProcs = () => api.get('/api/processes').then((p) => { procs = p; renderGlance(); }).catch(() => {});
  const loadEvents = () => api.get('/api/events?limit=200').then((e) => { evs = e; setHTML($('#ov-events', el), timelineHTML(e.events, 5)); renderGlance(); }).catch(() => {});
  loadHist(); loadStorage(); loadNet(); loadProcs(); loadEvents();
  const timers = [setInterval(loadHist, 60000), setInterval(loadStorage, 30000), setInterval(loadNet, 20000),
    setInterval(loadProcs, 6000), setInterval(loadEvents, 15000)];

  $('#ov-allev', el).addEventListener('click', () => {
    modal({ title: 'Activity', sub: 'Outages, restarts, drives, alerts and new devices.', wide: true,
      body: `${timelineHTML(evs?.events, 200)}<div class="row mt" style="justify-content:flex-end">${downloadLink('/api/export/events.csv', 'Export CSV')}</div>` });
  });

  function renderDrives() {
    const st = storage?.umbrel;
    if (st) {
      const [u, uu] = splitBytes(st.used);
      setHTML($('#ov-st-used', el), `${u}<small>${uu} used</small>`);
      setHTML($('#ov-st-free', el), `${bytes(st.free)} free`);
      const f = st.used / st.total;
      setBar($('#ov-st-bar', el), f, f > 0.9 ? 'critical' : f > 0.8 ? 'warning' : 'brand');
    }
    const drives = storage?.drives || [];
    setHTML($('#ov-drives', el), drives.length ? drives.map((d) => `<a class="li" href="#storage" style="text-decoration:none;color:inherit">
        <div class="iconwell cell">${icon(driveIcon(d.kind), 19)}</div>
        <div class="grow"><div class="t">${esc(d.model)}</div><div class="d">${bytes(d.size)}${d.smart?.temp != null ? ` · ${temp(d.smart.temp)}` : ''}${d.fsTotal ? ` · ${pct(100 * d.used / d.fsTotal)} full` : ''}</div></div>
        ${healthPill(d.smart)}</a>`).join('')
      : `<div class="hint mt">${icon('info-circle', 16)}No drives detected yet.</div>`);
  }

  function renderGlance() {
    const last = net?.speedtest?.last;
    const lat = net?.latency?.targets?.internet;
    const up = evs?.uptime?.day || net?.latency?.uptime?.day;
    const today = net?.usage?.today;
    const top = (procs?.apps || []).filter((a) => a.app && a.app !== 'system')[0];
    const online = net?.latency?.online;
    const row = (ic, k, v) => `<div class="g">${icon(ic, 17)}<span class="muted">${k}</span><span class="v">${v}</span></div>`;
    setHTML($('#ov-glance', el), [
      row(online === false ? 'alert-circle' : 'world', 'Internet', online === false ? '<span style="color:var(--critical-text)">Offline</span>'
        : lat?.last != null ? `Online · ${lat.last.toFixed(0)} ms` : 'Online'),
      row('shield-check', 'Uptime (24 h)', up ? `${up.percent >= 99.995 ? '100' : up.percent.toFixed(2)}%${up.outages ? ` · ${up.outages} outage${up.outages > 1 ? 's' : ''}` : ''}` : '–'),
      row('brand-speedtest', 'Last speed test', last ? `${esc(mbps(last.download))} ↓ ${esc(mbps(last.upload))} ↑` : '<a href="#network/speed-run">Run one</a>'),
      row('arrows-down-up', 'Data today', today ? bytes(today[0] + today[1]) : '–'),
      row('leaf', 'Energy (24 h)', el.__kwh != null ? `${el.__kwh.toFixed(2)} kWh · ${money(el.__kwh * (app.settings.price_per_kwh || 0))}` : '–'),
      row('activity', 'Busiest app', top ? `${esc(top.name)} · ${top.cpuSystem.toFixed(1)}%` : '–'),
    ].join(''));
  }

  function update(live) {
    const c = live.cpu, m = live.memory, p = live.power;
    updateGauge($('#g-cpu', el), c.total / 100, { value: `${c.total.toFixed(0)}<small>%</small>` });
    setHTML($('#ov-cpu', el), `<b>${esc(shortCpu(c.cpuModel))}</b>${c.freq > 200 ? ` · ${(c.freq / 1000).toFixed(1)} GHz` : ''}`);
    updateGauge($('#g-mem', el), m.percent / 100, { value: `${m.percent.toFixed(0)}<small>%</small>` });
    setHTML($('#ov-mem', el), `<b>${bytes(m.used)}</b> of ${bytes(m.total)}`);
    const lvl = tempLevel(c.temp);
    updateGauge($('#g-temp', el), c.temp != null ? clamp((c.temp - 20) / 80) : 0, { value: c.temp != null ? tempHTML(c.temp) : '–', color: statusColor(lvl) });
    setHTML($('#ov-temp', el), c.temp != null ? `${icon(lvl === 'good' ? 'circle-check' : 'alert-triangle', 14)} <b>${tempWord(lvl)}</b>` : 'No sensor found');
    const w = p?.wall;
    updateGauge($('#g-pow', el), w != null ? clamp(w / 50) : 0, { value: w != null ? `${w.toFixed(w < 10 ? 1 : 0)}<small>W</small>` : '–' });
    setHTML($('#ov-pow', el), w != null ? `<b>≈ ${money(p.monthlyCost)}</b> / month` : 'No power sensor');

    const h = app.hist;
    sparkline($('#sp-cpu', el), h.map((x) => x.cpu), 'var(--brand)', { max: 100 });
    sparkline($('#sp-mem', el), h.map((x) => x.mem), 'var(--s4)', { max: 100 });
    sparkline($('#sp-temp', el), h.map((x) => x.temp), statusColor(lvl), { max: 100 });
    sparkline($('#sp-pow', el), h.map((x) => x.power), 'var(--s3)');
    setHTML($('#ov-rx', el), rateHTML(live.net.rx));
    setHTML($('#ov-tx', el), rateHTML(live.net.tx));
    netChart.set(h);

    const alerts = live.alerts || [];
    setHTML($('#ov-alerts', el), alerts.map((a) => `<a class="banner ${a.level}" href="#${a.page}">${icon(a.level === 'critical' ? 'alert-circle' : 'alert-triangle', 20)}<div class="grow">${esc(a.text)}</div>${icon('chevron-right', 16)}</a>`).join(''));
    setHTML($('#ov-sub', el), alerts.length
      ? `${alerts.length} thing${alerts.length > 1 ? 's' : ''} need${alerts.length > 1 ? '' : 's'} your attention.`
      : `Everything looks healthy · up ${esc(duration(live.uptime, 1))}`);
  }

  return {
    update,
    destroy() { timers.forEach(clearInterval); netChart.destroy(); hist?.destroy(); },
  };
}
