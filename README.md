# App Magic

A Mac app for making your own apps and dashboards without writing code. You tell it what you want, or show it with
files, photos and screen recordings, and Claude designs and builds it on your Mac: saved properly, every version kept,
private by default, and checked for safety before anything goes on the web.

It is a friendly front end over real developer tools (Claude Code, git, GitHub, Xcode, Vercel and others), run on your
own Claude account.

## Install it

1. Download **App-Magic-0.3.0.zip** from the [latest release](https://github.com/oliverpdahl/app-magic/releases/latest).
2. Unzip it and drag **App Magic** into your Applications folder.
3. Open it. It is signed and notarized by Apple, so your Mac asks once whether to open an app from the internet: click **Open**.
4. App Magic walks you through the rest: putting Claude on your Mac, signing in, your plan, two optional developer
   accounts (Apple for iPhone apps, Google for Sign in with Google, Drive and Sheets; "Not now" skips either), and
   what it will do on your Mac.

You need a Mac with macOS 13 or later and a Claude account ([claude.ai](https://claude.ai)). Claude Max is recommended:
designing and building apps is heavy work.

**If the download gives you trouble**, download this repository instead (Code → Download ZIP), open the folder in
Claude Code, and ask Claude to build and install App Magic for you. `app/README.md` tells it how.

## What it does on your Mac

App Magic uses your whole Mac. Claude works in your `~/App Magic` folder, can read and change files, and runs programs
without stopping to ask each time. It asks first before spending money, publishing anything, deleting things or sending
messages as you, and it never types a password. Everything runs on your own Claude account; there is no separate API key.

## Changing App Magic

App Magic is yours to change. "Make changes to App Magic" opens a workshop window where Claude edits App Magic's own
code on your Mac. Every change is saved, so it can be undone.

## For developers

The code is in [`app/`](app/): a small Swift shell (window, menu bar, built-in browser), a local Python service
(standard library only), and a plain HTML/CSS/JS interface. Start with [`app/README.md`](app/README.md).

```
cd app
./magic run        # build and open it
./magic test       # service tests
./magic uitest     # click through the whole window against a stand-in Claude
./magic package    # the zip to send (signed and notarized when a Developer ID is on the Mac)
```

## License

MIT. See [LICENSE](LICENSE).
