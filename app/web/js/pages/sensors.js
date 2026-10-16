import { $, api, bar, bindSeg, clamp, esc, gauge, icon, money, seg, setHTML, statusColor, temp, tempHTML, tempLevel, tempWord, updateGauge } from '../ui.js';
import { TimeChart } from '../chart.js';

export default async function sensors(el, app) {
  el.innerHTML = `
    <div class="pagehead"><div><h1>Temperature & Power</h1><p>Every heat sensor in your Umbrel, and what it costs to keep it running.</p></div>
      <div class="actions">${seg('range', [['1h', '1 hour'], ['24h', '24 hours']], '24h')}</div></div>

    <div class="grid g2">
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('temperature', 17)}CPU temperature</div><span id="se-tpill"></span></div>
        <div class="row wrap" style="gap:22px">
          ${gauge(0, { id: 'se-g', cls: 'lg', label: 'CPU package', color: 'var(--good)' })}
          <div class="grow" style="min-width:200px"><div id="se-tchart"></div></div>
        </div>
        <p class="hint" style="margin-top:10px">${icon('info-circle', 15)}<span>Intel CPUs like the i3-8100T are designed to run up to 100°C. Under 70°C is comfortable; long stretches above 85°C usually mean dust or poor airflow.</span></p>
      </div>
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('bolt', 17)}Power draw</div><span id="se-psrc"></span></div>
        <div class="row wrap" style="gap:22px">
          ${gauge(0, { id: 'se-pg', cls: 'lg', label: 'watts', color: 'var(--s3)' })}
          <div class="grow" style="min-width:200px"><div id="se-pchart"></div></div>
        </div>
        <div class="stats mt" id="se-cost"></div>
      </div>
    </div>

    <div class="grid g2 mt">
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('flame', 17)}All sensors</div></div>
        <div id="se-list"></div>
      </div>
      <div class="col" style="gap:16px">
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('cpu', 17)}Power breakdown</div></div>
          <div id="se-zones"></div>
          <p class="explain" id="se-explain" style="margin:12px 0 0"></p>
        </div>
        <div class="glass card pad-l">
          <div class="cardhead"><div class="cardtitle">${icon('propeller', 17)}Fans</div></div>
          <div id="se-fans"></div>
        </div>
      </div>
    </div>`;

  let range = '24h';
  let pts = [];
  const opts = { height: 190, seconds: false, endLabel: 'now', timeFmt: (t) => new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) };
  const tChart = new TimeChart($('#se-tchart', el), { ...opts, autoMin: true, yFmt: (v) => temp(v), series: [{ key: 'temp', label: 'CPU', color: 'var(--s2)' }], empty: 'History builds up over time', label: 'CPU temperature history' });
  const pChart = new TimeChart($('#se-pchart', el), { ...opts, minMax: 10, yFmt: (v) => `${v.toFixed(v < 10 ? 1 : 0)} W`, series: [{ key: 'power', label: 'Power', color: 'var(--s3)' }], empty: 'History builds up over time', label: 'Power history' });
  const load = async () => {
    try { pts = (await api.get(`/api/history?range=${range}`)).points; } catch { pts = []; }
    const merged = pts.length > 3 ? pts : app.hist;
    tChart.set(merged); pChart.set(merged);
  };
  bindSeg(el, 'range', (v) => { range = v; load(); });
  load();
  const t = setInterval(load, 60000);

  function update(live) {
    const c = live.cpu;
    const lvl = tempLevel(c.temp);
    updateGauge($('#se-g', el), c.temp != null ? clamp((c.temp - 20) / 80) : 0, { value: c.temp != null ? tempHTML(c.temp) : '–', color: statusColor(lvl) });
    setHTML($('#se-tpill', el), c.temp == null ? '<span class="pill">No sensor</span>'
      : `<span class="pill ${lvl}">${icon(lvl === 'good' ? 'circle-check' : 'alert-triangle', 14)}${tempWord(lvl)}</span>`);

    // sensors grouped by chip
    const groups = {};
    for (const s of live.temps || []) (groups[s.group] = groups[s.group] || []).push(s);
    setHTML($('#se-list', el), Object.keys(groups).length ? Object.entries(groups).map(([g, list]) => `
      <div class="small faint" style="margin-top:10px;font-weight:600">${esc(g)}</div>
      ${list.map((s) => {
        const sl = tempLevel(s.current, s.high || 80, s.critical || 100);
        const top = s.critical || s.high || 100;
        return `<div class="sensor"><div class="name">${esc(s.label)}</div>
          <div class="val">${s.high ? `<span class="tiny faint">limit ${temp(s.high)}</span>` : ''}${temp(s.current)}</div>
          ${bar(clamp(s.current / top), sl)}</div>`;
      }).join('')}`).join('')
      : `<div class="empty">${icon('temperature', 30)}<h3>No temperature sensors</h3><p>Your hardware didn't expose any. On some mini PCs, the "coretemp" kernel module has to be loaded.</p></div>`);

    const p = live.power || {};
    updateGauge($('#se-pg', el), p.wall != null ? clamp(p.wall / 50) : 0, { value: p.wall != null ? `${p.wall.toFixed(p.wall < 10 ? 1 : 0)}<small>W</small>` : '–', label: p.source === 'plug' ? 'measured at the wall' : 'estimated total' });
    setHTML($('#se-psrc', el), p.source === 'plug' ? `<span class="pill good">${icon('plug-connected', 14)}Smart plug</span>` : p.source ? `<span class="pill">${icon('cpu', 14)}Estimated</span>` : '');
    setHTML($('#se-cost', el), p.wall != null ? `
      <div class="stat cell"><div class="k">Per month</div><div class="v">${money(p.monthlyCost)}</div><div class="d">${p.monthlyKwh} kWh</div></div>
      <div class="stat cell"><div class="k">Per year</div><div class="v">${money(p.yearlyCost)}</div><div class="d">at ${money(app.settings.price_per_kwh)}/kWh</div></div>
      <div class="stat cell"><div class="k">Per day</div><div class="v">${money(p.monthlyCost / 30.4)}</div><div class="d"><a href="#settings" class="faint">change price</a></div></div>`
      : `<div class="hint">${icon('info-circle', 15)}${esc(p.error || 'No power sensor found. Connect a Shelly or Tasmota smart plug in Settings for real readings.')}</div>`);

    const z = p.zones || {};
    const names = { package: 'Whole CPU package', cores: 'CPU cores', graphics: 'Integrated graphics', memory: 'Memory (DRAM)', platform: 'Platform (psys)' };
    const max = Math.max(1, ...Object.values(z));
    setHTML($('#se-zones', el), Object.keys(z).length ? Object.entries(z).map(([k, v]) => `
      <div class="sensor"><div class="name">${esc(names[k] || k)}</div><div class="val">${v.toFixed(2)} W</div>${bar(v / max, 's3')}</div>`).join('')
      : `<div class="small muted">No per-component readings on this hardware.</div>`);
    setHTML($('#se-explain', el), p.source === 'plug'
      ? 'Wall power comes from your smart plug, so it includes everything: CPU, drives, fans and power-supply losses.'
      : p.package != null
        ? `The CPU reports its own power use (<b>${p.package.toFixed(1)} W</b>). Drives, RAM, fans and the power brick add roughly <b>${app.settings.power_overhead_w} W</b> on a mini PC — adjust that in Settings, or add a smart plug for exact numbers.`
        : '');

    setHTML($('#se-fans', el), live.fans?.length ? live.fans.map((f) => `<div class="sensor"><div class="name">${esc(f.label)}</div><div class="val">${f.rpm.toLocaleString()} rpm</div>${bar(clamp(f.rpm / 5000), 's1')}</div>`).join('')
      : `<div class="small muted">Your mini PC doesn't report fan speeds to Linux. That's normal for most small business PCs.</div>`);
  }

  return { update, destroy() { clearInterval(t); tChart.destroy(); pChart.destroy(); } };
}
