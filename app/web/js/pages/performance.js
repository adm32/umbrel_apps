import { $, api, appIcon, bar, bindSeg, bytes, esc, icon, info, seg, setBar, setHTML, shortCpu } from '../ui.js';
import { TimeChart } from '../chart.js';

export default async function performance(el, app) {
  el.innerHTML = `
    <div class="pagehead"><div><h1>CPU & Memory</h1><p>Processor load and where memory goes.</p></div>
      <div class="actions">${seg('range', [['5m', '5 min'], ['1h', '1 hour'], ['24h', '24 hours']], '5m')}</div></div>

    <div class="grid g-main">
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('cpu', 17)}<span id="pf-model">Processor</span></div><span class="pill" id="pf-cores"></span></div>
        <div class="row wrap" style="gap:28px;margin-bottom:10px">
          <div><div class="small muted">Usage</div><div class="huge" id="pf-cpu">–</div></div>
          <div class="stats grow" id="pf-split"></div>
        </div>
        <div id="pf-cpuchart"></div>
      </div>
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('gauge', 17)}Load average ${info('Load is how many tasks want the CPU at the same time, averaged over 1, 5 and 15 minutes. If it stays below your number of threads, there\'s room to spare.')}</div></div>
        <div class="stats" id="pf-load"></div>
        <p class="explain" style="margin:14px 0 0" id="pf-loadtext"></p>
        <div class="cardhead" style="margin-top:18px"><div class="cardtitle">${icon('bolt', 17)}Clock speed</div></div>
        <div class="big" id="pf-freq">–</div>
        <div class="small muted" id="pf-freqmax"></div>
      </div>
    </div>

    <div class="section-title">Cores <small id="pf-coresub"></small></div>
    <div class="cores" id="pf-corelist"></div>

    <div class="grid g-main mt2">
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('stack-2', 17)}Memory ${info('<b>Cached</b> memory holds recently used files to make things faster. Linux gives it back the moment an app needs it, so it isn\'t "used up".')}</div><span class="small muted" id="pf-memtotal"></span></div>
        <div class="row wrap" style="gap:28px;margin-bottom:12px">
          <div><div class="small muted">In use by apps</div><div class="huge" id="pf-memused">–</div></div>
          <div class="grow" style="min-width:220px">
            <div class="stackbar" id="pf-membar"><i style="background:var(--s4)"></i><i style="background:var(--s1)"></i><i style="background:rgb(var(--ink) / .12)"></i></div>
            <div class="legend" id="pf-memlegend"></div>
          </div>
        </div>
        <div id="pf-memchart"></div>
      </div>
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('apps', 17)}Memory by app</div><a class="cardlink" href="#activity">All ${icon('chevron-right', 14)}</a></div>
        <div class="list" id="pf-memapps"><div class="skeleton" style="height:200px"></div></div>
        <div class="cardhead" style="margin-top:16px"><div class="cardtitle">${icon('refresh', 17)}Swap ${info('Swap is disk space used as overflow memory. A little is normal; lots means you\'re short on RAM.')}</div></div>
        <div id="pf-swap" class="small muted"></div>
      </div>
    </div>`;

  let range = '5m';
  let longPts = [];
  const cpuChart = new TimeChart($('#pf-cpuchart', el), {
    height: 210, max: 100, yFmt: (v) => `${v.toFixed(0)}%`, label: 'CPU usage',
    series: [{ key: 'cpu', label: 'CPU', color: 'var(--brand)' }],
  });
  const memChart = new TimeChart($('#pf-memchart', el), {
    height: 170, max: 100, yFmt: (v) => `${v.toFixed(0)}%`, label: 'Memory usage',
    series: [{ key: 'mem', label: 'Memory in use', color: 'var(--s4)' }],
  });
  const points = () => (range === '5m' ? app.hist : longPts);
  bindSeg(el, 'range', async (v) => {
    range = v;
    if (v !== '5m') longPts = (await api.get(`/api/history?range=${v}`)).points;
    cpuChart.set(points()); memChart.set(points());
  });

  const loadApps = () => api.get('/api/processes').then((p) => {
    const apps = [...(p.apps || [])].sort((a, b) => b.mem - a.mem).slice(0, 6);
    const total = app.live?.memory?.total || 1;
    setHTML($('#pf-memapps', el), apps.map((a) => `<div class="li">${appIcon(a.icon, a.name)}<div class="grow"><div class="t">${esc(a.name)}</div>${bar(a.mem / total, 's4', 'thin')}</div><div class="num small" style="width:72px;text-align:right">${bytes(a.mem)}</div></div>`).join(''));
  }).catch(() => {});
  loadApps();
  const t = setInterval(loadApps, 5000);

  function update(live) {
    const c = live.cpu, m = live.memory;
    setHTML($('#pf-model', el), esc(shortCpu(c.cpuModel) || 'Processor'));
    setHTML($('#pf-cores', el), `${c.physical} cores · ${c.logical} threads`);
    setHTML($('#pf-cpu', el), `${c.total.toFixed(0)}<small>%</small>`);
    setHTML($('#pf-split', el), `
      <div class="stat cell"><div class="k">Apps (user)</div><div class="v">${c.user.toFixed(0)}<small>%</small></div></div>
      <div class="stat cell"><div class="k">System</div><div class="v">${c.system.toFixed(0)}<small>%</small></div></div>
      <div class="stat cell"><div class="k" title="Time the CPU spent waiting on slow disks">Waiting on disk</div><div class="v">${c.iowait.toFixed(0)}<small>%</small></div></div>`);
    const n = c.logical || 1;
    setHTML($('#pf-load', el), ['1 min', '5 min', '15 min'].map((l, i) => `<div class="stat cell"><div class="k">${l}</div><div class="v">${c.load[i].toFixed(2)}</div></div>`).join(''));
    const l = c.load[0];
    setHTML($('#pf-loadtext', el), l < n * 0.7 ? `Relaxed — plenty of room on ${n} threads.`
      : l < n * 1.2 ? `Busy — your ${n} threads are mostly in use.` : `Overloaded — tasks are queueing. Check <a href="#activity">Activity</a>.`);
    const fq = c.freq > 200 ? c.freq : null;
    setHTML($('#pf-freq', el), fq ? `${(fq / 1000).toFixed(2)}<small>GHz</small>` : '–');
    setHTML($('#pf-freqmax', el), c.freqMax > 200 ? `Max turbo ${(c.freqMax / 1000).toFixed(2)} GHz — the CPU slows down when idle to save power.` : '');

    const cores = c.cores || [];
    setHTML($('#pf-coresub', el), `${cores.length} logical cores`);
    const list = $('#pf-corelist', el);
    if (list.children.length !== cores.length) {
      list.innerHTML = cores.map((_, i) => `<div class="glass core"><div class="row between"><span class="k">Core ${i}</span><span class="small faint" data-f></span></div><div class="v" data-v>–</div>${bar(0, 's1', 'thin')}</div>`).join('');
    }
    cores.forEach((v, i) => {
      const card = list.children[i];
      setHTML(card.querySelector('[data-v]'), `${v.toFixed(0)}<small class="muted" style="font-size:12px">%</small>`);
      const f = c.freqs?.[i];
      setHTML(card.querySelector('[data-f]'), f > 200 ? `${(f / 1000).toFixed(1)} GHz` : '');
      setBar(card.querySelector('.bar'), v / 100, 'brand');
    });

    setHTML($('#pf-memtotal', el), `${bytes(m.total)} installed`);
    setHTML($('#pf-memused', el), `${bytes(m.used).replace(' ', '<small>')}</small>`);
    const free = Math.max(0, m.total - m.used - m.cached);
    const segs = [m.used, Math.min(m.cached, m.total - m.used), free];
    $('#pf-membar', el).querySelectorAll('i').forEach((i, k) => { i.style.flex = `0 0 ${(100 * segs[k] / m.total).toFixed(2)}%`; });
    setHTML($('#pf-memlegend', el), `<span><i style="background:var(--s4)"></i>Apps ${bytes(m.used)}</span><span><i style="background:var(--s1)"></i>Cached ${bytes(m.cached)}</span><span><i style="background:rgb(var(--ink) / .22)"></i>Free ${bytes(free)}</span>`);
    setHTML($('#pf-swap', el), m.swapTotal
      ? `${bytes(m.swapUsed)} of ${bytes(m.swapTotal)} used ${bar(m.swapUsed / m.swapTotal, m.swapUsed / m.swapTotal > 0.5 ? 'warning' : 'brand', 'thin')}`
      : 'No swap configured.');
    if (range === '5m') { cpuChart.set(app.hist); memChart.set(app.hist); }
  }

  return { update, destroy() { clearInterval(t); cpuChart.destroy(); memChart.destroy(); } };
}
