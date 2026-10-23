// End-to-end test of App Magic's window. Starts the real service on a throwaway home folder with a stand-in Claude
// (tests/fake_claude.py), opens the real page in a headless Chrome, and clicks through every part of it the way a
// person would. Any script error on the page fails the run. Screenshots land in the folder printed at the end.
//
//   ./magic uitest            (or: node tests/ui_test.mjs [--keep] [--only name])
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const SRC = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'appmagic-ui-'));
const HOME = path.join(TMP, 'home'), FAKE = path.join(TMP, 'fake-claude'), SHOTS = path.join(TMP, 'shots');
const PORT = 8840 + Math.floor(Math.random() * 40);
const KEEP = process.argv.includes('--keep');
const ONLY = process.argv.includes('--only') ? process.argv[process.argv.indexOf('--only') + 1] : null;
fs.mkdirSync(FAKE, { recursive: true }); fs.mkdirSync(SHOTS, { recursive: true });
fs.writeFileSync(path.join(FAKE, 'signed-out'), 'yes');            // a brand-new Mac: Claude installed, nobody signed in

const CHROMES = [
  path.join(os.homedir(), 'Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
  '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
];
const CHROME = CHROMES.find((c) => fs.existsSync(c));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0, passes = 0;
const results = [];

// ---- fixtures: a folder with things that are and are not apps ----
function git(dir, ...args) { return new Promise((r) => spawn('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', ...args], { cwd: dir }).on('exit', r)); }
async function fixtures() {
  const root = path.join(TMP, 'projects');
  const mk = (rel, files) => { const d = path.join(root, rel); fs.mkdirSync(d, { recursive: true }); for (const [f, c] of Object.entries(files)) { fs.mkdirSync(path.dirname(path.join(d, f)), { recursive: true }); fs.writeFileSync(path.join(d, f), c); } return d; };
  const a = mk('recipe-box', { 'package.json': JSON.stringify({ name: 'recipe-box', description: 'Recipes for the family' }), 'index.js': 'console.log(1)\n', 'README.md': '# Recipe box\n\nRecipes for the family.\n' });
  await git(a, 'init', '-q'); await git(a, 'add', '-A'); await git(a, 'commit', '-qm', 'first');
  mk('budget-sheet', { 'requirements.txt': 'flask\n', 'app.py': 'print(1)\n', 'helper.py': 'x=1\n', 'README.md': '# Budget\n\nA budget dashboard.\n' });
  const d = mk('tax-papers', Object.fromEntries([...Array(8)].map((_, i) => [`doc${i}.md`, 'notes'])));
  await git(d, 'init', '-q');
  return root;
}

// ---- the service ----
function startService() {
  const env = { ...process.env, APP_MAGIC_HOME: HOME, APP_MAGIC_PORT: String(PORT), APP_MAGIC_CLAUDE: path.join(SRC, 'tests/fake_claude.py'),
    FAKE_CLAUDE_STATE: FAKE, APP_MAGIC_DRY: '1', APP_MAGIC_FAST: '1',
    APP_MAGIC_DEV_FAKE: JSON.stringify({ xcode: true, xcode_account: false, certificate: false, gcloud: false, gcloud_account: false }) };
  for (const k of Object.keys(env)) if (/^(CLAUDE|ANTHROPIC)/.test(k)) delete env[k];
  const p = spawn('python3', [path.join(SRC, 'service/magicd.py')], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let err = ''; p.stderr.on('data', (d) => { err += d; });
  return new Promise((resolve, reject) => {
    p.stdout.on('data', (d) => { const m = String(d).match(/http:\/\/127\.0\.0\.1:(\d+)\/\?t=(\S+)/); if (m) resolve({ p, base: `http://127.0.0.1:${m[1]}`, token: m[2], err: () => err }); });
    setTimeout(() => reject(new Error('service did not start: ' + err)), 15000);
  });
}

// ---- the browser, driven over the DevTools protocol ----
class Page {
  async open(url) {
    const dir = path.join(TMP, 'chrome');
    this.chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${dir}`,
      '--remote-debugging-port=0', '--window-size=1280,860', 'about:blank'], { stdio: 'ignore' });
    let port;
    for (let i = 0; i < 100 && !port; i++) { await sleep(100); try { port = fs.readFileSync(path.join(dir, 'DevToolsActivePort'), 'utf8').split('\n')[0]; } catch {} }
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    this.ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
    await new Promise((r) => this.ws.addEventListener('open', r));
    this.id = 0; this.pending = new Map(); this.errors = [];
    this.ws.addEventListener('message', (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && this.pending.has(msg.id)) { this.pending.get(msg.id)(msg); this.pending.delete(msg.id); }
      if (msg.method === 'Runtime.exceptionThrown') this.errors.push('exception: ' + (msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text));
      if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') this.errors.push('console: ' + msg.params.args.map((a) => a.value ?? a.description).join(' '));
      if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error' && !/favicon|EventSource|ERR_ABORTED|net::ERR_CONNECTION_REFUSED/.test(msg.params.entry.text + msg.params.entry.url)) this.errors.push('log: ' + msg.params.entry.text + ' ' + (msg.params.entry.url || ''));
    });
    await this.send('Runtime.enable'); await this.send('Log.enable'); await this.send('Page.enable');
    await this.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 860, deviceScaleFactor: 1, mobile: false });
    await this.goto(url);
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(method + ': no answer from the browser')); }, 15000);
      this.pending.set(id, (m) => { clearTimeout(timer); m.error ? reject(new Error(method + ': ' + m.error.message)) : resolve(m.result); });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async goto(url) { await this.send('Page.navigate', { url }); await sleep(900); }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error('in page: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  }
  async waitFor(expr, what, ms = 12000) {
    const end = Date.now() + ms; let last;
    while (Date.now() < end) { try { last = await this.eval(`return (${expr})`); if (last) return last; } catch (e) { last = e.message; } await sleep(120); }
    throw new Error(`timed out waiting for ${what}` + (last ? ` (last: ${String(last).slice(0, 160)})` : ''));
  }
  click(sel) { return this.eval(`const el = document.querySelector(${JSON.stringify(sel)}); if (!el) throw new Error('no element ${sel.replace(/'/g, '')}'); el.click(); return true;`); }
  type(sel, text) { return this.eval(`const el = document.querySelector(${JSON.stringify(sel)}); el.focus(); el.value = ${JSON.stringify(text)}; el.dispatchEvent(new Event('input', {bubbles:true})); return true;`); }
  text(sel) { return this.eval(`const el = document.querySelector(${JSON.stringify(sel)}); return el ? el.textContent.trim() : null;`); }
  visible(sel) { return `(() => { const el = document.querySelector(${JSON.stringify(sel)}); return !!el && !el.closest('[hidden]') && el.getClientRects().length > 0; })()`; }
  async shot(name) { const r = await this.send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.data, 'base64')); }
  close() { try { this.ws.close(); } catch {} this.chrome.kill(); }
}

const watchToasts = () => page.eval(`window.__toasts = window.__toasts || []; new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => n.classList && n.classList.contains('toast') && window.__toasts.push(n.textContent)))).observe(document.querySelector('#toasts'), { childList: true }); return true;`);
async function step(name, fn, allow = /ui test boom/) {
  if (ONLY && !name.includes(ONLY) && name !== 'setup') return;
  const errsBefore = page.errors.length;
  try {
    await fn();
    const newErrs = page.errors.slice(errsBefore).filter((e) => !allow.test(e) && !/ui test boom/.test(e));
    if (newErrs.length) throw new Error('page errors: ' + newErrs.join(' | '));
    passes++; results.push(`  ok    ${name}`); console.log(`  ok    ${name}`);
  } catch (e) {
    try { const t = await page.eval(`return (window.__toasts || []).slice(-4).join(' / ')`); if (t) e.message += ` [recent toasts: ${t}]`; } catch {}
    if (process.env.UI_DEBUG) try { e.message += ' [events: ' + await page.eval(`return (S.events || []).slice(-25).join(' ')`) + ']'; } catch {}
    failures++; results.push(`  FAIL  ${name}: ${e.message}`); console.log(`  FAIL  ${name}: ${e.message}`);
    try { await page.shot('FAIL-' + name.replace(/\W+/g, '-')); } catch {}
  }
}
const send = async (text) => { await page.type('#say', text); await page.eval(`document.querySelector('#composer').requestSubmit(); return true;`); };
const lastMagic = `([...document.querySelectorAll('#thread .msg.magic')].pop()?.textContent || '')`;   // in brackets: it is used as `${lastMagic}.includes(…)`
const idle = `document.querySelector('#doing').hidden`;

// ---- the run ----
const projects = await fixtures();
let svc = await startService();
const page = new Page();
await page.open(`${svc.base}/?t=${svc.token}`);
await watchToasts();

await step('setup', async () => {
  await page.waitFor(page.visible('#setup'), 'the setup sheet on a fresh Mac');
  await page.waitFor(`document.querySelector('#setup-body').textContent.includes('Not signed in yet')`, 'not signed in yet');
  await page.shot('01-setup-claude');
  await page.click('#setup-signin');
  await page.waitFor(`document.querySelector('#setup-log') && !document.querySelector('#setup-log').hidden`, 'the waiting note');
  fs.writeFileSync(path.join(FAKE, 'signed-in'), 'yes'); fs.rmSync(path.join(FAKE, 'signed-out'), { force: true });
  await page.waitFor(`document.querySelector('#setup-body').textContent.includes('Signed in as sarah@example.com')`, 'signed in', 15000);
  await page.click('#setup [data-step="1"]');
  await page.waitFor(`document.querySelector('#setup-body').textContent.includes('Claude Max')`, 'the plan step');
  await page.shot('02-setup-plan');
  await page.click('#setup .btn[data-step="2"]');
  await page.waitFor(`document.querySelector('#setup-body').textContent.includes('Two accounts worth having')`, 'the accounts step');
  await page.click('#setup [data-dev="apple"] [data-dev-want="1"]');
  await page.waitFor(`!!document.querySelector('#setup [data-dev="apple"] [data-dev-step="xcode"].ok') && document.querySelector('#setup [data-dev="apple"]').textContent.includes('Found on this Mac')`, 'Xcode seen on this Mac');
  await page.click('#setup [data-dev="apple"] [data-dev-step="enroll"] [data-dev-tick="1"]');
  await page.waitFor(`!!document.querySelector('#setup [data-dev="apple"] [data-dev-step="enroll"].ok')`, 'the ticked step');
  await page.click('#setup [data-dev="google"] [data-dev-want="0"]');
  await page.waitFor(`document.querySelector('#setup [data-dev="google"]').classList.contains('off')`, 'Google left for later');
  await page.shot('02b-setup-accounts');
  await page.click('#setup .btn[data-step="3"]');
  await page.waitFor(`document.querySelector('#setup-body').textContent.includes('whole Mac')`, 'the permission step');
  await page.click('#setup [data-step="4"]');
  await page.waitFor(`!!document.querySelector('#setup-finish')`, 'the last step');
  await page.click('#setup-finish');
  await page.waitFor(`document.querySelector('#setup').hidden`, 'the sheet to close');
  const st = await page.eval(`return (await (await fetch('/api/state')).json()).onboarded`);
  if (!st) throw new Error('onboarded was not saved');
});

await step('conversation', async () => {
  await send('Hello there');
  await page.waitFor(`${lastMagic}.includes('Fake Claude heard: Hello there') && ${idle}`, 'a reply', 15000);
  if (process.env.UI_DEBUG) console.log('events:', await page.eval(`return (S.events || []).join(' ')`));
  if (await page.eval(`return !document.querySelector('#streaming')`) !== true) throw new Error('the streaming bubble stayed behind');
  await page.shot('03-home-conversation');
});

await step('tools', async () => {
  await send(`TOOL:remember {"fact": "Likes big text"} TOOL:show_user {"title": "Here's what I saw", "bullets": ["You open an email", "You copy the amount"], "actions": ["Yes, design that", "Not quite"]}`);
  await page.waitFor(`[...document.querySelectorAll('#thread .msg.card h4')].some((h) => h.textContent.includes("Here's what I saw"))`, 'the card from show_user', 15000);
  await page.waitFor(`${idle}`, 'the turn to end', 15000);
  await page.waitFor(`[...document.querySelectorAll('#know .row')].some((r) => r.textContent.includes('Things I know about you') && r.textContent.includes('1'))`, 'the remembered fact counted', 8000);
  await page.click('#thread .msg.card .choices button');
  await page.waitFor(`${lastMagic}.includes('Yes, design that')`, 'the button reply', 15000);
  await page.waitFor(idle, 'idle');
});

await step('design', async () => {
  await send('DESIGN a recipe box for my family');
  await page.waitFor(`!!document.querySelector('#thread .msg.design')`, 'a design card in the chat', 15000);
  await page.waitFor(idle, 'idle', 15000);
  const cards = await page.eval(`return [...document.querySelectorAll('#thread .msg.design')].map((c) => c.dataset.url)`);
  if (cards.length !== 1 || !/FakeCanvas123/.test(cards[0])) throw new Error('expected one card for the published canvas, got: ' + cards.join(', '));
  if (!(await page.eval(`return document.querySelector('#design').hidden && document.querySelector('#view-home').classList.contains('on')`))) throw new Error('the design took over the window by itself');
  if (await page.eval(`return !!document.querySelector('.mode')`)) throw new Error('the Message/Design switch is still there');
  await page.shot('04a-design-card');
  await page.click('#thread .msg.design .design-open');
  await page.waitFor(page.visible('#design'), 'the design pop-up to open');
  const href = await page.eval(`return document.querySelector('#browser-link').getAttribute('href')`);
  if (!/FakeCanvas123/.test(href)) throw new Error('canvas link not set: ' + href);
  await page.waitFor(idle, 'idle', 15000);
  await page.shot('04-design');
  await page.type('#change-text', 'make the text bigger');
  await page.eval(`document.querySelector('#change').requestSubmit(); return true;`);
  await page.waitFor(`${lastMagic}.includes('make the text bigger')`, 'the change to reach Claude', 15000);
  await page.click('#design-close');
  await page.waitFor(`document.querySelector('#design').hidden`, 'back to the chat');
});

await step('canvas-sign-in-help', async () => {
  await page.eval(`window.magicNative.show('design'); return true;`);
  await page.waitFor(page.visible('#design'), 'the design pop-up');
  await page.eval(`window.magicNative.browserAt('https://accounts.google.com/v3/signin/identifier?continue=x'); return true;`);
  await page.waitFor(page.visible('#signin-help'), 'the sign-in help on a Google sign-in page');
  await page.shot('04b-sign-in-help');
  await page.eval(`window.magicNative.browserAt('https://claude.ai/login?returnTo=x'); return true;`);
  if (!(await page.eval(`return !document.querySelector('#signin-help').hidden`))) throw new Error('help hidden on the claude.ai login page');
  await page.eval(`window.magicNative.browserAt('https://claude.ai/artifact/FakeCanvas123'); return true;`);
  await page.waitFor(`document.querySelector('#signin-help').hidden`, 'the help to go away once signed in');
  await page.click('#design-close');
  await page.waitFor(`document.querySelector('#design').hidden`, 'back to the chat');
});

await step('claude-crash-offers-fix', async () => {
  await page.waitFor(idle, 'idle');
  await send('CRASH please');
  await page.waitFor(`[...document.querySelectorAll('#thread .msg.error')].some((m) => m.querySelector('[data-fix]'))`, 'an error with Fix it with Claude', 15000);
  await page.waitFor(page.visible('#problem'), 'the problem banner');
  await page.shot('05-problem-banner');
  await page.click('#problem-fix');
  await page.waitFor(`[...document.querySelectorAll('.toast')].some((t) => t.textContent.includes('test mode'))`, 'the workshop opening (test mode)');
  const rec = fs.readdirSync(path.join(HOME, 'Changes')).filter((f) => f.endsWith('.md'));
  if (!rec.length || !fs.readFileSync(path.join(HOME, 'Changes', rec.at(-1)), 'utf8').includes('Claude stopped')) throw new Error('the fix request did not describe the problem');
});

await step('signed-out-message', async () => {
  await page.waitFor(idle, 'idle');
  await send('SIGNEDOUT check');
  await page.waitFor(`[...document.querySelectorAll('#thread .msg.error')].some((m) => m.textContent.includes("isn't signed in") && m.querySelector('[data-setup]'))`, 'the signed-out message with Open setup', 15000);
  await page.click('#setup-close').catch(() => {});
  await page.eval(`document.querySelector('#setup').hidden = true; return true;`);
});

await step('stop', async () => {
  await page.waitFor(idle, 'idle', 15000);
  await send('SLOW one');
  await page.waitFor(page.visible('#stop'), 'the stop button');
  await page.click('#stop');
  await page.waitFor(idle, 'stopped', 15000);
});

await step('background-work', async () => {
  await send('BACKGROUND please');
  await page.waitFor(`document.querySelector('#doing .now').textContent.includes('Writing index.html')`, 'the file being written to show', 20000);
  await page.waitFor(`document.querySelector('#doing .now').textContent.includes('KB so far')`, 'the size so far', 8000);
  await page.waitFor(`${lastMagic}.includes('Both are running') && ${idle} && document.querySelectorAll('#bg li').length === 2`, 'the reply with two jobs still running', 15000);
  await page.waitFor(`document.querySelector('#bg li small')?.textContent.includes('sheetA.jpg')`, 'the helper step on its line', 8000);
  await page.shot('04b-background-jobs');
  if (await page.eval(`return document.querySelector('#thread').textContent.includes('HELPER-ONLY')`)) throw new Error("a helper's words showed as App Magic's reply");
  if (!await page.eval(`return [...document.querySelectorAll('#thread .steps li')].some((li) => li.textContent.includes('Writing index.html'))`)) throw new Error('the step is not in the conversation');
  await page.waitFor(`${lastMagic}.includes('pictures look fine')`, 'the turn the finished helper started', 15000);
  await page.waitFor(`${lastMagic}.trim() === 'The clips are rendered too.' && ${idle} && document.querySelector('#bg').hidden`, 'the last job to finish', 15000);
  if (process.env.UI_DEBUG) console.log('events:', await page.eval(`return (S.events || []).join(' ')`), await page.eval(`return document.querySelector('#thread').children.length + ' ' + JSON.stringify(R && {el: !!R.el, final: R.final, shown: R.shown, raf: R.raf})`));
  const notes = await page.eval(`return [...document.querySelectorAll('#thread .bgnote')].map((x) => x.className).join(',')`);
  if ((notes.match(/started/g) || []).length !== 2 || (notes.match(/done/g) || []).length !== 2) throw new Error('background notes: ' + notes + ' / ' + await page.eval(`return [...document.querySelector('#thread').children].slice(-9).map((x) => x.className + ':' + x.textContent.slice(0, 40)).join(' | ')`));
  if (await page.eval(`return document.querySelector('.topbar nav [data-view="home"]').classList.contains('working')`)) throw new Error('Home still shows work going on');
});

await step('ask', async () => {
  await page.click('#ask-open');
  await page.waitFor(page.visible('#ask-text'), 'the Ask bar');
  await page.type('#ask-text', 'how much Claude do I have left?');
  await page.eval(`document.querySelector('#ask-form').requestSubmit(); return true;`);
  await page.waitFor(`document.querySelector('#ask-answer').textContent.includes('Fake Claude heard')`, 'an answer in the Ask bar', 15000);
  await page.shot('06-ask');
  await page.eval(`document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return true;`);
  await page.waitFor(`document.querySelector('#ask').hidden`, 'Ask to close');
});

await step('find-and-bring-in', async () => {
  await page.click('.topbar nav button[data-view="apps"]');
  await page.click('#bring-open');
  await page.type('#where', projects);
  await page.click('#where-go');
  await page.waitFor(`!!document.querySelector('.scan.done')`, 'the search to finish', 30000);
  const names = await page.eval(`return [...document.querySelectorAll('#found input.nm')].map((i) => i.value)`);
  if (!names.some((n) => n.startsWith('Fake '))) throw new Error('Claude-suggested names missing: ' + names.join(', '));
  if (!(await page.text('.overview'))) throw new Error('no overview paragraph');
  await page.shot('07-search-done');
  const ticked = await page.eval(`return document.querySelectorAll('#found input[type=checkbox]:checked').length`);
  if (ticked !== 2) throw new Error(`expected 2 ticked (the two apps), got ${ticked}`);
  await page.eval(`const n = document.querySelector('#found input.nm'); n.value = 'Family Recipes'; return true;`);
  await page.click('#bring-go');
  await page.waitFor(`[...document.querySelectorAll('#apps-table .tr b')].some((b) => b.textContent === 'Family Recipes')`, 'the renamed app in the list', 20000);
  await page.waitFor(`document.querySelectorAll('#apps-table .tr:not(.th)').length === 2`, 'both apps listed', 10000);
  await page.shot('08-apps');
});

await step('audit-and-fix-these', async () => {
  await page.click('#apps-table [data-audit]');
  await page.waitFor(page.visible('#audit-modal .modal'), 'the safety report', 20000);
  const n = await page.eval(`return document.querySelectorAll('#audit-checks .check').length`);
  if (n !== 5) throw new Error(`expected 5 checks, got ${n}`);
  await page.shot('09-audit');
  const canFix = await page.eval(`return !document.querySelector('#audit-fix').hidden`);
  if (canFix) { await page.click('#audit-fix'); await page.waitFor(`${lastMagic}.includes('safety checks')`, 'the fix request to reach Claude', 15000); await page.waitFor(idle, 'idle', 15000); }
  else await page.eval(`document.querySelector('#audit-modal').hidden = true; return true;`);
});

await step('capabilities', async () => {
  await page.click('.topbar nav button[data-view="capabilities"]');
  await page.waitFor(`document.querySelectorAll('#caps .tile').length >= 5`, 'the tiles');
  await page.shot('10-capabilities');
  await page.click('#caps button.tile[data-setup-step]');                 // Apple: they said yes, so it needs them
  await page.waitFor(`document.querySelector('#setup-body').textContent.includes('Two accounts worth having')`, 'the accounts step from Capabilities');
  await page.click('#setup-close');
  const hasButton = await page.eval(`return !!document.querySelector('#caps button.tile[data-say]')`);
  if (hasButton) { await page.click('#caps button.tile[data-say]'); await page.waitFor(`${lastMagic}.includes('Help me set up')`, 'set-up request to reach Claude', 15000); await page.waitFor(idle, 'idle', 15000); await page.click('.topbar nav button[data-view="capabilities"]'); }
  await page.click('#self-audit');
  await page.waitFor(`document.querySelector('#audit-target').textContent.includes('App Magic') && ${page.visible('#audit-modal .modal')}`, 'the self-check report', 15000);
  await page.eval(`document.querySelector('#audit-modal').hidden = true; return true;`);
  await page.type('#learn-text', 'text me when an order comes in');
  await page.eval(`document.querySelector('#learn').requestSubmit(); return true;`);
  await page.waitFor(`${lastMagic}.includes('text me when an order comes in')`, 'the learn request', 15000);
  await page.waitFor(idle, 'idle', 15000);
});

await step('accounts', async () => {
  await page.click('.topbar nav button[data-view="accounts"]');
  await page.waitFor(`document.querySelector('#accts').textContent.includes('sarah@example.com')`, 'the Claude account card');
  await page.waitFor(`!!document.querySelector('#accts .meter')`, 'a usage meter (from the rate-limit notes)');
  await page.shot('11-accounts');
});

await step('change-app-magic', async () => {
  await page.click('#change-open');
  await page.type('#change-request', 'Add a weekly summary button');
  await page.click('#change-go');
  await page.waitFor(`[...document.querySelectorAll('.toast')].some((t) => t.textContent.includes('Workshop (test mode)'))`, 'the workshop (test mode)');
});

await step('window-error-reported', async () => {
  await page.eval(`setTimeout(() => { throw new Error('ui test boom'); }); return true;`);
  await page.waitFor(`${page.visible('#problem')} && document.querySelector('#problem-what').textContent.includes('ui test boom')`, 'the banner for a page error');
  await page.click('#problems-link');
  await page.waitFor(`document.querySelectorAll('#problems-list .check').length >= 2`, 'the problems list');
  await page.shot('12-problems');
  await page.click('#problems-clear');
  await page.waitFor(`document.querySelector('#problem').hidden && document.querySelector('#problems-link').hidden`, 'all dismissed');
});

await step('drop-a-file', async () => {
  await page.click('.topbar nav button[data-view="home"]');
  await page.waitFor(idle, 'idle', 15000);
  await page.eval(`addFiles([new File(['name,amount\\nA,1\\n'], 'orders.csv', { type: 'text/csv' })]); return true;`);
  await page.waitFor(`[...document.querySelectorAll('#thread .msg.you')].some((m) => m.textContent.includes('Dropped in: orders.csv'))`, 'the drop to show', 10000);
  await page.waitFor(`[...document.querySelectorAll('#know .row')].some((r) => r.textContent.includes('Files and notes') && r.textContent.includes('1'))`, 'the file counted in What I know', 10000);
});

await step('menubar-page', async () => {
  await page.goto(`${svc.base}/menubar`);
  await page.waitFor(`document.querySelectorAll('#plans .plan').length >= 1`, 'plans and usage');
  await page.shot('13-menubar');
  await page.goto(`${svc.base}/`);
  await page.waitFor(`!!document.querySelector('#thread .msg')`, 'the main page again');
});

await step('service-restart-reloads-window', async () => {
  const boot = await page.eval(`return S.boot`);
  svc.p.kill('SIGTERM'); await sleep(1200);
  svc = await startService();
  await page.waitFor(`typeof S !== 'undefined' && S.boot && S.boot !== ${JSON.stringify(boot)}`, 'the window to reload on the new service', 20000);
  await page.waitFor(`document.querySelectorAll('#thread .msg').length >= 1 && !!S.state`, 'the page to come back');
});

await step('service-errors-become-problems', async () => {
  const r = await page.eval(`return await api('/api/apps/audit', { name: 'nope' })`);
  if (!r.error) throw new Error('expected a plain error for a missing app');
  const bad = await page.eval(`const r = await fetch('/api/context/remove', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{"id": 5}'}); return r.status`);
  if (bad < 500) throw new Error('a malformed request should have been caught and recorded, got HTTP ' + bad);
  await page.waitFor(page.visible('#problem'), 'a banner for a service error');
  await page.shot('14-service-problem');
}, /status of (400|500)/);

const fatal = page.errors.filter((e) => !/ui test boom/.test(e));
page.close(); svc.p.kill('SIGTERM');
console.log(`\nApp Magic window test: ${passes} passed, ${failures} failed\n` + results.join('\n'));
if (fatal.length) console.log('\nPage errors:\n  ' + fatal.join('\n  '));
console.log(`\nScreenshots: ${SHOTS}`);
if (!KEEP && !failures) fs.rmSync(path.join(TMP, 'chrome'), { recursive: true, force: true });
process.exit(failures ? 1 : 0);
