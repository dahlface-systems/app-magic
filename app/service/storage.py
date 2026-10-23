"""Where App Magic keeps everything.

One folder (default ~/App Magic, override with APP_MAGIC_HOME) holds all of it. Every other module asks this one for a
path instead of building its own, so "where does this go" has exactly one answer.
"""
import json, os, re, secrets, shutil, threading, time
from pathlib import Path

HOME = Path(os.environ.get("APP_MAGIC_HOME") or (Path.home() / "App Magic"))
SRC = Path(__file__).resolve().parent.parent          # the app's own source folder (service/, ui/, shell/)
INTERNAL = HOME / ".magic"

# kind -> (folder, what goes there). This table is the single source of truth; CLAUDE.md is written from it.
MAP = {
    "context":      ("Context",      "Everything the person has shown or told App Magic: recordings, photos, videos, files, notes. One folder per item with the original, a plain summary and meta.json."),
    "designs":      ("Designs",      "One folder per app design. The Claude Design sources (project/canvas.json and one .dc.html per screen) live in <name>/project. design.json records the canvas link."),
    "apps":         ("Apps",         "One folder per app: the real code. Each is its own git repository. apps.json is the registry of every app, made here or brought in."),
    "capabilities": ("Capabilities", "One folder per capability: capability.json (name, state), instructions.md (when and how to use it), lessons.md (what went wrong before), selftest.sh."),
    "accounts":     ("Accounts",     "accounts.json: which logins exist and how full each one is. Never a password, token or key; those stay in the Mac keychain."),
    "changes":      ("Changes",      "One markdown file per change the person asked for to App Magic itself: what was asked, what changed, how to undo it."),
    "rules":        ("Rules",        "about-you.md (what App Magic has learned about the person), safety.md (what must be asked first), lessons.md (general lessons)."),
    "audits":       ("Audits",       "Security audit reports, one folder per app (or 'app-magic' for the audit of App Magic itself), newest file last."),
    "scratch":      (".magic/scratch", "Throwaway working files. Anything here can be deleted at any time."),
}


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:60] or "untitled"


def where(kind, name=None):
    """The folder for a kind of thing; with a name, that thing's own folder (created)."""
    if kind not in MAP:
        raise KeyError(f"unknown kind {kind!r}; known kinds: {', '.join(MAP)}")
    p = HOME / MAP[kind][0]
    if name:
        p = p / slug(name)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {} if default is None else default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}-{secrets.token_hex(3)}.tmp")    # one per writer: two threads can save at once
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def append_line(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(text.rstrip("\n") + "\n")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def config():
    return read_json(INTERNAL / "config.json")


def set_config(**kw):
    c = config()
    c.update(kw)
    write_json(INTERNAL / "config.json", c)
    return c


def token():
    p = INTERNAL / "token"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_urlsafe(32))
        os.chmod(p, 0o600)
    return p.read_text().strip()


SAFETY = """# What App Magic must ask before doing

Ask the person first, every time, before any of these. Use the ask_user tool, then stop and wait for the answer.

- Spending money, or signing up for anything that will cost money.
- Publishing something so that people other than the person can open it.
- Deleting anything that is not in .magic/scratch.
- Sending a message, email or post as the person.
- Typing a password. The person types passwords; App Magic never does.

Before anything leaves this Mac, run security_audit on it and show the result.
"""

ABOUT_YOU = """# What App Magic knows about you

Nothing yet. This file grows as you show and tell App Magic things. You can read it, edit it, or delete any line.
"""

SEED_CAPABILITIES = {
    "save-every-version": {"title": "Save every version", "service": "GitHub", "check": "gh",
                           "instructions": "Use git for every app in Apps/. Commit after each change the person keeps. Use the gh CLI for GitHub: `gh repo list`, `gh repo clone`, `gh repo create --private`. New repositories are always private unless the person says otherwise."},
    "use-my-mac": {"title": "Use my Mac for me", "service": "Screen, mouse, keyboard, files", "check": "always",
                   "instructions": "You can run commands and read and write files on this Mac. Say what you are about to do before doing anything the person would notice on screen."},
    "put-things-on-the-web": {"title": "Put things on the web", "service": "Vercel", "check": "vercel",
                              "instructions": "Use the Vercel tools or the vercel CLI. Every deployment gets sign-in protection. Show the price before anything paid."},
    "remember-information": {"title": "Remember information", "service": "Supabase", "check": "supabase",
                             "instructions": "Use the Supabase tools. Row level security is always on. Service keys never go in app code; they go in the keychain."},
    "make-iphone-and-mac-apps": {"title": "Make iPhone and Mac apps", "service": "Apple Developer", "check": "apple",
                                 "instructions": "Use xcodebuild. Signing needs the person's Apple Developer membership ($99 a year): ask first. The setup sheet's Accounts step walks them through joining; send them there (Open setup) rather than explaining it again."},
    "use-google": {"title": "Use Google", "service": "Google Developer", "check": "google",
                   "instructions": "Use the gcloud CLI for Google Cloud: projects, APIs (Drive, Sheets, Calendar, Maps), OAuth clients for Sign in with Google. Anything that costs money needs a yes first. Keys and client secrets go in the keychain. If gcloud is missing or signed out, the setup sheet's Accounts step walks the person through it."},
    "claude-design": {"title": "Design screens", "service": "Claude Design", "check": "always",
                      "instructions": "Design with the Artifact tool (action quickstart, intent design), or the /design command. Keep the sources in Designs/<name>/ and call design_register when the canvas exists."},
}


def version_source():
    """Give an installed copy of App Magic's code its own version history, so every change the workshop makes can be
    undone. A copy that already sits inside a repository (a developer's checkout) is left alone."""
    import subprocess
    run = lambda *a: subprocess.run(["git", "-C", str(SRC), *a], capture_output=True, text=True, timeout=60)   # noqa: E731
    try:
        if run("rev-parse", "--is-inside-work-tree").returncode == 0:
            return False
        if not os.access(SRC, os.W_OK):
            return False
        run("init", "-q")
        run("config", "user.name", "App Magic"); run("config", "user.email", "app-magic@this-mac.local")
        (SRC / ".gitignore").exists() or (SRC / ".gitignore").write_text("build/\nshell/.build/\n__pycache__/\n*.pyc\n.DS_Store\n")
        run("add", "-A")
        v = (SRC / "VERSION").read_text().strip() if (SRC / "VERSION").exists() else "?"
        return run("commit", "-qm", f"App Magic {v}, as installed").returncode == 0
    except Exception:
        return False


def write_claude_md():
    rows = "\n".join(f"- `{folder}/` ({kind}): {what}" for kind, (folder, what) in MAP.items())
    (HOME / "CLAUDE.md").write_text(f"""# App Magic's home folder

You are working inside App Magic's home folder, `{HOME}`. Everything App Magic knows and makes lives here, and each kind of
thing has exactly one place. Ask the `where_to_store` tool whenever you are about to save something; never invent a new
top-level folder and never save into the person's Desktop, Documents or Downloads unless they ask for that.

{rows}

Secrets (passwords, tokens, API keys) never go in any file here. They belong in the Mac keychain
(`security add-generic-password -s "App Magic <name>" -a "$USER" -w`), and files refer to them by name.

Read `Rules/safety.md` and `Rules/about-you.md` at the start of a job. Write down what you learn with the `remember` tool.

App Magic's own program code is not in this folder. It is at `{SRC}`. Do not edit it from here: when the person wants App
Magic itself changed, call `request_app_change`, which opens the workshop window made for that.
""")


def ensure():
    """Create the whole layout. Safe to call on every start."""
    HOME.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(HOME, 0o700)
    except OSError:
        pass
    for kind in MAP:
        where(kind)
    for sub in ("conversations", "logs", "scratch", "uploads"):
        (INTERNAL / sub).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(INTERNAL, 0o700)
    except OSError:
        pass
    token()
    write_claude_md()
    rules = where("rules")
    if not (rules / "safety.md").exists():
        (rules / "safety.md").write_text(SAFETY)
    if not (rules / "about-you.md").exists():
        (rules / "about-you.md").write_text(ABOUT_YOU)
    if not (rules / "lessons.md").exists():
        (rules / "lessons.md").write_text("# Lessons\n\nThings that went wrong once and how to do them properly.\n")
    for name, cap in SEED_CAPABILITIES.items():
        d = where("capabilities", name)
        if not (d / "capability.json").exists():
            write_json(d / "capability.json", {"name": name, "title": cap["title"], "service": cap["service"],
                                               "check": cap["check"], "state": "not-yet", "note": ""})
            (d / "instructions.md").write_text(f"# {cap['title']}\n\n{cap['instructions']}\n")
            (d / "lessons.md").write_text(f"# Lessons: {cap['title']}\n")
        elif cap["check"] in ("apple", "google"):                 # older copies checked only for Xcode
            old = read_json(d / "capability.json")
            if old.get("check") != cap["check"]:
                write_json(d / "capability.json", {**old, "check": cap["check"]})
                (d / "instructions.md").write_text(f"# {cap['title']}\n\n{cap['instructions']}\n")
    if not (where("apps") / "apps.json").exists():
        write_json(where("apps") / "apps.json", {"apps": []})
    # Claude Code settings for sessions that run in this folder. Personal settings are not loaded (see claude_session.py),
    # so this file is the whole configuration of App Magic's Claude.
    settings = HOME / ".claude" / "settings.json"
    if not settings.exists():
        write_json(settings, {"permissions": {"defaultMode": "bypassPermissions"}, "skipDangerousModePermissionPrompt": True})
    if "created" not in config():
        set_config(created=now(), source_dir=str(SRC))
    else:
        set_config(source_dir=str(SRC))
    return HOME


# ---- context library ---------------------------------------------------------------------------------
def context_add(title, summary="", kind="note", path=None, text=None, source=""):
    cid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    d = where("context") / cid
    d.mkdir(parents=True, exist_ok=True)
    original = ""
    if path:
        src = Path(path).expanduser()
        if src.exists() and src.is_file():
            dst = d / ("original" + src.suffix.lower())
            shutil.copy2(src, dst)
            original = dst.name
            if not title:
                title = src.name
    if text:
        (d / "note.md").write_text(text)
    if summary:
        (d / "summary.md").write_text(summary.strip() + "\n")
    meta = {"id": cid, "title": title or "Untitled", "kind": kind, "original": original, "source": source or (str(path) if path else ""),
            "added": now(), "summary": summary.strip()}
    write_json(d / "meta.json", meta)
    return meta


def context_list():
    out = []
    for d in sorted(where("context").iterdir(), reverse=True):
        m = read_json(d / "meta.json")
        if m:
            out.append(m)
    return out


def context_update(cid, **fields):
    d = where("context") / cid
    m = read_json(d / "meta.json")
    if not m:
        raise KeyError(cid)
    m.update(fields)
    if "summary" in fields:
        (d / "summary.md").write_text(fields["summary"].strip() + "\n")
    write_json(d / "meta.json", m)
    return m


def context_remove(cid):
    d = where("context") / cid
    if d.exists() and d.parent == where("context"):
        shutil.rmtree(d)
        return True
    return False


# ---- apps registry -----------------------------------------------------------------------------------
def apps():
    return read_json(where("apps") / "apps.json", {"apps": []}).get("apps", [])


def app_get(name):
    s = slug(name)
    for a in apps():
        if a.get("slug") == s or a.get("name", "").lower() == (name or "").lower():
            return a
    return None


def app_save(entry):
    entry["slug"] = entry.get("slug") or slug(entry["name"])
    entry.setdefault("added", now())
    entry["updated"] = now()
    lst = [a for a in apps() if a.get("slug") != entry["slug"]]
    lst.append(entry)
    lst.sort(key=lambda a: a.get("name", "").lower())
    write_json(where("apps") / "apps.json", {"apps": lst})
    return entry


# ---- designs -----------------------------------------------------------------------------------------
def design_save(app, url="", folder="", screens=None):
    d = where("designs", app)
    rec = read_json(d / "design.json")
    rec.update({"app": app, "slug": slug(app), "folder": folder or str(d), "updated": now()})
    if url:
        rec["url"] = url
    if screens is not None:
        rec["screens"] = screens
    rec.setdefault("created", now())
    write_json(d / "design.json", rec)
    a = app_get(app) or {"name": app, "source": "made-here", "lives": "nowhere yet", "status": "being designed", "path": ""}
    a["design_url"] = rec.get("url", "")
    a["design_folder"] = rec["folder"]
    app_save(a)
    return rec


def designs():
    out = []
    for d in sorted(where("designs").iterdir()):
        r = read_json(d / "design.json")
        if r:
            out.append(r)
    return out


def log_action(what, detail=""):
    append_line(INTERNAL / "logs" / "actions.log", f"{now()}  {what}" + (f"  |  {detail}" if detail else ""))
