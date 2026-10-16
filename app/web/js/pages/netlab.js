import { $, api, bar, cancelJob, empty, esc, icon, runJob, setHTML, store, toast } from '../ui.js';
import { TimeChart } from '../chart.js';

const TABS = [
  ['devices', 'Devices', 'radar-2'], ['ping', 'Ping', 'activity-heartbeat'], ['trace', 'Traceroute', 'route'],
  ['dns', 'DNS lookup', 'list-search'], ['ports', 'Port check', 'lock-open'], ['listening', 'Open on Umbrel', 'server'],
  ['subnet', 'Subnet calculator', 'binary'], ['quiz', 'Subnetting practice', 'school'], ['lan', 'LAN speed', 'transfer'],
];

// ------------------------------------------------------------------ IPv4 maths

const ip2n = (ip) => ip.split('.').reduce((a, o) => (a << 8) + (+o), 0) >>> 0;
const n2ip = (n) => [24, 16, 8, 0].map((s) => (n >>> s) & 255).join('.');
const maskN = (p) => (p === 0 ? 0 : (0xffffffff << (32 - p)) >>> 0);
const validIp = (s) => /^(\d{1,3})(\.\d{1,3}){3}$/.test(s) && s.split('.').every((o) => +o <= 255);
function maskToPrefix(m) {
  const n = ip2n(m);
  const p = 32 - Math.log2(((~n) >>> 0) + 1);
  return Number.isInteger(p) ? p : null;
}
export function parseCidr(input) {
  const t = input.trim().replace(/\s+/g, ' ');
  let ip, p;
  if (t.includes('/')) {
    [ip, p] = t.split('/');
    p = p.includes('.') ? maskToPrefix(p) : +p;
  } else if (t.includes(' ')) {
    [ip, p] = t.split(' ');
    p = maskToPrefix(p);
  } else { ip = t; p = 24; }
  if (!validIp(ip) || p == null || isNaN(p) || p < 0 || p > 32) return null;
  return { ip, prefix: p };
}
export function subnet(ip, prefix) {
  const a = ip2n(ip), m = maskN(prefix);
  const net = (a & m) >>> 0, bc = (net | (~m >>> 0)) >>> 0;
  const hosts = prefix >= 31 ? (prefix === 32 ? 1 : 2) : 2 ** (32 - prefix) - 2;
  const first = prefix >= 31 ? net : net + 1, last = prefix >= 31 ? bc : bc - 1;
  const o1 = a >>> 24;
  const cls = o1 < 128 ? 'A' : o1 < 192 ? 'B' : o1 < 224 ? 'C' : o1 < 240 ? 'D (multicast)' : 'E (reserved)';
  const priv = (o1 === 10) || (o1 === 172 && ((a >>> 16) & 255) >= 16 && ((a >>> 16) & 255) <= 31) || (o1 === 192 && ((a >>> 16) & 255) === 168);
  const special = o1 === 127 ? 'Loopback' : (o1 === 169 && ((a >>> 16) & 255) === 254) ? 'Link-local (APIPA)' : o1 === 100 && ((a >>> 16) & 255) >= 64 && ((a >>> 16) & 255) <= 127 ? 'Carrier-grade NAT (CGNAT)' : null;
  return { ip, prefix, a, m, net, bc, hosts, first, last, cls, priv, special, wildcard: (~m) >>> 0 };
}
function binary(n, prefix) {
  const bits = n.toString(2).padStart(32, '0');
  let out = '';
  for (let i = 0; i < 32; i++) {
    if (i && i % 8 === 0) out += '<span class="sep">.</span>';
    out += `<span class="${i < prefix ? 'n' : 'h'}">${bits[i]}</span>`;
  }
  return out;
}

export default async function netlab(el) {
  let tab = (location.hash.split('/')[1]) || store.get('netlab-tab', 'devices');
  let job = null;

  el.innerHTML = `
    <div class="pagehead"><div><h1>Network Lab</h1><p>Real networking tools that run from your Umbrel — great for studying. Each tool shows the command it's based on so you can try it in a terminal too.</p></div></div>
    <div class="tabs" role="tablist">${TABS.map(([id, l, ic]) => `<button role="tab" data-tab="${id}" class="${id === tab ? 'on' : ''}">${icon(ic, 16)}${l}</button>`).join('')}</div>
    <div id="nl-body"></div>`;

  const body = $('#nl-body', el);
  let charts = [];

  function show(t) {
    if (job && job.status === 'running') cancelJob(job.id);
    job = null;
    charts.forEach((c) => c.destroy());
    charts = [];
    tab = t;
    body.onclick = null;
    store.set('netlab-tab', t);
    el.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('on', b.dataset.tab === t));
    ({ devices, ping, trace, dns, ports, listening, subnet: subnetTab, quiz, lan })[t]();
    window.__vitalsGlass?.(body);
  }
  el.querySelector('.tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (b) show(b.dataset.tab); });

  const lesson = (html) => `<div class="glass card mt"><div class="cardtitle" style="margin-bottom:8px">${icon('school', 16)}How it works</div><div class="explain">${html}</div></div>`;
  const cmd = (c) => `<span class="cmdline">${icon('chevron-right', 13)}${esc(c)}</span>`;

  async function runTool(url, payload, onTick) {
    try {
      job = await runJob(url, payload, (j) => { job = j; onTick(j); }, 300);
      if (job.status === 'error') toast(job.error, 'error');
      return job;
    } catch (e) { toast(e.message, 'error'); return null; }
  }

  // ---------------------------------------------------------------- devices
  async function devices() {
    body.innerHTML = `<div class="glass card pad-l">
        <div class="row wrap between" style="gap:12px"><div><div style="font-weight:640;font-size:16px">Devices on your network</div><div class="small muted" id="dv-sub">Finds everything connected to your home network.</div></div>
        <button class="btn primary" id="dv-go">${icon('radar-2', 16)}Scan network</button></div>
        <div id="dv-prog"></div><div id="dv-list" class="mt"></div></div>
      ${lesson('Your Umbrel asks every address in your local range "who has this IP?" using <b>ARP</b> (Address Resolution Protocol). Devices answer with their <b>MAC address</b> — a hardware ID whose first half (the <b>OUI</b>) identifies the manufacturer. That\'s how we can guess "Apple" or "Espressif" without logging into anything. ARP only works inside your own network segment (Layer 2), which is why this can\'t see devices on the internet.')}`;
    const render = (r) => {
      if (!r) { setHTML($('#dv-list', body), empty('radar-2', 'No scan yet', 'Press "Scan network" to discover devices. It takes about 10 seconds.')); return; }
      setHTML($('#dv-sub', body), `${r.devices.length} devices on ${esc(r.subnet)} · ${cmd('nmap -sn ' + r.subnet)}`);
      setHTML($('#dv-list', body), `<div class="tablewrap"><table class="t"><thead><tr><th>Device</th><th>IP address</th><th class="hide-sm">MAC address</th><th class="r hide-sm">Latency</th></tr></thead><tbody>
        ${r.devices.map((d) => `<tr><td><div class="row"><span class="devicon">${icon(devIcon(d), 19)}</span><div class="col" style="gap:0"><b style="font-weight:600">${esc(d.name || d.vendor || 'Unknown device')}</b>
          <span class="tiny faint">${esc(d.vendor || '')}${d.router ? ' · <span class="pill sm brand">router</span>' : ''}${d.self ? ' · <span class="pill sm good">this Umbrel</span>' : ''}</span></div></div></td>
          <td class="mono">${esc(d.ip)}</td><td class="mono hide-sm faint">${esc(d.mac || '–')}</td><td class="r hide-sm">${d.latency != null ? `${d.latency} ms` : '–'}</td></tr>`).join('')}</tbody></table></div>`);
    };
    render(await api.get('/api/tools/lanscan/last').catch(() => null));
    $('#dv-go', body).onclick = async (e) => {
      e.target.closest('button').disabled = true;
      const j = await runTool('/api/tools/lanscan', {}, (x) => setHTML($('#dv-prog', body), `<div class="small muted mt">${esc(x.phase || 'Scanning…')}</div>${bar(x.progress || 0.05, 'brand', 'thin')}`));
      setHTML($('#dv-prog', body), '');
      if (j?.status === 'done') render(j.result);
      e.target.closest('button').disabled = false;
    };
  }

  // ---------------------------------------------------------------- ping
  function ping() {
    body.innerHTML = `<div class="grid g-main"><div class="glass card pad-l">
        <div class="formrow"><div class="field"><label>Host or IP</label><input class="input" id="pg-host" value="${esc(store.get('ping-host', '1.1.1.1'))}" placeholder="e.g. 1.1.1.1, google.com, 192.168.1.1"></div>
        <div class="field" style="max-width:110px;min-width:90px"><label>Count</label><select class="input" id="pg-n"><option>5</option><option selected>10</option><option>20</option><option>50</option></select></div>
        <button class="btn primary" id="pg-go" style="height:38px">${icon('player-play', 16)}Ping</button></div>
        <div id="pg-chart" class="mt2"></div><div class="stats mt" id="pg-stats"></div></div>
        <div class="glass card pad-l"><div class="cardhead"><div class="cardtitle">${icon('chevron-right', 16)}Output</div><span id="pg-cmd"></span></div><div class="terminal" id="pg-out"></div></div></div>
      ${lesson('<b>Ping</b> sends ICMP "echo request" packets and times the "echo reply". The time is the <b>round-trip time (RTT)</b>. Under 20 ms is great for the internet; a few ms is normal on your LAN. Varying times are called <b>jitter</b>; missing replies are <b>packet loss</b> — both hurt calls and games more than raw speed does.')}`;
    const ch = new TimeChart($('#pg-chart', body), { height: 160, area: true, minMax: 5, yFmt: (v) => `${v.toFixed(v < 10 ? 1 : 0)} ms`, timeFmt: (t) => `#${t}`, tipTime: (t) => `Reply ${t}`, empty: 'Round-trip times appear here', series: [{ key: 'ms', label: 'RTT', color: 'var(--s1)' }] });
    charts.push(ch);
    $('#pg-go', body).onclick = async () => {
      const host = $('#pg-host', body).value.trim();
      store.set('ping-host', host);
      const out = $('#pg-out', body);
      out.textContent = '';
      const j = await runTool('/api/tools/ping', { host, count: +$('#pg-n', body).value }, (x) => {
        setHTML($('#pg-cmd', body), x.data?.command ? cmd(x.data.command) : '');
        out.textContent = x.allLines.join('\n');
        out.scrollTop = out.scrollHeight;
        ch.set((x.data?.times || []).map((ms, i) => ({ t: i + 1, ms })));
      });
      if (j?.status === 'done') {
        const r = j.result;
        setHTML($('#pg-stats', body), `<div class="stat cell"><div class="k">Average</div><div class="v">${r.avg ?? '–'}<small>ms</small></div></div>
          <div class="stat cell"><div class="k">Min / Max</div><div class="v">${r.min ?? '–'} / ${r.max ?? '–'}<small>ms</small></div></div>
          <div class="stat cell"><div class="k">Packet loss</div><div class="v" style="color:${r.loss ? 'var(--critical-text)' : 'inherit'}">${r.loss}<small>%</small></div></div>`);
      }
    };
  }

  // ---------------------------------------------------------------- traceroute
  function trace() {
    body.innerHTML = `<div class="grid g-main"><div class="glass card pad-l">
        <div class="formrow"><div class="field"><label>Destination</label><input class="input" id="tr-host" value="${esc(store.get('trace-host', 'google.com'))}"></div>
        <div class="field" style="max-width:150px;min-width:120px"><label>Method</label><select class="input" id="tr-mode"><option value="udp">UDP (classic)</option><option value="icmp">ICMP (like Windows)</option><option value="tcp">TCP port 443</option></select></div>
        <button class="btn primary" id="tr-go" style="height:38px">${icon('route', 16)}Trace</button></div>
        <div id="tr-hops" class="hops mt2"></div></div>
        <div class="glass card pad-l"><div class="cardhead"><div class="cardtitle">${icon('chevron-right', 16)}Output</div><span id="tr-cmd"></span></div><div class="terminal" id="tr-out"></div></div></div>
      ${lesson('Every IP packet carries a <b>TTL</b> (time to live). Each router on the way subtracts 1 and, when it hits 0, sends back an ICMP "time exceeded" message. Traceroute sends packets with TTL 1, 2, 3… so each router along the path reveals itself. <b>Blue hops</b> are private addresses (your home network or ISP equipment). <b>* * *</b> means that router doesn\'t answer — common and usually harmless.')}`;
    $('#tr-go', body).onclick = async () => {
      const host = $('#tr-host', body).value.trim();
      store.set('trace-host', host);
      const out = $('#tr-out', body);
      out.textContent = '';
      const render = (hops, done) => setHTML($('#tr-hops', body), (hops || []).map((h) => `<div class="hop"><div class="n ${h.timeout ? 'timeout' : h.private ? 'private' : ''}">${h.hop}</div>
        <div class="grow" style="min-width:0"><div class="mono ellipsis">${h.timeout ? '* * *' : esc(h.ip || '')}</div><div class="tiny faint ellipsis">${h.timeout ? 'No reply' : esc(h.name || (done ? '' : '…'))}${h.private ? ' · private network' : ''}</div></div>
        <div class="num small">${h.avg != null ? `${h.avg} ms` : ''}</div></div>`).join(''));
      const j = await runTool('/api/tools/traceroute', { host, mode: $('#tr-mode', body).value }, (x) => {
        setHTML($('#tr-cmd', body), x.data?.command ? cmd(x.data.command) : '');
        out.textContent = x.allLines.join('\n');
        out.scrollTop = out.scrollHeight;
        render(x.data?.hops);
      });
      if (j?.status === 'done') render(j.result.hops, true);
    };
  }

  // ---------------------------------------------------------------- DNS
  function dns() {
    body.innerHTML = `<div class="glass card pad-l">
        <div class="formrow"><div class="field"><label>Domain (or IP for PTR)</label><input class="input" id="dn-name" value="${esc(store.get('dns-name', 'umbrel.com'))}"></div>
        <div class="field" style="max-width:120px;min-width:100px"><label>Record</label><select class="input" id="dn-type">${['A', 'AAAA', 'MX', 'TXT', 'NS', 'CNAME', 'SOA', 'CAA', 'SRV', 'PTR'].map((t) => `<option>${t}</option>`).join('')}</select></div>
        <div class="field" style="max-width:220px;min-width:170px"><label>Ask</label><select class="input" id="dn-srv"><option value="system">Your network's DNS</option><option value="cloudflare">Cloudflare 1.1.1.1</option><option value="google">Google 8.8.8.8</option><option value="quad9">Quad9 9.9.9.9</option><option value="compare">Compare all four</option></select></div>
        <button class="btn primary" id="dn-go" style="height:38px">${icon('search', 16)}Look up</button></div>
        <div id="dn-out" class="mt2"></div></div>
      ${lesson('<b>DNS</b> turns names into addresses. <b>A</b> = IPv4 address, <b>AAAA</b> = IPv6, <b>MX</b> = mail servers, <b>NS</b> = who is authoritative for the domain, <b>TXT</b> = free text (used for email security like SPF/DKIM), <b>CNAME</b> = an alias. The <b>TTL</b> says how many seconds resolvers may cache the answer. "Compare" shows whether different resolvers agree — handy when a change is still propagating. If your router runs Pi-hole or AdGuard, blocked domains will return 0.0.0.0 there but not on public resolvers.')}`;
    const go = async () => {
      const name = $('#dn-name', body).value.trim();
      store.set('dns-name', name);
      setHTML($('#dn-out', body), '<div class="skeleton" style="height:90px"></div>');
      try {
        const r = await api.post('/api/tools/dns', { name, type: $('#dn-type', body).value, server: $('#dn-srv', body).value });
        setHTML($('#dn-out', body), `<div class="small muted" style="margin-bottom:8px">${cmd(`dig ${r.type} ${r.name}`)}</div>` + r.results.map((x) => `
          <div class="cell" style="padding:12px 14px;margin-bottom:10px"><div class="row between" style="margin-bottom:6px"><b>${esc({ system: 'Your network', cloudflare: 'Cloudflare', google: 'Google', quad9: 'Quad9' }[x.server] || x.server)} <span class="faint mono small">${esc(x.resolver)}</span></b>
            <span class="row" style="gap:6px">${x.dnssec ? `<span class="pill sm good">${icon('shield-check', 12)}DNSSEC</span>` : ''}${x.ms != null ? `<span class="pill sm">${x.ms} ms</span>` : ''}</span></div>
            ${x.error ? `<div class="small" style="color:var(--critical-text)">${esc(x.error)}</div>` : x.records.length ? x.records.map((rec) => `<div class="row between mono small" style="padding:3px 0"><span style="overflow-wrap:anywhere">${esc(rec.value)}</span><span class="faint nowrap">TTL ${rec.ttl}</span></div>`).join('') : '<div class="small muted">No records of this type.</div>'}
            ${x.cname ? `<div class="tiny faint">via CNAME ${esc(x.cname)}</div>` : ''}</div>`).join(''));
      } catch (e) { setHTML($('#dn-out', body), `<div class="banner critical">${icon('alert-circle', 18)}${esc(e.message)}</div>`); }
    };
    $('#dn-go', body).onclick = go;
    $('#dn-name', body).onkeydown = (e) => { if (e.key === 'Enter') go(); };
  }

  // ---------------------------------------------------------------- ports
  function ports() {
    body.innerHTML = `<div class="grid g-main"><div class="glass card pad-l">
        <div class="formrow"><div class="field"><label>Device (IP or name)</label><input class="input" id="pt-host" value="${esc(store.get('port-host', ''))}" placeholder="e.g. 192.168.1.1"></div>
        <div class="field" style="max-width:220px;min-width:160px"><label>Ports</label><select class="input" id="pt-preset"><option value="common">Common services (60)</option><option value="top1000">1–1024 (well-known)</option><option value="custom">Custom…</option></select></div>
        <div class="field hide" id="pt-customf" style="max-width:200px"><label>Custom ports</label><input class="input" id="pt-custom" placeholder="22,80,8000-8100"></div>
        <button class="btn primary" id="pt-go" style="height:38px">${icon('lock-open', 16)}Check</button></div>
        <div id="pt-res" class="mt2"></div></div>
        <div class="glass card pad-l"><div class="cardhead"><div class="cardtitle">${icon('chevron-right', 16)}Output</div><span id="pt-cmd"></span></div><div class="terminal" id="pt-out"></div></div></div>
      ${lesson('A <b>port</b> is a numbered door on a device; services listen behind specific ones (22 = SSH, 80/443 = web, 445 = file sharing). This does a <b>TCP connect scan</b>: it tries the full 3-way handshake (SYN → SYN-ACK → ACK). If it completes, the port is <b>open</b>. Only scan devices you own — scanning other people\'s networks can be against the rules of your ISP.')}`;
    $('#pt-preset', body).onchange = (e) => $('#pt-customf', body).classList.toggle('hide', e.target.value !== 'custom');
    $('#pt-go', body).onclick = async () => {
      const host = $('#pt-host', body).value.trim();
      if (!host) { toast('Enter a device address', 'error'); return; }
      store.set('port-host', host);
      const preset = $('#pt-preset', body).value;
      const portsSpec = preset === 'custom' ? $('#pt-custom', body).value : preset;
      const out = $('#pt-out', body);
      setHTML($('#pt-res', body), `<div class="small muted">Scanning…</div>${bar(0.1, 'brand', 'thin')}`);
      const j = await runTool('/api/tools/portscan', { host, ports: portsSpec }, (x) => {
        setHTML($('#pt-cmd', body), x.data?.command ? cmd(x.data.command) : '');
        out.textContent = x.allLines.join('\n');
      });
      if (j?.status === 'done') {
        const r = j.result;
        setHTML($('#pt-res', body), r.open.length ? `<div style="font-weight:620;margin-bottom:8px">${r.open.length} open of ${r.scanned} checked on ${esc(r.host)}</div>
          <div class="list">${r.open.map((p) => `<div class="li"><span class="pill good">${p.port}/tcp</span><div class="grow">${esc(p.service || 'Unknown service')}</div>${p.port === 23 || p.port === 2375 ? `<span class="pill sm warning">${icon('alert-triangle', 12)}insecure</span>` : ''}</div>`).join('')}</div>`
          : empty('lock', 'No open ports found', `None of the ${r.scanned} ports answered. The device may have a firewall, or it's offline.`));
      } else setHTML($('#pt-res', body), '');
    };
  }

  // ---------------------------------------------------------------- listening
  async function listening() {
    body.innerHTML = `<div class="glass card pad-l"><div class="cardhead"><div class="cardtitle">${icon('server', 17)}Services listening on this Umbrel</div><span>${cmd('ss -tulnp')}</span></div><div id="ls-body"><div class="skeleton" style="height:240px"></div></div></div>
      ${lesson('These are the ports your Umbrel accepts connections on. <b>Network</b> means other devices can reach it; <b>local only</b> means only programs on the Umbrel itself can. umbrelOS serves every app on its own port — that\'s why you open apps at addresses like <span class="mono">umbrel.local:2100</span>. <b>TCP</b> is connection-based (web, SSH); <b>UDP</b> is fire-and-forget (DNS, VPNs, discovery).')}`;
    try {
      const rows = await api.get('/api/tools/listening');
      setHTML($('#ls-body', body), `<div class="tablewrap"><table class="t"><thead><tr><th>Port</th><th>Protocol</th><th>Used by</th><th class="hide-sm">Process</th><th>Reachable from</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td class="mono"><b>${r.port}</b></td><td>${r.proto.toUpperCase()}</td><td>${esc(r.app || r.service || '–')}${r.app && r.service ? ` <span class="faint small">· ${esc(r.service)}</span>` : ''}</td>
        <td class="hide-sm mono small faint">${esc(r.process || '')}</td><td><span class="pill sm ${r.scope === 'network' ? 'brand' : ''}">${r.scope === 'network' ? 'network' : 'local only'}</span></td></tr>`).join('')}</tbody></table></div>`);
    } catch (e) { setHTML($('#ls-body', body), `<div class="hint">${icon('info-circle', 15)}${esc(e.message)}</div>`); }
  }

  // ---------------------------------------------------------------- subnet calculator
  function subnetTab() {
    body.innerHTML = `<div class="glass card pad-l">
        <div class="formrow"><div class="field"><label>IP address with prefix or mask</label><input class="input mono" id="sn-in" value="${esc(store.get('subnet-in', '192.168.1.50/24'))}" placeholder="10.0.12.7/20 or 172.16.5.4 255.255.240.0"></div>
        <div class="field" style="max-width:190px;min-width:150px"><label>Split into subnets</label><select class="input" id="sn-split"><option value="1">Don't split</option>${[2, 4, 8, 16, 32, 64].map((n) => `<option>${n}</option>`).join('')}</select></div></div>
        <div id="sn-out" class="mt2"></div></div>
      ${lesson('A <b>/24</b> prefix means the first 24 bits are the <b>network</b> part (blue) and the remaining 8 are for <b>hosts</b> (orange). The network address has all host bits 0, the broadcast address has them all 1, and everything in between is usable: 2<sup>host bits</sup> − 2. Splitting borrows host bits: every extra bit doubles the number of subnets and halves their size.')}`;
    const calc = () => {
      const v = $('#sn-in', body).value;
      store.set('subnet-in', v);
      const p = parseCidr(v);
      if (!p) { setHTML($('#sn-out', body), `<div class="hint">${icon('info-circle', 15)}Type something like <span class="mono">192.168.1.50/24</span> or <span class="mono">10.0.0.1 255.255.0.0</span>.</div>`); return; }
      const s = subnet(p.ip, p.prefix);
      const split = +$('#sn-split', body).value;
      const kv = (k, v2) => `<dt>${k}</dt><dd class="mono">${v2}</dd>`;
      let subs = '';
      if (split > 1) {
        const np = p.prefix + Math.log2(split);
        if (np > 30) subs = `<div class="hint mt">${icon('alert-triangle', 15)}Too small to split ${split} ways.</div>`;
        else {
          const size = 2 ** (32 - np);
          subs = `<div class="section-title" style="margin:22px 0 10px;font-size:15px">${split} subnets of /${np} (${(size - 2).toLocaleString()} hosts each)</div><div class="tablewrap"><table class="t"><thead><tr><th>#</th><th>Network</th><th>Usable range</th><th class="hide-sm">Broadcast</th></tr></thead><tbody>
            ${Array.from({ length: split }, (_, i) => { const n = s.net + i * size; return `<tr><td class="faint">${i + 1}</td><td class="mono">${n2ip(n)}/${np}</td><td class="mono">${n2ip(n + 1)} – ${n2ip(n + size - 2)}</td><td class="mono hide-sm">${n2ip(n + size - 1)}</td></tr>`; }).join('')}</tbody></table></div>`;
        }
      }
      setHTML($('#sn-out', body), `<div class="grid g2"><dl class="kv">
          ${kv('Network address', `${n2ip(s.net)}/${s.prefix}`)}${kv('Subnet mask', n2ip(s.m))}${kv('Wildcard mask', n2ip(s.wildcard))}${kv('Broadcast', n2ip(s.bc))}</dl>
        <dl class="kv">${kv('First usable', n2ip(s.first))}${kv('Last usable', n2ip(s.last))}${kv('Usable hosts', s.hosts.toLocaleString())}${kv('Type', `${s.special || (s.priv ? 'Private (RFC 1918)' : 'Public')} · class ${s.cls}`)}</dl></div>
        <div class="cell mt" style="padding:14px 16px"><div class="tiny faint" style="margin-bottom:6px">Binary — <span style="color:var(--s1)">network bits</span> · <span style="color:var(--s2)">host bits</span></div>
          <div class="binary">IP&nbsp;&nbsp;&nbsp;${binary(s.a, s.prefix)}</div><div class="binary">Mask&nbsp;${binary(s.m, s.prefix)}</div><div class="binary">Net&nbsp;&nbsp;${binary(s.net, s.prefix)}</div></div>${subs}`);
    };
    $('#sn-in', body).oninput = calc;
    $('#sn-split', body).onchange = calc;
    calc();
  }

  // ---------------------------------------------------------------- quiz
  function quiz() {
    const st = store.get('quiz', { right: 0, total: 0, streak: 0, best: 0 });
    let q = null;
    const rnd = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
    const randIp = () => [[10, 172, 100, 8, 23, 51, 81, 145, 203][rnd(0, 8)], rnd(0, 255), rnd(0, 255), rnd(1, 254)].join('.');
    const shuffle = (a) => a.sort(() => Math.random() - 0.5);
    function make() {
      const kind = rnd(0, 5);
      const p = rnd(16, 30);
      const ip = Math.random() < 0.5 ? `192.168.${rnd(0, 255)}.${rnd(1, 254)}` : randIp();
      const s = subnet(ip, p);
      const near = (n, step) => [n, (n + step) >>> 0, (n - step) >>> 0, (n + step * 2) >>> 0];
      const blk = 2 ** (32 - p);
      switch (kind) {
        case 0: return { text: `What is the <b>network address</b> of <span class="mono">${ip}/${p}</span>?`, answer: n2ip(s.net), options: near(s.net, blk).map(n2ip), why: `AND the IP with the mask ${n2ip(s.m)}: host bits become 0 → ${n2ip(s.net)}. Block size is ${blk}.` };
        case 1: return { text: `What is the <b>broadcast address</b> of <span class="mono">${ip}/${p}</span>?`, answer: n2ip(s.bc), options: [s.bc, s.bc - 1, s.bc + blk, s.net].map((n) => n2ip(n >>> 0)), why: `Set all host bits to 1: network ${n2ip(s.net)} + block ${blk} − 1 = ${n2ip(s.bc)}.` };
        case 2: return { text: `How many <b>usable hosts</b> fit in a <span class="mono">/${p}</span>?`, answer: s.hosts.toLocaleString(), options: [s.hosts, s.hosts + 2, s.hosts * 2 + 2, Math.max(1, s.hosts / 2 - 1)].map((n) => n.toLocaleString()), why: `${32 - p} host bits → 2<sup>${32 - p}</sup> = ${blk} addresses, minus network and broadcast = ${s.hosts.toLocaleString()}.` };
        case 3: return { text: `Which <b>subnet mask</b> is <span class="mono">/${p}</span>?`, answer: n2ip(s.m), options: [p, p - 1, p + 1, p - 8 > 0 ? p - 8 : p + 2].map((x) => n2ip(maskN(Math.min(32, x)))), why: `${p} ones followed by ${32 - p} zeros = ${n2ip(s.m)}.` };
        case 4: return { text: `<span class="mono">${n2ip(s.m)}</span> is which <b>prefix length</b>?`, answer: `/${p}`, options: [p, p - 1, p + 1, p + 2].map((x) => `/${x}`), why: `Count the 1-bits in the mask: ${p}.` };
        default: {
          const same = Math.random() < 0.5;
          const other = same ? n2ip(s.first + rnd(0, Math.max(0, s.hosts - 1))) : n2ip((s.net + blk + rnd(1, Math.max(1, blk - 2))) >>> 0);
          return { text: `Are <span class="mono">${ip}</span> and <span class="mono">${other}</span> in the <b>same /${p} subnet</b>?`, answer: same ? 'Yes' : 'No', options: ['Yes', 'No'], why: `${ip}/${p} covers ${n2ip(s.net)} – ${n2ip(s.bc)}. ${other} is ${same ? 'inside' : 'outside'} that range.` };
        }
      }
    }
    function next() {
      q = make();
      q.options = shuffle([...new Set(q.options)]);
      if (!q.options.includes(q.answer)) q.options[0] = q.answer;
      setHTML($('#qz', body), `<div style="font-size:18px;font-weight:600;letter-spacing:-0.02em;line-height:1.4">${q.text}</div>
        <div id="qz-opts" class="mt">${q.options.map((o) => `<button class="quiz-opt" data-o="${esc(o)}">${esc(o)}</button>`).join('')}</div><div id="qz-why" class="mt"></div>`);
    }
    function score() {
      setHTML($('#qz-score', body), `<div class="stat cell"><div class="k">Score</div><div class="v">${st.right}<small>/ ${st.total}</small></div></div>
        <div class="stat cell"><div class="k">Streak</div><div class="v">${st.streak}${st.streak >= 5 ? ' 🔥' : ''}</div></div>
        <div class="stat cell"><div class="k">Best streak</div><div class="v">${st.best}</div></div>`);
    }
    body.innerHTML = `<div class="grid g-main"><div class="glass card pad-l"><div id="qz"></div></div>
      <div class="glass card pad-l"><div class="cardtitle" style="margin-bottom:10px">${icon('school', 16)}Your progress</div><div class="stats" id="qz-score"></div>
      <button class="btn sm ghost mt" id="qz-reset">Reset score</button>
      <p class="explain mt">Subnetting questions like these show up in CCNA and Network+ exams. Use the calculator tab to check your working — it shows the binary.</p></div></div>`;
    body.onclick = (e) => {
      if (e.target.closest('#qz-reset')) { Object.assign(st, { right: 0, total: 0, streak: 0, best: 0 }); store.set('quiz', st); score(); return; }
      if (e.target.closest('#qz-next')) { next(); return; }
      const b = e.target.closest('[data-o]');
      if (!b || $('#qz-opts', body).dataset.done) return;
      $('#qz-opts', body).dataset.done = '1';
      const ok = b.dataset.o === q.answer;
      st.total++; if (ok) { st.right++; st.streak++; st.best = Math.max(st.best, st.streak); } else st.streak = 0;
      store.set('quiz', st);
      body.querySelectorAll('[data-o]').forEach((x) => { if (x.dataset.o === q.answer) x.classList.add('right'); else if (x === b) x.classList.add('wrong'); });
      setHTML($('#qz-why', body), `<div class="banner ${ok ? 'info' : 'warning'}">${icon(ok ? 'circle-check' : 'bulb', 18)}<div class="grow"><b>${ok ? 'Correct!' : `Answer: ${esc(q.answer)}`}</b> ${q.why}</div><button class="btn sm primary" id="qz-next">Next ${icon('chevron-right', 14)}</button></div>`);
      score();
    };
    score();
    next();
  }

  // ---------------------------------------------------------------- LAN speed
  function lan() {
    body.innerHTML = `<div class="glass card pad-l">
        <div class="row wrap between" style="gap:12px"><div><div style="font-weight:640;font-size:16px">Speed between this device and your Umbrel</div>
        <div class="small muted">Measures your home network — Wi-Fi or cable — not your internet. Great for checking how fast you can move projects and samples onto the server.</div></div>
        <button class="btn primary" id="ln-go">${icon('transfer', 16)}Start</button></div>
        <div class="stats mt2" id="ln-res"><div class="stat cell"><div class="k">${icon('cloud-download', 14)}Umbrel → you</div><div class="v">–</div></div><div class="stat cell"><div class="k">${icon('cloud-upload', 14)}You → Umbrel</div><div class="v">–</div></div></div>
        <div id="ln-note" class="mt"></div></div>
      ${lesson('Wi-Fi speeds on the box (like "AX3000") are shared, half-duplex air time — real transfers are usually 30–60% of that. <b>Gigabit Ethernet</b> tops out around 940 Mb/s (≈ 110 MB/s) after protocol overhead. If you\'re far below that on a cable, check for a 100 Mb/s link (a damaged cable or old switch).')}`;
    $('#ln-go', body).onclick = async (e) => {
      const btn = e.target.closest('button');
      btn.disabled = true;
      const res = $('#ln-res', body);
      const cells = res.querySelectorAll('.v');
      const fmt = (bps) => bps >= 1e9 ? `${(bps / 1e9).toFixed(2)}<small>Gb/s</small>` : `${(bps / 1e6).toFixed(0)}<small>Mb/s</small>`;
      try {
        // download: stream for ~8 s
        let bytes = 0;
        const t0 = performance.now();
        const ctrl = new AbortController();
        const r = await fetch('/api/lantest/download?mb=2000', { signal: ctrl.signal, cache: 'no-store' });
        const reader = r.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          bytes += value.length;
          const dt = (performance.now() - t0) / 1000;
          if (dt > 0.3) setHTML(cells[0], fmt((bytes * 8) / dt));
          if (dt > 8) { ctrl.abort(); break; }
        }
        const down = (bytes * 8) / ((performance.now() - t0) / 1000);
        setHTML(cells[0], fmt(down));
        // upload: send growing chunks for ~8 s
        const chunk = new Uint8Array(8 * 1024 * 1024);
        for (let i = 0; i < chunk.length; i += 65536) crypto.getRandomValues(chunk.subarray(i, i + 65536));
        let sent = 0;
        const u0 = performance.now();
        while ((performance.now() - u0) / 1000 < 8) {
          await new Promise((resolve, reject) => {
            const x = new XMLHttpRequest();
            x.open('POST', '/api/lantest/upload');
            const base = sent;
            x.upload.onprogress = (ev) => setHTML(cells[1], fmt(((base + ev.loaded) * 8) / ((performance.now() - u0) / 1000)));
            x.onload = resolve;
            x.onerror = reject;
            x.send(new Blob([chunk, chunk, chunk, chunk]));
          });
          sent += chunk.length * 4;
        }
        const up = (sent * 8) / ((performance.now() - u0) / 1000);
        setHTML(cells[1], fmt(up));
        const mins = (10e9 * 8) / up / 60;
        setHTML($('#ln-note', body), `<p class="explain">Copying a <b>10 GB</b> sample library to your Umbrel would take about <b>${mins < 1 ? `${Math.round(mins * 60)} seconds` : `${mins.toFixed(1)} minutes`}</b> over this connection.
          ${up < 200e6 ? ' That\'s Wi-Fi territory — plug in a cable for big transfers.' : up > 700e6 ? ' That\'s close to the gigabit maximum.' : ''}</p>`);
      } catch (err) { toast(`LAN test failed: ${err.message}`, 'error'); }
      btn.disabled = false;
    };
  }

  show(tab in Object.fromEntries(TABS.map((t) => [t[0], 1])) ? tab : 'devices');
  return { destroy() { if (job && job.status === 'running') cancelJob(job.id); charts.forEach((c) => c.destroy()); } };
}

function devIcon(d) {
  const v = `${d.vendor || ''} ${d.name || ''}`.toLowerCase();
  if (d.self) return 'server';
  if (d.router || /router|fritz|ubiquiti|tp-link|netgear|asus|mikrotik|eero|linksys|gateway/.test(v)) return 'router';
  if (/iphone|android|pixel|galaxy|phone/.test(v)) return 'device-mobile';
  if (/macbook|laptop|thinkpad/.test(v)) return 'device-laptop';
  if (/apple/.test(v)) return 'brand-apple';
  if (/tv|roku|chromecast|sonos|lg elec|samsung/.test(v)) return 'device-tv';
  if (/print|hp|epson|brother|canon/.test(v)) return 'printer';
  if (/focusrite|audio|universal audio|rme|motu|presonus|dante/.test(v)) return 'headphones';
  if (/espressif|tuya|shelly|sonoff|iot|raspberry/.test(v)) return 'cpu-2';
  return 'device-desktop';
}
