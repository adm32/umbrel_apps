import { $, api, appIcon, bar, bindSeg, bytes, duration, esc, icon, modal, seg, setHTML, store } from '../ui.js';

const COLS = [
  ['name', 'Name', ''], ['appName', 'App', 'hide-sm'], ['cpuSystem', 'CPU', 'r'], ['mem', 'Memory', 'r'],
  ['threads', 'Threads', 'r hide-sm'], ['pid', 'PID', 'r hide-sm'], ['user', 'User', 'hide-sm'],
];

export default async function activity(el) {
  let view = store.get('activity-view', 'apps');
  let sort = store.get('activity-sort', { key: 'cpuSystem', dir: -1 });
  let q = '';
  let data = null;
  let appFilter = '';

  el.innerHTML = `
    <div class="pagehead"><div><h1>Activity</h1><p>What's running on your Umbrel right now, grouped by app. Updates every 2 seconds.</p></div>
      <div class="actions">${seg('view', [['apps', 'Apps'], ['procs', 'Processes']], view)}</div></div>
    <div class="glass card" style="margin-bottom:16px"><div class="stats" id="ac-sum"></div></div>
    <div class="glass card pad-l">
      <div class="row wrap" style="gap:10px;margin-bottom:10px" id="ac-tools">
        <input class="input grow" id="ac-q" placeholder="Search processes, apps or commands" style="max-width:420px" aria-label="Search">
        <span id="ac-filter"></span><span class="grow"></span><span class="small faint" id="ac-note"></span>
      </div>
      <div id="ac-body"><div class="skeleton" style="height:320px"></div></div>
    </div>`;

  bindSeg(el, 'view', (v) => { view = v; store.set('activity-view', v); render(); });
  $('#ac-q', el).addEventListener('input', (e) => { q = e.target.value.toLowerCase(); render(); });

  function summary() {
    const apps = data.apps.filter((a) => a.app && a.app !== 'system');
    const sys = data.apps.find((a) => a.app === 'system');
    setHTML($('#ac-sum', el), `
      <div class="stat cell"><div class="k">${icon('apps', 14)}Apps running</div><div class="v">${apps.length}</div></div>
      <div class="stat cell"><div class="k">${icon('list-search', 14)}Processes</div><div class="v">${data.count}</div><div class="d">${data.threads.toLocaleString()} threads</div></div>
      <div class="stat cell"><div class="k">${icon('cpu', 14)}Apps' CPU</div><div class="v">${apps.reduce((a, x) => a + x.cpuSystem, 0).toFixed(1)}<small>%</small></div></div>
      <div class="stat cell"><div class="k">${icon('server', 14)}umbrelOS itself</div><div class="v">${sys ? sys.cpuSystem.toFixed(1) : '0'}<small>% CPU</small></div><div class="d">${sys ? bytes(sys.mem) : ''}</div></div>`);
  }

  function render() {
    if (!data) return;
    summary();
    $('#ac-q', el).placeholder = view === 'apps' ? 'Search apps' : 'Search processes, apps or commands';
    setHTML($('#ac-filter', el), appFilter && view === 'procs' ? `<button class="pill brand" id="ac-clear" style="border:0;cursor:pointer">${esc(appFilter)} ${icon('x', 12)}</button>` : '');
    setHTML($('#ac-note', el), view === 'procs' ? 'CPU = share of the whole processor' : '');
    if (view === 'apps') {
      const apps = data.apps.filter((a) => !q || a.name.toLowerCase().includes(q));
      const maxMem = Math.max(1, ...apps.map((a) => a.mem));
      setHTML($('#ac-body', el), `<div class="tablewrap"><table class="t"><thead><tr><th>App</th><th class="r">CPU</th><th class="r">Memory</th><th class="r hide-sm">Processes</th><th></th></tr></thead><tbody>
        ${apps.map((a) => `<tr class="clickable" data-app="${esc(a.name)}"><td><div class="row">${appIcon(a.icon, a.name)}<div class="col" style="gap:0"><b style="font-weight:600">${esc(a.name)}</b><span class="tiny faint">${esc((a.containers || []).slice(0, 3).join(', '))}</span></div></div></td>
          <td class="r"><div class="cpu-cell"><span class="num">${a.cpuSystem.toFixed(1)}%</span>${bar(a.cpuSystem / 100, 'brand', 'thin')}</div></td>
          <td class="r"><div class="cpu-cell"><span class="num">${bytes(a.mem)}</span>${bar(a.mem / maxMem, 's4', 'thin')}</div></td>
          <td class="r hide-sm">${a.procs}</td><td class="r faint">${icon('chevron-right', 16)}</td></tr>`).join('')}
      </tbody></table></div>`);
      return;
    }
    let rows = data.processes;
    if (appFilter) rows = rows.filter((r) => r.appName === appFilter);
    if (q) rows = rows.filter((r) => `${r.name} ${r.appName} ${r.cmd} ${r.pid}`.toLowerCase().includes(q));
    rows = [...rows].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      return (typeof x === 'string' ? x.localeCompare(y) : x - y) * sort.dir;
    }).slice(0, 250);
    setHTML($('#ac-body', el), `<div class="tablewrap"><table class="t"><thead><tr>${COLS.map(([k, l, c]) =>
      `<th class="sortable ${c} ${sort.key === k ? 'sorted' : ''}" data-sort="${k}">${l}${sort.key === k ? (sort.dir < 0 ? ' ↓' : ' ↑') : ''}</th>`).join('')}</tr></thead><tbody>
      ${rows.map((r) => `<tr class="clickable" data-pid="${r.pid}"><td><div class="ellipsis" style="max-width:280px;font-weight:560">${esc(r.name)}</div></td>
        <td class="hide-sm"><span class="ellipsis small muted" style="display:block;max-width:180px">${esc(r.appName)}</span></td>
        <td class="r"><div class="cpu-cell"><span class="num">${r.cpuSystem.toFixed(1)}%</span>${bar(r.cpuSystem / 100, 'brand', 'thin')}</div></td>
        <td class="r num">${bytes(r.mem)}</td><td class="r hide-sm">${r.threads}</td><td class="r hide-sm faint">${r.pid}</td><td class="hide-sm small muted">${esc(r.user)}</td></tr>`).join('')}
      </tbody></table></div>${rows.length === 0 ? '<div class="empty">No matching processes</div>' : ''}`);
  }

  el.addEventListener('click', (e) => {
    const th = e.target.closest('[data-sort]');
    if (th) {
      const k = th.dataset.sort;
      sort = { key: k, dir: sort.key === k ? -sort.dir : (['name', 'appName', 'user'].includes(k) ? 1 : -1) };
      store.set('activity-sort', sort);
      render();
      return;
    }
    if (e.target.closest('#ac-clear')) { appFilter = ''; render(); return; }
    const ar = e.target.closest('[data-app]');
    if (ar) {
      appFilter = ar.dataset.app;
      view = 'procs';
      el.querySelectorAll('[data-seg="view"] button').forEach((b) => b.classList.toggle('on', b.dataset.v === 'procs'));
      render();
      return;
    }
    const pr = e.target.closest('[data-pid]');
    if (pr) {
      const p = data.processes.find((x) => x.pid === +pr.dataset.pid);
      if (!p) return;
      modal({
        title: p.name, sub: `${esc(p.appName)}${p.container ? ` · container <span class="mono">${esc(p.container)}</span>` : ''}`,
        body: `<dl class="kv"><dt>Process ID</dt><dd>${p.pid}</dd><dt>CPU</dt><dd>${p.cpuSystem.toFixed(1)}% of the processor (${p.cpu.toFixed(1)}% of one core)</dd>
          <dt>Memory</dt><dd>${bytes(p.mem)} (${p.memPct.toFixed(1)}%)</dd><dt>Threads</dt><dd>${p.threads}</dd><dt>Status</dt><dd>${esc(p.status)}</dd>
          <dt>User</dt><dd>${esc(p.user)}</dd><dt>Running for</dt><dd>${esc(duration(Date.now() / 1000 - p.started))}</dd></dl>
          <div class="small muted mt" style="margin:14px 0 6px">Command</div><div class="terminal" style="color:var(--text-1)">${esc(p.cmd || p.name)}</div>`,
      });
    }
  });

  async function load() {
    try { data = await api.get('/api/processes'); render(); } catch { /* retry next tick */ }
  }
  await load();
  const t = setInterval(load, 2000);
  return { destroy() { clearInterval(t); } };
}
