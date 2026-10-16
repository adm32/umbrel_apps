import { $, ago, api, bar, bytes, bytesHTML, confirmDialog, empty, esc, icon, runJob, setHTML, store, toast } from '../ui.js';

const NOTES = [['1/1', 4], ['1/2', 2], ['1/4', 1], ['1/8', 0.5], ['1/16', 0.25], ['1/32', 0.125], ['1/64', 0.0625]];
const NOTE_NAMES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];

export default async function studio(el) {
  let state = await api.get('/api/studio').catch(() => ({ last: null, drives: [] }));
  let res = state.last;
  let cwd = store.get('studio-path', '');
  const del = new Set();

  el.innerHTML = `
    <div class="pagehead"><div><h1>Studio</h1><p>Tools for producers: understand your sample library, reclaim space from duplicate samples and DAW backups, and plan recording sessions.</p></div></div>
    <div class="grid g-main">
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('folder-open', 17)}Sample library scanner</div></div>
        <div class="crumbs" id="sd-crumbs"></div>
        <div class="folderlist cell" id="sd-folders"></div>
        <div class="row wrap mt" style="gap:10px"><button class="btn primary" id="sd-scan">${icon('search', 16)}Scan this folder</button><span class="small muted" id="sd-sel"></span></div>
        <div id="sd-prog"></div>
      </div>
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('microphone', 17)}Recording space</div></div>
        <div class="formrow">
          <div class="field"><label>Sample rate</label><select class="input" id="rc-sr">${[44100, 48000, 88200, 96000, 192000].map((r) => `<option value="${r}" ${r === 48000 ? 'selected' : ''}>${r / 1000} kHz</option>`).join('')}</select></div>
          <div class="field"><label>Bit depth</label><select class="input" id="rc-bd"><option value="16">16-bit</option><option value="24" selected>24-bit</option><option value="32">32-bit float</option></select></div>
          <div class="field" style="max-width:110px"><label>Tracks</label><input class="input" id="rc-tr" type="number" min="1" max="256" value="${store.get('rc-tracks', 8)}"></div>
        </div>
        <div id="rc-out" class="mt"></div>
      </div>
    </div>
    <div id="sd-results"></div>
    <div class="section-title">Tempo tools</div>
    <div class="grid g-main">
      <div class="glass card pad-l">
        <div class="row wrap" style="gap:18px;align-items:flex-end">
          <div class="field" style="width:120px"><label>Tempo (BPM)</label><input class="input" id="tp-bpm" type="number" min="20" max="400" step="0.1" value="${store.get('bpm', 120)}" style="font-size:20px;font-weight:650;height:46px"></div>
          <div class="grow" style="min-width:200px"><button class="taparea" id="tp-tap">${icon('hand-click', 22)}<div>Tap the beat</div><div class="tiny" style="opacity:.8" id="tp-taps">tap 4+ times · or press T</div></button></div>
        </div>
        <div class="tablewrap mt"><table class="t"><thead><tr><th>Note</th><th class="r">Straight</th><th class="r">Dotted</th><th class="r">Triplet</th><th class="r hide-sm">Hz (LFO)</th></tr></thead><tbody id="tp-table"></tbody></table></div>
        <p class="hint mt">${icon('bulb', 15)}<span>Delay times line up echoes with your groove. For reverb, try a <b>pre-delay</b> of a 1/64 note and a <b>decay</b> that ends before the next 1/2 note: <b id="tp-rev"></b>.</span></p>
      </div>
      <div class="glass card pad-l">
        <div class="cardhead"><div class="cardtitle">${icon('wave-sine', 17)}Note ↔ frequency</div>
          <select class="input" id="nf-a4" style="height:30px;width:auto;font-size:12.5px"><option value="440">A4 = 440 Hz</option><option value="442">A4 = 442 Hz</option><option value="432">A4 = 432 Hz</option></select></div>
        <div class="field"><label>Frequency (Hz) — e.g. a resonance you want to EQ out</label><input class="input" id="nf-hz" type="number" value="${store.get('nf-hz', 110)}" min="8" max="20000"></div>
        <div id="nf-out" class="mt"></div>
        <div class="tablewrap mt"><table class="t"><thead><tr><th>Octave</th>${['C', 'E', 'G', 'A'].map((n) => `<th class="r">${n}</th>`).join('')}</tr></thead><tbody id="nf-table"></tbody></table></div>
      </div>
    </div>`;

  // ---------------------------------------------------------------- folder browser
  async function browse(path) {
    try {
      const b = await api.get(`/api/studio/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`);
      cwd = b.path;
      store.set('studio-path', cwd);
      const parts = (b.display || '').split('/').filter(Boolean);
      let acc = '';
      const base = cwd.slice(0, cwd.length - (b.display || '').length + (parts[0] || '').length);
      setHTML($('#sd-crumbs', el), `<button data-path="">${icon('home', 14)}</button>` + parts.map((p, i) => {
        acc = i === 0 ? base : `${acc}/${p}`;
        return `${icon('chevron-right', 12)}<button data-path="${esc(acc)}">${esc(p)}</button>`;
      }).join(''));
      setHTML($('#sd-folders', el), b.dirs.length ? b.dirs.map((d) => `<button data-path="${esc(d.path)}">${icon('folder', 18)}<span class="grow ellipsis">${esc(d.name)}</span>${icon('chevron-right', 14)}</button>`).join('')
        : '<div class="small muted" style="padding:12px">No sub-folders here.</div>');
      setHTML($('#sd-sel', el), cwd ? `Will scan <b>${esc(b.display)}</b>` : 'Pick a folder — your samples drive, Home, or a project folder.');
      $('#sd-scan', el).disabled = !cwd;
    } catch (e) {
      if (path) { store.set('studio-path', ''); browse(''); } else toast(e.message, 'error');
    }
  }

  // ---------------------------------------------------------------- results
  function renderResults() {
    const box = $('#sd-results', el);
    if (!res) { setHTML(box, ''); return; }
    const fmtMax = Math.max(1, ...res.formats.map((f) => f.size));
    const delBytes = () => {
      let n = 0;
      for (const g of res.duplicates) for (const c of g.copies) if (del.has(c.id)) n += g.size;
      for (const b of res.backups) if (del.has(b.id)) n += b.size;
      return n;
    };
    setHTML(box, `
      <div class="section-title">${esc(res.root)} <small>scanned ${ago(res.at)} · ${res.files.toLocaleString()} files in ${res.seconds}s</small></div>
      <div class="grid g4">
        <div class="glass card"><div class="small muted">${icon('music', 14)} Audio files</div><div class="big">${res.audioFiles.toLocaleString()}</div></div>
        <div class="glass card"><div class="small muted">${icon('database', 14)} Library size</div><div class="big">${bytesHTML(res.audioBytes)}</div></div>
        <div class="glass card"><div class="small muted">${icon('clock', 14)} Audio (WAV/AIFF)</div><div class="big">${res.hours.toLocaleString()}<small>hours</small></div></div>
        <div class="glass card"><div class="small muted">${icon('copy', 14)} Wasted on duplicates</div><div class="big" style="color:${res.duplicateWaste ? 'var(--brand-text)' : 'inherit'}">${bytesHTML(res.duplicateWaste)}</div></div>
      </div>
      <div class="grid g3 mt">
        <div class="glass card pad-l"><div class="cardtitle" style="margin-bottom:10px">${icon('file-text', 16)}Formats</div>
          ${res.formats.map((f) => `<div class="sensor"><div class="name"><b>${esc(f.ext.toUpperCase())}</b> <span class="faint small">${f.count.toLocaleString()} files</span></div><div class="val small">${bytes(f.size)}</div>${bar(f.size / fmtMax, 'brand', 'thin')}</div>`).join('') || '<div class="small muted">No audio found.</div>'}</div>
        <div class="glass card pad-l"><div class="cardtitle" style="margin-bottom:10px">${icon('wave-square', 16)}Sample rates & bit depth</div>
          ${res.sampleRates.map(([k, v]) => `<div class="sensor"><div class="name">${esc(k)}</div><div class="val small">${v.toLocaleString()}</div>${bar(v / Math.max(1, res.sampleRates[0][1]), 's1', 'thin')}</div>`).join('')}
          <div class="row wrap mt" style="gap:6px">${res.bitDepths.map(([k, v]) => `<span class="pill">${esc(k)} · ${v.toLocaleString()}</span>`).join('')}</div></div>
        <div class="glass card pad-l"><div class="cardtitle" style="margin-bottom:10px">${icon('vinyl', 16)}Projects found <span class="faint">(${res.projectCount})</span></div>
          <div class="list">${res.projects.slice(0, 6).map((p) => `<div class="li"><div class="grow"><div class="t">${esc(p.name)}</div><div class="d">${esc(p.daw)} · ${p.modified ? ago(p.modified) : ''}</div></div></div>`).join('') || '<div class="small muted">No DAW projects in this folder.</div>'}</div></div>
      </div>
      <div class="grid g2 mt">
        <div class="glass card pad-l"><div class="cardhead"><div class="cardtitle">${icon('copy', 16)}Duplicate samples <span class="faint">(${res.duplicates.length})</span></div>
          ${res.duplicates.length ? `<button class="btn sm" id="sd-autodup">Select extra copies</button>` : ''}</div>
          <div style="max-height:420px;overflow:auto">${res.duplicates.map((g) => `<div style="padding:10px 0;border-top:1px solid var(--line)">
            <div class="row between"><b class="ellipsis">${esc(g.name)}</b><span class="small muted nowrap">${g.copies.length} copies · ${bytes(g.size)} each</span></div>
            ${g.copies.map((c, i) => `<label class="row small" style="gap:8px;margin-top:6px;cursor:pointer"><input type="checkbox" class="check" data-del="${c.id}" ${del.has(c.id) ? 'checked' : ''}>
              <span class="ellipsis ${i === 0 ? '' : 'muted'}" title="${esc(c.path)}">${esc(c.path)}</span></label>`).join('')}</div>`).join('') || empty('circle-check', 'No duplicates', 'Every sample in this folder is unique.')}</div></div>
        <div class="glass card pad-l"><div class="cardhead"><div class="cardtitle">${icon('clock', 16)}DAW backup files <span class="faint">(${res.backups.length} · ${bytes(res.backupBytes)})</span></div>
          ${res.backups.length ? `<button class="btn sm" id="sd-allbak">Select all</button>` : ''}</div>
          <p class="small muted" style="margin-top:0">Ableton, FL Studio, REAPER and Cubase save automatic backup copies of your project files. Your actual projects and audio aren't included here.</p>
          <div style="max-height:340px;overflow:auto">${res.backups.map((b) => `<label class="row small" style="gap:8px;padding:6px 0;border-top:1px solid var(--line);cursor:pointer"><input type="checkbox" class="check" data-del="${b.id}" ${del.has(b.id) ? 'checked' : ''}>
            <span class="ellipsis grow muted" title="${esc(b.path)}">${esc(b.path)}</span><span class="num nowrap">${bytes(b.size)}</span></label>`).join('') || empty('circle-check', 'No backup clutter', 'Nothing to tidy up here.')}</div></div>
      </div>
      <div class="cleanbar glass lensed ${del.size ? '' : 'hide'}" data-lens="40"><div class="grow"><b>${del.size} file${del.size === 1 ? '' : 's'} selected</b> <span class="muted">· ${bytes(delBytes())}</span></div>
        <button class="btn danger" id="sd-delete">${icon('trash', 16)}Delete selected</button></div>
      <div class="glass card mt"><div class="cardtitle" style="margin-bottom:6px">${icon('chart-bar', 16)}Biggest audio files</div>
        <div class="list">${res.largest.slice(0, 8).map((f) => `<div class="li"><span class="grow ellipsis small" title="${esc(f.path)}">${esc(f.path)}</span><span class="num small nowrap">${bytes(f.size)}</span></div>`).join('')}</div></div>`);
    window.__vitalsGlass?.(box);
  }

  function updateBar() {
    const barEl = $('#sd-results .cleanbar', el);
    if (!barEl) return;
    let n = 0;
    for (const g of res.duplicates) for (const c of g.copies) if (del.has(c.id)) n += g.size;
    for (const b of res.backups) if (del.has(b.id)) n += b.size;
    barEl.classList.toggle('hide', !del.size);
    setHTML(barEl.querySelector('.grow'), `<b>${del.size} file${del.size === 1 ? '' : 's'} selected</b> <span class="muted">· ${bytes(n)}</span>`);
  }

  // ---------------------------------------------------------------- calculators
  function recording() {
    const sr = +$('#rc-sr', el).value, bd = +$('#rc-bd', el).value, tr = Math.max(1, +$('#rc-tr', el).value || 1);
    store.set('rc-tracks', tr);
    const bps = sr * (bd / 8) * tr; // mono tracks
    const perHour = bps * 3600;
    const drives = (state.drives || []).slice(0, 4);
    setHTML($('#rc-out', el), `<div class="stats"><div class="stat cell"><div class="k">Per minute</div><div class="v">${bytesHTML(bps * 60)}</div></div>
      <div class="stat cell"><div class="k">Per hour</div><div class="v">${bytesHTML(perHour)}</div></div>
      <div class="stat cell"><div class="k">Data rate</div><div class="v">${(bps / 1e6).toFixed(1)}<small>MB/s</small></div></div></div>
      <div class="list mt">${drives.map((d) => `<div class="li"><div class="grow"><div class="t">${esc(d.model)}</div><div class="d">${bytes(d.free)} free</div></div><div class="mid">${Math.floor(d.free / perHour).toLocaleString()}<small>hours</small></div></div>`).join('')}</div>
      <div class="tiny faint">${tr} mono track${tr > 1 ? 's' : ''} recording at once. Stereo tracks count as two.</div>`);
  }

  function tempo() {
    const bpm = Math.min(400, Math.max(20, +$('#tp-bpm', el).value || 120));
    store.set('bpm', bpm);
    const beat = 60000 / bpm;
    const ms = (v) => v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v.toFixed(v < 100 ? 1 : 0)} ms`;
    setHTML($('#tp-table', el), NOTES.map(([n, b]) => `<tr><td><b>${n}</b></td><td class="r">${ms(beat * b)}</td><td class="r">${ms(beat * b * 1.5)}</td><td class="r">${ms(beat * b * 2 / 3)}</td><td class="r hide-sm faint">${(1000 / (beat * b)).toFixed(2)} Hz</td></tr>`).join(''));
    setHTML($('#tp-rev', el), `pre-delay ${ms(beat / 16)}, decay ${ms(beat * 2 - beat / 16)}`);
  }

  function noteFreq() {
    const a4 = +$('#nf-a4', el).value;
    const hz = +$('#nf-hz', el).value;
    store.set('nf-hz', hz);
    if (hz > 0) {
      const n = 12 * Math.log2(hz / a4) + 57;
      const r = Math.round(n);
      const cents = Math.round((n - r) * 100);
      setHTML($('#nf-out', el), `<div class="row" style="gap:14px"><div class="huge" style="font-size:44px">${NOTE_NAMES[((r % 12) + 12) % 12]}<small>${Math.floor(r / 12)}</small></div>
        <div class="small muted">${cents === 0 ? 'Exactly in tune' : `${Math.abs(cents)} cents ${cents > 0 ? 'sharp' : 'flat'}`}<br>${(a4 * 2 ** ((r - 57) / 12)).toFixed(2)} Hz is the exact note</div></div>`);
    }
    setHTML($('#nf-table', el), [0, 1, 2, 3, 4, 5, 6].map((o) => `<tr><td>${o}</td>${[0, 4, 7, 9].map((k) => `<td class="r num">${(a4 * 2 ** ((o * 12 + k - 57) / 12)).toFixed(o < 2 ? 2 : 1)}</td>`).join('')}</tr>`).join(''));
  }

  const taps = [];
  function tap() {
    const now = performance.now();
    if (taps.length && now - taps[taps.length - 1] > 2500) taps.length = 0;
    taps.push(now);
    if (taps.length > 8) taps.shift();
    if (taps.length >= 2) {
      const iv = (taps[taps.length - 1] - taps[0]) / (taps.length - 1);
      $('#tp-bpm', el).value = (60000 / iv).toFixed(1);
      tempo();
    }
    setHTML($('#tp-taps', el), taps.length < 4 ? `${taps.length} tap${taps.length === 1 ? '' : 's'} — keep going` : `${(60000 / ((taps[taps.length - 1] - taps[0]) / (taps.length - 1))).toFixed(1)} BPM`);
  }
  const onKey = (e) => { if ((e.key === 't' || e.key === 'T') && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName)) tap(); };
  document.addEventListener('keydown', onKey);

  // ---------------------------------------------------------------- events
  el.addEventListener('click', async (e) => {
    const p = e.target.closest('[data-path]');
    if (p) { browse(p.dataset.path); return; }
    if (e.target.closest('#tp-tap')) { tap(); return; }
    if (e.target.closest('#sd-scan')) {
      const btn = e.target.closest('button');
      btn.disabled = true;
      del.clear();
      try {
        const j = await runJob('/api/studio/scan', { path: cwd }, (x) => setHTML($('#sd-prog', el), `<div class="small muted mt">${esc(x.phase || 'Starting…')}</div>${bar(x.progress || 0.04, 'brand', 'thin')}`), 400);
        setHTML($('#sd-prog', el), '');
        if (j.status === 'done') { res = j.result; renderResults(); $('#sd-results', el).scrollIntoView({ behavior: 'smooth', block: 'start' }); } else toast(j.error || 'Scan stopped', 'error');
      } catch (err) { toast(err.message, 'error'); }
      btn.disabled = false;
      return;
    }
    if (e.target.closest('#sd-autodup')) { for (const g of res.duplicates) g.copies.slice(1).forEach((c) => del.add(c.id)); renderResults(); return; }
    if (e.target.closest('#sd-allbak')) { res.backups.forEach((b) => del.add(b.id)); renderResults(); return; }
    if (e.target.closest('#sd-delete')) {
      const ok = await confirmDialog({ title: `Delete ${del.size} files?`, text: 'They\'re removed from your Umbrel permanently — there\'s no trash for this. At least one copy of every duplicate is always kept.', confirm: 'Delete', danger: true });
      if (!ok) return;
      try {
        const r = await api.post('/api/studio/delete', { ids: [...del] });
        toast(`Deleted ${r.removed} files · ${bytes(r.freed)} freed`, 'good');
        const gone = new Set(del);
        res.duplicates = res.duplicates.map((g) => ({ ...g, copies: g.copies.filter((c) => !gone.has(c.id)) })).filter((g) => g.copies.length > 1);
        res.backups = res.backups.filter((b) => !gone.has(b.id));
        res.duplicateWaste = res.duplicates.reduce((a, g) => a + g.size * (g.copies.length - 1), 0);
        res.backupBytes = res.backups.reduce((a, b) => a + b.size, 0);
        del.clear();
        renderResults();
      } catch (err) { toast(err.message, 'error'); }
    }
  });
  el.addEventListener('change', (e) => {
    const c = e.target.closest('[data-del]');
    if (c) { c.checked ? del.add(c.dataset.del) : del.delete(c.dataset.del); updateBar(); return; }
    if (e.target.closest('#rc-sr, #rc-bd')) recording();
    if (e.target.closest('#nf-a4')) noteFreq();
  });
  el.addEventListener('input', (e) => {
    if (e.target.id === 'rc-tr') recording();
    if (e.target.id === 'tp-bpm') tempo();
    if (e.target.id === 'nf-hz') noteFreq();
  });

  browse(cwd);
  recording();
  tempo();
  noteFreq();
  renderResults();
  return { destroy() { document.removeEventListener('keydown', onKey); } };
}
