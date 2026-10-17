import {
  $, ago, api, bindSeg, bytes, bytesHTML, cancelJob, clamp, downloadLink, esc, gauge, icon, info, mbps, rate,
  rateHTML, runJob, seg, setHTML, toast, updateGauge,
} from '../ui.js';
import { BarChart, TimeChart } from '../chart.js';
import { timelineHTML } from './overview.js';

const GRADE_TEXT = {
  'A+': 'Excellent — calls and games stay smooth even while someone downloads.',
  A: 'Great — your connection stays responsive under load.',
  B: 'Good — a little lag can creep in when the line is busy.',
  C: 'Fair — calls may stutter during big downloads or uploads.',
  D: 'Poor — turn on "SQM" or "QoS" in your router to fix this.',
  F: 'Bad — heavy lag under load. Enable SQM/QoS (cake or fq_codel) on your router.',
};
const KIND = { ethernet: ['Ethernet', 'network'], wifi: ['Wi-Fi', 'wifi'], vpn: ['VPN', 'shield-check'], virtual: ['Virtual', 'box'] };
const TABS = [['live', 'Live'], ['speed', 'Speed test'], ['usage', 'Data usage'], ['ports', 'Ports']];
const SERIES_COLOR = { gateway: 'var(--s1)', internet: 'var(--s2)' };

export default async function network(el, app) {
  let tab = (app.sub || 'live').replace('-run', '');
  if (!TABS.some(([k]) => k === tab)) tab = 'live';
  const autoRun = app.sub === 'speed-run';

  el.innerHTML = `
    <div class="pagehead"><div><h1>Network</h1><p>Your connection, traffic and internet speed.</p></div>
      <div class="actions">${seg('tab', TABS, tab)}</div></div>
    <section data-tab="live">
      <div class="grid g-main">
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('arrows-down-up', 17)}Live traffic</div></div>
          <div class="row" style="gap:30px;margin-bottom:6px">
            <div><div class="legend" style="margin:0"><span><i style="background:var(--s1)"></i>Download</span></div><div class="big" id="nw-rx">–</div></div>
            <div><div class="legend" style="margin:0"><span><i style="background:var(--s2)"></i>Upload</span></div><div class="big" id="nw-tx">–</div></div>
          </div>
          <div id="nw-live"></div>
        </div>
        <div class="glass card pad-l" id="nw-conn"><div class="skeleton" style="height:240px"></div></div>
      </div>
      <div class="grid g-main mt">
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('activity-heartbeat', 17)}Latency ${info('Your Umbrel pings your router and the internet every 5 seconds. <b>Jitter</b> is how much the delay varies; <b>loss</b> is pings that never came back. Both matter more than raw speed for calls and gaming.')}</div></div>
          <div class="stats" id="nw-lat"></div>
          <div id="nw-latchart" class="mt"></div>
          <div class="legend" id="nw-latlegend"></div>
        </div>
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('shield-check', 17)}Internet uptime</div></div>
          <div class="stats" id="nw-up"></div>
          <div class="small faint" style="margin:14px 0 4px">Recent outages</div>
          <div id="nw-outages"></div>
        </div>
      </div>
    </section>

    <section data-tab="speed">
      <div class="grid g-main">
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('brand-speedtest', 17)}Speed test ${info('Runs from your Umbrel to Cloudflare\'s nearest server, so it measures your internet line — not your Wi-Fi. Uses about 0.5–2 GB of data on fast lines.')}</div></div>
          <div class="row wrap" style="gap:24px;align-items:center">
            <div class="speedgauge">${gauge(0, { id: 'nw-sg', cls: 'lg', value: '–', label: 'Mb/s', color: 'var(--s1)' })}
              <button class="btn primary lg" id="nw-start">${icon('player-play', 18)}Start test</button></div>
            <div class="grow" style="min-width:240px"><div class="stats" id="nw-res"></div><div id="nw-bloat" class="mt"></div></div>
          </div>
        </div>
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('chart-area-line', 17)}Past tests</div>${downloadLink('/api/export/speedtests.csv', 'CSV')}</div>
          <div id="nw-hist"></div>
          <div class="legend"><span><i style="background:var(--s1)"></i>Download</span><span><i style="background:var(--s2)"></i>Upload</span></div>
          <div class="small faint mt" id="nw-auto"></div>
        </div>
      </div>
    </section>

    <section data-tab="usage">
      <div class="grid g-main">
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('chart-bar', 17)}<span id="nw-utitle">Last 30 days</span> ${info('Counts everything your Umbrel sends and receives, including devices streaming from it (Jellyfin, Immich, file shares). Set the day your data plan resets in Settings.')}</div>${seg('urange', [['30d', '30 days'], ['24h', '24 hours']], '30d')}</div>
          <div id="nw-usage"></div>
          <div class="legend"><span><i style="background:var(--s1)"></i>Downloaded</span><span><i style="background:var(--s2)"></i>Uploaded</span></div>
        </div>
        <div class="glass card pad-l"><div class="stats" id="nw-totals" style="grid-template-columns:1fr 1fr"></div></div>
      </div>
    </section>

    <section data-tab="ports"><div class="grid g3" id="nw-ifaces"></div></section>`;

  const charts = {};
  const makeCharts = () => {
    if (tab === 'live' && !charts.live) {
      charts.live = new TimeChart($('#nw-live', el), { height: 190, minMax: 1250, yFmt: (v) => rate(v), endLabel: 'now', label: 'Live network traffic',
        series: [{ key: 'rx', label: 'Download', color: 'var(--s1)' }, { key: 'tx', label: 'Upload', color: 'var(--s2)' }] });
      charts.lat = new TimeChart($('#nw-latchart', el), { height: 160, area: false, minMax: 20, yFmt: (v) => `${v.toFixed(v < 10 ? 1 : 0)} ms`, endLabel: 'now', empty: 'Collecting pings…',
        series: [{ key: 'gateway', label: 'Your router', color: SERIES_COLOR.gateway }, { key: 'internet', label: 'Internet', color: SERIES_COLOR.internet }] });
    }
    if (tab === 'speed' && !charts.hist) {
      charts.hist = new TimeChart($('#nw-hist', el), { height: 190, area: false, minMax: 10, yFmt: (v) => mbps(v), tipTime: (t) => new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }),
        timeFmt: (t) => new Date(t * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }), empty: 'Run your first test',
        series: [{ key: 'download', label: 'Download', color: 'var(--s1)' }, { key: 'upload', label: 'Upload', color: 'var(--s2)' }] });
    }
    if (tab === 'usage' && !charts.usage) {
      charts.usage = new BarChart($('#nw-usage', el), { height: 200, yFmt: (v) => bytes(v), xLabel: (r) => r.short,
        series: [{ key: 'rx', label: 'Downloaded', color: 'var(--s1)' }, { key: 'tx', label: 'Uploaded', color: 'var(--s2)' }] });
    }
  };
  const showTab = (t) => {
    tab = t;
    el.querySelectorAll('section[data-tab]').forEach((s) => s.classList.toggle('hide', s.dataset.tab !== t));
    el.querySelectorAll('[data-seg="tab"] button').forEach((b) => b.classList.toggle('on', b.dataset.v === t));
    history.replaceState(null, '', `#network/${t}`);
    makeCharts();
    if (net) render();
  };
  bindSeg(el, 'tab', showTab);

  let net = null;
  let outages = [];
  let urange = '30d';
  let showIp = false;
  let running = null;
  bindSeg(el, 'urange', (v) => { urange = v; renderUsage(); });

  async function load() {
    try {
      [net, outages] = await Promise.all([api.get('/api/network'),
        api.get('/api/events?limit=200').then((e) => e.events.filter((x) => x.kind === 'outage' || x.kind === 'lan_outage'))]);
      render();
    } catch { /* keep last */ }
  }

  function renderUsage() {
    const u = net.usage;
    if (charts.usage) {
      if (urange === '30d') {
        setHTML($('#nw-utitle', el), 'Last 30 days');
        charts.usage.set(u.days.map((d) => ({ ...d, label: new Date(d.day + 'T12:00').toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }), short: new Date(d.day + 'T12:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) })));
      } else {
        setHTML($('#nw-utitle', el), 'Last 24 hours');
        charts.usage.set(u.hours.map((h) => ({ ...h, label: `${h.hour.slice(11)}:00 – ${h.hour.slice(11)}:59`, short: `${h.hour.slice(11)}:00` })));
      }
    }
    const tot = (a) => a[0] + a[1];
    setHTML($('#nw-totals', el), `
      <div class="stat cell"><div class="k">Today</div><div class="v">${bytesHTML(tot(u.today))}</div><div class="d">↓ ${bytes(u.today[0])} · ↑ ${bytes(u.today[1])}</div></div>
      <div class="stat cell"><div class="k">Billing cycle</div><div class="v">${bytesHTML(tot(u.cycle))}</div><div class="d">since ${new Date(u.cycleStart + 'T12:00').toLocaleDateString([], { month: 'short', day: 'numeric' })}</div></div>
      <div class="stat cell"><div class="k">This month</div><div class="v">${bytesHTML(tot(u.month))}</div><div class="d">↓ ${bytes(u.month[0])}</div></div>
      <div class="stat cell"><div class="k">Daily average</div><div class="v">${bytesHTML(u.days.reduce((a, d) => a + d.rx + d.tx, 0) / 30)}</div><div class="d">last 30 days</div></div>`);
  }

  function render() {
    if (tab === 'live') renderLive();
    if (tab === 'speed') {
      const st = net.speedtest || {};
      if (!running) showResult(st.last);
      charts.hist?.set((st.history || []).map((h) => ({ t: h.at, download: h.download, upload: h.upload })));
      setHTML($('#nw-auto', el), app.settings.auto_speedtest_hours ? `Automatic test every ${app.settings.auto_speedtest_hours} h` : '<a href="#settings">Schedule automatic tests</a>');
    }
    if (tab === 'usage') renderUsage();
    if (tab === 'ports') renderPorts();
  }

  function renderLive() {
    const pub = net.public;
    const def = (net.interfaces || []).find((i) => i.default);
    const online = net.latency?.online;
    setHTML($('#nw-conn', el), `
      <div class="cardhead"><div class="cardtitle">${icon('world', 17)}Connection</div>${online === false ? `<span class="pill critical">${icon('alert-circle', 14)}Offline</span>` : `<span class="pill good">${icon('circle-check', 14)}Online</span>`}</div>
      <dl class="kv">
        <dt>Public IP</dt><dd>${pub?.ip ? `<span class="mono">${showIp ? esc(pub.ip) : esc(pub.ip.replace(/[\d\w]+([.:])[\d\w]+$/, '•••$1•••'))}</span> <button class="btn ghost sm icon" id="nw-eye" title="Show / hide">${icon('eye', 15)}</button>` : '–'}</dd>
        <dt>Provider</dt><dd>${esc(pub?.isp || '–')}</dd>
        <dt>Local IP</dt><dd class="mono">${esc(def?.ipv4?.[0] || '–')}</dd>
        <dt>Router</dt><dd class="mono">${esc(net.gateway || '–')}</dd>
        <dt>DNS</dt><dd class="mono">${esc((net.dns || []).join(', ') || '–')}</dd>
        <dt>Link</dt><dd>${def ? `${KIND[def.kind]?.[0] || def.kind}${def.speedMbps ? ` · ${def.speedMbps >= 1000 ? def.speedMbps / 1000 + ' Gb/s' : def.speedMbps + ' Mb/s'}` : ''}` : '–'}</dd>
      </dl>
      ${def?.speedMbps && def.speedMbps < 1000 && def.kind === 'ethernet' ? `<div class="banner warning" style="margin:12px 0 0">${icon('alert-triangle', 18)}<div class="small">Your Ethernet link is only ${def.speedMbps} Mb/s — often a worn cable or an old switch port.</div></div>` : ''}`);

    const L = net.latency?.targets || {};
    const keys = ['gateway', 'internet'].filter((k) => L[k]);
    setHTML($('#nw-lat', el), keys.map((k) => `
      <div class="stat cell"><div class="k">${esc(L[k].label)}</div><div class="v">${L[k].avg != null ? `${L[k].avg.toFixed(1)}<small>ms</small>` : '–'}</div>
      <div class="d">jitter ${L[k].jitter ?? '–'} ms · ${L[k].loss ? `<span style="color:var(--warning-text)">${L[k].loss}% lost</span>` : 'no loss'}</div></div>`).join('') || '<div class="small muted">Latency monitoring is off — turn it on in Settings.</div>');
    const map = new Map();
    for (const k of keys) for (const [t, v] of L[k].series) { const p = map.get(t) || { t }; p[k] = v; map.set(t, p); }
    charts.lat?.set([...map.values()].sort((a, b) => a.t - b.t));
    // legend colours follow the series, not their position
    setHTML($('#nw-latlegend', el), keys.map((k) => `<span><i style="background:${SERIES_COLOR[k]}"></i>${esc(L[k].label)}</span>`).join(''));

    const up = net.latency?.uptime;
    const fmt = (u) => (u ? `${u.percent >= 99.995 ? '100' : u.percent.toFixed(2)}<small>%</small>` : '–');
    setHTML($('#nw-up', el), `
      <div class="stat cell"><div class="k">Last 24 hours</div><div class="v">${fmt(up?.day)}</div><div class="d">${up?.day?.outages || 0} outage${up?.day?.outages === 1 ? '' : 's'}</div></div>
      <div class="stat cell"><div class="k">Last 7 days</div><div class="v">${fmt(up?.week)}</div><div class="d">${up?.week ? `${Math.round(up.week.downSeconds / 60)} min down` : ''}</div></div>`);
    setHTML($('#nw-outages', el), outages.length ? timelineHTML(outages, 4) : '<div class="small muted">No outages recorded. 🎉</div>');
  }

  function renderPorts() {
    setHTML($('#nw-ifaces', el), (net.interfaces || []).map((i) => {
      const [kname, kic] = KIND[i.kind] || ['Other', 'network'];
      const w = i.wifi || {};
      const kv = (k, v) => (v ? `<dt>${k}</dt><dd class="mono">${esc(v)}</dd>` : '');
      return `<div class="glass card"><div class="row" style="gap:12px;margin-bottom:10px"><div class="iconwell cell">${icon(kic, 20)}</div>
        <div class="grow"><div style="font-weight:620">${kname}${i.default ? ' <span class="pill sm brand">main</span>' : ''}</div><div class="small muted mono">${esc(i.name)}</div></div>
        <span class="pill sm ${i.state === 'up' ? 'good' : ''}">${i.state === 'up' ? 'Connected' : i.state === 'down' ? 'Not connected' : esc(i.state || '')}</span></div>
        <dl class="kv small">${kv('IPv4', (i.ipv4 || []).join(', '))}${kv('IPv6', i.ipv6?.[0])}${kv('MAC', i.mac)}
        ${kv('Speed', i.speedMbps ? `${i.speedMbps} Mb/s ${i.duplex || ''}` : '')}${kv('MTU', i.mtu)}${kv('Network', w.ssid)}${kv('Signal', w.signal ? `${w.signal} dBm` : '')}
        ${kv('Band', w.freq ? (+w.freq > 5900 ? '6 GHz' : +w.freq > 4900 ? '5 GHz' : '2.4 GHz') : '')}</dl></div>`;
    }).join(''));
  }

  function showResult(r) {
    if (!r) {
      setHTML($('#nw-res', el), ['Download', 'Upload', 'Ping'].map((k) => `<div class="stat cell"><div class="k">${k}</div><div class="v">–</div></div>`).join(''));
      return;
    }
    updateGauge($('#nw-sg', el), clamp(Math.log10(Math.max(1, r.download)) / 4), { value: r.download >= 1000 ? (r.download / 1000).toFixed(2) : r.download.toFixed(0), label: r.download >= 1000 ? 'Gb/s down' : 'Mb/s down', color: 'var(--s1)' });
    setHTML($('#nw-res', el), `
      <div class="stat cell"><div class="k">${icon('arrow-down', 14)}Download</div><div class="v">${esc(mbps(r.download))}</div><div class="d">${esc(ago(r.at))}</div></div>
      <div class="stat cell"><div class="k">${icon('arrow-up', 14)}Upload</div><div class="v">${esc(mbps(r.upload))}</div><div class="d">${esc(r.isp || '')}</div></div>
      <div class="stat cell"><div class="k">${icon('activity-heartbeat', 14)}Ping</div><div class="v">${r.ping}<small>ms</small></div><div class="d">jitter ${r.jitter} ms</div></div>`);
    const g = r.bufferbloat;
    setHTML($('#nw-bloat', el), g ? `<div class="row" style="gap:12px;align-items:flex-start"><div class="grade ${g === 'A+' ? 'Ap' : g}">${g}</div>
      <div class="grow"><div style="font-weight:600">Responsiveness under load ${info('"Bufferbloat": how much your ping rises while the line is full. A big rise makes calls and games lag even on a fast connection.')}</div><div class="small muted">${esc(GRADE_TEXT[g])}</div></div></div>` : '');
  }

  async function startTest() {
    const b = $('#nw-start', el);
    if (running) { cancelJob(running); return; }
    b.innerHTML = `${icon('player-stop', 18)}Stop`;
    try {
      const job = await runJob('/api/speedtest', {}, (j) => {
        running = j.id;
        const live = j.data?.live;
        const phase = j.phase || 'Connecting';
        if (phase === 'Latency') updateGauge($('#nw-sg', el), j.progress, { value: j.data.ping ?? '…', label: 'measuring ping', color: 'var(--s3)' });
        else if (live != null) updateGauge($('#nw-sg', el), clamp(Math.log10(Math.max(1, live)) / 4), { value: live >= 1000 ? (live / 1000).toFixed(2) : live.toFixed(0), label: `${live >= 1000 ? 'Gb/s' : 'Mb/s'} ${phase.toLowerCase()}`, color: phase === 'Upload' ? 'var(--s2)' : 'var(--s1)' });
        setHTML($('#nw-res', el), `
          <div class="stat cell"><div class="k">${icon('arrow-down', 14)}Download</div><div class="v">${j.data.download != null ? esc(mbps(j.data.download)) : phase === 'Download' ? '…' : '–'}</div></div>
          <div class="stat cell"><div class="k">${icon('arrow-up', 14)}Upload</div><div class="v">${j.data.upload != null ? esc(mbps(j.data.upload)) : phase === 'Upload' ? '…' : '–'}</div></div>
          <div class="stat cell"><div class="k">${icon('activity-heartbeat', 14)}Ping</div><div class="v">${j.data.ping != null ? `${j.data.ping}<small>ms</small>` : '…'}</div></div>`);
      }, 250);
      running = null;
      if (job.status === 'done') toast(`Download ${mbps(job.result.download)} · Upload ${mbps(job.result.upload)}`, 'good');
      else if (job.status === 'error') toast(job.error, 'error');
      await load();
    } catch (err) { toast(err.message, 'error'); }
    running = null;
    if ($('#nw-start', el)) $('#nw-start', el).innerHTML = `${icon('player-play', 18)}Test again`;
  }

  el.addEventListener('click', (e) => {
    if (e.target.closest('#nw-eye')) { showIp = !showIp; renderLive(); }
    if (e.target.closest('#nw-start')) startTest();
  });

  function update(liveSnap) {
    if (tab !== 'live') return;
    setHTML($('#nw-rx', el), rateHTML(liveSnap.net.rx));
    setHTML($('#nw-tx', el), rateHTML(liveSnap.net.tx));
    charts.live?.set(app.hist);
  }

  showTab(tab);
  await load();
  if (autoRun) startTest();
  const t = setInterval(load, 10000);
  return {
    update,
    onRoute(sub) { const t2 = (sub || 'live').replace('-run', ''); if (TABS.some(([k]) => k === t2)) showTab(t2); if (sub === 'speed-run') startTest(); },
    destroy() { clearInterval(t); Object.values(charts).forEach((c) => c.destroy()); },
  };
}
