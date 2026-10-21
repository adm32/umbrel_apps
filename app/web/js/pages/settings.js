import { ago, api, bindSeg, confirmDialog, esc, icon, info, modal, seg, toast } from '../ui.js';
import { BUILTIN, umbrelAsset, WALLPAPERS } from '../app.js';

export default async function settings(el, app) {
  const s = app.settings;
  const sys = app.system;
  const row = (t, d, ctl) => `<div class="setrow"><div><div class="t">${t}</div>${d ? `<div class="d">${d}</div>` : ''}</div><div class="ctl">${ctl}</div></div>`;
  const num = (key, val, step, min, max, w = 90) => `<input class="input" type="number" data-num="${key}" value="${val}" step="${step}" min="${min}" max="${max}" style="width:${w}px">`;
  const sel = (key, opts, val) => `<select class="input" data-sel="${key}">${opts.map(([v, l]) => `<option value="${v}" ${String(v) === String(val) ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
  const tog = (key, on) => `<label class="toggle"><input type="checkbox" data-tog="${key}" ${on ? 'checked' : ''}><span></span></label>`;

  el.innerHTML = `
    <div class="pagehead"><div><h1>Settings</h1><p>Changes save automatically.</p></div></div>

    <div class="section-title" id="security" style="margin-top:0">Security</div>
    <div class="glass card pad-l" id="sec-box"><div class="skeleton" style="height:120px"></div></div>

    <div class="section-title">Appearance</div>
    <div class="glass card pad-l">
      ${row('Theme', 'White with orange accents by default. "Home" looks like Apple\'s Home app. Auto follows your device.', seg('theme', [['light', 'Light'], ['home', 'Home'], ['dark', 'Dark'], ['auto', 'Auto']], s.theme || 'light'))}
      ${row('Accent colour', 'Warm orange, or the colour umbrelOS pairs with your wallpaper.', seg('accent', [['orange', 'Orange'], ['wallpaper', 'Match wallpaper']], s.accent || 'orange'))}
      ${row('Glass', 'Liquid glass bends the light at the edges (Chrome, Edge, Brave, Arc). Frosted works everywhere. Solid is easiest to read.', seg('glass', [['liquid', 'Liquid'], ['frosted', 'Frosted'], ['solid', 'Solid']], s.glass || 'liquid'))}
      ${row('Wallpaper blur', '', `<input type="range" min="0" max="40" step="2" value="${s.wallpaper_blur || 0}" data-range="wallpaper_blur" style="width:160px;accent-color:var(--brand)">`)}
      <div style="padding:14px 2px 4px"><div class="t" style="font-weight:560;margin-bottom:4px">Wallpaper</div><div class="small muted" style="margin-bottom:12px">Warm built-in gradients, or the same wallpapers umbrelOS uses (loaded from your Umbrel).</div>
        <div class="wallgrid" id="wp-grid">
          ${Object.entries(BUILTIN).filter(([k]) => k !== 'homekit').map(([k, [name]]) => `<button data-wp="${k}" class="${s.wallpaper === k ? 'on' : ''}" aria-label="${name}"><div class="wallpaper" data-builtin="${k}" style="position:absolute;inset:0;z-index:0"></div><span style="z-index:1">${name}</span></button>`).join('')}
        </div>
        <div id="wp-umbrel-wrap"><div class="small muted" style="margin:16px 0 10px">From umbrelOS</div>
        <div class="wallgrid" id="wp-umbrel">
          ${Object.keys(WALLPAPERS).map((k) => `<button data-wp="${k}" class="${String(s.wallpaper) === k ? 'on' : ''}" aria-label="umbrelOS wallpaper ${k}"><img src="${umbrelAsset(`/assets/wallpapers/generated-avif/thumbnails/${k}.avif`)}" alt="" loading="lazy" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></button>`).join('')}
        </div></div></div>
    </div>

    <div class="section-title">Units & power</div>
    <div class="glass card pad-l">
      ${row('Temperature', '', seg('temp_unit', [['C', '°C'], ['F', '°F']], s.temp_unit))}
      ${row('Electricity price', 'What you pay per kWh — check your bill.', `${sel('currency', [['€', '€'], ['$', '$'], ['£', '£'], ['kr ', 'kr'], ['CHF ', 'CHF'], ['¥', '¥']], s.currency)}${num('price_per_kwh', s.price_per_kwh, 0.01, 0, 10)}`)}
      ${row('Extra power estimate', 'Added to the CPU\'s own reading to estimate the whole PC (drives, RAM, power brick). 5–8 W suits most mini PCs.', `${num('power_overhead_w', s.power_overhead_w, 0.5, 0, 200)}<span class="muted">W</span>`)}
      ${row('Smart plug', 'For exact wall power, plug your Umbrel into a Shelly or Tasmota smart plug on your network.', `${sel('smart_plug.type', [['none', 'None'], ['shelly2', 'Shelly Plus / Gen 2+'], ['shelly1', 'Shelly Gen 1'], ['tasmota', 'Tasmota']], s.smart_plug.type)}<input class="input" data-text="smart_plug.host" value="${esc(s.smart_plug.host)}" placeholder="192.168.1.80" style="width:150px">`)}
    </div>

    <div class="section-title">Network</div>
    <div class="glass card pad-l">
      ${row('Data plan resets on day', 'Used for the "this billing cycle" total.', num('net_reset_day', s.net_reset_day, 1, 1, 28, 80))}
      ${row('Latency monitor', 'Pings your router and the internet every 5 seconds so you can spot drop-outs.', tog('latency_enabled', s.latency_enabled))}
      ${row('Internet ping target', 'An address that\'s always up.', `<input class="input" data-text="latency_target" value="${esc(s.latency_target)}" style="width:170px">`)}
      ${row('Watch for new devices', 'Scans your network in the background and tells you when an unknown device joins.', sel('lan_scan_minutes', [[0, 'Off'], [15, 'Every 15 min'], [30, 'Every 30 min'], [60, 'Every hour'], [180, 'Every 3 hours']], s.lan_scan_minutes))}
      ${row('Automatic speed tests', 'Builds a history so you can see if your provider delivers what you pay for. Each test uses some data.', sel('auto_speedtest_hours', [[0, 'Off'], [1, 'Every hour'], [3, 'Every 3 hours'], [6, 'Every 6 hours'], [12, 'Twice a day'], [24, 'Daily']], s.auto_speedtest_hours))}
    </div>

    <div class="section-title" id="notify">Notifications</div>
    <div class="glass card pad-l">
      ${row('Send alerts to my phone', 'Get a push when a drive is failing, the CPU overheats, the internet drops, the Umbrel restarts or a new device joins.',
        sel('notify.type', [['none', 'Off'], ['ntfy', 'ntfy (free app)'], ['discord', 'Discord'], ['telegram', 'Telegram'], ['webhook', 'Webhook (advanced)']], s.notify.type))}
      <div data-ntype="ntfy">${row(`ntfy topic ${info('Install the free <b>ntfy</b> app on your phone, subscribe to a topic name only you know (like <i>umbrel-adam-7x2k</i>), and enter the same name here. Self-hosting ntfy? Put your server address in the second box.')}`, '',
        `<input class="input" data-text="notify.topic" value="${esc(s.notify.topic)}" placeholder="umbrel-yourname-7x2k" style="width:190px"><input class="input" data-text="notify.url" value="${esc(s.notify.type === 'ntfy' ? s.notify.url : '')}" placeholder="https://ntfy.sh" style="width:150px">`)}</div>
      <div data-ntype="discord webhook">${row(`Webhook URL ${info('Discord: Server settings → Integrations → Webhooks → New webhook → Copy URL.')}`, '',
        `<input class="input" data-text="notify.url" value="${esc(['discord', 'webhook'].includes(s.notify.type) ? s.notify.url : '')}" placeholder="https://…" style="width:320px;max-width:60vw">`)}</div>
      <div data-ntype="telegram">${row(`Telegram ${info('Create a bot with <b>@BotFather</b> to get a token, send your bot a message, then get your chat ID from <b>@userinfobot</b>.')}`, '',
        `<input class="input" data-text="notify.token" value="${esc(s.notify.type === 'telegram' ? s.notify.token : '')}" placeholder="Bot token" style="width:200px"><input class="input" data-text="notify.chat" value="${esc(s.notify.chat)}" placeholder="Chat ID" style="width:120px">`)}</div>
      <div data-ntype="ntfy discord telegram webhook">
        ${row('Which alerts', '', sel('notify.min_level', [['warning', 'Warnings and problems'], ['critical', 'Only serious problems']], s.notify.min_level))}
        ${row('Tell me when things are fixed', '', tog('notify.resolved', s.notify.resolved))}
        ${row('Check it works', '', `<button class="btn sm" id="nt-test">${icon('bell', 15)}Send a test</button>`)}
      </div>
    </div>

    <div class="section-title">Alerts</div>
    <div class="glass card pad-l">
      ${row('CPU temperature warning', '', `${num('alerts.cpu_temp', s.alerts.cpu_temp, 1, 50, 105, 80)}<span class="muted">°C</span>`)}
      ${row('Drive temperature warning', '', `${num('alerts.disk_temp', s.alerts.disk_temp, 1, 35, 80, 80)}<span class="muted">°C</span>`)}
      ${row('Storage almost full at', '', `${num('alerts.disk_usage', s.alerts.disk_usage, 1, 50, 99, 80)}<span class="muted">%</span>`)}
    </div>

    <div class="section-title">About</div>
    <div class="glass card pad-l">
      <dl class="kv">
        <dt>Vitals</dt><dd>${esc(sys.version || '')}${sys.demo ? ' · demo mode' : ''}</dd>
        <dt>Device</dt><dd>${esc(sys.device || '–')}</dd><dt>Processor</dt><dd>${esc(sys.cpuModel || '–')}</dd>
        <dt>System</dt><dd>${esc(sys.os || '–')} · Linux ${esc(sys.kernel || '')}</dd><dt>Hostname</dt><dd>${esc(sys.hostname || '–')}</dd>
        <dt>Health data</dt><dd>${sys.smartAvailable ? 'available' : 'not available'}</dd><dt>Power sensor</dt><dd>${sys.powerAvailable ? 'available' : esc(sys.powerError || 'not found')}</dd>
      </dl>
      <p class="hint mt">${icon('shield-check', 15)}<span>Your data stays on your Umbrel. The only outside connections are the speed test and public-IP lookup (Cloudflare), and app icons from the Umbrel app gallery.</span></p>
    </div>`;

  const showNotifyFields = (type) => el.querySelectorAll('[data-ntype]').forEach((x) => x.classList.toggle('hide', !x.dataset.ntype.split(' ').includes(type)));
  showNotifyFields(s.notify.type);
  el.querySelector('#nt-test').addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    b.disabled = true;
    try {
      await new Promise((r) => setTimeout(r, 450));  // let a pending save land first
      await api.post('/api/notify/test', {});
      toast('Test sent — check your phone', 'good');
    } catch (err) { toast(err.message, 'error', 6000); }
    b.disabled = false;
  });

  // edits made in quick succession are merged into one save (none get lost)
  let timer;
  let pending = {};
  const save = (patch, msg = 'Saved') => {
    for (const [k, v] of Object.entries(patch)) {
      pending[k] = v && typeof v === 'object' && !Array.isArray(v) ? { ...(pending[k] || app.settings[k]), ...v } : v;
    }
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const p = pending;
      pending = {};
      try { await app.saveSettings(p); toast(msg, 'good', 1400); } catch (e) { toast(e.message, 'error'); }
    }, 350);
  };
  const nested = (key, value) => {
    const [a, b] = key.split('.');
    return b ? { [a]: { [b]: value } } : { [a]: value };
  };

  for (const key of ['theme', 'accent', 'glass', 'temp_unit']) bindSeg(el, key, (v) => save({ [key]: v }));
  el.addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.num) save(nested(t.dataset.num, +t.value));
    if (t.dataset.sel) save(nested(t.dataset.sel, ['auto_speedtest_hours', 'lan_scan_minutes'].includes(t.dataset.sel) ? +t.value : t.value));
    if (t.dataset.sel === 'notify.type') showNotifyFields(t.value);
    if (t.dataset.text) save(nested(t.dataset.text, t.value.trim()));
    if (t.dataset.tog) save(nested(t.dataset.tog, t.checked));
  });
  el.addEventListener('input', (e) => {
    const r = e.target.closest('[data-range]');
    if (r) { document.documentElement.style.setProperty('--wp-blur', `${r.value}px`); save({ wallpaper_blur: +r.value }); }
  });
  // umbrelOS wallpapers only load when Vitals runs on an Umbrel; hide them otherwise
  el.querySelectorAll('#wp-umbrel img').forEach((img) => img.addEventListener('error', () => {
    img.parentElement.remove();
    if (!el.querySelector('#wp-umbrel button')) el.querySelector('#wp-umbrel-wrap')?.remove();
  }));
  el.querySelector('.card').addEventListener('click', (e) => {
    const b = e.target.closest('[data-wp]');
    if (!b) return;
    el.querySelectorAll('[data-wp]').forEach((x) => x.classList.toggle('on', x === b));
    save({ wallpaper: b.dataset.wp }, 'Wallpaper changed');
  });
  // ------------------------------------------------------------ security
  const box = el.querySelector('#sec-box');
  const pwFields = (withCurrent) => `${withCurrent ? '<input class="input" type="password" id="pw-cur" placeholder="Current password" autocomplete="current-password" style="width:100%">' : ''}
    <input class="input" type="password" id="pw-new" placeholder="New password (10+ characters)" autocomplete="new-password" style="width:100%">
    <input class="input" type="password" id="pw-rep" placeholder="Repeat it" autocomplete="new-password" style="width:100%">`;

  async function loadSecurity() {
    const sec = await api.get('/api/auth/security');
    const devices = sec.sessions.map((x) => `<div class="li"><div class="iconwell cell">${icon(/iPhone|Android|Mobile/.test(x.agent) ? 'device-mobile' : 'device-laptop', 18)}</div>
      <div class="grow"><div class="t">${esc(x.ip)}${x.current ? ' <span class="pill sm good">this device</span>' : ''}</div><div class="d">${esc(browserName(x.agent))} · signed in ${esc(ago(x.created))} · active ${esc(ago(x.last))}</div></div>
      ${x.current ? '' : `<button class="btn sm ghost" data-end="${x.id}">Sign out</button>`}</div>`).join('');
    box.innerHTML = `
      <div class="setrow"><div><div class="t">Password for remote access ${info('Vitals only asks for a password when it is opened through a public domain such as <b>vitals.admcore.hu</b>. On your home network and over Tailscale it opens straight away.')}</div>
        <div class="d">${sec.configured ? 'Set. Anyone opening Vitals through your domain must sign in.' : '<b style="color:var(--critical-text)">Not set</b> — Vitals refuses all access through your domain until you set one.'}</div></div>
        <div class="ctl"><button class="btn sm ${sec.configured ? '' : 'primary'}" id="pw-btn">${icon('lock', 15)}${sec.configured ? 'Change password' : 'Set password'}</button></div></div>
      <div class="setrow"><div><div class="t">Two-factor codes ${info('After your password, Vitals also asks for a 6-digit code from an authenticator app (Apple Passwords, Google Authenticator, 1Password, Authy…). A stolen password alone is then useless.')}</div>
        <div class="d">${sec.totp ? 'On' : 'Off'}${!sec.configured ? ' — set a password first' : ''}</div></div>
        <div class="ctl"><button class="btn sm" id="totp-btn" ${sec.configured ? '' : 'disabled'}>${icon('shield-check', 15)}${sec.totp ? 'Turn off' : 'Turn on'}</button></div></div>
      <div style="padding:14px 2px 0"><div class="row between"><div class="t" style="font-weight:560">Signed-in devices</div>
        ${sec.sessions.length > 1 ? '<button class="btn sm ghost" id="end-all">Sign out all others</button>' : ''}</div>
        <div class="list">${devices || '<div class="small muted" style="padding:8px 0">No devices signed in through your domain.</div>'}</div></div>`;

    box.querySelector('#pw-btn').onclick = () => {
      const m = modal({
        title: sec.configured ? 'Change password' : 'Set a password', sub: sec.configured ? 'Other signed-in devices will be signed out.' : 'Use something long that you don\'t use anywhere else.',
        body: `<div class="col" style="gap:10px">${pwFields(sec.configured)}<div class="small" id="pw-msg" style="color:var(--critical-text)"></div></div>`,
        actions: [{ label: 'Cancel', cls: 'ghost' }, { label: 'Save', cls: 'primary', keepOpen: true, onClick: async ({ close }) => {
          const q = (id) => m.body.querySelector(id)?.value || '';
          const msg = m.body.querySelector('#pw-msg');
          if (q('#pw-new') !== q('#pw-rep')) { msg.textContent = 'The two passwords don\'t match'; return; }
          try {
            if (sec.configured) await api.post('/api/auth/password', { current: q('#pw-cur'), password: q('#pw-new') });
            else await api.post('/api/auth/setup', { password: q('#pw-new') });
            close();
            toast('Password saved', 'good');
            loadSecurity();
          } catch (err) { msg.textContent = err.message; }
        } }],
      });
    };

    box.querySelector('#totp-btn').onclick = async () => {
      if (sec.totp) {
        const m = modal({ title: 'Turn off two-factor codes?', sub: 'Enter your password to confirm.',
          body: '<input class="input" type="password" id="tf-pw" placeholder="Password" style="width:100%"><div class="small mt" id="tf-msg" style="color:var(--critical-text)"></div>',
          actions: [{ label: 'Cancel', cls: 'ghost' }, { label: 'Turn off', cls: 'danger', keepOpen: true, onClick: async ({ close }) => {
            try { await api.post('/api/auth/totp/disable', { password: m.body.querySelector('#tf-pw').value }); close(); toast('Two-factor codes turned off', 'good'); loadSecurity(); } catch (err) { m.body.querySelector('#tf-msg').textContent = err.message; }
          } }] });
        return;
      }
      const t = await api.get('/api/auth/totp/setup');
      const m = modal({ title: 'Turn on two-factor codes', sub: 'Scan this with your authenticator app, then type the 6-digit code it shows.',
        body: `<div style="display:flex;justify-content:center;margin:4px 0 12px"><div style="background:#fff;border-radius:16px;padding:8px;line-height:0">${t.svg}</div></div>
          <div class="small muted" style="text-align:center">Can't scan? Enter this key: <span class="mono" style="user-select:all">${esc(t.secret.replace(/(.{4})/g, '$1 ').trim())}</span></div>
          <input class="input mt" id="tf-code" inputmode="numeric" maxlength="6" placeholder="6-digit code" style="width:100%;text-align:center;letter-spacing:.3em;font-size:18px;margin-top:14px">
          <div class="small mt" id="tf-msg" style="color:var(--critical-text)"></div>`,
        actions: [{ label: 'Cancel', cls: 'ghost' }, { label: 'Turn on', cls: 'primary', keepOpen: true, onClick: async ({ close }) => {
          try { await api.post('/api/auth/totp/enable', { code: m.body.querySelector('#tf-code').value }); close(); toast('Two-factor codes are on', 'good'); loadSecurity(); } catch (err) { m.body.querySelector('#tf-msg').textContent = err.message; }
        } }] });
    };

    box.querySelectorAll('[data-end]').forEach((b) => { b.onclick = async () => { await api.post('/api/auth/sessions/end', { id: b.dataset.end }); loadSecurity(); }; });
    box.querySelector('#end-all')?.addEventListener('click', async () => {
      if (await confirmDialog({ title: 'Sign out all other devices?', text: 'They will need the password (and code) again.', confirm: 'Sign out' })) {
        await api.post('/api/auth/sessions/end', { all: true });
        loadSecurity();
      }
    });
  }
  const jump = (sub) => { if (sub === 'notify' || sub === 'security') el.querySelector(`#${sub}`)?.scrollIntoView({ behavior: 'smooth' }); };
  // jump only once the security box has its real height, or we'd land in the wrong place
  loadSecurity().catch((e) => { box.innerHTML = `<div class="small muted">${esc(e.message)}</div>`; })
    .then(() => { if (app.sub === 'security' || app.sub === 'notify') jump(app.sub); });
  return { onRoute: jump };
}

function browserName(ua = '') {
  const b = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${b} on ${os}` : b;
}
