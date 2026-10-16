import {
  $, api, ago, bindSeg, bytes, bytesHTML, cancelJob, clamp, esc, gauge, icon, mbps, rate, rateHTML, runJob, seg,
  setHTML, toast, updateGauge,
} from '../ui.js';
import { BarChart, TimeChart } from '../chart.js';

const GRADE_TEXT = {
  'A+': 'Excellent — video calls and gaming stay smooth even while someone downloads.',
  A: 'Great — your connection stays responsive under load.',
  B: 'Good — a little lag can creep in when the line is busy.',
  C: 'Fair — calls may stutter while big downloads or uploads run.',
  D: 'Poor — your router lets queues build up. Turning on "SQM" or "QoS" on the router usually fixes this.',
  F: 'Bad — heavy lag under load. Enable SQM/QoS (cake or fq_codel) on your router.',
};
const KIND = { ethernet: ['Ethernet', 'network'], wifi: ['Wi-Fi', 'wifi'], vpn: ['VPN', 'shield-check'], virtual: ['Virtual', 'box'] };

export default async function network(el, app) {
  el.innerHTML = `
    <div class="pagehead"><div><h1>Network</h1><p id="nw-sub">Your connection, live traffic, data usage and internet speed.</p></div></div>

    <div class="grid g-main">
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('arrows-down-up', 17)}Live traffic</div><span class="small muted">all physical network ports</span></div>
        <div class="row" style="gap:30px;margin-bottom:6px">
          <div><div class="legend" style="margin:0"><span><i style="background:var(--s1)"></i>Download</span></div><div class="big" id="nw-rx">–</div></div>
          <div><div class="legend" style="margin:0"><span><i style="background:var(--s2)"></i>Upload</span></div><div class="big" id="nw-tx">–</div></div>
        </div>
        <div id="nw-live"></div>
      </div>
      <div class="glass card pad-l" id="nw-conn"><div class="skeleton" style="height:240px"></div></div>
    </div>

    <div class="section-title">Speed test</div>
    <div class="grid g-main">
      <div class="glass card pad-l">
        <div class="row wrap" style="gap:24px;align-items:center">
          <div class="speedgauge">${gauge(0, { id: 'nw-sg', cls: 'lg', value: '–', label: 'Mb/s', color: 'var(--s1)' })}
            <button class="btn primary lg" id="nw-start">${icon('player-play', 18)}Start test</button></div>
          <div class="grow" style="min-width:240px">
            <div class="stats" id="nw-res"></div>
            <div id="nw-bloat" class="mt"></div>
            <div class="hint mt">${icon('info-circle', 15)}<span>Runs from your Umbrel to Cloudflare's nearest server, so it measures your internet line — not your Wi-Fi. Uses about 0.5–2 GB of data on fast lines.</span></div>
          </div>
        </div>
      </div>
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('chart-area-line', 17)}Past tests</div><span class="small muted" id="nw-auto"></span></div>
        <div id="nw-hist"></div>
        <div class="legend"><span><i style="background:var(--s1)"></i>Download</span><span><i style="background:var(--s2)"></i>Upload</span></div>
      </div>
    </div>

    <div class="section-title">Data usage</div>
    <div class="grid g-main">
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('chart-bar', 17)}<span id="nw-utitle">Last 30 days</span></div>${seg('urange', [['30d', '30 days'], ['24h', '24 hours']], '30d')}</div>
        <div id="nw-usage"></div>
        <div class="legend"><span><i style="background:var(--s1)"></i>Downloaded</span><span><i style="background:var(--s2)"></i>Uploaded</span></div>
      </div>
      <div class="glass card pad-l"><div class="stats" id="nw-totals" style="grid-template-columns:1fr 1fr"></div>
        <p class="hint mt">${icon('info-circle', 15)}<span>Counts everything your Umbrel sends and receives, including other devices streaming from it (Jellyfin, Immich, file shares). Set the day your data plan resets in Settings.</span></p></div>
    </div>

    <div class="section-title">Latency monitor <small>pinged every 5 seconds</small></div>
    <div class="glass card pad-l">
      <div class="stats" id="nw-lat"></div>
      <div id="nw-latchart" class="mt"></div>
      <div class="legend" id="nw-latlegend"></div>
    </div>

    <div class="section-title">Network ports</div>
    <div class="grid g3" id="nw-ifaces"></div>`;

  const live = new TimeChart($('#nw-live', el), { height: 200, minMax: 1250, yFmt: (v) => rate(v), endLabel: 'now', label: 'Live network traffic',
    series: [{ key: 'rx', label: 'Download', color: 'var(--s1)' }, { key: 'tx', label: 'Upload', color: 'var(--s2)' }] });
  const hist = new TimeChart($('#nw-hist', el), { height: 190, area: false, minMax: 10, yFmt: (v) => mbps(v), tipTime: (t) => new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }),
    timeFmt: (t) => new Date(t * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }), empty: 'Run your first test',
    series: [{ key: 'download', label: 'Download', color: 'var(--s1)' }, { key: 'upload', label: 'Upload', color: 'var(--s2)' }] });
  const usage = new BarChart($('#nw-usage', el), { height: 190, yFmt: (v) => bytes(v), xLabel: (r) => r.short,
    series: [{ key: 'rx', label: 'Downloaded', color: 'var(--s1)' }, { key: 'tx', label: 'Uploaded', color: 'var(--s2)' }] });
  const lat = new TimeChart($('#nw-latchart', el), { height: 180, area: false, minMax: 20, yFmt: (v) => `${v.toFixed(v < 10 ? 1 : 0)} ms`, endLabel: 'now', empty: 'Collecting pings…',
    series: [{ key: 'gateway', label: 'Your router', color: 'var(--s1)' }, { key: 'internet', label: 'Internet', color: 'var(--s2)' }] });

  let net = null;
  let urange = '30d';
  let showIp = false;
  let running = null;
  bindSeg(el, 'urange', (v) => { urange = v; renderUsage(); });

  async function load() {
    try { net = await api.get('/api/network'); render(); } catch (e) { /* keep last */ }
  }

  function renderUsage() {
    const u = net.usage;
    if (urange === '30d') {
      setHTML($('#nw-utitle', el), 'Last 30 days');
      usage.set(u.days.map((d) => ({ ...d, label: new Date(d.day + 'T12:00').toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }), short: new Date(d.day + 'T12:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) })));
    } else {
      setHTML($('#nw-utitle', el), 'Last 24 hours');
      usage.set(u.hours.map((h) => ({ ...h, label: `${h.hour.slice(11)}:00 – ${h.hour.slice(11)}:59`, short: `${h.hour.slice(11)}:00` })));
    }
    const tot = (a) => a[0] + a[1];
    setHTML($('#nw-totals', el), `
      <div class="stat cell"><div class="k">Today</div><div class="v">${bytesHTML(tot(u.today))}</div><div class="d">↓ ${bytes(u.today[0])} · ↑ ${bytes(u.today[1])}</div></div>
      <div class="stat cell"><div class="k">This billing cycle</div><div class="v">${bytesHTML(tot(u.cycle))}</div><div class="d">since ${new Date(u.cycleStart + 'T12:00').toLocaleDateString([], { month: 'short', day: 'numeric' })}</div></div>
      <div class="stat cell"><div class="k">This month</div><div class="v">${bytesHTML(tot(u.month))}</div><div class="d">↓ ${bytes(u.month[0])}</div></div>
      <div class="stat cell"><div class="k">30-day average</div><div class="v">${bytesHTML(u.days.reduce((a, d) => a + d.rx + d.tx, 0) / 30)}</div><div class="d">per day</div></div>`);
  }

  function render() {
    const pub = net.public;
    const def = (net.interfaces || []).find((i) => i.default);
    const online = net.latency?.online;
    setHTML($('#nw-conn', el), `
      <div class="cardhead"><div class="cardtitle">${icon('world', 17)}Connection</div>${online === false ? `<span class="pill critical">${icon('alert-circle', 14)}Offline</span>` : `<span class="pill good">${icon('circle-check', 14)}Online</span>`}</div>
      <dl class="kv">
        <dt>Public IP</dt><dd>${pub?.ip ? `<span class="mono">${showIp ? esc(pub.ip) : esc(pub.ip.replace(/[\d\w]+([.:])[\d\w]+$/, '•••$1•••'))}</span> <button class="btn ghost sm icon" id="nw-eye" title="Show / hide">${icon('eye', 15)}</button>` : '–'}</dd>
        <dt>Provider</dt><dd>${esc(pub?.isp || '–')}${pub?.asn ? ` <span class="faint">AS${pub.asn}</span>` : ''}</dd>
        <dt>Location</dt><dd>${esc([pub?.city, pub?.country].filter(Boolean).join(', ') || '–')}</dd>
        <dt>Local IP</dt><dd class="mono">${esc(def?.ipv4?.[0] || '–')}</dd>
        <dt>Router</dt><dd class="mono">${esc(net.gateway || '–')}</dd>
        <dt>DNS servers</dt><dd class="mono">${esc((net.dns || []).join(', ') || '–')}</dd>
        <dt>Link</dt><dd>${def ? `${KIND[def.kind]?.[0] || def.kind}${def.speedMbps ? ` · ${def.speedMbps >= 1000 ? def.speedMbps / 1000 + ' Gb/s' : def.speedMbps + ' Mb/s'}` : ''}` : '–'}</dd>
      </dl>
      ${def?.speedMbps && def.speedMbps < 1000 && def.kind === 'ethernet' ? `<div class="banner warning mt" style="margin-top:12px">${icon('alert-triangle', 18)}<div class="small">Your Ethernet link is only ${def.speedMbps} Mb/s. A worn cable or an old switch port can cause this.</div></div>` : ''}`);

    const st = net.speedtest || {};
    if (!running) showResult(st.last);
    hist.set((st.history || []).map((h) => ({ t: h.at, download: h.download, upload: h.upload })));
    setHTML($('#nw-auto', el), app.settings.auto_speedtest_hours ? `auto every ${app.settings.auto_speedtest_hours} h` : '<a href="#settings" class="faint">schedule tests</a>');
    renderUsage();

    const L = net.latency?.targets || {};
    const keys = ['gateway', 'internet'].filter((k) => L[k]);
    setHTML($('#nw-lat', el), keys.map((k) => `
      <div class="stat cell"><div class="k">${esc(L[k].label)}</div><div class="v">${L[k].avg != null ? `${L[k].avg.toFixed(1)}<small>ms avg</small>` : '–'}</div>
      <div class="d">jitter ${L[k].jitter ?? '–'} ms · ${L[k].loss ? `<span style="color:var(--warning)">${L[k].loss}% lost</span>` : 'no loss'}</div></div>`).join('') || '<div class="small muted">Latency monitoring is off. Turn it on in Settings.</div>');
    const map = new Map();
    for (const k of keys) for (const [t, v] of L[k].series) { const p = map.get(t) || { t }; p[k] = v; map.set(t, p); }
    lat.set([...map.values()].sort((a, b) => a.t - b.t));
    setHTML($('#nw-latlegend', el), keys.map((k, i) => `<span><i style="background:var(--s${i + 1})"></i>${esc(L[k].label)}</span>`).join(''));

    setHTML($('#nw-ifaces', el), (net.interfaces || []).map((i) => {
      const [kname, kic] = KIND[i.kind] || ['Other', 'network'];
      const w = i.wifi || {};
      return `<div class="glass card"><div class="row" style="gap:12px;margin-bottom:10px"><div class="iconwell cell">${icon(kic, 20)}</div>
        <div class="grow"><div style="font-weight:620">${kname}${i.default ? ' <span class="pill sm brand">main</span>' : ''}</div><div class="small muted mono">${esc(i.name)}</div></div>
        <span class="pill sm ${i.state === 'up' ? 'good' : ''}">${i.state === 'up' ? 'Connected' : i.state === 'down' ? 'Not connected' : esc(i.state || '')}</span></div>
        <dl class="kv small">${i.ipv4?.length ? `<dt>IPv4</dt><dd class="mono">${esc(i.ipv4.join(', '))}</dd>` : ''}
        ${i.ipv6?.length ? `<dt>IPv6</dt><dd class="mono">${esc(i.ipv6[0])}${i.ipv6.length > 1 ? ` +${i.ipv6.length - 1}` : ''}</dd>` : ''}
        ${i.mac ? `<dt>MAC</dt><dd class="mono">${esc(i.mac)}</dd>` : ''}
        ${i.speedMbps ? `<dt>Speed</dt><dd>${i.speedMbps} Mb/s ${esc(i.duplex || '')}</dd>` : ''}
        ${i.mtu ? `<dt>MTU</dt><dd>${i.mtu}</dd>` : ''}
        ${w.ssid ? `<dt>Network</dt><dd>${esc(w.ssid)}</dd>` : ''}${w.signal ? `<dt>Signal</dt><dd>${esc(w.signal)} dBm</dd>` : ''}${w.txRate ? `<dt>Rate</dt><dd>${esc(w.txRate)}</dd>` : ''}${w.freq ? `<dt>Band</dt><dd>${+w.freq > 5900 ? '6 GHz' : +w.freq > 4900 ? '5 GHz' : '2.4 GHz'}</dd>` : ''}
        </dl></div>`;
    }).join(''));
  }

  function showResult(r) {
    if (!r) {
      setHTML($('#nw-res', el), `<div class="stat cell"><div class="k">${icon('arrow-down', 14)}Download</div><div class="v">–</div></div><div class="stat cell"><div class="k">${icon('arrow-up', 14)}Upload</div><div class="v">–</div></div><div class="stat cell"><div class="k">${icon('activity-heartbeat', 14)}Ping</div><div class="v">–</div></div>`);
      return;
    }
    updateGauge($('#nw-sg', el), clamp(Math.log10(Math.max(1, r.download)) / 4), { value: r.download >= 1000 ? (r.download / 1000).toFixed(2) : r.download.toFixed(0), label: r.download >= 1000 ? 'Gb/s down' : 'Mb/s down', color: 'var(--s1)' });
    setHTML($('#nw-res', el), `
      <div class="stat cell"><div class="k">${icon('arrow-down', 14)}Download</div><div class="v">${esc(mbps(r.download))}</div><div class="d">${esc(ago(r.at))}</div></div>
      <div class="stat cell"><div class="k">${icon('arrow-up', 14)}Upload</div><div class="v">${esc(mbps(r.upload))}</div><div class="d">${esc(r.isp || '')}</div></div>
      <div class="stat cell"><div class="k">${icon('activity-heartbeat', 14)}Ping</div><div class="v">${r.ping}<small>ms</small></div><div class="d">jitter ${r.jitter} ms</div></div>`);
    const g = r.bufferbloat;
    setHTML($('#nw-bloat', el), g ? `<div class="row" style="gap:12px;align-items:flex-start"><div class="grade ${g === 'A+' ? 'Ap' : g}">${g}</div>
      <div class="grow"><div style="font-weight:600">Responsiveness under load</div><div class="small muted">${esc(GRADE_TEXT[g])} Ping rose to ${Math.max(r.downloadLoadedPing || 0, r.uploadLoadedPing || 0).toFixed(0)} ms while busy.</div></div></div>` : '');
  }

  el.addEventListener('click', async (e) => {
    if (e.target.closest('#nw-eye')) { showIp = !showIp; render(); }
    const b = e.target.closest('#nw-start');
    if (!b) return;
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
      if (job.status === 'done') { toast(`Download ${mbps(job.result.download)} · Upload ${mbps(job.result.upload)}`, 'good'); await load(); }
      else if (job.status === 'error') toast(job.error, 'error');
      else { await load(); }
    } catch (err) { toast(err.message, 'error'); }
    running = null;
    b.innerHTML = `${icon('player-play', 18)}Test again`;
  });

  function update(liveSnap) {
    setHTML($('#nw-rx', el), rateHTML(liveSnap.net.rx));
    setHTML($('#nw-tx', el), rateHTML(liveSnap.net.tx));
    live.set(app.hist);
  }

  await load();
  const t = setInterval(load, 10000);
  return { update, destroy() { clearInterval(t); [live, hist, usage, lat].forEach((c) => c.destroy()); } };
}
