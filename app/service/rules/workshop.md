# The App Magic workshop

You are Claude Code, working on App Magic itself. The person asked for a change to their own copy of App Magic. You are in
its source folder, in a terminal window that is separate from the app on purpose: you can close, rebuild and reopen App
Magic from here without losing this conversation.

The person may not be a developer. Say what you are going to change in one or two plain sentences, make the change, show
that it works, and say how to undo it.

## How App Magic is put together

- `ui/` is everything the person sees: `index.html`, `app.css`, `app.js` (the window) and `menubar.html` (the menu bar
  panel). Plain HTML, CSS and JavaScript, no build step. Look: the Blush tokens at the top of `app.css` (warm white,
  plum ink, one rose accent). One accent action per screen, plain words, top bar on every page.
- `service/` is everything App Magic does, in standard-library Python:
  `magicd.py` (the local service the window talks to), `claude_session.py` (the AI layer: Claude Code on the person's
  Claude account, never an API key), `tools_mcp.py` (the tools Claude can call), `storage.py` (where everything is
  kept), `audit.py` (the security audit), `apps.py` (finding and bringing in apps, capabilities), `accounts.py`,
  and `rules/app-magic.md` (how the in-app Claude behaves).
- `shell/` is the Mac app in Swift: the window, the menu bar item, the built-in browser pane, keeping the service
  running, and the two pages that work without the service (`ui/setup-tools.html`, `ui/broken.html`). Keep it thin.
- `magic` is the command line (`./magic help`). `tests/test_service.py` tests the service; `tests/ui_test.mjs` drives the
  whole window in a headless browser against a stand-in Claude (`tests/fake_claude.py`) when Node and a test Chrome are
  on this Mac.
- `service/problems.py` keeps every error App Magic hit, in `.magic/logs/problems.jsonl` in the home folder. When the
  workshop was opened with "Fix it with Claude", the request names the problem and its details: start there. The service
  log is `.magic/logs/service.log`; Claude's own errors are in `.magic/logs/claude-<conversation>.log`.
- The person's data is not here. It is in their App Magic home folder (default `~/App Magic`): Context, Designs, Apps,
  Capabilities, Accounts, Changes, Rules, Audits. `storage.py` is the single source of truth for that layout.

## Making a change

1. Find the smallest change that does what was asked. Match the code and the look that is already there.
2. A new ability for the in-app Claude is usually a new tool in `tools_mcp.py` (add it to `TOOLS`), plus a line in
   `rules/app-magic.md` saying when to use it.
3. Run `./magic test`. Add a test for anything with logic in it.
4. Put the change in front of them:
   - changed only `ui/` or `service/`: `./magic reload` (the service restarts and the window reloads itself);
   - changed `shell/` or `Info.plist`: `./magic rebuild` (rebuilds the Mac app and reopens it; needs Apple's tools).
   `./magic restart` just closes and reopens App Magic. Run `./magic uitest` if it can run here.
5. Save it as a version: `git add -A && git commit` in this folder with a plain message. If this folder is not a git
   repository yet, `git init` first. Never push anywhere unless asked.
6. Write it down: fill in the change record in the home folder's `Changes/` (the path is given below when the workshop
   was opened from the app): what changed in plain words, and how to undo it (`git revert <commit>` then
   `./magic restart`).

## Lines not to cross without being asked in so many words

- The service answers only on 127.0.0.1, needs its token, and checks the Host header. Do not loosen that.
- The AI layer uses the Claude account only. Do not add API keys or other providers' keys to any file.
- `Rules/safety.md` in the home folder (ask first before spending, publishing, deleting, sending; never type a password)
  and the five-check security audit stay in force.
- Secrets never go in files. They go in the Mac keychain.
- Do not delete or rewrite the person's home folder contents to make a code change easier.
