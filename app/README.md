# App Magic (the app)

Version 0.3.0, 2026-10-23: notarized, with the optional developer-accounts step. (0.2.0, 2026-10-07, was the first version meant for other people.) Designs and the plan are one folder up (`../README.md`, `../PLAN.md`).

## Run it

```
./magic run        # builds if needed, then opens App Magic
./magic help       # everything else
```

The app is `build/App Magic.app`. It reads `ui/` and `service/` straight from this folder, so changes to those show up on
`./magic reload` without rebuilding.

## How it is put together

| Part | What it is |
|---|---|
| `shell/` | The Mac app (Swift, AppKit + WebKit): window, menu bar item, built-in browser pane, keeps the service running |
| `service/magicd.py` | The local service the window talks to. 127.0.0.1 only, token required, Host header checked |
| `service/claude_session.py` | The AI layer: one windowless Claude Code per conversation, on the Claude account (no API key, ever) |
| `service/tools_mcp.py` | 24 tools Claude can call: where to store things, context, remembering, apps, bringing apps in, designs, security audit, capabilities, asking the person, opening the workshop |
| `service/storage.py` | Where everything is kept. One table, one answer per kind of thing |
| `service/audit.py` | The security audit: five checks for any app, five for App Magic itself |
| `service/finder.py` | The search for apps already on the Mac, in three reported steps: look through folders, open each one, Claude reads the evidence and, for each one, judges it (yours / someone else's / a copy / not an app), suggests a plain name, and writes a short summary; then one paragraph about the whole set. Also finds or draws a real picture of each one: a picture already in the project, the page itself drawn by `AppMagic --snapshot` (no window, served through a short-lived local server), or its icon; never an invented one |
| `service/apps.py` | Listing GitHub, bringing apps in, capability states |
| `service/accounts.py` | Which AI accounts exist and how full Claude is (from Claude Code's own rate-limit notes) |
| `service/rules/` | `app-magic.md`: how the in-app Claude behaves. `workshop.md`: how the workshop Claude changes App Magic |
| `ui/` | What the person sees: plain HTML, CSS, JavaScript in the Blush look |
| `magic` | Command line: build, run, restart, reload, workshop, audit, test, selfcheck |
| `service/claude_cli.py` | The one way App Magic finds and starts Claude Code, with a clean environment (the Claude account only) |
| `service/onboarding.py` | First run: installs Claude Code with Anthropic's installer, opens the sign-in window, remembers what was seen |
| `service/problems.py` | Every error from the window, the service, Claude or the search, written down once; "Fix it with Claude" turns one into a workshop request |
| `tests/` | `./magic test`: 19 service tests on a throwaway home. `./magic uitest`: 18 end-to-end steps that click through the whole window in a headless Chrome against `tests/fake_claude.py`, a stand-in Claude that never uses anyone's account |

## Where things are stored

Everything the person makes or shows lives in `~/App Magic` (override with `APP_MAGIC_HOME`):
`Context/ Designs/ Apps/ Capabilities/ Accounts/ Changes/ Rules/ Audits/` plus `.magic/` for internals. `storage.MAP` is
the single source of truth; the in-app Claude asks the `where_to_store` tool before saving anything, and the home
folder's `CLAUDE.md` is generated from the same table. Secrets go in the Mac keychain, never in a file.

## The AI layer

`claude -p` with stream-json both ways, `--dangerously-skip-permissions`, `--setting-sources project,local` (so personal
hooks and plugins stay out and it behaves the same on every Mac), App Magic's rules appended, App Magic's tools as an MCP
server, and `CLAUDE_CODE_ARTIFACT=1` so Claude Design works headless. claude.ai
connectors on the account (Vercel, Supabase, …) come along as extra tools.

## Changing App Magic itself

"Make changes to App Magic" (footer, menu bar, or the in-app Claude's `request_app_change` tool) opens the workshop: a
separate Terminal window styled for App Magic, running interactive Claude Code in this folder with permissions bypassed.
It is separate so it can run `./magic restart` (close, rebuild, reopen the app) without cutting off its own conversation.
Each request gets a record in `~/App Magic/Changes/`.

## Sending it to someone

`./magic package` builds `../dist/App-Magic-<version>.zip`: one app for Apple silicon and Intel Macs (macOS 13 or later)
that carries its own code. On first launch it copies that code to `~/Library/Application Support/App Magic/source`,
gives it its own version history, and runs from there, so the workshop changes that person's own copy. A newer
version replaces it and keeps the old one beside it.

On their Mac, first launch goes: Apple's developer tools if missing (App Magic offers Apple's installer) → the setup
sheet: put Claude on the Mac (Anthropic's installer), sign in (a small Terminal window; their browser approves), the
plan, the two optional developer accounts (Apple Developer, Google Developer: yes or not now, then each step checked on
the Mac or ticked by them; `service/devaccounts.py`), a plain statement of what App Magic does on their Mac → home.

On a Mac with the Developer ID certificate ("Developer ID Application: OLIVER PAYNE DAHL (SBLP6WV9LG)", made 2026-10-23)
`package` signs it with the hardened runtime (`shell/AppMagic.entitlements` allows Apple Events for Terminal), sends it
to Apple's notary with the App Store Connect key in `~/.appstoreconnect`, and staples the ticket, so it opens with one
plain "Open" click. Without the certificate it falls back to ad hoc signing, and people need Privacy & Security →
"Open Anyway".

## When something goes wrong

Errors in the window, the service, Claude or the search are recorded (`~/App Magic/.magic/logs/problems.jsonl`) and
shown in a banner with "Fix it with Claude", which opens the workshop with the problem written out. If the service
itself will not start, the Mac app shows a page with the same button. Plain situations (not signed in, out of usage,
Claude busy) get a plain sentence instead of a problem.

## Not built yet

Screen recording · taking over the screen (computer use) · first-run installer (Homebrew, Xcode, sign-in, plan and
permission screens) · moving between accounts when one fills up · usage for Codex and Grok · the Ask bar from outside the
app · capability sign-in flows · sharing changes with someone else.

Checked live on 2026-10-07: the workshop window (opened over App Magic's window, Claude started with permissions
bypassed and answered), Anthropic's installer run headless into a throwaway home, the packaged app starting as a fresh
install with a bare PATH, a real Claude conversation using App Magic's tools, and the Ask bar.

Not tried live: `claude auth login` from the sign-in window (it would have touched the developer's own login), a full `/design`
run and signing in to claude.ai inside the browser pane, bringing in from GitHub, Apple's developer-tools installer
(the development Mac already has the tools).
