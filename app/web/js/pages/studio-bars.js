// Bars ↔ time: where in the song (in minutes and seconds) a bar falls at a given BPM, and back.
import { esc, icon, info, store } from '../ui.js';

const SIGS = [['4/4', 4, 4], ['3/4', 3, 4], ['2/4', 2, 4], ['5/4', 5, 4], ['6/8', 6, 8], ['7/8', 7, 8], ['12/8', 12, 8]];

/** Seconds per beat (the time signature's lower number) when BPM counts quarter notes, like every DAW. */
export function beatSeconds(bpm, den) {
  return (60 / bpm) * (4 / den);
}

export function barToSeconds(bar, beat, bpm, num, den, offset = 0) {
  return ((bar - 1) * num + (beat - 1)) * beatSeconds(bpm, den) + offset;
}

export function secondsToBar(t, bpm, num, den, offset = 0) {
  const beats = (t - offset) / beatSeconds(bpm, den);
  if (beats < 0) return null;
  const whole = Math.floor(beats + 1e-9);
  return { bar: Math.floor(whole / num) + 1, beat: (whole % num) + 1, into: beats - whole };
}

/** "1:23.5", "83.5", "1:02:03" -> seconds */
export function parseTime(s) {
  const parts = String(s || '').trim().replace(',', '.').split(':');
  if (!parts[0] || parts.length > 3 || parts.some((p) => p === '' || isNaN(+p))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

export function fmtTime(sec) {
  const neg = sec < 0;
  sec = Math.abs(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const ss = s.toFixed(3).padStart(6, '0');
  return `${neg ? '-' : ''}${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${ss}`;
}

export function mount(root) {
  const saved = store.get('bars', { sig: '4/4', offset: 0, every: 8, bars: 64, bar: 33, beat: 1, time: '1:00' });
  root.innerHTML = `<div class="glass card pad-l">
    <div class="cardhead"><div class="cardtitle">${icon('clock', 17)}Bars ↔ time ${info('Type the song\'s BPM and a bar number to see exactly where it lands, or type a time to see which bar that is. BPM counts quarter notes, the same way your DAW does. If the song has a pickup or silence before bar 1, put that in <b>Bar 1 starts at</b>.')}</div></div>
    <div class="formrow">
      <div class="field" style="max-width:120px"><label>BPM</label><input class="input" id="bt-bpm" type="number" min="20" max="400" step="0.01" value="${store.get('bpm', 120)}" style="font-size:20px;font-weight:650;height:46px"></div>
      <div class="field" style="max-width:110px"><label>Time signature</label><select class="input" id="bt-sig" style="height:46px">${SIGS.map(([n]) => `<option ${n === saved.sig ? 'selected' : ''}>${n}</option>`).join('')}</select></div>
      <div class="field" style="max-width:150px"><label>Bar 1 starts at</label><input class="input" id="bt-off" value="${esc(String(saved.offset || 0))}" placeholder="0:00.000" style="height:46px"></div>
    </div>
    <div class="grid g2 mt" style="gap:12px">
      <div class="cell" style="padding:14px 16px">
        <div class="small muted" style="margin-bottom:8px">Bar → time</div>
        <div class="row wrap" style="gap:8px;align-items:center"><span>Bar</span><input class="input" id="bt-bar" type="number" min="1" value="${saved.bar}" style="width:90px">
          <span>beat</span><input class="input" id="bt-beat" type="number" min="1" value="${saved.beat}" style="width:70px"></div>
        <div class="huge" style="font-size:38px;margin-top:10px" id="bt-time-out">–</div><div class="small muted" id="bt-time-sub"></div>
      </div>
      <div class="cell" style="padding:14px 16px">
        <div class="small muted" style="margin-bottom:8px">Time → bar</div>
        <div class="row wrap" style="gap:8px;align-items:center"><span>At</span><input class="input" id="bt-time" value="${esc(saved.time)}" placeholder="1:23.5" style="width:120px"><span class="small faint">min:sec</span></div>
        <div class="huge" style="font-size:38px;margin-top:10px" id="bt-bar-out">–</div><div class="small muted" id="bt-bar-sub"></div>
      </div>
    </div>
    <div class="row wrap mt between" style="gap:10px;margin-top:18px">
      <div class="small muted">Section markers</div>
      <div class="row" style="gap:8px">
        <span class="small muted">every</span><select class="input" id="bt-every" style="height:32px;width:auto">${[4, 8, 16, 32].map((n) => `<option value="${n}" ${n === saved.every ? 'selected' : ''}>${n} bars</option>`).join('')}</select>
        <span class="small muted">song length</span><input class="input" id="bt-len" type="number" min="1" max="2000" value="${saved.bars}" style="width:80px;height:32px"><span class="small muted">bars</span>
      </div>
    </div>
    <div class="tablewrap mt"><table class="t"><thead><tr><th>Bar</th><th class="r">Starts at</th><th class="r">Seconds</th><th class="r hide-sm">Length so far</th></tr></thead><tbody id="bt-rows"></tbody></table></div>
  </div>`;

  const $ = (id) => root.querySelector(id);
  function calc() {
    const bpm = Math.min(400, Math.max(20, parseFloat($('#bt-bpm').value) || 120));
    const [, num, den] = SIGS.find(([n]) => n === $('#bt-sig').value) || SIGS[0];
    const offset = parseTime($('#bt-off').value) ?? 0;
    const bar = Math.max(1, parseInt($('#bt-bar').value, 10) || 1);
    const beat = Math.min(num, Math.max(1, parseInt($('#bt-beat').value, 10) || 1));
    const every = +$('#bt-every').value;
    const len = Math.min(2000, Math.max(1, parseInt($('#bt-len').value, 10) || 64));
    store.set('bpm', bpm);
    store.set('bars', { sig: $('#bt-sig').value, offset: $('#bt-off').value, every, bars: len, bar, beat, time: $('#bt-time').value });

    const barLen = beatSeconds(bpm, den) * num;
    const t = barToSeconds(bar, beat, bpm, num, den, offset);
    $('#bt-time-out').textContent = fmtTime(t);
    $('#bt-time-sub').textContent = `${t.toFixed(3)} s · one bar lasts ${barLen.toFixed(3)} s`;

    const at = parseTime($('#bt-time').value);
    const pos = at == null ? null : secondsToBar(at, bpm, num, den, offset);
    $('#bt-bar-out').textContent = pos ? `Bar ${pos.bar}` : '–';
    $('#bt-bar-sub').textContent = at == null ? 'Type a time like 1:23.5'
      : !pos ? 'That\'s before bar 1'
        : `beat ${pos.beat} of ${num}${pos.into > 0.01 ? `, ${Math.round(pos.into * 100)}% into it` : ''}`;

    const rows = [];
    for (let b = 1; b <= len; b += every) {
      const s = barToSeconds(b, 1, bpm, num, den, offset);
      rows.push(`<tr${b === bar ? ' style="font-weight:650"' : ''}><td>${b}</td><td class="r num">${fmtTime(s)}</td><td class="r num faint">${s.toFixed(2)}</td><td class="r hide-sm faint">${b - 1} bars</td></tr>`);
    }
    const end = barToSeconds(len + 1, 1, bpm, num, den, offset);
    rows.push(`<tr><td><b>End</b></td><td class="r num"><b>${fmtTime(end)}</b></td><td class="r num faint">${end.toFixed(2)}</td><td class="r hide-sm faint">${len} bars</td></tr>`);
    $('#bt-rows').innerHTML = rows.join('');
  }
  root.addEventListener('input', calc);
  root.addEventListener('change', calc);
  calc();
}
