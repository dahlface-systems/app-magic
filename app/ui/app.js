// App Magic's window. Plain JavaScript on purpose: this file is part of the layer the workshop is allowed to change, and
// it reloads without rebuilding the app. It talks to the local service (magicd) and, when running inside the Mac app,
// to the native shell through `bridge`.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.magic;
const bridge = (cmd, data = {}) => { if (native) native.postMessage({ cmd, ...data }); return !!native; };

const S = { view: 'home', designOpen: false, state: null, busy: {}, streaming: {}, designUrl: '', designApp: '', boot: null, askOpen: false, signingIn: false, picks: [] };

// Every call to the service goes through here. It never throws: a failure comes back as {error}, and anything that
// looks like a bug is reported, so it shows up in the problems banner with "Fix it with Claude".
async function api(path, body) {
  let r;
  try {
    r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) {
    return { ok: false, error: "App Magic's service isn't answering. It restarts by itself; try again in a moment.", offline: true };
  }
  let data;
  try { data = await r.json(); } catch (e) {
    report('window', `The service sent something unreadable for ${path} (HTTP ${r.status})`, String(e && e.stack || e));
    return { ok: false, error: 'Something inside App Magic didn\'t work.' };
  }
  if (r.status >= 500 && !data.problem) report('window', `${path} failed with HTTP ${r.status}`, JSON.stringify(data).slice(0, 2000));
  return data;
}
const cap = (s) => String(s || '').replace(/^./, (c) => c.toUpperCase());
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// A small, safe subset of markdown: paragraphs, lists, bold, code, links. Everything is escaped first.
function rich(text) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out = []; let list = null, para = [], code = null;
  const flush = () => { if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; } if (list) { out.push(`<${list.tag}>` + list.items.map((i) => '<li>' + inline(i) + '</li>').join('') + `</${list.tag}>`); list = null; } };
  for (const line of String(text || '').split('\n')) {
    if (line.trim().startsWith('```')) { if (code === null) { flush(); code = []; } else { out.push('<pre>' + esc(code.join('\n')) + '</pre>'); code = null; } continue; }
    if (code !== null) { code.push(line); continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/), h = line.match(/^#{1,4}\s+(.*)/);
    if (ul || ol) { const tag = ul ? 'ul' : 'ol'; if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; } if (!list || list.tag !== tag) { flush(); list = { tag, items: [] }; } list.items.push((ul || ol)[1]); }
    else if (h) { flush(); out.push('<p><strong>' + inline(h[1]) + '</strong></p>'); }
    else if (!line.trim()) flush();
    else { if (list) flush(); para.push(line); }
  }
  if (code !== null) out.push('<pre>' + esc(code.join('\n')) + '</pre>');
  flush();
  return out.join('');
}

// ---- problems: report, show, and offer to fix ----------------------------------------------------------
const reported = new Set();
function report(where, what, detail = '', context = {}) {
  const key = where + '|' + what;
  if (reported.has(key)) return;
  reported.add(key); setTimeout(() => reported.delete(key), 60000);
  fetch('/api/problem', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ where, what, detail, context: { view: S.view, recent_events: (S.events || []).slice(-12), ...context } }) }).catch(() => {});
}
window.addEventListener('error', (e) => report('window', e.message || 'A script error', (e.error && e.error.stack) || `${e.filename}:${e.lineno}:${e.colno}`));
window.addEventListener('unhandledrejection', (e) => report('window', 'An unfinished action failed: ' + String(e.reason && e.reason.message || e.reason), String(e.reason && e.reason.stack || '')));
function showProblems(list) {
  S.problems = list || [];
  const p = S.problems.find((x) => !x.dismissed && !x.fixing);          // once Claude is on it, only the footer link stays
  const b = $('#problem');
  $('#problems-link').hidden = !S.problems.length;
  $('#problems-link').textContent = S.problems.length === 1 ? '1 problem' : `${S.problems.length} problems`;
  if (!p) { b.hidden = true; return; }
  b.hidden = false; b.dataset.id = p.id;
  $('#problem-say').textContent = p.say + (p.count > 1 ? ` (${p.count} times)` : '');
  $('#problem-what').textContent = p.what;
  $('#problem-fix').textContent = p.fixing ? 'Claude is on it' : 'Fix it with Claude';
  $('#problem-fix').disabled = !!p.fixing;
}
async function refreshProblems() { const r = await api('/api/problems'); if (r.problems) showProblems(r.problems); }
async function fixProblem(id) {
  const r = await api('/api/problems/fix', { id });
  toast(r.ok ? (r.dry ? 'Workshop (test mode): nothing was opened.' : 'The workshop opened in its own window. Claude is looking for the cause; App Magic may close and reopen when it is fixed.') : (r.error || 'The workshop did not open.'));
  refreshProblems();
}
document.addEventListener('click', async (e) => {
  const f = e.target.closest('[data-fix]'); if (f) { e.preventDefault(); $('#problems-modal').hidden = true; fixProblem(f.dataset.fix); return; }
  const d = e.target.closest('[data-dismiss]'); if (d) { await api('/api/problems/dismiss', { id: d.dataset.dismiss }); refreshProblems(); listProblems(); return; }
  if (e.target.id === 'problem-fix') fixProblem($('#problem').dataset.id);
  if (e.target.id === 'problem-dismiss') { await api('/api/problems/dismiss', { id: $('#problem').dataset.id }); refreshProblems(); }
  if (e.target.id === 'problems-link') listProblems(true);
  if (e.target.id === 'problems-clear') { await api('/api/problems/dismiss', { all: true }); refreshProblems(); $('#problems-modal').hidden = true; }
});
function listProblems(open) {
  if (!open && $('#problems-modal').hidden) return;
  const list = S.problems || [];
  $('#problems-list').innerHTML = list.length ? list.map((p) => `<div class="check fail"><span class="mk">!</span><div><b>${esc(p.say)}</b><p>${esc(p.what)}</p>
    <p>${esc(p.when.replace('T', ' at '))}${p.count > 1 ? ` · ${p.count} times` : ''}${p.fixing ? ' · Claude was asked to fix it' : ''}</p>
    <div class="actions"><button class="mini" data-fix="${esc(p.id)}">Fix it with Claude</button><button class="link" data-dismiss="${esc(p.id)}">Dismiss</button></div></div></div>`).join('')
    : '<p class="note">Nothing has gone wrong lately.</p>';
  $('#problems-modal').hidden = false;
}

function toast(text) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = text;
  $('#toasts').append(t); setTimeout(() => t.remove(), 7000);
}

// ---- views -------------------------------------------------------------------------------------------
function show(view) {
  if (view === 'design') return openDesign();                           // designs are a pop-up over the chat, not a page
  if (S.designOpen) closeDesign();
  S.view = view;
  $$('.view').forEach((v) => v.classList.toggle('on', v.id === 'view-' + view));
  $$('.topbar nav button').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
  if (view === 'apps') { renderApps(); if (!S.scan) api('/api/apps/scan').then((r) => { if (r.search) { $('#bring').hidden = false; renderScan(r.search); } }); }
  if (view === 'capabilities') refreshCaps();
  if (view === 'accounts') refresh();
  if (view === 'home') { scrollThread(); $('#say').focus(); }
}
document.addEventListener('click', (e) => {
  const v = e.target.closest('[data-view]'); if (v) show(v.dataset.view);
  const c = e.target.closest('[data-close]'); if (c) hideOverlay(c.closest('.overlay'));
  if (e.target.classList.contains('overlay')) { hideOverlay(e.target); S.askOpen = false; }
});

// ---- state -------------------------------------------------------------------------------------------
async function refresh() {
  const s = await api('/api/state');
  if (!s || s.error || !s.accounts) return;                           // the service is restarting: keep what is on screen
  S.state = s;
  if (S.boot && S.boot !== s.boot) return location.reload();          // the service restarted with new code
  S.boot = s.boot;
  $('#status-line').textContent = s.accounts.line;
  $('#status-pill').classList.toggle('needs', !!s.accounts.needs_you);
  const k = s.context.kinds || {}, rows = [];
  const add = (label, value, action) => rows.push(`<div class="row"><b>${esc(label)}</b>${action ? `<button class="link" style="padding:0;min-height:0" data-say="${esc(action)}">${esc(value)}</button>` : `<span>${esc(value)}</span>`}</div>`);
  add('Recordings', k.recording ? `${k.recording} watched` : 'None yet');
  add('Photos, videos', (k.photo || 0) + (k.video || 0) ? `${(k.photo || 0) + (k.video || 0)}` : 'None yet');
  add('Files and notes', (k.file || 0) + (k.note || 0) ? `${(k.file || 0) + (k.note || 0)}` : 'None yet');
  add('Apps brought in', s.context.brought_in ? `${s.context.brought_in}` : 'None yet');
  add('Things I know about you', s.context.about_you ? `${s.context.about_you}` : 'Nothing yet');
  $('#know').innerHTML = rows.join('');
  $('#know-hint').textContent = s.context.count + s.context.brought_in === 0 ? 'Drop in a file, or bring in an app you already have. The more I see, the better it fits you.' : 'Kept on your Mac. Sent to Claude only to do a job you asked for.';
  const carry = (s.apps || []).slice().sort((a, b) => String(b.updated).localeCompare(String(a.updated))).slice(0, 3);
  $('#carry').innerHTML = carry.length ? carry.map((a) => `<button data-open-app="${esc(a.name)}"><span class="thumb"></span><span><b>${esc(a.name)}</b><small>${esc(a.status || a.lives || '')}</small></span></button>`).join('') : '<p class="hint">Nothing yet.</p>';
  renderAccounts(s.accounts);
  renderCaps(s.capabilities);
  if (S.view === 'apps') renderApps();
  nameDesignCards();
  $('#setup-open').hidden = !!s.claude_ready;
  showProblems(s.problems);
  if (!S.setupShown && (!s.onboarded || !s.claude_ready)) { S.setupShown = true; openSetup(); }
}

function renderAccounts(a) {
  const need = a.accounts.filter((x) => x.state === 'needs-you');
  $('#acct-head').textContent = need.length ? `${a.accounts.length - need.length} fine. ${need.length === 1 ? 'One needs' : need.length + ' need'} you.` : 'All fine.';
  const chip = { 'in-use': ['In use now', 'accent'], 'nearly-full': ['Nearly full', 'line'], full: ['Full', 'dark'], ready: ['Ready', ''], 'needs-you': ['Needs you', ''], 'not-yet': ['Not set up', ''] };
  $('#accts').innerHTML = a.accounts.sort((x, y) => (y.state === 'needs-you') - (x.state === 'needs-you')).map((x) => {
    const [label, cls] = chip[x.state] || [x.state, ''];
    const wins = (x.windows || []).map((w) => `<div class="win"><span>${esc(w.label)}: ${Math.round(w.used * 100)}% used</span><span>fresh ${esc(w.resets || 'soon')}</span></div>`).join('');
    return `<div class="acct ${x.state === 'needs-you' ? 'needs' : ''}"><div class="top"><b>${esc(x.service)}${x.email ? ', ' + esc(x.email) : ''}</b><span class="chip ${cls}">${label}</span></div>
      ${x.used != null ? `<div class="meter" role="progressbar" aria-label="${esc(x.service)} usage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(x.used * 100)}"><i style="width:${Math.max(2, Math.round(x.used * 100))}%"></i></div>` : ''}
      <div class="bot"><span>${esc(x.say)}</span><span>${esc(x.plan || '')}</span></div>${wins}</div>`;
  }).join('');
  $('#acct-foot').textContent = 'Usage figures come from Claude itself each time App Magic does a job. Moving to another account by itself when one fills up is not built yet.';
}

function renderCaps(caps) {
  const words = { on: 'On', 'not-yet': 'Not yet. Set it up', 'signing-in': 'Signing in', 'needs-you': 'Needs you' };
  $('#caps').innerHTML = (caps || []).map((c) => {
    const inner = `<span><b>${esc(c.title)}</b><small>${esc(c.service || '')}</small></span><span class="st">${words[c.state] || c.state}${c.note ? '. ' + esc(c.note) : ''}</span>`;
    if (c.state === 'on') return `<div class="tile on">${inner}</div>`;
    if (c.check === 'apple' || c.check === 'google') return `<button class="tile ${c.state}" data-setup-step="2">${inner}</button>`;
    return `<button class="tile ${c.state}" data-say="${esc(`Help me set up "${c.title}" (${c.service}). Tell me in plain words what it needs from me, then do the rest.`)}">${inner}</button>`;
  }).join('');
}
async function refreshCaps() { renderCaps((await api('/api/capabilities')).capabilities); }

// ---- conversation ------------------------------------------------------------------------------------
function scrollThread() { const t = $('#thread'); t.scrollTop = t.scrollHeight; }
const nearBottom = () => { const t = $('#thread'); return t.scrollHeight - t.scrollTop - t.clientHeight < 120; };
function item(it, cls = '') {
  const t = $('#thread');
  if (it.k === 'step') return addStep(it);
  if (it.k === 'task') return addTaskNote(it);
  if (it.k === 'magic' && R.el) { S.streaming.home = ''; streamTo(it.text, true); return; }   // the words already showing become the message
  endStream();
  let el = document.createElement('div');
  if (it.k === 'you') { el.className = 'msg you'; el.innerHTML = rich(it.text); }
  else if (it.k === 'magic') { el.className = 'msg magic'; el.innerHTML = rich(it.text); }
  else if (it.k === 'error') {
    el.className = 'msg error ' + cls; el.textContent = it.text;
    if (it.problem) el.insertAdjacentHTML('beforeend', `<div class="choices"><button data-fix="${esc(it.problem)}">Fix it with Claude</button></div>`);
    if (/Open Setup/.test(it.text)) el.insertAdjacentHTML('beforeend', '<div class="choices"><button data-setup>Open setup</button></div>');
  }
  else if (it.k === 'show' || it.k === 'ask') {
    el.className = 'msg card';
    el.innerHTML = (it.title ? `<h4>${esc(it.title)}</h4>` : '') + (it.text ? rich(it.text) : '') +
      ((it.bullets || []).length ? '<ol>' + it.bullets.map((b, i) => `<li><b>${i + 1}</b><span>${esc(b)}</span></li>`).join('') + '</ol>' : '') +
      ((it.actions || []).length ? '<div class="choices">' + it.actions.map((a) => `<button data-say="${esc(a)}">${esc(a)}</button>`).join('') + '</div>' : '');
  }
  else if (it.k === 'design') {
    el.className = 'msg design'; el.dataset.url = it.url; el.dataset.app = it.app || '';
    el.innerHTML = `<button class="design-open" data-open-design="${esc(it.url)}"><span class="canvas" aria-hidden="true"><i></i><i></i><i></i></span>
      <span><b>${esc(designName(it.url, it.app))}</b><small>A design is ready to look at</small></span><span class="go">Open</span></button>`;
  }
  else return;
  t.append(el); scrollThread();
}

// Each step Claude takes shows in the conversation as a short line, in a group that keeps the last few in view and folds
// the rest away. While a turn is running, the newest line pulses, so it is always clear what is happening right now.
function addStep(it) {
  const t = $('#thread'), stick = nearBottom();
  let g = R.el ? R.el.previousElementSibling : t.lastElementChild;
  if (!g || !g.classList.contains('steps')) {
    g = document.createElement('div'); g.className = 'steps';
    g.innerHTML = '<button type="button" class="more" hidden></button><ol></ol>';
    R.el ? t.insertBefore(g, R.el) : t.append(g);
  }
  const li = document.createElement('li'); li.textContent = it.text; $('ol', g).append(li);
  const n = $$('li', g).length, more = $('.more', g);
  more.hidden = n <= 3; if (n > 3) more.textContent = g.classList.contains('open') ? 'Show fewer steps' : `${n - 3} earlier ${n - 3 === 1 ? 'step' : 'steps'}`;
  markNow(); if (stick) scrollThread();
}
function addTaskNote(it) {
  const el = document.createElement('div'), what = it.kind === 'helper' ? 'a helper' : 'a command';
  el.className = 'bgnote ' + it.state;
  el.textContent = it.state === 'started' ? `Started ${what} in the background: ${it.text}`
    : it.state === 'done' ? `Finished in the background: ${it.text}${it.took ? ' (' + dur(it.took) + ')' : ''}` : `Stopped before finishing: ${it.text}`;
  const stick = nearBottom(); R.el ? $('#thread').insertBefore(el, R.el) : $('#thread').append(el); if (stick) scrollThread();
}
function markNow() {
  $$('#thread .steps li.now').forEach((x) => x.classList.remove('now'));
  const last = R.el ? R.el.previousElementSibling : $('#thread').lastElementChild;
  if (A.home.busy && last && last.classList.contains('steps')) $('li:last-child', last).classList.add('now');
}

// Claude's words appear as they arrive, eased so a burst reads as a quick run of typing rather than a jump. The bubble
// carries a blinking caret until the message is complete.
const R = { el: null, target: '', shown: 0, raf: 0, final: false };
const calm = matchMedia('(prefers-reduced-motion: reduce)');
function streamTo(text, final = false) {
  if (!R.el) { R.el = document.createElement('div'); R.el.id = 'streaming'; R.el.className = 'msg magic streaming'; R.shown = 0; $('#thread').append(R.el); markNow(); }
  R.target = text; R.final = final;
  if (!R.raf) R.raf = requestAnimationFrame(reveal);
}
function reveal() {
  R.raf = 0; if (!R.el) return;
  const stick = nearBottom();
  R.shown = calm.matches ? R.target.length : Math.min(R.target.length, R.shown + Math.max(3, Math.ceil((R.target.length - R.shown) / 5)));
  const done = R.shown >= R.target.length;
  R.el.innerHTML = rich(R.target.slice(0, R.shown));
  if (stick) scrollThread();
  if (!done) R.raf = requestAnimationFrame(reveal);
  else if (R.final) { R.el.removeAttribute('id'); R.el.classList.remove('streaming'); R.el = null; }
}
function endStream() { if (R.raf) cancelAnimationFrame(R.raf); R.raf = 0; R.el?.remove(); R.el = null; }

// ---- what is going on right now ----------------------------------------------------------------------
// The service sends the whole picture on every change: whether a turn is running, what it is doing and since when, and
// the helpers and commands still running alongside it or in the background after the reply. A one-second tick keeps the
// clocks moving, so the window always shows that work is going on and for how long.
const A = { home: { busy: false, doing: '', since: 0, doing_since: 0, tasks: [] } };
const dur = (sec) => { sec = Math.max(0, Math.round(sec)); return sec < 60 ? sec + 's' : Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0'); };
const ago = (t) => dur(Date.now() / 1000 - t);
function setActivity(c, a) {
  const was = (A[c] || {}).busy;
  A[c] = { busy: !!a.busy, doing: a.doing || '', since: a.since || Date.now() / 1000, doing_since: a.doing_since || Date.now() / 1000, tasks: a.tasks || [] };
  S.busy[c] = A[c].busy;
  if (c === 'home') renderActivity();
  return was && !A[c].busy;                                         // true when a turn just ended
}
function renderActivity() {
  const a = A.home, d = $('#doing'), stick = nearBottom();         // the bars below the thread change its height
  d.hidden = !a.busy;
  $('#stop').hidden = !a.busy; $('#send').hidden = a.busy;
  $('#quick').hidden = a.busy;
  if (a.busy) { $('.now', d).textContent = a.doing || 'Thinking'; tick(); }
  else if (R.el && !R.final) { endStream(); S.streaming.home = ''; }
  markNow();
  const bg = $('#bg');
  bg.hidden = !a.tasks.length;
  if (a.tasks.length) {
    const all = a.tasks.every((x) => x.background);
    bg.innerHTML = `<div class="head"><b>${a.busy ? (all ? 'Also running in the background' : 'Working alongside') : 'Still running in the background'}</b>${a.busy ? '' : '<small>The reply comes when these finish. You can keep talking meanwhile.</small>'}</div>
      <ul>${a.tasks.map((x) => `<li><span class="bdot" aria-hidden="true"></span><span class="what"><b>${x.kind === 'helper' ? 'Helper' : x.kind === 'command' ? 'Command' : 'Job'}: ${esc(x.title)}</b>${x.now ? `<small>${esc(x.now)}</small>` : ''}</span><span class="clock" data-since="${x.since}">${ago(x.since)}</span></li>`).join('')}</ul>`;
  }
  $$('.topbar [data-view="home"]').forEach((b) => b.classList.toggle('working', a.busy || a.tasks.length > 0));
  if (stick) scrollThread();
}
function tick() {
  const a = A.home;
  if (a.busy) {
    const step = Date.now() / 1000 - a.doing_since, all = Date.now() / 1000 - a.since;
    $('#doing .clock').textContent = all - step > 5 ? `${dur(step)} · ${dur(all)} in all` : dur(all);
  }
  $$('#bg .clock').forEach((el) => { el.textContent = ago(+el.dataset.since); });
}
setInterval(tick, 1000);
async function loadConversation() {
  const c = await api('/api/conversation?c=home');
  endStream(); $('#thread').innerHTML = '';
  if (!c.items.length) item({ k: 'magic', text: 'What do you want to make? Tell me, or show me.' });
  $('#thread .msg')?.classList.toggle('first', !c.items.length);
  c.items.forEach((it) => item(it));
  setActivity('home', c);
  if (c.busy && c.stream) { S.streaming.home = c.stream; streamTo(c.stream); }
  if (c.design_url) setDesign(c.design_url);
  renderQuick(!c.items.length);
}
function renderQuick(fresh) {
  $('#quick').innerHTML = fresh
    ? '<button class="lead" data-do="files">Drop in files</button><button data-do="bring">Bring in an app I already have</button>'
    : (S.designUrl ? '<button data-do="open-design">Open the design</button>' : '');
}
async function say(text, opts = {}) {
  text = (text || '').trim(); if (!text) return;
  const r = await api('/api/chat', { c: opts.c || 'home', text, shown: opts.shown });
  if (!r.ok) toast(r.error || 'That did not send.');
  return r;
}
$('#composer').addEventListener('submit', async (e) => {
  e.preventDefault();
  const box = $('#say'), text = box.value; if (!text.trim()) return;
  box.value = ''; box.style.height = '';
  const r = await say(text);
  if (r && !r.ok) box.value = text;
  renderQuick(false);
});
$('#say').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#composer').requestSubmit(); } });
$('#say').addEventListener('input', (e) => { e.target.style.height = ''; e.target.style.height = Math.min(150, e.target.scrollHeight) + 'px'; });
$('#stop').addEventListener('click', () => api('/api/stop', { c: 'home' }));
$('#thread').addEventListener('click', (e) => {
  const more = e.target.closest('.steps .more'); if (!more) return;
  const g = more.closest('.steps'), n = $$('li', g).length; g.classList.toggle('open');
  more.textContent = g.classList.contains('open') ? 'Show fewer steps' : `${n - 3} earlier ${n - 3 === 1 ? 'step' : 'steps'}`;
});
document.addEventListener('click', (e) => {
  const s = e.target.closest('[data-say]'); if (s) { say(s.dataset.say); if (S.view !== 'home') show('home'); }
  const d = e.target.closest('[data-do]');
  if (d) ({ files: () => $('#file-input').click(), bring: () => { show('apps'); $('#bring').hidden = false; }, 'open-design': () => openDesign() })[d.dataset.do]?.();
  const o = e.target.closest('[data-open-design]');
  if (o) { const card = o.closest('.msg.design'); openDesign(o.dataset.openDesign, designName(card.dataset.url, card.dataset.app)); }
  const a = e.target.closest('[data-open-app]');
  if (a) { const app = (S.state.apps || []).find((x) => x.name === a.dataset.openApp); if (app && app.design_url) openDesign(app.design_url, app.name); else show('apps'); }
});

// ---- dropping things in ------------------------------------------------------------------------------
async function addFiles(files) {
  const added = [];
  for (const f of files) {
    const r = await fetch('/api/context/upload?name=' + encodeURIComponent(f.name), { method: 'POST', body: f }).then((x) => x.json());
    if (r.ok) added.push({ name: f.name, id: r.item.id, path: r.path });
  }
  if (!added.length) return;
  const list = added.map((a) => `- ${a.name} (context id ${a.id}, saved at ${a.path})`).join('\n');
  show('home');
  say(`I dropped in ${added.length} file${added.length > 1 ? 's' : ''}:\n${list}\n\nLook at each one, save a one or two sentence summary of what it tells you about how I work, and then tell me briefly what you see.`,
    { shown: 'Dropped in: ' + added.map((a) => a.name).join(', ') });
}
$('#add-file').addEventListener('click', () => $('#file-input').click());
$('#file-input').addEventListener('change', (e) => { addFiles([...e.target.files]); e.target.value = ''; });
let dragDepth = 0;
window.addEventListener('dragenter', (e) => { if (e.dataTransfer?.types?.includes('Files')) { dragDepth++; $('#dropzone').hidden = false; e.preventDefault(); } });
window.addEventListener('dragover', (e) => { if (e.dataTransfer?.types?.includes('Files')) e.preventDefault(); });
window.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('#dropzone').hidden = true; });
window.addEventListener('drop', (e) => { e.preventDefault(); dragDepth = 0; $('#dropzone').hidden = true; if (e.dataTransfer?.files?.length) addFiles([...e.dataTransfer.files]); });

// ---- apps and bringing them in -----------------------------------------------------------------------
function renderApps() {
  const apps = (S.state && S.state.apps) || [];
  const from = (a) => ({ 'made-here': 'Made here' }[a.source] || (a.source === 'GitHub' ? 'GitHub' : 'A folder on this Mac'));
  $('#apps-table').innerHTML = !apps.length ? '<div class="empty">No apps yet. Make something new, or bring in one you already have.</div>' :
    '<div class="tr th"><div class="c1">App</div><div class="c2">Came from</div><div class="c3">Lives</div><div class="c4">Right now</div><div class="c5"></div></div>' +
    apps.map((a) => {
      const fails = a.audit && a.audit.fails;
      const now = fails ? `<span class="chip dark">${esc(a.audit.summary)}</span>` : esc(cap(a.status || (a.audit ? 'Fine' : 'Not checked yet')));
      return `<div class="tr"><div class="c1">${pic({ label: a.name, thumb: a.thumb, picture_kind: a.picture_kind })}<span><b>${esc(a.name)}</b><small>${esc(a.summary || a.kind || '')}</small></span></div>
        <div class="c2">${from(a)}</div><div class="c3">${esc(cap(a.lives || 'Nowhere yet'))}</div><div class="c4">${now}</div>
        <div class="c5">${a.design_url ? `<button class="mini" data-design="${esc(a.name)}">Design</button>` : ''}${a.path ? `<button class="mini" data-audit="${esc(a.name)}">Check safety</button>` : ''}<button class="mini" data-work="${esc(a.name)}">Work on it</button></div></div>`;
    }).join('');
}
$('#apps-table').addEventListener('click', async (e) => {
  const d = e.target.closest('[data-design]'), a = e.target.closest('[data-audit]'), w = e.target.closest('[data-work]');
  if (d) { const app = S.state.apps.find((x) => x.name === d.dataset.design); openDesign(app.design_url, app.name); }
  if (a) { a.disabled = true; a.textContent = 'Checking'; const r = await api('/api/apps/audit', { name: a.dataset.audit }); a.disabled = false; a.textContent = 'Check safety'; if (r.report) showAudit(r.report); else toast(r.error); refresh(); }
  if (w) { show('home'); $('#say').value = `About ${w.dataset.work}: `; $('#say').focus(); }
});
$('#bring-open').addEventListener('click', () => { $('#bring').hidden = !$('#bring').hidden; });
$('#make-new').addEventListener('click', () => { show('home'); $('#say').focus(); });
if (native) $('#pick-folder').hidden = false;
$('#pick-folder').addEventListener('click', () => bridge('pickFolder'));
function renderPicks(title, picks, kind) {
  S.picks = picks;
  if (!picks.length) { $('#found').innerHTML = `<p class="note">${esc(title)}</p>`; return; }
  $('#found').innerHTML = `<h3>${esc(title)}</h3>` + picks.map((p, i) => `<label class="pick"><input type="checkbox" data-i="${i}" ${p.suggest && !p.already ? 'checked' : ''} ${p.already ? 'disabled' : ''}>
      <span><b>${esc(p.name)}</b><small>${esc(p.already ? 'Already in App Magic' : p.what)}</small></span><span class="when">${esc(p.changed ? 'Changed ' + p.changed : '')}</span></label>`).join('') +
    `<div class="bar"><span class="note" id="pick-count"></span><button class="btn accent" id="bring-go">Bring in</button></div>`;
  const count = () => { const n = $$('#found input:checked').length; $('#pick-count').textContent = `${n} ticked of ${picks.length}`; $('#bring-go').textContent = n ? `Bring in ${n === 1 ? 'this one' : 'these ' + n}` : 'Bring in'; $('#bring-go').disabled = !n; };
  $$('#found input').forEach((i) => i.addEventListener('change', count)); count();
  $('#bring-go').addEventListener('click', () => bringIn($$('#found input:checked').map((i) => picks[+i.dataset.i].source)));
}
$('#gh-load').addEventListener('click', async (e) => {
  e.target.disabled = true; e.target.textContent = 'Asking GitHub';
  const r = await api('/api/github'); e.target.disabled = false; e.target.textContent = 'Show my GitHub';
  if (!r.connected) return renderPicks('GitHub is not connected yet. ' + (r.error || ''), []);
  renderPicks('Which of these do you want in App Magic?', r.repos.map((x) => ({ name: x.name, what: x.what + (x.private ? '' : ' Public.'), changed: x.changed, suggest: x.suggest, already: x.already, source: x.slug })));
});
// The search for apps already on this Mac, shown as it happens: look, open each one, then Claude reads the code.
const VERDICT = { yours: ['Looks like yours', 'accent'], 'not-yours': ["Someone else's", ''], copy: ['A copy', ''], 'not-an-app': ['Not an app', ''] };
// The picture of a project: a real one when there is one (a screenshot in it, the page itself, its icon), else its initial.
const PIC_SAYS = { screenshot: 'A picture found in the project', page: 'The page itself, drawn just now', icon: 'Its icon' };
function pic(it) {
  const name = it.label || it.name || '?';
  return it.thumb ? `<span class="pic ${it.picture_kind === 'icon' ? 'icon' : ''}" title="${PIC_SAYS[it.picture_kind] || ''}"><img src="/api/thumb/${it.thumb}.png" alt="${esc(PIC_SAYS[it.picture_kind] || 'Picture')} of ${esc(name)}" data-zoom="${esc(name)}"></span>`
    : `<span class="pic none" aria-hidden="true">${esc(name.trim()[0].toUpperCase())}</span>`;
}
document.addEventListener('click', (e) => {
  const z = e.target.closest('img[data-zoom]'); if (!z) return;
  e.preventDefault();
  const o = document.createElement('div'); o.className = 'overlay zoom';
  o.innerHTML = `<figure><img src="${z.src}" alt="${esc(z.alt)}"><figcaption>${esc(z.dataset.zoom)}. ${esc(z.closest('.pic').title)}.</figcaption></figure>`;
  o.addEventListener('click', () => o.remove()); document.body.append(o);
});
function scanRow(it, final) {
  const st = it.state || 'found';
  const v = VERDICT[it.verdict];
  const chip = st === 'found' ? '<span class="chip guess">Found</span>' : st === 'reading' ? '<span class="chip guess">Opening it</span>'
    : v ? `<span class="chip ${st === 'judged' ? v[1] : 'guess'}">${st === 'judged' ? v[0] : 'First look: ' + v[0].toLowerCase()}</span>` : '';
  const box = final ? `<input type="checkbox" data-i="${it.i}" ${it.verdict === 'yours' && !it.already ? 'checked' : ''} ${it.already ? 'disabled' : ''} aria-label="Bring in ${esc(it.label || it.name)}">` : '<span class="sdot"></span>';
  const where = esc(String(it.path || '').replace(/^\/Users\/[^/]+/, '~'));
  const named = st === 'judged' && it.label;                    // Claude has read it and suggested what to call it
  const title = final ? `<input class="nm" type="text" data-name="${it.i}" value="${esc(it.label || it.name)}" aria-label="Name for ${esc(it.name)}" size="${Math.max(8, (it.label || it.name).length + 1)}">`
    : `<b>${esc(named ? it.label : it.name)}</b>`;
  const about = it.already ? 'Already in App Magic' : (it.summary || it.what || it.kind || '');
  return `<label class="srow ${st}" data-i="${it.i}">${box}${pic(it)}<span class="smain">${title}<span class="where">${where}</span>
    <small class="what ${it.summary ? 'sum' : ''}">${esc(about)}</small>${it.why ? `<small>${esc(it.why)}${it.changed ? ' Changed ' + esc(it.changed) + '.' : ''}</small>` : ''}${st === 'read' && (it.looked_at || []).length ? `<small>Opened: ${esc(it.looked_at.join(', '))}</small>` : ''}</span>${chip}</label>`;
}
function scanSteps(status) {
  const order = ['looking', 'reading', 'judging', 'done'], labels = { looking: '1 Looking through folders', reading: '2 Opening each one', judging: '3 Reading the code', done: 'Done' };
  const at = order.indexOf(status === 'stopped' ? 'done' : status);
  return order.slice(0, 3).map((k, i) => `<span class="${i < at ? 'was' : i === at ? 'now' : ''}">${labels[k]}</span>`).join('');
}
function renderScan(sc) {
  S.scan = sc; if (!sc) return;
  const live = !['done', 'stopped'].includes(sc.status);
  const found = $('#found');
  if (live) {
    found.innerHTML = `<div class="scan"><div class="scan-head"><span class="pulse"></span><b id="scan-phase">${esc(sc.phase || 'Looking')}</b><button class="mini" id="scan-stop">Stop</button></div>
      <div class="scan-steps" id="scan-steps">${scanSteps(sc.status)}</div><div class="scan-path" id="scan-path">&nbsp;</div>
      <div class="note" id="scan-count"></div><div class="scan-list" id="scan-list">${sc.items.map((it) => scanRow(it)).join('')}</div></div>`;
    $('#scan-stop').onclick = () => api('/api/apps/scan/stop', {});
    scanCount();
    return;
  }
  const yours = sc.items.filter((x) => x.verdict === 'yours'), rest = sc.items.filter((x) => x.verdict !== 'yours');
  const group = (title, list) => list.length ? `<h4>${title} (${list.length})</h4>` + list.map((it) => scanRow(it, true)).join('') : '';
  found.innerHTML = `<div class="scan done"><div class="scan-head"><span class="pulse"></span><b>${esc(sc.phase || 'Done')}</b><button class="mini" id="scan-again">Look again</button></div>
    ${sc.overview ? `<p class="overview">${esc(sc.overview)}</p>` : ''}
    <div class="note">Looked through ${sc.looked.toLocaleString()} folders in ${esc(sc.places.join(', '))}. ${sc.items.some((x) => x.judged_by) ? 'Claude read each one and suggested a name. Click a name to change it.' : 'These are first looks; Claude has not read them.'}</div>
    ${sc.items.length ? `<div class="scan-list">${group('These look like yours', yours)}${rest.length ? `<details ${yours.length ? '' : 'open'}><summary>${rest.length} that don't look like your apps</summary>
      ${group("Someone else's work", rest.filter((x) => x.verdict === 'not-yours'))}${group('Copies of ones above', rest.filter((x) => x.verdict === 'copy'))}${group('Not apps: documents, data, small scripts', rest.filter((x) => x.verdict === 'not-an-app'))}</details>` : ''}</div>
    <div class="bar"><span class="note" id="pick-count"></span><button class="btn accent" id="bring-go">Bring in</button></div>` : '<p class="note">Nothing there looked like an app. Try a different place, or look all over the computer.</p>'}</div>`;
  $('#scan-again').onclick = () => startScan(sc.hint, sc.everywhere);
  if (!sc.items.length) return;
  const count = () => { const n = $$('#found input[type=checkbox]:checked').length; $('#pick-count').textContent = `${n} ticked of ${sc.items.length} found`; $('#bring-go').textContent = n ? `Bring in ${n === 1 ? 'this one' : 'these ' + n}` : 'Bring in'; $('#bring-go').disabled = !n; };
  $$('#found input[type=checkbox]').forEach((i) => i.addEventListener('change', count)); count();
  $$('#found input.nm').forEach((i) => i.addEventListener('input', () => { i.size = Math.max(8, i.value.length + 1); }));
  $('#bring-go').onclick = () => bringIn($$('#found input[type=checkbox]:checked').map((c) => { const it = sc.items[+c.dataset.i]; const nm = $(`#found input.nm[data-name="${it.i}"]`); return { source: it.source, name: (nm && nm.value.trim()) || it.label || it.name, summary: it.summary || '', what: it.what || '', thumb: it.thumb || '', picture_kind: it.picture_kind || '' }; }));
}
function scanCount() {
  const sc = S.scan; if (!sc || !$('#scan-count')) return;
  const read = sc.items.filter((x) => x.state && x.state !== 'found' && x.state !== 'reading').length, judged = sc.items.filter((x) => x.state === 'judged').length;
  $('#scan-count').textContent = `${sc.looked.toLocaleString()} folders looked in · ${sc.items.length} could be apps` + (read ? ` · ${read} opened` : '') + (judged ? ` · ${judged} read by Claude` : '');
}
function scanEvent(e) {
  const sc = S.scan;
  if (e.k === 'start' || e.k === 'done') { renderScan(e.search); if (e.k === 'done') refresh(); return; }
  if (!sc || sc.id !== e.id || !$('#scan-list')) return;
  if (e.k === 'phase') { sc.phase = e.text; sc.status = e.status; $('#scan-phase').textContent = e.text; $('#scan-steps').innerHTML = scanSteps(e.status); }
  else if (e.k === 'look') { sc.looked = e.looked; $('#scan-path').textContent = e.path; }
  else if (e.k === 'found') { const it = { ...e.item, i: e.i, state: 'found' }; sc.items[e.i] = it; sc.looked = e.looked; $('#scan-list').insertAdjacentHTML('beforeend', scanRow(it)); $('#scan-list').scrollTop = $('#scan-list').scrollHeight; }
  else if (e.k === 'thumb') { const it = sc.items[e.i]; if (it) { it.thumb = e.thumb; it.picture_kind = e.picture_kind; const row = $(`#scan-list .srow[data-i="${e.i}"]`); if (row) row.outerHTML = scanRow(it); } }
  else if (e.k === 'reading' || e.k === 'read' || e.k === 'judged') {
    const was = sc.items[e.i] || {};
    const it = e.k === 'reading' ? { ...was, state: 'reading' } : { ...e.item, thumb: e.item.thumb || was.thumb, picture_kind: e.item.picture_kind || was.picture_kind }; sc.items[e.i] = it;
    const row = $(`#scan-list .srow[data-i="${e.i}"]`);
    if (row) { row.outerHTML = scanRow(it); const now = $(`#scan-list .srow[data-i="${e.i}"]`); if (e.k !== 'judged' || !S.scanHover) now.scrollIntoView({ block: 'nearest' }); }
    if (e.k === 'reading') $('#scan-path').textContent = e.path;
    if (e.k === 'judged') $('#scan-path').textContent = 'Claude on ' + it.name + ': ' + (it.why || '');
  }
  scanCount();
}
async function startScan(where, everywhere) {
  $('#bring').hidden = false;
  const r = await api('/api/apps/scan', { where: where || '', everywhere: !!everywhere });
  if (r.search) renderScan(r.search);
}
// Bringing apps in. One comes straight back; several run in the background and report as they go.
async function bringIn(items) {
  items = items.map((x) => (typeof x === 'string' ? { source: x } : x));
  if (!items.length) return;
  if ($('#bring-go')) { $('#bring-go').disabled = true; $('#bring-go').textContent = 'Bringing them in'; }
  const r = await api('/api/apps/bring', { items });
  if (r.started) { $('#found').innerHTML = `<div class="scan"><div class="scan-head"><span class="pulse"></span><b id="bring-phase">Bringing in ${r.total} apps</b></div><div class="note" id="bring-note">Copying each one, leaving out libraries and password files, and running the safety checks.</div></div>`; return; }
  broughtIn(r.results.filter((x) => x.ok).map((x) => x.app.name), r.results.filter((x) => !x.ok).map((x) => x.error), r.results.filter((x) => x.ok && x.audit && x.audit.fails).map((x) => x.app.name));
  const worst = r.results.find((x) => x.ok && x.audit && x.audit.fails); if (worst) showAudit(worst.audit);
}
async function broughtIn(ok, errors, toFix) {
  S.scan = null;
  $('#found').innerHTML = `<p class="note">${ok.length ? `Brought in ${ok.length === 1 ? esc(ok[0]) : ok.length + ' apps'}. ` : ''}${toFix.length ? `${toFix.length === 1 ? esc(toFix[0]) + ' has' : toFix.length + ' have'} safety problems to fix; they are marked in the list below. ` : ''}${errors.map(esc).join(' ')}</p>`;
  await refresh(); renderApps();
}
function bringEvent(e) {
  if (e.k === 'one' && $('#bring-phase')) { $('#bring-phase').textContent = `Bringing in ${e.n} of ${e.total}: ${e.name}`; }
  else if (e.k === 'done') broughtIn(e.ok || [], e.errors || [], e.to_fix || []);
}
$('#where-go').addEventListener('click', () => startScan($('#where').value));
$('#where').addEventListener('keydown', (e) => { if (e.key === 'Enter') startScan(e.target.value); });
$('#look-everywhere').addEventListener('click', () => startScan('', true));

// ---- capabilities, safety ----------------------------------------------------------------------------
$('#learn').addEventListener('submit', (e) => { e.preventDefault(); const t = $('#learn-text').value.trim(); if (!t) return; $('#learn-text').value = ''; show('home'); say('I want App Magic to be able to do this: ' + t, { shown: t }); });
$('#self-audit').addEventListener('click', async (e) => { e.target.disabled = true; const r = await api('/api/apps/audit', { target: 'self' }); e.target.disabled = false; showAudit(r.report); $('#self-line').textContent = r.report.summary + '. Checked just now.'; });
function showAudit(rep) {
  $('#audit-target').textContent = 'Safety checks: ' + rep.target;
  $('#audit-summary').textContent = rep.summary;
  const mk = { pass: 'OK', fail: '!', unknown: '?' };
  $('#audit-checks').innerHTML = rep.checks.map((c) => `<div class="check ${c.status}"><span class="mk">${mk[c.status]}</span><div><b>${esc(c.title)}</b><p>${esc(c.say)}</p>
    ${c.findings.length ? '<ul>' + c.findings.slice(0, 8).map((f) => `<li>${esc(f.what)}${f.sample ? ' <code>' + esc(f.sample) + '</code>' : ''}</li>`).join('') + '</ul>' : ''}${c.fix ? `<p class="fix">To fix: ${esc(c.fix)}</p>` : ''}</div></div>`).join('');
  const fix = $('#audit-fix'); fix.hidden = !rep.fails || rep.target === 'App Magic';
  fix.onclick = () => { $('#audit-modal').hidden = true; show('home'); say(`The safety checks on ${rep.target} found: ${rep.checks.filter((c) => c.status === 'fail').map((c) => c.title + ' (' + c.say + ')').join('; ')}. The full report is at ${rep.saved}. Fix these, show me what you changed before anything goes live, then run the checks again.`, { shown: `Fix the safety problems in ${rep.target}` }); };
  $('#audit-modal').hidden = false;
}

// ---- the design canvas -------------------------------------------------------------------------------
// A design never takes over the window by itself: it arrives as a card in the conversation, and opening the card shows
// the canvas in a pop-up over the chat. The canvas is the Mac app's own browser pane, placed over #browser-slot.
function designName(url, app) { return app || ((S.state && S.state.apps) || []).find((x) => x.design_url === url)?.name || 'Your design'; }
function nameDesignCards() { $$('#thread .msg.design').forEach((el) => { $('b', el).textContent = designName(el.dataset.url, el.dataset.app); }); }
function openDesign(url, app) {
  if (url) setDesign(url, app);
  if (!S.designUrl) return;
  S.designOpen = true; $('#design').hidden = false;
  requestAnimationFrame(openBrowser);                                   // after the sheet is laid out, so the pane lands on the slot
}
function closeDesign() { S.designOpen = false; $('#design').hidden = true; bridge('browserClose'); if (S.view === 'home') $('#say').focus(); }
function hideOverlay(o) { if (!o) return; if (o.id === 'design') closeDesign(); else o.hidden = true; }
$('#design-close').addEventListener('click', closeDesign);
function setDesign(url, app) {
  S.designUrl = url; if (app) S.designApp = app;
  $('#design-name').textContent = S.designApp || 'Design';
  $('#browser-link').href = url; $('#browser-link').hidden = !!native;
  $('#browser-msg').textContent = native ? 'Opening the canvas' : 'The canvas opens inside the App Magic app. In a plain browser, use this:';
  renderQuick(false);
}
function slotFrame() { const r = $('#browser-slot').getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; }
function openBrowser() { if (S.designUrl) bridge('browserOpen', { url: S.designUrl, ...slotFrame() }); }
window.addEventListener('resize', () => { if (S.designOpen) bridge('browserFrame', slotFrame()); });
$('#design-external').addEventListener('click', () => { if (!bridge('openExternal', { url: S.designUrl })) window.open(S.designUrl, '_blank'); });
$('#design-reload').addEventListener('click', () => bridge('browserReload'));
// Signing in to claude.ai inside the pane. Passkeys (and so Google's usual sign-in) only work in real browsers, so when the
// pane lands on a sign-in page a strip explains the email route. The shell reports every address the pane reaches.
const SIGNIN_PAGE = /^https:\/\/(accounts\.google\.com|appleid\.apple\.com)\/|^https:\/\/claude\.ai\/(login|magic-link|verify|oauth)/;
function browserAt(url) {
  const signing = SIGNIN_PAGE.test(url || '');
  if (signing !== S.signingIn) {
    // Back on claude.ai after signing in: go to the canvas the person was trying to see.
    if (S.signingIn && /^https:\/\/claude\.ai\//.test(url) && S.designUrl && !url.startsWith(S.designUrl)) bridge('browserLoad', { url: S.designUrl });
    S.signingIn = signing;
    $('#signin-help').hidden = !signing;
    if (S.designOpen) bridge('browserFrame', slotFrame());
  }
}
$('#signin-email').addEventListener('click', () => bridge('browserLoad', { url: 'https://claude.ai/login' }));
$('#signin-link').addEventListener('click', () => bridge('browserPasteLink'));
$('#change').addEventListener('submit', (e) => { e.preventDefault(); const t = $('#change-text').value.trim(); if (!t) return; $('#change-text').value = ''; say(`About the ${S.designApp || 'current'} design: ${t}`, { shown: t }); });

// ---- Ask ---------------------------------------------------------------------------------------------
function openAsk() {
  S.askOpen = true; bridge('browserClose');
  $('#ask').hidden = false; $('#ask-answer').hidden = true; $('#ask-answer').innerHTML = ''; $('#ask-text').value = '';
  $('#ask-about').textContent = S.designOpen ? `About the ${S.designApp || ''} design` : S.view === 'home' ? '' : 'About ' + S.view;
  $('#ask-rows').innerHTML = [['Bring in an app I already have', 'bring'], ['Drop in files', 'files'], ['Check App Magic itself', 'self'], ['Make changes to App Magic', 'change']]
    .map(([l, d]) => `<button data-ask-do="${d}">${l}</button>`).join('');
  $('#ask-text').focus();
}
function closeAsk() { S.askOpen = false; $('#ask').hidden = true; if (S.designOpen) openBrowser(); }
$('#ask-open').addEventListener('click', openAsk);
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); S.askOpen ? closeAsk() : openAsk(); }
  if (e.key === 'Escape') { if (S.askOpen) return closeAsk(); $$('.overlay').forEach(hideOverlay); }
});
$('#ask-rows').addEventListener('click', (e) => {
  const d = e.target.closest('[data-ask-do]'); if (!d) return; closeAsk();
  ({ bring: () => { show('apps'); $('#bring').hidden = false; }, files: () => $('#file-input').click(), self: () => { show('capabilities'); $('#self-audit').click(); }, change: () => openChange() })[d.dataset.askDo]();
});
$('#ask-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = $('#ask-text').value.trim(); if (!q) return;
  const where = S.designOpen ? `the design of ${S.designApp || 'an app'} (${S.designUrl})` : `the ${S.view} page`;
  S.streaming.ask = ''; $('#ask-answer').hidden = false; $('#ask-answer').textContent = 'Thinking';
  $('#ask-rows').innerHTML = '';
  await say(`[Asked from the Ask bar while looking at ${where}. Answer in two or three plain sentences first; only then, if useful, offer one or two things you could do.]\n${q}`, { c: 'ask', shown: q });
});

// ---- changing App Magic itself -----------------------------------------------------------------------
function openChange(text = '') { $('#change-modal').hidden = false; $('#change-request').value = text; $('#change-request').focus(); }
$('#change-open').addEventListener('click', () => openChange());
$('#change-go').addEventListener('click', async (e) => {
  e.target.disabled = true;
  const r = await api('/api/workshop', { request: $('#change-request').value });
  e.target.disabled = false;
  if (r.ok) { $('#change-modal').hidden = true; toast(r.dry ? 'Workshop (test mode): nothing was opened.' : 'The workshop is open in its own window. App Magic may close and reopen while it works.'); }
  else $('#change-note').textContent = 'The workshop did not open: ' + (r.error || 'unknown reason');
});

// ---- live events -------------------------------------------------------------------------------------
function listen() {
  const es = new EventSource('/api/events');
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    S.events = (S.events || []).slice(-40); S.events.push(e.t + (e.c ? ':' + e.c : '') + (e.t === 'busy' ? ':' + e.busy : ''));   // sent along with problem reports
    if (e.t === 'stream-start') S.streaming[e.c] = '';
    else if (e.t === 'item' && e.c === 'home') { item(e.item); if (e.item.k === 'magic') { $('#design-last').innerHTML = rich(e.item.text.split('\n')[0].slice(0, 220)); } }
    else if (e.t === 'item' && e.c === 'ask' && (e.item.k === 'magic' || e.item.k === 'error')) { $('#ask-answer').hidden = false; $('#ask-answer').innerHTML = rich(e.item.text); S.streaming.ask = ''; }
    else if (e.t === 'delta') { S.streaming[e.c] = (S.streaming[e.c] || '') + e.text; if (e.c === 'home') streamTo(S.streaming.home); else if (e.c === 'ask') $('#ask-answer').innerHTML = rich(S.streaming.ask); }
    else if (e.t === 'busy') { const ended = setActivity(e.c, e); if (e.c === 'home' && S.designOpen && e.busy) $('#design-last').textContent = e.doing || 'Working'; if (ended) refresh(); }
    else if (e.t === 'design' && e.url) { setDesign(e.url, e.app); if (e.app) $$('#thread .msg.design').forEach((el) => { if (el.dataset.url === e.url) el.dataset.app = e.app; }); nameDesignCards(); }
    else if (e.t === 'open' && e.url) openDesign(e.url);                // Claude was asked to show something now
    else if (e.t === 'refresh' || e.t === 'usage') refresh();
    else if (e.t === 'note') { toast(e.text); bridge('note', { text: e.text }); }
    else if (e.t === 'audit' && e.report) showAudit(e.report);
    else if (e.t === 'scan') scanEvent(e);
    else if (e.t === 'bring') bringEvent(e);
    else if (e.t === 'problem' || e.t === 'problems-changed') refreshProblems();
    else if (e.t === 'setup') setupEvent(e);
    else if (e.t === 'setup-needed') openSetup();
  };
  es.onerror = () => { es.close(); setTimeout(() => { listen(); refresh().catch(() => {}); }, 2000); };
}

// The native shell calls these.
window.magicNative = {
  folderPicked: (path) => { $('#where').value = path; show('apps'); startScan(path); },
  show: (view) => show(view),
  ask: () => openAsk(),
  change: (text) => openChange(text || ''),
  setup: () => openSetup(0),
  browserAt: (url) => browserAt(url),
  noLink: () => toast('Copy the sign-in link from Claude’s email first: right-click the button in the email and choose Copy Link.'),
};

// ---- first run: Claude, the plan, and saying plainly what App Magic does -------------------------------
const SETUP_STEPS = ['Claude', 'Plan', 'Accounts', 'Permission', 'Make'];
function openSetup(step) { S.setupStep = step ?? S.setupStep ?? 0; $('#setup').hidden = false; bridge('browserClose'); renderSetup(); }
function closeSetup() { $('#setup').hidden = true; if (S.designOpen) openBrowser(); }
async function renderSetup(fresh) {
  const st = S.setup = await api('/api/setup' + (fresh ? '?fresh=1' : ''));
  if (st.error) return;
  const c = st.claude, step = S.setupStep || 0;
  $('#setup-steps').innerHTML = SETUP_STEPS.map((n, i) => `<li class="${i === step ? 'now' : i < step ? 'was' : ''}">${i + 1} ${n}</li>`).join('');
  const body = $('#setup-body');
  if (step === 0) {
    const doing = st.jobs.install === 'running' ? 'install' : st.jobs.signin === 'waiting' ? 'signin' : '';
    body.innerHTML = `<h2>First, meet your builder.</h2>
      <p class="lead">Claude does the designing and the building. App Magic uses your own Claude account for all of it, never a separate key.</p>
      <ul class="checks">
        <li class="${c.installed ? 'ok' : ''}"><b>${c.installed ? 'Claude is on this Mac' : 'Claude isn\'t on this Mac yet'}</b>${c.installed ? '' : '<span>A free download from Anthropic, about a minute.</span>'}</li>
        <li class="${c.signed_in ? 'ok' : ''}"><b>${c.signed_in ? 'Signed in as ' + esc(c.email) : 'Not signed in yet'}</b>${c.signed_in ? '' : '<span>A small window opens and your browser asks you to approve. App Magic never sees your password.</span>'}</li>
      </ul>
      <div id="setup-log" class="setup-log" ${doing ? '' : 'hidden'}>${doing === 'signin' ? 'Waiting for you to finish signing in, in the window that opened…' : 'Starting…'}</div>
      <div class="actions">${!c.installed ? `<button class="btn accent" id="setup-install" ${doing ? 'disabled' : ''}>Put Claude on this Mac</button>`
        : !c.signed_in ? `<button class="btn accent" id="setup-signin" ${doing ? 'disabled' : ''}>${doing ? 'Waiting for sign-in' : 'Sign in with Claude'}</button>`
        : '<button class="btn accent" data-step="1">Next</button>'}</div>`;
  } else if (step === 1) {
    body.innerHTML = `<h2>Pick a plan that keeps up.</h2>
      <p class="lead">Designing and building apps is heavy work for Claude. On a small plan you'll run out partway through, usually right when it's getting good.</p>
      <div class="plan-card"><div class="plan-top"><b>Claude Max</b><span class="chip accent">Recommended</span></div>
        <div class="plan-price"><b>$200</b><span>a month, paid to Claude</span></div>
        <p>Room to design and build for hours at a stretch, with App Magic working in the background.</p></div>
      <p class="note">${c.plan ? `You're on ${esc(c.plan)}.` : 'App Magic could not tell which plan you have.'} The Accounts page always shows how much you have left.</p>
      <div class="actions">${/Max/.test(c.plan || '') ? '<button class="btn accent" data-step="2">Next</button>' : '<button class="btn accent" id="setup-upgrade">Get Claude Max</button><button class="btn outline" data-step="2">Carry on with my plan</button>'}</div>`;
  } else if (step === 2) {
    const dev = (await api('/api/setup/dev')).accounts || [];
    body.innerHTML = `<h2>Two accounts worth having.</h2>
      <p class="lead">Both are optional. Say yes and App Magic walks you through it now; say not now and you can do it any time from Capabilities.</p>
      ${dev.map(devCard).join('')}
      <div class="actions"><button class="btn accent" data-step="3">Next</button><button class="link" data-step="1">Back</button></div>`;
  } else if (step === 3) {
    body.innerHTML = `<p class="label">Just so you know</p><h2>App Magic uses your whole Mac.</h2>
      <ul class="plain">
        <li>Claude works in your App Magic folder and can read and change files on this Mac.</li>
        <li>It runs programs without asking you each time, so it can build without stopping to check in.</li>
        <li>It asks first before spending money, publishing anything, deleting things or sending messages as you, and it never types a password.</li>
        <li>When you ask to change App Magic itself, a separate window opens and App Magic may close and reopen. The first time, your Mac asks whether App Magic may control Terminal: say OK.</li>
      </ul>
      <div class="actions"><button class="btn accent" data-step="4">I understand</button><button class="link" data-step="2">Back</button></div>`;
  } else {
    body.innerHTML = `<h2>${c.signed_in ? 'Claude is in. Start making.' : 'Almost there.'}</h2>
      <p class="lead">${c.signed_in ? 'Tell App Magic what you want to make, show it with files and pictures, or bring in apps you already have.' : 'Claude still needs signing in before App Magic can do anything.'}</p>
      <div class="actions">${c.signed_in ? '<button class="btn accent" id="setup-finish">Start making</button><button class="btn outline" id="setup-bring">Bring in apps I already have</button>' : '<button class="btn accent" data-step="0">Back to Claude</button>'}</div>
      <p class="note">App Magic lives on your Mac. It's yours to edit and improve.</p>`;
  }
}
// One developer account on the Accounts step: the choice, then each step with what this Mac shows or the person ticked.
function devCard(a) {
  const head = `<div class="dev-top"><b>${esc(a.title)}</b>${a.ready ? '<span class="chip dark">All set</span>' : `<span class="chip line">${esc(a.cost)}</span>`}</div><p>${esc(a.for)}</p>`;
  if (a.want == null && !a.ready) return `<div class="dev-card" data-dev="${a.key}">${head}
    <div class="actions"><button class="btn accent" data-dev-want="1">Yes, set this up</button><button class="btn outline" data-dev-want="0">Not now</button></div></div>`;
  if (a.want === false && !a.ready) return `<div class="dev-card off" data-dev="${a.key}">${head}
    <p class="note">Not now. <button class="link" data-dev-want="1">Set it up after all</button></p></div>`;
  const steps = a.steps.map((s) => `<li class="${s.done ? 'ok' : ''}" data-dev-step="${s.id}"><b>${esc(s.title)}</b>
      <span>${s.seen ? 'Found on this Mac' + (s.detail ? ': ' + esc(s.detail) : '') + '.' : esc(s.how)}</span>
      ${s.seen ? '' : `<div class="dev-do">${s.link ? `<button class="link" data-open-url="${esc(s.link)}">${esc(s.link_title || 'Open')}</button>` : ''}${s.ask && !s.done ? `<button class="link" data-dev-ask="${esc(s.ask)}">Ask Claude to do it</button>` : ''}${s.check ? (s.done ? '' : '<button class="link" data-dev-check>Check again</button>') : `<button class="link" data-dev-tick="${s.done ? 0 : 1}">${s.done ? 'Not done yet' : 'I did this'}</button>`}</div>`}</li>`).join('');
  return `<div class="dev-card" data-dev="${a.key}">${head}<ul class="checks">${steps}</ul>
    ${a.ready ? '' : '<p class="note"><button class="link" data-dev-want="0">Not now</button></p>'}</div>`;
}
async function setupEvent(e) {
  if (e.k === 'log') { const l = $('#setup-log'); if (l) { l.hidden = false; l.textContent = e.line; } }
  else if (e.k === 'done') {
    if (e.job === 'install') toast(e.ok ? 'Claude is on this Mac.' : 'Putting Claude on this Mac did not work. See the problem below.');
    if (e.job === 'signin' && e.ok) toast('Signed in as ' + (e.email || 'you') + '.');
    refresh(); if (!$('#setup').hidden) renderSetup(true);
  }
}
document.addEventListener('click', async (e) => {
  const t = e.target;
  if (t.closest('[data-setup]')) { openSetup(0); return; }
  const ss = t.closest('[data-setup-step]'); if (ss) { openSetup(+ss.dataset.setupStep); return; }
  const st = t.closest('[data-step]');
  if (st && t.closest('#setup')) { S.setupStep = +st.dataset.step; if (S.setupStep === 2) api('/api/setup/mark', { plan_seen: true }); if (S.setupStep === 4) api('/api/setup/mark', { permission_seen: true }); renderSetup(); return; }
  const dev = t.closest('[data-dev]');
  if (dev && t.closest('#setup')) {
    const account = dev.dataset.dev, li = t.closest('[data-dev-step]');
    if (t.dataset.devWant != null) { await api('/api/setup/dev', { account, want: t.dataset.devWant === '1' }); renderSetup(); return; }
    if (t.dataset.devTick != null) { await api('/api/setup/dev', { account, step: li.dataset.devStep, done: t.dataset.devTick === '1' }); renderSetup(); return; }
    if (t.hasAttribute('data-dev-check')) { t.textContent = 'Checking…'; await renderSetup(); return; }
    if (t.dataset.openUrl) { const u = t.dataset.openUrl; if (!bridge('openExternal', { url: u })) window.open(u, '_blank'); return; }
    if (t.dataset.devAsk) { closeSetup(); show('home'); say(t.dataset.devAsk); return; }
  }
  if (t.id === 'setup-install') { t.disabled = true; t.textContent = 'Putting Claude on this Mac'; const r = await api('/api/setup/install-claude', {}); if (r.installed) renderSetup(true); else $('#setup-log').hidden = false; }
  if (t.id === 'setup-signin') {
    t.disabled = true; t.textContent = 'Waiting for sign-in';
    const r = await api('/api/setup/signin', {});
    if (!r.ok) { toast(r.error || 'The sign-in window did not open.'); t.disabled = false; t.textContent = 'Sign in with Claude'; return; }
    const l = $('#setup-log'); l.hidden = false; l.textContent = 'Waiting for you to finish signing in, in the window that opened…';
  }
  if (t.id === 'setup-upgrade') { const u = 'https://claude.ai/upgrade'; if (!bridge('openExternal', { url: u })) window.open(u, '_blank'); }
  if (t.id === 'setup-finish' || t.id === 'setup-bring') { await api('/api/setup/mark', { onboarded: true }); closeSetup(); refresh(); if (t.id === 'setup-bring') { show('apps'); $('#bring').hidden = false; } else show('home'); }
  if (t.id === 'setup-close') closeSetup();
});

(async function start() {
  await refresh();
  await loadConversation();
  refreshProblems();
  const params = new URLSearchParams(location.search);
  if (!params.has('nolive')) listen();                                   // nolive: a still page, for screenshots and tests
  const want = params.get('view'); if (want) show(want);
  $('#say').focus();
})();
