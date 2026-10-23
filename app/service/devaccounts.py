"""The two developer accounts App Magic offers at first run: Apple (iPhone and Mac apps that open without warnings) and
Google (Sign in with Google, Drive, Sheets, Maps). Both are optional. The person says yes or not now; for a yes, the
setup sheet shows each step, checks this Mac for the ones it can see, and lets the person tick the ones it can't.

Choices and ticks live in config.json under "dev_accounts": {"apple": {"want": true, "done": ["two-factor"]}, ...}.
"""
import json, os, re, shutil, subprocess

import claude_cli, storage

ACCOUNTS = {
    "apple": {
        "title": "Apple Developer",
        "for": "Put your apps on your iPhone and in the App Store, and make Mac apps that open without a warning.",
        "cost": "$99 a year, paid to Apple",
        "capability": "make-iphone-and-mac-apps",
        "steps": [
            {"id": "two-factor", "title": "Your Apple Account has two-step sign-in",
             "how": "Apple asks for this before you can join. Most iPhones have it on already.",
             "link": "https://support.apple.com/en-us/102660", "link_title": "How to check", "implied": "xcode_account"},
            {"id": "enroll", "title": "Join the Apple Developer Program",
             "how": "Sign in with your Apple Account and enroll as an individual. Apple can take up to two days to approve you.",
             "link": "https://developer.apple.com/programs/enroll/", "link_title": "Open Apple's sign-up", "implied": "paid_certificate"},
            {"id": "xcode", "title": "Xcode is on this Mac", "check": "xcode",
             "how": "Apple's free app for making apps. It's big, so the download can take an hour.",
             "link": "macappstore://apps.apple.com/app/xcode/id497799835", "link_title": "Get Xcode"},
            {"id": "xcode-account", "title": "Xcode knows your developer account", "check": "xcode_account",
             "how": "In Xcode: Settings, Accounts, the + button, Apple Account. Sign in there yourself."},
            {"id": "certificate", "title": "This Mac can sign your apps", "check": "certificate",
             "how": "A signing certificate on this Mac. Claude can make it for you once Xcode knows your account.",
             "ask": "Make a signing certificate for my Apple Developer account on this Mac, using Xcode. Tell me if you need me to click anything."},
        ],
    },
    "google": {
        "title": "Google Developer",
        "for": "Let your apps use Sign in with Google, Google Drive, Sheets, Calendar and Maps.",
        "cost": "Free to start. Google asks before anything costs money.",
        "capability": "use-google",
        "steps": [
            {"id": "google-account", "title": "You have a Google account",
             "how": "Any Gmail address works. Use the one you want your apps to belong to.",
             "link": "https://accounts.google.com/signup", "link_title": "Make one"},
            {"id": "cloud-console", "title": "Google Cloud is switched on for that account",
             "how": "Open the Google Cloud console, sign in and accept the terms. A free project is made for you; no card is needed.",
             "link": "https://console.cloud.google.com/", "link_title": "Open Google Cloud"},
            {"id": "gcloud", "title": "Google's tool is on this Mac", "check": "gcloud",
             "how": "The command-line tool Claude uses to set up Google for your apps.",
             "ask": "Install Google's command-line tool (the Google Cloud CLI, gcloud) on this Mac. Tell me in plain words when it's done."},
            {"id": "gcloud-signed-in", "title": "This Mac is signed in to Google", "check": "gcloud_account",
             "how": "Your browser opens and asks you to approve. App Magic never sees your password.",
             "ask": "Sign this Mac in to Google with the Google Cloud CLI (gcloud auth login). Open the sign-in in my browser and wait for me to approve it."},
        ],
    },
}


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=claude_cli.env())
        return r.returncode, (r.stdout or "").strip()
    except Exception:
        return 127, ""


def _gcloud():
    found = shutil.which("gcloud", path=claude_cli.path_env())
    if found:
        return found
    for p in ("~/google-cloud-sdk/bin/gcloud", "/opt/homebrew/share/google-cloud-sdk/bin/gcloud", "/usr/local/share/google-cloud-sdk/bin/gcloud"):
        if os.path.exists(os.path.expanduser(p)):
            return os.path.expanduser(p)
    return None


def probe():
    """What this Mac shows for each checkable step: True, False, or a short detail string when it is there.
    APP_MAGIC_DEV_FAKE (JSON) stands in for the real checks in tests."""
    fake = os.environ.get("APP_MAGIC_DEV_FAKE")
    if fake:
        return json.loads(fake)
    out = {}
    rc, path = _run(["xcode-select", "-p"], 8)
    out["xcode"] = rc == 0 and ".app/" in path
    rc, teams = _run(["defaults", "read", "com.apple.dt.Xcode", "IDEProvisioningTeamByIdentifier"], 8)
    if rc != 0:
        rc, teams = _run(["defaults", "read", "com.apple.dt.Xcode", "IDEProvisioningTeams"], 8)
    out["xcode_account"] = rc == 0 and "teamID" in teams
    rc, ids = _run(["security", "find-identity", "-v", "-p", "codesigning"], 10)
    kinds = sorted(set(re.findall(r'"(Apple Development|Apple Distribution|Developer ID Application)', ids)))
    out["certificate"] = ", ".join(kinds) if kinds else False
    out["paid_certificate"] = any(k != "Apple Development" for k in kinds)        # only members get these
    g = _gcloud()
    out["gcloud"] = bool(g)
    if g:
        rc, acct = _run([g, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"], 20)
        out["gcloud_account"] = acct.splitlines()[0] if rc == 0 and acct else False
    else:
        out["gcloud_account"] = False
    return out


def _choices():
    return storage.config().get("dev_accounts") or {}


def status(seen=None):
    """Each account with its choice and each step's state: done (seen on this Mac or ticked), or to do."""
    seen = probe() if seen is None else seen
    picked, out = _choices(), []
    for key, acct in ACCOUNTS.items():
        mine = picked.get(key) or {}
        steps = []
        for s in acct["steps"]:
            found = seen.get(s["check"]) if s.get("check") else seen.get(s.get("implied", ""))
            done = bool(found) or s["id"] in (mine.get("done") or [])
            steps.append({**s, "done": done, "seen": bool(found), "detail": found if isinstance(found, str) else ""})
        out.append({"key": key, "title": acct["title"], "for": acct["for"], "cost": acct["cost"], "want": mine.get("want"),
                    "steps": steps, "ready": all(s["done"] for s in steps)})
    return out


def choose(account, want=None, step=None, done=None):
    """Record a yes / not now for an account, or a tick on a step the Mac can't see for itself."""
    if account not in ACCOUNTS:
        return {"ok": False, "error": f"There is no {account!r} account here."}
    picked = _choices()
    mine = dict(picked.get(account) or {})
    if want is not None:
        mine["want"] = bool(want)
    if step is not None:
        ids = [s["id"] for s in ACCOUNTS[account]["steps"]]
        if step not in ids:
            return {"ok": False, "error": f"There is no step {step!r}."}
        ticks = [i for i in (mine.get("done") or []) if i != step]
        if done:
            ticks.append(step)
        mine["done"] = ticks
    picked[account] = mine
    storage.set_config(dev_accounts=picked)
    _sync_capability(account)
    return {"ok": True, "accounts": status()}


def _sync_capability(account):
    """Keep the matching capability's note in step with the setup sheet, so the Capabilities page and Claude agree."""
    name = ACCOUNTS[account]["capability"]
    p = storage.where("capabilities", name) / "capability.json"
    cap = storage.read_json(p)
    if not cap:
        return
    cap["wanted"] = (_choices().get(account) or {}).get("want")
    cap["state"], cap["note"] = capability_state(account)
    storage.write_json(p, cap)


def capability_state(account):
    """Used by apps.capability_state: on when every step is done, needs-you when they said yes and steps remain."""
    acct = next(a for a in status() if a["key"] == account)
    if acct["ready"]:
        return "on", ""
    left = sum(not s["done"] for s in acct["steps"])
    if acct["want"]:
        return "needs-you", f"{left} step{'s' if left != 1 else ''} left"
    return "not-yet", ""
