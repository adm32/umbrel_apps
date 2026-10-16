import { $, $$, ago, api, bar, bytes, bytesHTML, confirmDialog, esc, icon, runJob, setHTML, toast } from '../ui.js';

const SAFETY = {
  safe: ['good', 'shield-check', 'Safe'],
  review: ['warning', 'eye', 'Review first'],
  careful: ['critical', 'alert-triangle', 'Permanent'],
  info: ['', 'info-circle', 'Info'],
};

export default async function cleaner(el) {
  let scan = null;
  let stats = { freed: 0, runs: 0 };
  const sel = {}; // category id -> Set(item ids)

  el.innerHTML = `
    <div class="pagehead"><div><h1>Cleaner</h1><p>Finds leftovers that pile up on a home server — old app versions, oversized logs, temp files and junk your Mac or PC leaves on shared folders. Nothing is deleted until you say so.</p></div></div>
    <div id="cl-main"></div>`;

  try { ({ scan, stats } = await api.get('/api/cleaner')); } catch { /* fresh */ }

  function hero(busy = false, phase = '', progress = 0) {
    return `<div class="glass card pad-l" style="text-align:center">
      <button class="scanorb ${busy ? 'busy' : ''}" id="cl-scan" ${busy ? 'disabled' : ''}>${icon(busy ? 'search' : 'sparkles', 34)}${busy ? 'Scanning…' : 'Scan'}</button>
      <div style="font-weight:620;font-size:16px">${busy ? esc(phase || 'Looking around…') : 'Find leftover files'}</div>
      <div class="small muted" style="margin-top:4px">${busy ? '' : stats.freed ? `Vitals has freed <b style="color:var(--text-1)">${bytes(stats.freed)}</b> so far${stats.last ? ` · last clean ${ago(stats.last.at)}` : ''}` : 'Takes about a minute. Your files and installed apps are never touched.'}</div>
      ${busy ? `<div style="max-width:360px;margin:14px auto 0">${bar(progress, 'brand', 'thin')}</div>` : ''}</div>`;
  }

  function selectedSize() {
    let n = 0;
    for (const c of scan?.categories || []) for (const i of c.items) if (sel[c.id]?.has(i.id)) n += i.size;
    return n;
  }

  function render() {
    if (!scan) { setHTML($('#cl-main', el), hero()); return; }
    const cats = scan.categories.filter((c) => c.items.length);
    const clean = scan.categories.filter((c) => !c.items.length && !c.infoOnly);
    setHTML($('#cl-main', el), `
      <div class="glass card pad-l"><div class="row wrap between" style="gap:16px">
        <div><div class="small muted">You can free up</div><div class="huge">${bytesHTML(scan.total)}</div><div class="small muted">Scanned ${ago(scan.at)}</div></div>
        <button class="btn" id="cl-scan">${icon('refresh', 16)}Scan again</button></div></div>
      <div class="col mt" style="gap:12px">${cats.map(catHTML).join('')}</div>
      ${clean.length ? `<div class="glass card mt"><div class="cardtitle" style="margin-bottom:8px">${icon('circle-check', 17)}Already clean</div><div class="row wrap" style="gap:8px">${clean.map((c) => `<span class="pill good">${icon('check', 13)}${esc(c.title)}</span>`).join('')}</div></div>` : ''}
      <div class="cleanbar glass lensed" data-lens="40">
        <div class="grow"><div style="font-weight:640" id="cl-selsize">${bytes(selectedSize())} selected</div><div class="tiny muted">Items marked "Permanent" stay unchecked unless you pick them.</div></div>
        <button class="btn primary lg" id="cl-go" ${selectedSize() ? '' : 'disabled'}>${icon('brush', 18)}Clean up</button>
      </div>`);
    window.__vitalsGlass?.(el);
  }

  function catHTML(c) {
    const [cls, ic, label] = SAFETY[c.safety] || SAFETY.safe;
    const s = sel[c.id] || new Set();
    const all = c.items.length && c.items.every((i) => s.has(i.id));
    const some = !all && c.items.some((i) => s.has(i.id));
    return `<div class="glass catcard" data-cat="${c.id}">
      <div class="cathead">
        ${c.infoOnly ? `<span style="width:20px"></span>` : `<input type="checkbox" class="check" data-catcheck="${c.id}" ${all ? 'checked' : ''} ${some ? 'data-ind="1"' : ''} aria-label="Select ${esc(c.title)}">`}
        <div class="iconwell cell">${icon(c.icon, 20)}</div>
        <div class="grow"><div style="font-weight:620">${esc(c.title)} <span class="pill sm ${cls}">${icon(ic, 12)}${label}</span></div><div class="small muted" style="margin-top:2px">${esc(c.description)}</div>${c.note ? `<div class="tiny" style="color:var(--warning);margin-top:3px">${esc(c.note)}</div>` : ''}</div>
        <div class="size">${bytes(c.size)}<div class="tiny faint" style="font-weight:500">${c.count} item${c.count > 1 ? 's' : ''}</div></div>
        <span class="chev">${icon('chevron-right', 18)}</span>
      </div>
      <div class="catbody">${c.items.map((i) => `<label class="catitem">${c.infoOnly ? '' : `<input type="checkbox" class="check" data-item="${c.id}:${i.id}" ${s.has(i.id) ? 'checked' : ''}>`}
        <div class="grow"><div class="ellipsis">${esc(i.label)}</div><div class="tiny faint ellipsis">${esc(i.detail || '')}</div></div><div class="num">${bytes(i.size)}</div></label>`).join('')}
        ${c.id === 'trash' ? `<a class="btn sm mt" href="${location.protocol}//${location.hostname}/files/Trash" target="_blank" rel="noopener">${icon('external-link', 14)}Open Files → Trash</a>` : ''}</div></div>`;
  }

  function syncChecks() {
    $$('[data-ind]', el).forEach((x) => { x.indeterminate = true; });
    const n = selectedSize();
    setHTML($('#cl-selsize', el), `${bytes(n)} selected`);
    const go = $('#cl-go', el);
    if (go) go.disabled = !n;
  }

  async function doScan() {
    setHTML($('#cl-main', el), hero(true, 'Starting…'));
    try {
      const job = await runJob('/api/cleaner/scan', {}, (j) => setHTML($('#cl-main', el), hero(true, j.phase, j.progress)), 400);
      if (job.status !== 'done') throw new Error(job.error || 'Scan stopped');
      scan = job.result;
      for (const c of scan.categories) sel[c.id] = new Set(c.default && !c.infoOnly ? c.items.map((i) => i.id) : []);
      render();
      syncChecks();
    } catch (e) { toast(e.message, 'error'); scan = null; render(); }
  }

  el.addEventListener('click', async (e) => {
    if (e.target.closest('#cl-scan')) { doScan(); return; }
    const head = e.target.closest('.cathead');
    if (head && !e.target.closest('input')) { head.parentElement.classList.toggle('open'); return; }
    if (e.target.closest('#cl-go')) {
      const picked = scan.categories.filter((c) => sel[c.id]?.size);
      const permanent = picked.filter((c) => c.safety === 'careful');
      const ok = await confirmDialog({
        title: `Clean up ${bytes(selectedSize())}?`,
        text: `${picked.map((c) => esc(c.title)).join(', ')}.${permanent.length ? ` <b style="color:var(--critical-text)">${permanent.map((c) => esc(c.title)).join(', ')} can't be undone.</b>` : ' Apps keep running normally.'}`,
        confirm: 'Clean up', danger: permanent.length > 0,
      });
      if (!ok) return;
      const selection = Object.fromEntries(Object.entries(sel).filter(([, s]) => s.size).map(([k, s]) => [k, [...s]]));
      setHTML($('#cl-main', el), hero(true, 'Cleaning…'));
      try {
        const job = await runJob('/api/cleaner/clean', { scanId: scan.id, selection }, (j) => setHTML($('#cl-main', el), hero(true, `${j.phase || 'Cleaning'} · ${bytes(j.data?.freed || 0)} freed`, j.progress)), 400);
        if (job.status !== 'done') throw new Error(job.error || 'Cleaning stopped');
        stats = job.result.stats;
        scan = null;
        setHTML($('#cl-main', el), `<div class="glass card pad-l" style="text-align:center;padding:36px">
          <div class="iconwell" style="margin:0 auto 12px;width:64px;height:64px;border-radius:20px;background:rgba(12,163,12,.22);color:var(--good-text)">${icon('circle-check', 34)}</div>
          <div class="huge">${bytesHTML(job.result.freed)}</div><div style="font-weight:620;margin-top:6px">freed up</div>
          ${job.result.errors.length ? `<div class="hint mt" style="justify-content:center">${icon('info-circle', 15)}${job.result.errors.length} item(s) couldn't be removed: ${esc(job.result.errors[0])}</div>` : ''}
          <button class="btn mt2" id="cl-scan" style="margin-top:22px">${icon('refresh', 16)}Scan again</button></div>`);
      } catch (err) { toast(err.message, 'error'); scan = null; render(); }
    }
  });

  el.addEventListener('change', (e) => {
    const cc = e.target.closest('[data-catcheck]');
    if (cc) {
      const c = scan.categories.find((x) => x.id === cc.dataset.catcheck);
      sel[c.id] = new Set(cc.checked ? c.items.map((i) => i.id) : []);
      $$(`[data-item^="${c.id}:"]`, el).forEach((x) => { x.checked = cc.checked; });
      cc.indeterminate = false;
      syncChecks();
      return;
    }
    const it = e.target.closest('[data-item]');
    if (it) {
      const [cid, iid] = it.dataset.item.split(':');
      sel[cid] = sel[cid] || new Set();
      it.checked ? sel[cid].add(iid) : sel[cid].delete(iid);
      const c = scan.categories.find((x) => x.id === cid);
      const head = el.querySelector(`[data-catcheck="${cid}"]`);
      const n = c.items.filter((i) => sel[cid].has(i.id)).length;
      head.checked = n === c.items.length;
      head.indeterminate = n > 0 && n < c.items.length;
      syncChecks();
    }
  });

  if (scan) for (const c of scan.categories) sel[c.id] = new Set(c.default && !c.infoOnly ? c.items.map((i) => i.id) : []);
  render();
  syncChecks();
  return {};
}
