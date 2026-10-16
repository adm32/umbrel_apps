import {
  $, api, appIcon, bar, bytes, bytesHTML, cancelJob, diskRateHTML, empty, esc, healthPill, hoursText, icon, modal,
  runJob, setHTML, temp, toast,
} from '../ui.js';
import { sparkline } from '../chart.js';
import { driveIcon } from './overview.js';

const TRANSPORT = { nvme: 'NVMe', sata: 'SATA', usb: 'USB', mmc: 'SD/eMMC', virtio: 'Virtual', other: 'Internal' };

function speedClass(mbps) {
  if (mbps >= 1500) return ['NVMe-class fast', 'good'];
  if (mbps >= 350) return ['SATA SSD speed', 'good'];
  if (mbps >= 100) return ['Hard-drive speed', 'warning'];
  return ['Slow — check the cable or USB port', 'serious'];
}

export default async function storage(el, app) {
  el.innerHTML = `
    <div class="pagehead"><div><h1>Storage</h1><p>Every drive connected to your Umbrel — health, space and speed. External USB drives show up here too.</p></div>
      <div class="actions"><button class="btn" id="st-refresh">${icon('refresh', 16)}Refresh</button><button class="btn primary" id="st-bench">${icon('gauge', 16)}Speed test a drive</button></div></div>
    <div class="glass card pad-l" id="st-summary"><div class="skeleton" style="height:60px"></div></div>
    <div class="section-title">Drives <small id="st-count"></small></div>
    <div class="drivegrid" id="st-drives"><div class="skeleton" style="height:260px"></div><div class="skeleton" style="height:260px"></div></div>
    <div class="section-title">What's using space</div>
    <div class="glass card pad-l" id="st-apps"></div>`;

  let data = null;
  let analysis = null;

  async function load(refresh = false) {
    try {
      data = await api.get(`/api/storage${refresh ? '?refresh=1' : ''}`);
      analysis = analysis || data.analysis;
      render();
    } catch (e) { toast(e.message, 'error'); }
  }

  function render() {
    const u = data.umbrel;
    const drives = data.drives || [];
    const healthy = drives.filter((d) => d.smart?.health === 'good').length;
    const issues = drives.filter((d) => ['warning', 'critical'].includes(d.smart?.health)).length;
    const ext = drives.filter((d) => d.external).length;
    if (u) {
      const f = u.used / u.total;
      setHTML($('#st-summary', el), `
        <div class="row wrap between" style="gap:20px">
          <div><div class="small muted">umbrelOS storage</div><div class="huge">${bytesHTML(u.used)}</div><div class="muted small">used of ${bytes(u.total)} · <b style="color:var(--text-1)">${bytes(u.free)} free</b></div></div>
          <div class="stats grow" style="max-width:560px">
            <div class="stat cell"><div class="k">${icon('database', 14)}Drives</div><div class="v">${drives.length}</div><div class="d">${ext} external</div></div>
            <div class="stat cell"><div class="k">${icon('circle-check', 14)}Healthy</div><div class="v">${healthy}</div><div class="d">${issues ? `${issues} need attention` : 'no problems found'}</div></div>
            <div class="stat cell"><div class="k">${icon('arrows-down-up', 14)}Activity</div><div class="v" id="st-io-total">–</div><div class="d">read + write now</div></div>
          </div>
        </div>
        <div class="bar thick" style="margin-top:16px"><i class="${f > 0.9 ? 'critical' : f > 0.8 ? 'warning' : 'brand'}" style="width:${(f * 100).toFixed(1)}%"></i></div>`);
    }
    setHTML($('#st-count', el), `${drives.length} found`);
    setHTML($('#st-drives', el), drives.length ? drives.map(driveCard).join('') : `<div class="glass card span2">${empty('database', 'No drives found', 'Vitals couldn\'t list block devices. It needs the privileged mode set in its docker-compose file.')}</div>`);
    drives.forEach((d) => drawSpark(d));
    renderApps();
  }

  function driveCard(d) {
    const sm = d.smart || {};
    const life = sm.lifeLeft;
    const stat = (k, v, dsc = '') => `<div class="stat cell"><div class="k">${k}</div><div class="v">${v}</div>${dsc ? `<div class="d">${dsc}</div>` : ''}</div>`;
    const parts = d.partitions.filter((p) => p.size > 64e6).map((p) => {
      const us = p.usage;
      const label = p.label || p.mountpoint || p.name;
      const f = us ? us.used / us.total : 0;
      return `<div class="part"><div class="row between"><span class="ellipsis"><b>${esc(label)}</b> <span class="faint">${esc(p.fstype || '')}${p.umbrel ? ' · umbrelOS data' : ''}</span></span>
        <span class="muted nowrap">${us ? `${bytes(us.used)} / ${bytes(us.total)}` : p.mountpoint ? '' : `${bytes(p.size)} · not mounted`}</span></div>
        ${us ? bar(f, f > 0.9 ? 'critical' : f > 0.8 ? 'warning' : 'brand', 'thin') : ''}</div>`;
    }).join('');
    return `<div class="glass drive" data-drive="${esc(d.name)}">
      <div class="head"><div class="iconwell cell">${icon(driveIcon(d.kind), 24)}</div>
        <div class="grow"><div class="title ellipsis">${esc(d.model)}</div>
          <div class="meta"><span class="pill sm">${esc(TRANSPORT[d.transport] || d.transport)}</span>${esc(d.kind)} · ${bytes(d.size)}${d.system ? ' · <span class="pill sm brand">umbrelOS</span>' : ''}</div></div>
        ${healthPill(sm)}</div>
      <div class="stats mt">
        ${stat(icon('temperature', 13) + 'Temp', sm.temp != null ? temp(sm.temp) : '–')}
        ${stat(icon('clock', 13) + 'Powered on', sm.powerOnHours != null ? hoursText(sm.powerOnHours).split(',')[0] : '–', sm.powerCycles != null ? `${sm.powerCycles} starts` : '')}
        ${life != null ? stat(icon('heart-rate-monitor', 13) + 'Life left', `${life}<small>%</small>`, sm.written ? `${bytes(sm.written)} written` : '') : stat(icon('cloud-upload', 13) + 'Written', sm.written ? bytes(sm.written) : '–')}
      </div>
      ${(sm.issues || []).length || (sm.notes || []).length || sm.error ? `<div class="issues">${(sm.issues || []).map((i) => `<div class="hint" style="color:var(--warning)">${icon('alert-triangle', 15)}${esc(i)}</div>`).join('')}${(sm.notes || []).map((i) => `<div class="hint">${icon('info-circle', 15)}${esc(i)}</div>`).join('')}${sm.error ? `<div class="hint">${icon('info-circle', 15)}${esc(sm.error)}</div>` : ''}</div>` : ''}
      <div class="parts">${parts}</div>
      <div class="io">
        <div class="cell stat"><div class="k"><span class="dot" style="background:var(--s1)"></span>Read</div><div class="v" data-r>–</div><div data-sr style="height:30px;margin-top:4px"></div></div>
        <div class="cell stat"><div class="k"><span class="dot" style="background:var(--s2)"></span>Write</div><div class="v" data-w>–</div><div data-sw style="height:30px;margin-top:4px"></div></div>
      </div>
      <div class="row mt" style="gap:8px">
        <button class="btn sm" data-details="${esc(d.name)}">${icon('stethoscope', 15)}Health details</button>
        ${data.targets.some((t) => t.drive === d.name) ? `<button class="btn sm" data-benchdrive="${esc(d.name)}">${icon('gauge', 15)}Speed test</button>` : ''}
        <span class="grow"></span><span class="tiny faint">/dev/${esc(d.name)}</span>
      </div></div>`;
  }

  function drawSpark(d) {
    const card = el.querySelector(`[data-drive="${CSS.escape(d.name)}"]`);
    if (!card) return;
    const h = d.ioHistory || [];
    const max = Math.max(1, ...h.map((x) => Math.max(x[1], x[2])));
    sparkline(card.querySelector('[data-sr]'), h.map((x) => x[1]), 'var(--s1)', { max, height: 30 });
    sparkline(card.querySelector('[data-sw]'), h.map((x) => x[2]), 'var(--s2)', { max, height: 30 });
  }

  function renderApps() {
    const box = $('#st-apps', el);
    if (!analysis) {
      setHTML(box, `<div class="row wrap between" style="gap:16px"><div><div style="font-weight:620">See which apps take up the most space</div><div class="small muted">Measures each app's data folder and Docker images. Takes a minute on big installs.</div></div>
        <button class="btn primary" id="st-analyze">${icon('chart-bar', 16)}Analyze space</button></div><div id="st-anprog"></div>`);
      return;
    }
    const apps = analysis.apps || [];
    const max = Math.max(1, ...apps.map((a) => a.total));
    const other = Object.entries(analysis.other || {}).filter(([, v]) => v > 0);
    setHTML(box, `<div class="cardhead"><div class="cardtitle">${icon('apps', 17)}Space by app</div><button class="btn sm" id="st-analyze">${icon('refresh', 15)}Measure again</button></div>
      <div class="list">${apps.map((a) => `<div class="li">${appIcon(a.icon, a.name)}
        <div class="grow"><div class="row between"><span class="t">${esc(a.name)}${a.installed === false ? ' <span class="pill sm warning">not installed</span>' : ''}</span><span class="num">${bytes(a.total)}</span></div>
        <div class="stackbar" style="height:8px;margin-top:6px"><i style="flex:0 0 ${(100 * a.data / max).toFixed(2)}%;background:var(--s1)"></i><i style="flex:0 0 ${(100 * a.images / max).toFixed(2)}%;background:var(--s4)"></i></div></div></div>`).join('')}</div>
      <div class="legend"><span><i style="background:var(--s1)"></i>App data</span><span><i style="background:var(--s4)"></i>App images (program files)</span></div>
      ${other.length ? `<div class="stats mt">${other.map(([k, v]) => `<div class="stat cell"><div class="k">${esc(k)}</div><div class="v">${bytesHTML(v)}</div></div>`).join('')}</div>` : ''}
      ${apps.some((a) => a.installed === false) ? `<a class="banner warning mt" href="#cleaner" style="margin-top:14px">${icon('brush', 18)}<div class="grow">Some data belongs to apps that aren't installed anymore. The Cleaner can remove it.</div>${icon('chevron-right', 16)}</a>` : ''}`);
  }

  el.addEventListener('click', async (e) => {
    if (e.target.closest('#st-refresh')) { await load(true); toast('Drive list refreshed', 'good', 1800); }
    if (e.target.closest('#st-bench')) benchModal();
    const bd = e.target.closest('[data-benchdrive]');
    if (bd) benchModal(bd.dataset.benchdrive);
    const dt = e.target.closest('[data-details]');
    if (dt) detailsModal(dt.dataset.details);
    if (e.target.closest('#st-analyze')) {
      const btn = e.target.closest('#st-analyze');
      btn.disabled = true;
      btn.innerHTML = `${icon('refresh', 16, 'spin')}Measuring…`;
      try {
        const job = await runJob('/api/storage/analyze', {}, (j) => { if ($('#st-anprog', el)) setHTML($('#st-anprog', el), `<div class="small muted mt">${esc(j.phase || 'Starting…')}</div>${bar(j.progress, 'brand', 'thin')}`); });
        if (job.status === 'done') { analysis = job.result; renderApps(); } else toast(job.error || 'Measuring failed', 'error');
      } catch (err) { toast(err.message, 'error'); }
    }
  });

  async function detailsModal(dev) {
    const d = data.drives.find((x) => x.name === dev);
    const m = modal({ title: d.model, sub: `${esc(d.kind)} · ${bytes(d.size)} · /dev/${esc(dev)}`, wide: true, body: '<div class="skeleton" style="height:240px"></div>' });
    let sm;
    try { sm = await api.get(`/api/storage/${dev}/smart?refresh=1`); } catch (e) { setHTML(m.body, esc(e.message)); return; }
    const kv = (k, v) => v == null || v === '' ? '' : `<dt>${k}</dt><dd>${v}</dd>`;
    const attrs = sm.attributes || [];
    setHTML(m.body, !sm.available ? empty('info-circle', sm.standby ? 'The drive is asleep' : 'No health data', sm.standby ? 'Vitals doesn\'t wake sleeping drives just to check them. It will read them next time they spin up.' : esc(sm.error || 'This drive doesn\'t report S.M.A.R.T. data.')) : `
      <div class="row wrap" style="gap:10px;margin-bottom:16px">${healthPill(sm)}${sm.passed === true ? `<span class="pill">${icon('shield-check', 14)}Self-assessment passed</span>` : ''}</div>
      ${(sm.issues || []).map((i) => `<div class="banner warning">${icon('alert-triangle', 18)}<div>${esc(i)}</div></div>`).join('')}
      <div class="grid g2">
        <dl class="kv">${kv('Model', esc(sm.model))}${kv('Serial', esc(sm.serial))}${kv('Firmware', esc(sm.firmware))}${kv('Interface', esc(sm.protocol + (sm.sataVersion ? ` · ${sm.sataVersion}` : '')))}${kv('Spin speed', sm.rpm ? `${sm.rpm} rpm` : null)}</dl>
        <dl class="kv">${kv('Temperature', sm.temp != null ? temp(sm.temp) : null)}${kv('Powered on', hoursText(sm.powerOnHours))}${kv('Power cycles', sm.powerCycles)}${kv('Data written', sm.written ? bytes(sm.written) : null)}${kv('Data read', sm.read ? bytes(sm.read) : null)}${kv('Life left', sm.lifeLeft != null ? `${sm.lifeLeft}%` : null)}</dl>
      </div>
      ${sm.lastSelfTest ? `<div class="hint mt">${icon('stethoscope', 15)}Last self-test: ${esc(sm.lastSelfTest.type)} — ${esc(sm.lastSelfTest.result)}${sm.lastSelfTest.hours ? ` (at ${sm.lastSelfTest.hours} h)` : ''}</div>` : ''}
      <details class="mt"><summary class="small muted" style="cursor:pointer">All ${attrs.length} raw attributes (for the curious)</summary>
        <div class="tablewrap"><table class="t"><thead><tr>${attrs[0]?.id != null ? '<th>ID</th>' : ''}<th>Attribute</th>${attrs[0]?.value != null ? '<th class="r">Value</th><th class="r">Worst</th><th class="r">Threshold</th>' : ''}<th class="r">Raw</th></tr></thead>
        <tbody>${attrs.map((a) => `<tr>${a.id != null ? `<td class="faint">${a.id}</td>` : ''}<td>${esc(a.name)}${a.failing ? ' <span class="pill sm critical">failing</span>' : ''}</td>${a.value != null ? `<td class="r">${a.value}</td><td class="r">${a.worst}</td><td class="r">${a.thresh}</td>` : ''}<td class="r mono">${esc(a.raw)}</td></tr>`).join('')}</tbody></table></div></details>
      <div class="row mt" style="gap:10px"><button class="btn sm" id="st-selftest">${icon('player-play', 15)}Run a 2-minute self-test</button><span class="small muted" id="st-stmsg"></span></div>`);
    m.body.querySelector('#st-selftest')?.addEventListener('click', async (ev) => {
      ev.target.closest('button').disabled = true;
      try { const r = await api.post(`/api/storage/${dev}/selftest`); setHTML(m.body.querySelector('#st-stmsg'), esc(r.message) + ' Check back in a few minutes.'); } catch (err) { toast(err.message, 'error'); }
    });
  }

  function benchModal(dev) {
    const targets = data.targets || [];
    if (!targets.length) { toast('No writable drives to test', 'error'); return; }
    let job = null;
    const m = modal({
      title: 'Drive speed test',
      sub: 'Writes a temporary file, reads it back, then deletes it. Safe for your data. Apps may be a little slower for a few seconds.',
      body: `<div class="formrow">
          <div class="field"><label>Drive</label><select class="input" id="bm-target">${targets.map((t) => `<option value="${esc(t.mountpoint)}" ${t.drive === dev ? 'selected' : ''}>${esc(t.model)} — ${esc(t.mountpoint)} (${bytes(t.free)} free)</option>`).join('')}</select></div>
          <div class="field" style="max-width:140px;min-width:120px"><label>Test size</label><select class="input" id="bm-size"><option value="256">256 MB</option><option value="512" selected>512 MB</option><option value="1024">1 GB</option><option value="2048">2 GB</option></select></div>
        </div><div id="bm-out" class="mt2"></div>`,
      actions: [
        { label: 'Close', cls: 'ghost', onClick: () => { if (job?.status === 'running') cancelJob(job.id); } },
        { label: 'Start test', cls: 'primary', icon: 'player-play', keepOpen: true, onClick: ({ button }) => start(button) },
      ],
    });
    const out = m.body.querySelector('#bm-out');
    async function start(button) {
      button.disabled = true;
      const mount = m.body.querySelector('#bm-target').value;
      const size = +m.body.querySelector('#bm-size').value;
      try {
        job = await runJob('/api/storage/benchmark', { mountpoint: mount, sizeMb: size }, (j) => {
          job = j;
          setHTML(out, `<div class="row between"><b>${esc(j.phase || 'Preparing')}</b><span class="mid">${j.data?.live != null ? `${j.data.live}<small>MB/s</small>` : ''}</span></div>${bar(j.progress, 'brand', 'thick')}`);
        }, 250);
        if (job.status !== 'done') throw new Error(job.error || 'Test stopped');
        const r = job.result;
        const [wl, wc] = speedClass(r.writeMBps);
        const [rl, rc] = speedClass(r.readMBps);
        const gb10 = 10e9 / (r.writeMBps * 1e6);
        setHTML(out, `<div class="stats">
            <div class="stat cell"><div class="k">${icon('cloud-upload', 14)}Write</div><div class="v">${r.writeMBps}<small>MB/s</small></div><div class="d"><span class="pill sm ${wc}">${wl}</span></div></div>
            <div class="stat cell"><div class="k">${icon('cloud-download', 14)}Read</div><div class="v">${r.readMBps}<small>MB/s</small></div><div class="d"><span class="pill sm ${rc}">${rl}</span></div></div>
            ${r.randIops ? `<div class="stat cell"><div class="k">${icon('dots', 14)}Random 4K</div><div class="v">${r.randIops.toLocaleString()}<small>IOPS</small></div><div class="d">${r.randLatencyMs} ms each</div></div>` : ''}
          </div>
          <p class="explain mt">Copying a <b>10 GB</b> sample library onto this drive would take about <b>${gb10 < 60 ? `${gb10.toFixed(0)} seconds` : `${(gb10 / 60).toFixed(1)} minutes`}</b>.
          ${r.randIops ? `Random reads matter for apps with databases (Immich, Nextcloud, Bitcoin): ${r.randIops > 5000 ? 'this drive is great for them.' : r.randIops > 500 ? 'this drive is fine for them.' : 'this drive will make them feel sluggish — a spinning disk is best for big media files.'}` : ''}</p>
          ${!r.direct ? `<div class="hint">${icon('info-circle', 15)}This filesystem doesn't support direct I/O, so read speed may be flattered by caching.</div>` : ''}`);
      } catch (err) {
        setHTML(out, `<div class="banner critical">${icon('alert-circle', 18)}<div>${esc(err.message)}</div></div>`);
      }
      button.disabled = false;
      button.lastChild.textContent = 'Test again';
    }
  }

  function update(live) {
    if (!data) return;
    let total = 0;
    for (const d of data.drives || []) {
      const io = live.disks?.[d.name];
      const card = el.querySelector(`[data-drive="${CSS.escape(d.name)}"]`);
      if (!io || !card) continue;
      total += io.read + io.write;
      setHTML(card.querySelector('[data-r]'), diskRateHTML(io.read));
      setHTML(card.querySelector('[data-w]'), diskRateHTML(io.write));
      d.ioHistory = [...(d.ioHistory || []), [live.t, io.read, io.write]].slice(-90);
      drawSpark(d);
    }
    const t = $('#st-io-total', el);
    if (t) setHTML(t, diskRateHTML(total));
  }

  await load();
  const timer = setInterval(() => load(), 60000);
  return { update, destroy() { clearInterval(timer); } };
}

