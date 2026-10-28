// Downloader: YouTube (and other sites yt-dlp supports) to MP3 / WAV / MP4 on the Umbrel.
import { ago, api, bar, bindSeg, bytes, cancelJob, confirmDialog, esc, icon, info, runJob, seg, setHTML, store, toast } from '../ui.js';

const FORMATS = [['mp3', 'MP3'], ['wav', 'WAV'], ['mp4', 'MP4 video']];

function dur(s) {
  if (!s && s !== 0) return '';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}` : `${m}:${String(x).padStart(2, '0')}`;
}

export function mount(root) {
  let fmt = store.get('dl-format', 'mp3');
  let preview = null;
  let alive = true;
  const active = new Map(); // job id -> {title, fmt, job}

  root.innerHTML = `
    <div class="glass card pad-l">
      <div class="cardhead"><div class="cardtitle">${icon('cloud-download', 17)}Download from YouTube ${info('Paste a link from YouTube, YouTube Music, SoundCloud, Bandcamp, Vimeo and many more sites. Files are saved on your Umbrel in <b>Home → Downloads → YouTube</b>, so you can open them from Files or your network share, or save them to the device you\'re using. Only download things you have the right to use.')}</div></div>
      <div class="row wrap" style="gap:10px">
        <input class="input grow" id="dl-url" placeholder="https://www.youtube.com/watch?v=…" autocomplete="off" spellcheck="false" style="min-width:240px;height:44px;font-size:15px">
        <button class="btn" id="dl-paste" title="Paste from clipboard">${icon('copy', 16)}Paste</button>
      </div>
      <div id="dl-preview" class="mt"></div>
      <div class="row wrap mt" style="gap:12px;align-items:center">
        ${seg('dlfmt', FORMATS, fmt)}
        <select class="input" id="dl-q" style="height:34px;width:auto">${[['1080', '1080p'], ['720', '720p'], ['480', '480p'], ['best', 'Best (up to 4K)']].map(([v, l]) => `<option value="${v}" ${store.get('dl-q', '1080') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <span class="small muted" id="dl-fmtnote"></span>
        <span class="grow"></span>
        <button class="btn primary" id="dl-go" disabled>${icon('cloud-download', 16)}Download</button>
      </div>
      <div id="dl-active"></div>
    </div>
    <div class="glass card pad-l mt">
      <div class="cardhead"><div class="cardtitle">${icon('music', 17)}Downloaded</div><span class="small muted" id="dl-folder"></span></div>
      <div class="list" id="dl-list"><div class="skeleton" style="height:90px"></div></div>
      <div class="row wrap mt between" style="gap:10px;margin-top:16px">
        <span class="tiny faint" id="dl-ver"></span>
        <button class="btn sm ghost" id="dl-update">${icon('refresh', 15)}Update downloader</button>
      </div>
    </div>`;

  const $ = (s) => root.querySelector(s);
  const fmtNote = () => {
    $('#dl-q').classList.toggle('hide', fmt !== 'mp4');
    setHTML($('#dl-fmtnote'), fmt === 'mp3' ? '320 kbps with cover art' : fmt === 'wav' ? 'uncompressed, ready for your DAW' : 'H.264 — plays everywhere');
  };
  bindSeg(root, 'dlfmt', (v) => { fmt = v; store.set('dl-format', v); fmtNote(); });
  $('#dl-q').addEventListener('change', (e) => store.set('dl-q', e.target.value));
  fmtNote();

  let lookup = 0;
  async function getInfo() {
    const url = $('#dl-url').value.trim();
    preview = null;
    $('#dl-go').disabled = !url;
    if (!/^https?:\/\/\S+\.\S+/.test(url)) { setHTML($('#dl-preview'), ''); return; }
    const mine = ++lookup;
    setHTML($('#dl-preview'), '<div class="row" style="gap:12px"><div class="skeleton" style="width:120px;height:68px;border-radius:10px"></div><div class="grow"><div class="skeleton" style="height:16px;width:70%"></div><div class="skeleton" style="height:12px;width:40%;margin-top:8px"></div></div></div>');
    try {
      const i = await api.post('/api/yt/info', { url });
      if (mine !== lookup) return;
      preview = i;
      setHTML($('#dl-preview'), `<div class="row" style="gap:14px">
        ${i.thumbnail ? `<img src="${esc(i.thumbnail)}" alt="" style="width:128px;height:72px;object-fit:cover;border-radius:10px;flex:none">` : ''}
        <div class="grow" style="min-width:0"><div style="font-weight:620" class="ellipsis">${esc(i.title || 'Untitled')}</div>
        <div class="small muted">${esc([i.uploader, dur(i.duration), i.maxHeight ? `up to ${i.maxHeight}p` : '', i.site !== 'Youtube' ? i.site : ''].filter(Boolean).join(' · '))}</div>
        ${i.isLive ? '<div class="small" style="color:var(--warning-text)">This is a live stream — it can\'t be downloaded until it ends.</div>' : ''}</div></div>`);
    } catch (e) {
      if (mine !== lookup) return;
      setHTML($('#dl-preview'), `<div class="hint" style="color:var(--critical-text)">${icon('alert-circle', 15)}${esc(e.message)}</div>`);
    }
  }
  let t;
  $('#dl-url').addEventListener('input', () => { clearTimeout(t); t = setTimeout(getInfo, 500); });
  $('#dl-url').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#dl-go').click(); });
  $('#dl-paste').addEventListener('click', async () => {
    try { $('#dl-url').value = (await navigator.clipboard.readText()).trim(); getInfo(); } catch { $('#dl-url').focus(); toast('Press ⌘V / Ctrl V to paste', 'info', 2500); }
  });

  function drawActive() {
    setHTML($('#dl-active'), [...active.values()].map(({ title, fmt: f, job }) => {
      const d = job.data || {};
      const speed = d.speed ? ` · ${bytes(d.speed)}/s` : '';
      return `<div class="cell" style="padding:12px 14px;margin-top:12px"><div class="row between" style="gap:10px">
        <div class="grow" style="min-width:0"><div class="ellipsis" style="font-weight:600">${esc(title)}</div>
        <div class="small muted">${esc(job.phase || 'Starting')} · ${f.toUpperCase()}${d.total ? ` · ${bytes(d.done)} of ${bytes(d.total)}` : ''}${speed}</div></div>
        <button class="btn sm ghost icon" data-cancel="${job.id}" title="Stop">${icon('x', 15)}</button></div>
        <div style="margin-top:8px">${bar(Math.max(0.03, job.progress || 0), 'brand', 'thin')}</div></div>`;
    }).join(''));
  }

  async function follow(job, title, f) {
    active.set(job.id, { title, fmt: f, job });
    drawActive();
    for (;;) {
      if (!alive) return;
      try { job = await api.get(`/api/jobs/${job.id}`); } catch { /* blip: keep polling */ }
      active.set(job.id, { title, fmt: f, job });
      drawActive();
      if (job.status !== 'running') break;
      await new Promise((r) => setTimeout(r, 500));
    }
    active.delete(job.id);
    drawActive();
    if (job.status === 'done') { toast(`Downloaded ${job.result.title}`, 'good'); load(); }
    else if (job.status === 'error') toast(job.error, 'error', 7000);
  }

  $('#dl-go').addEventListener('click', async () => {
    const url = $('#dl-url').value.trim();
    if (!url) return;
    try {
      const job = await api.post('/api/yt/download', { url, format: fmt, quality: $('#dl-q').value });
      const title = preview?.title || url;
      $('#dl-url').value = '';
      setHTML($('#dl-preview'), '');
      $('#dl-go').disabled = true;
      preview = null;
      follow(job, title, fmt);
    } catch (e) { toast(e.message, 'error'); }
  });

  async function load() {
    try {
      const s = await api.get('/api/yt');
      setHTML($('#dl-folder'), `Saved in ${esc(s.folder)}`);
      setHTML($('#dl-ver'), s.version ? `yt-dlp ${esc(s.version)}${s.updating ? ' · updating…' : ''}` : 'Downloader not installed');
      for (const j of s.running) if (!active.has(j.id)) follow(j, j.data?.url || 'Download', j.data?.format || '');
      setHTML($('#dl-list'), s.items.length ? s.items.slice(0, 100).map((x) => `<div class="li">
        <div class="iconwell cell">${icon(x.format === 'mp4' ? 'device-tv' : 'music', 18)}</div>
        <div class="grow"><div class="t">${esc(x.title)}</div><div class="d"><span class="pill sm">${x.format.toUpperCase()}</span> ${bytes(x.size)} · ${esc(ago(x.at))}${x.exists ? '' : ' · <span style="color:var(--warning-text)">moved or deleted</span>'}</div></div>
        ${x.exists ? `<a class="btn sm ghost icon" href="/api/yt/file/${x.id}" download title="Save to this device">${icon('cloud-download', 16)}</a>` : ''}
        <button class="btn sm ghost icon" data-del="${x.id}" title="${x.exists ? 'Delete' : 'Remove from list'}">${icon('trash', 15)}</button></div>`).join('')
        : '<div class="small muted" style="padding:6px 0">Nothing yet. Paste a link above.</div>');
    } catch (e) { setHTML($('#dl-list'), `<div class="small muted">${esc(e.message)}</div>`); }
  }

  root.addEventListener('click', async (e) => {
    const c = e.target.closest('[data-cancel]');
    if (c) { cancelJob(c.dataset.cancel); return; }
    const d = e.target.closest('[data-del]');
    if (d) {
      if (!(await confirmDialog({ title: 'Delete this download?', text: 'The file is removed from your Umbrel.', confirm: 'Delete', danger: true }))) return;
      await api.post('/api/yt/delete', { id: d.dataset.del });
      load();
      return;
    }
    if (e.target.closest('#dl-update')) {
      const b = e.target.closest('#dl-update');
      b.disabled = true;
      setHTML($('#dl-ver'), 'Updating yt-dlp…');
      try {
        const j = await runJob('/api/yt/update', {}, null, 1000);
        if (j.status === 'done') toast(`Downloader updated to ${j.result.version}`, 'good');
        else toast(j.error || 'Update failed', 'error');
      } catch (err) { toast(err.message, 'error'); }
      b.disabled = false;
      load();
    }
  });

  load();
  return { destroy() { alive = false; } };
}
