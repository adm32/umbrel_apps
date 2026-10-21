// Sign-in page. Self-contained: nothing else in /js is served before you sign in.
const $ = (id) => document.getElementById(id);
const next = () => {
  const n = new URLSearchParams(location.search).get('next') || '';
  return n.startsWith('#') ? `/${n}` : '/';  // only same-site fragments, never an external URL
};

async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' });
  let data = {};
  try { data = await r.json(); } catch { /* empty */ }
  return { status: r.status, data };
}

let mode = 'login';
let needCode = false;

function show(el, on) { $(el).classList.toggle('hide', !on); }

async function init() {
  const st = await (await fetch('/api/auth/status', { credentials: 'same-origin' })).json();
  if (st.local || st.signedIn) { location.replace(next()); return; }
  if (!st.configured) {
    mode = 'setup';
    $('title').textContent = 'Set up remote access';
    $('sub').textContent = 'Create the password you\'ll use to open Vitals from outside your home.';
    show('setupCode', true);
    show('confirm', true);
    show('rememberRow', false);
    $('password').autocomplete = 'new-password';
    $('password').placeholder = 'New password (10+ characters)';
    $('go').textContent = 'Create password';
    $('note').innerHTML = 'Find the setup code in umbrelOS: <b>Files → Apps → adam-vitals → data → SETUP-CODE.txt</b>. Or open Vitals on your home network and set the password in Settings → Security.';
    $('setupCode').focus();
  } else {
    $('note').textContent = 'Forgot it? Open Vitals at home (or over Tailscale) and change it in Settings → Security.';
    $('password').focus();
  }
}

$('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('msg').textContent = '';
  $('go').disabled = true;
  try {
    if (mode === 'setup') {
      if ($('password').value !== $('confirm').value) { $('msg').textContent = 'The two passwords don\'t match'; return; }
      const r = await post('/api/auth/setup', { setupCode: $('setupCode').value, password: $('password').value });
      if (r.status === 200) { location.replace(next()); return; }
      $('msg').textContent = r.data.error || 'Something went wrong';
      return;
    }
    const r = await post('/api/auth/login', { password: $('password').value, code: $('code').value, remember: $('remember').checked });
    if (r.status === 200) { location.replace(next()); return; }
    if (r.data.needCode) {
      needCode = true;
      show('code', true);
      $('sub').textContent = 'Enter the 6-digit code from your authenticator app.';
      $('code').focus();
      return;
    }
    $('msg').textContent = r.data.error || 'Couldn\'t sign in';
    if (needCode) { $('code').value = ''; $('code').focus(); } else { $('password').select(); }
  } catch {
    $('msg').textContent = 'Can\'t reach Vitals right now';
  } finally {
    $('go').disabled = false;
  }
});

$('code').addEventListener('input', () => { if ($('code').value.replace(/\D/g, '').length === 6) $('form').requestSubmit(); });

init();
