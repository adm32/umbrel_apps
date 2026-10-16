import {
  $, api, appIcon, bar, bytes, bytesHTML, clamp, duration, esc, gauge, healthPill, icon, mbps, money,
  pct, rate, rateHTML, setBar, setHTML, shortCpu, splitBytes, statusColor, temp, tempHTML, tempLevel, tempWord, updateGauge,
} from '../ui.js';
import { TimeChart, sparkline } from '../chart.js';

const KIND_ICON = { 'NVMe SSD': 'cpu-2', SSD: 'device-floppy', 'Hard drive': 'disc', 'USB drive': 'usb', 'USB hard drive': 'usb', 'SD card': 'device-floppy', eMMC: 'cpu-2' };
export const driveIcon = (k) => KIND_ICON[k] || 'database';

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

export default async function overview(el, app) {
  el.innerHTML = `
    <div class="pagehead"><div><h1>${greeting()}</h1><p id="ov-sub">Here's how your Umbrel is doing right now.</p></div></div>
    <div id="ov-alerts"></div>
    <div class="grid g4">
      <a class="glass card tile" href="#performance">${gauge(0, { id: 'g-cpu', label: 'CPU', color: 'var(--brand)' })}
        <div class="info"><div class="k">${icon('cpu', 16)}Processor</div><div class="sub" id="ov-cpu1">–</div><div class="sub" id="ov-cpu2">–</div><div class="spark" id="sp-cpu"></div></div></a>
      <a class="glass card tile" href="#performance">${gauge(0, { id: 'g-mem', label: 'Memory', color: 'var(--s4)' })}
        <div class="info"><div class="k">${icon('stack-2', 16)}Memory</div><div class="sub" id="ov-mem1">–</div><div class="sub" id="ov-mem2">–</div><div class="spark" id="sp-mem"></div></div></a>
      <a class="glass card tile" href="#sensors">${gauge(0, { id: 'g-temp', label: 'CPU temp', color: 'var(--good)' })}
        <div class="info"><div class="k">${icon('temperature', 16)}Temperature</div><div class="sub" id="ov-t1">–</div><div class="sub" id="ov-t2">–</div><div class="spark" id="sp-temp"></div></div></a>
      <a class="glass card tile" href="#sensors">${gauge(0, { id: 'g-pow', label: 'Power', color: 'var(--s3)' })}
        <div class="info"><div class="k">${icon('bolt', 16)}Power</div><div class="sub" id="ov-p1">–</div><div class="sub" id="ov-p2">–</div><div class="spark" id="sp-pow"></div></div></a>
    </div>

    <div class="grid g-main mt">
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('world', 17)}Network right now</div><a class="cardlink" href="#network">Details ${icon('chevron-right', 14)}</a></div>
        <div class="row" style="gap:28px;margin-bottom:6px">
          <div><div class="legend" style="margin:0"><span><i style="background:var(--s1)"></i>Download</span></div><div class="big" id="ov-rx">–</div></div>
          <div><div class="legend" style="margin:0"><span><i style="background:var(--s2)"></i>Upload</span></div><div class="big" id="ov-tx">–</div></div>
          <div class="grow"></div>
          <div style="text-align:right"><div class="small muted">Used today</div><div class="mid" id="ov-today">–</div></div>
        </div>
        <div id="ov-netchart"></div>
      </div>
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('database', 17)}Storage</div><a class="cardlink" href="#storage">Drives ${icon('chevron-right', 14)}</a></div>
        <div class="row between"><div class="big" id="ov-st-used">–</div><div class="small muted" id="ov-st-free"></div></div>
        <div class="bar thick" id="ov-st-bar" style="margin:10px 0 6px"><i class="brand" style="width:0"></i></div>
        <div class="list" id="ov-drives"><div class="skeleton" style="height:120px;margin-top:12px"></div></div>
      </div>
    </div>

    <div class="grid g3 mt">
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('brand-speedtest', 17)}Internet</div><span id="ov-online"></span></div>
        <div class="stats" id="ov-speed"></div>
        <a class="btn sm mt" href="#network" style="margin-top:14px">${icon('player-play', 15)}Run a speed test</a>
      </div>
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('activity', 17)}Busiest apps</div><a class="cardlink" href="#activity">Activity ${icon('chevron-right', 14)}</a></div>
        <div class="list" id="ov-apps"><div class="skeleton" style="height:150px"></div></div>
      </div>
      <div class="glass card">
        <div class="cardhead"><div class="cardtitle">${icon('clock', 17)}Today</div></div>
        <div class="stats" id="ov-todaystats"></div>
      </div>
    </div>

    <div class="section-title">Last 24 hours <small>one point per minute</small></div>
    <div class="grid g3">
      <div class="glass card"><div class="cardhead"><div class="cardtitle">CPU usage</div></div><div id="h-cpu"></div></div>
      <div class="glass card"><div class="cardhead"><div class="cardtitle">CPU temperature</div></div><div id="h-temp"></div></div>
      <div class="glass card"><div class="cardhead"><div class="cardtitle">Power draw</div></div><div id="h-pow"></div></div>
    </div>`;

  const netChart = new TimeChart($('#ov-netchart', el), {
    height: 170, series: [{ key: 'rx', label: 'Download', color: 'var(--s1)' }, { key: 'tx', label: 'Upload', color: 'var(--s2)' }],
    yFmt: (v) => rate(v), minMax: 1250, endLabel: 'now', label: 'Network throughput, last 5 minutes',
  });
  const h24 = { seconds: false, timeFmt: (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), endLabel: 'now', height: 150 };
  const hCpu = new TimeChart($('#h-cpu', el), { ...h24, series: [{ key: 'cpu', label: 'CPU', color: 'var(--brand)' }], max: 100, yFmt: (v) => `${v.toFixed(0)}%`, empty: 'History builds up over the first minutes' });
  const hTemp = new TimeChart($('#h-temp', el), { ...h24, series: [{ key: 'temp', label: 'CPU temp', color: 'var(--s2)' }], autoMin: true, yFmt: (v) => temp(v), empty: 'No temperature sensor yet' });
  const hPow = new TimeChart($('#h-pow', el), { ...h24, series: [{ key: 'power', label: 'Power', color: 'var(--s3)' }], minMax: 10, yFmt: (v) => `${v.toFixed(v < 10 ? 1 : 0)} W`, empty: 'No power reading yet' });

  let storage = null, net = null, procs = null;
  const loadHist = () => api.get('/api/history?range=24h').then((h) => {
    hCpu.set(h.points); hTemp.set(h.points); hPow.set(h.points);
    const pts = h.points.filter((p) => p.power != null);
    el.__kwh = pts.reduce((a, p) => a + p.power * 60, 0) / 3.6e6;
    el.__kwhSpan = pts.length;
  }).catch(() => {});
  const loadStorage = () => api.get('/api/storage').then((s) => { storage = s; renderDrives(); }).catch(() => {});
  const loadNet = () => api.get('/api/network').then((n) => { net = n; renderInternet(); }).catch(() => {});
  const loadProcs = () => api.get('/api/processes').then((p) => { procs = p; renderApps(); }).catch(() => {});
  loadHist(); loadStorage(); loadNet(); loadProcs();
  const timers = [setInterval(loadHist, 60000), setInterval(loadStorage, 30000), setInterval(loadNet, 20000), setInterval(loadProcs, 5000)];

  function renderDrives() {
    const st = storage?.umbrel;
    if (st) {
      const [u, uu] = splitBytes(st.used);
      setHTML($('#ov-st-used', el), `${u}<small>${uu} used</small>`);
      setHTML($('#ov-st-free', el), `${bytes(st.free)} free of ${bytes(st.total)}`);
      const f = st.used / st.total;
      setBar($('#ov-st-bar', el), f, f > 0.9 ? 'critical' : f > 0.8 ? 'warning' : 'brand');
    }
    const drives = storage?.drives || [];
    setHTML($('#ov-drives', el), drives.length ? drives.map((d) => {
      const used = d.fsTotal ? d.used / d.fsTotal : null;
      return `<a class="li" href="#storage" style="text-decoration:none;color:inherit">
        <div class="iconwell cell">${icon(driveIcon(d.kind), 20)}</div>
        <div class="grow"><div class="t">${esc(d.model)}</div><div class="d">${esc(d.kind)} · ${bytes(d.size)}${d.smart?.temp != null ? ` · ${temp(d.smart.temp)}` : ''}${used != null ? ` · ${pct(used * 100)} full` : ''}</div></div>
        ${healthPill(d.smart)}</a>`;
    }).join('') : '<div class="hint mt">' + icon('info-circle', 16) + 'No drives detected yet.</div>');
  }

  function renderInternet() {
    const last = net?.speedtest?.last;
    const lat = net?.latency?.targets?.internet;
    const online = net?.latency?.online;
    setHTML($('#ov-online', el), online == null ? '' : online ? `<span class="pill good">${icon('circle-check', 14)}Online</span>` : `<span class="pill critical">${icon('alert-circle', 14)}Offline</span>`);
    setHTML($('#ov-speed', el), `
      <div class="stat cell"><div class="k">${icon('arrow-down', 14)}Download</div><div class="v">${last ? esc(mbps(last.download)) : '–'}</div><div class="d">${last ? 'last test' : 'no test yet'}</div></div>
      <div class="stat cell"><div class="k">${icon('arrow-up', 14)}Upload</div><div class="v">${last ? esc(mbps(last.upload)) : '–'}</div><div class="d">${last ? new Date(last.at * 1000).toLocaleDateString() : ''}</div></div>
      <div class="stat cell"><div class="k">${icon('activity-heartbeat', 14)}Ping now</div><div class="v">${lat?.last != null ? `${lat.last.toFixed(0)}<small>ms</small>` : '–'}</div><div class="d">${lat?.loss ? `${lat.loss}% loss` : 'to ' + esc((lat?.label || '1.1.1.1').split(' ')[0])}</div></div>`);
  }

  function renderApps() {
    const apps = (procs?.apps || []).filter((a) => a.app && a.app !== 'system').slice(0, 4);
    setHTML($('#ov-apps', el), apps.length ? apps.map((a) => `
      <div class="li">${appIcon(a.icon, a.name)}<div class="grow"><div class="t">${esc(a.name)}</div><div class="d">${bytes(a.mem)} memory</div></div>
      <div style="width:90px;text-align:right"><div class="small num">${a.cpuSystem.toFixed(1)}%</div>${bar(a.cpuSystem / 100, 'brand', 'thin')}</div></div>`).join('')
      : '<div class="hint">' + icon('info-circle', 16) + 'Measuring app activity…</div>');
  }

  function renderToday(live) {
    const today = net?.usage?.today || [0, 0];
    const kwh = el.__kwh;
    setHTML($('#ov-todaystats', el), `
      <div class="stat cell"><div class="k">${icon('arrows-down-up', 14)}Data used</div><div class="v">${bytesHTML(today[0] + today[1])}</div><div class="d">${bytes(today[0])} down</div></div>
      <div class="stat cell"><div class="k">${icon('leaf', 14)}Energy</div><div class="v">${kwh != null && el.__kwhSpan > 30 ? `${kwh.toFixed(2)}<small>kWh</small>` : '–'}</div><div class="d">${kwh != null && el.__kwhSpan > 30 ? `≈ ${money(kwh * (app.settings.price_per_kwh || 0))} · 24 h` : 'needs ~30 min of data'}</div></div>
      <div class="stat cell"><div class="k">${icon('clock', 14)}Uptime</div><div class="v" style="font-size:16px">${esc(duration(live.uptime, 2))}</div><div class="d">since last restart</div></div>`);
  }

  function update(live) {
    const c = live.cpu, m = live.memory, p = live.power;
    updateGauge($('#g-cpu', el), c.total / 100, { value: `${c.total.toFixed(0)}<small>%</small>` });
    setHTML($('#ov-cpu1', el), `<b>${esc(shortCpu(c.cpuModel))}</b> · ${c.logical} threads`);
    setHTML($('#ov-cpu2', el), `${c.freq > 200 ? `${(c.freq / 1000).toFixed(2)} GHz · ` : ''}load ${c.load[0].toFixed(2)}`);
    updateGauge($('#g-mem', el), m.percent / 100, { value: `${m.percent.toFixed(0)}<small>%</small>` });
    setHTML($('#ov-mem1', el), `<b>${bytes(m.used)}</b> of ${bytes(m.total)}`);
    setHTML($('#ov-mem2', el), `${bytes(m.cached)} cached`);

    const lvl = tempLevel(c.temp);
    updateGauge($('#g-temp', el), c.temp != null ? clamp((c.temp - 20) / 80) : 0, { value: c.temp != null ? tempHTML(c.temp) : '–', color: statusColor(lvl) });
    const hot = (storage?.drives || []).map((d) => d.smart?.temp).filter((t) => t != null);
    setHTML($('#ov-t1', el), c.temp != null ? `${icon(lvl === 'good' ? 'circle-check' : 'alert-triangle', 14)} <b>${tempWord(lvl)}</b>` : 'No CPU sensor found');
    setHTML($('#ov-t2', el), live.fans?.length ? `Fan ${live.fans[0].rpm.toLocaleString()} rpm` : hot.length ? `Hottest drive ${temp(Math.max(...hot))}` : '');

    const w = p?.wall;
    updateGauge($('#g-pow', el), w != null ? clamp(w / 50) : 0, { value: w != null ? `${w.toFixed(w < 10 ? 1 : 0)}<small>W</small>` : '–' });
    setHTML($('#ov-p1', el), w != null ? `<b>≈ ${money(p.monthlyCost)}</b> per month` : 'No power sensor');
    setHTML($('#ov-p2', el), p?.package != null ? `CPU ${p.package.toFixed(1)} W · ${p.source === 'plug' ? 'smart plug' : 'estimated'}` : '');

    const hist = app.hist;
    sparkline($('#sp-cpu', el), hist.map((x) => x.cpu), 'var(--brand)', { max: 100 });
    sparkline($('#sp-mem', el), hist.map((x) => x.mem), 'var(--s4)', { max: 100 });
    sparkline($('#sp-temp', el), hist.map((x) => x.temp), statusColor(lvl), { max: 100 });
    sparkline($('#sp-pow', el), hist.map((x) => x.power), 'var(--s3)');

    setHTML($('#ov-rx', el), rateHTML(live.net.rx));
    setHTML($('#ov-tx', el), rateHTML(live.net.tx));
    const today = net?.usage?.today;
    if (today) setHTML($('#ov-today', el), bytesHTML(today[0] + today[1]));
    netChart.set(hist);

    const alerts = live.alerts || [];
    setHTML($('#ov-alerts', el), alerts.map((a) => `<a class="banner ${a.level}" href="#${a.page}">${icon(a.level === 'critical' ? 'alert-circle' : 'alert-triangle', 20)}<div class="grow">${esc(a.text)}</div>${icon('chevron-right', 16)}</a>`).join(''));
    setHTML($('#ov-sub', el), alerts.length
      ? `${alerts.length} thing${alerts.length > 1 ? 's' : ''} need${alerts.length > 1 ? '' : 's'} your attention.`
      : `Everything looks healthy · up ${esc(duration(live.uptime, 1))}`);
    renderToday(live);
  }

  return {
    update,
    destroy() { timers.forEach(clearInterval); [netChart, hCpu, hTemp, hPow].forEach((c) => c.destroy()); },
  };
}
