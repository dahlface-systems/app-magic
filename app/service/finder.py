"""Finding the apps someone already has on their Mac.

Three steps, each one reported as it happens so the window can show the search instead of a spinner:

  1. LOOK   walk the chosen folders (or the whole home folder) and notice anything that could be an app;
  2. READ   open each one: what kind of thing it is, who wrote it, whether it is a second copy of another one;
  3. JUDGE  Claude reads what was gathered, including the top of the main code file, and says for each one whether it
            looks like the person's own app, in a plain sentence. Without Claude the READ step's own judgment stands.

Nothing here changes any file.
"""
import hashlib, json, os, re, shutil, subprocess, threading, time
from pathlib import Path

import claude_cli, storage

SKIP = {"node_modules", ".git", ".next", "dist", "build", ".build", "DerivedData", "Pods", "venv", ".venv", "__pycache__", "target", ".turbo", ".vercel",
        "coverage", ".cache", "vendor", ".svelte-kit", "out", ".expo", ".gradle", "site-packages", "bower_components", ".Trash"}
HOME_SKIP = {"Library", "Applications", "Music", "Movies", "Pictures", "Public"}
BUNDLES = {"app", "photoslibrary", "bundle", "framework", "playground", "lproj", "sparsebundle", "pkg", "musiclibrary", "tvlibrary", "imovielibrary", "docset", "xcassets", "xcodeproj", "xcworkspace"}
MARKERS = [("package.json", "a web app"), ("Package.swift", "a Swift app"), ("pyproject.toml", "a Python app"), ("requirements.txt", "a Python app"),
           ("manage.py", "a Django site"), ("Cargo.toml", "a Rust program"), ("go.mod", "a Go program"), ("Gemfile", "a Ruby app"),
           ("pubspec.yaml", "a Flutter app"), ("vercel.json", "a web app"), ("index.html", "a web page")]
CODE = {".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".py": "Python", ".swift": "Swift", ".rb": "Ruby", ".go": "Go", ".rs": "Rust",
        ".sh": "shell scripts", ".html": "HTML", ".css": "CSS", ".php": "PHP", ".java": "Java", ".kt": "Kotlin", ".applescript": "AppleScript", ".sql": "SQL"}
DOCS = {".md", ".pdf", ".docx", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".json", ".pptx"}
PLACE_WORDS = {"documents": "Documents", "desktop": "Desktop", "downloads": "Downloads", "dev": "dev", "developer": "Developer", "projects": "Projects",
               "code": "Code", "sites": "Sites", "src": "src", "github": "GitHub", "repos": "repos", "work": "Work"}
EVERYWHERE = re.compile(r"(?i)\b(everywhere|anywhere|all over|whole (computer|mac|machine|laptop)|entire (computer|mac|machine|laptop)|my (whole )?(computer|mac|laptop))\b")


def _run(cmd, cwd=None, timeout=12):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 127, "", str(e)


def short(path):
    return str(path).replace(str(Path.home()), "~", 1)


def ago(ts):
    d = time.time() - ts
    for n, unit in ((365 * 86400, "year"), (30 * 86400, "month"), (7 * 86400, "week"), (86400, "day"), (3600, "hour")):
        if d >= n:
            k = int(d // n)
            return f"{k} {unit}{'s' if k != 1 else ''} ago"
    return "just now"


_ME = None


def me():
    """Who the person is, as far as their code is concerned: the name and addresses their own work is signed with."""
    global _ME
    if _ME is None:
        _, email, _ = _run(["git", "config", "--global", "user.email"])
        _, name, _ = _run(["git", "config", "--global", "user.name"])
        login = storage.config().get("github_login", "")
        if not login and shutil.which("gh"):
            rc, login, _ = _run(["gh", "api", "user", "-q", ".login"], timeout=15)
            login = login if rc == 0 else ""
            if login:
                storage.set_config(github_login=login)
        _ME = {"emails": {e.lower() for e in [email] if e}, "name": name.lower(), "login": login.lower()}
    return _ME


# ---- step 1: look ------------------------------------------------------------------------------------
def describe(folder):
    """Cheap first look: could this folder be an app or a project? Returns the basics, or None."""
    folder = Path(folder)
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return None
    names = {e.name for e in entries}
    kind = None
    if any(n.endswith((".xcodeproj", ".xcworkspace")) for n in names):
        kind = "an iPhone or Mac app"
    for marker, what in MARKERS:
        if kind:
            break
        if marker in names:
            kind = what
    if not kind:
        exts = {}
        for e in entries:
            ext = os.path.splitext(e.name)[1].lower()
            if ext in CODE and e.is_file(follow_symlinks=False):
                exts[ext] = exts.get(ext, 0) + 1
        code_files = sum(n for x, n in exts.items() if x not in (".html", ".css", ".sql"))
        if ".git" in names and (code_files or names & {"Makefile", "src", "Sources", "app", "lib"}):
            top = max(exts, key=exts.get) if exts else ""
            kind = {".swift": "a Mac tool (Swift)", ".py": "Python scripts", ".sh": "shell scripts"}.get(top, "a code project")
        elif code_files >= 2 and names & {"Makefile", "README.md", "README"}:
            top = max(exts, key=exts.get)
            kind = {".swift": "a Mac tool (Swift)", ".py": "Python scripts", ".sh": "shell scripts"}.get(top, "a code project")
        elif ".git" in names:
            kind = "a folder with version history"
    if not kind:
        return None
    detail = ""
    if "package.json" in names:
        pkg = storage.read_json(folder / "package.json")
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        for dep, label in (("next", "Next.js site"), ("react-native", "phone app (React Native)"), ("expo", "phone app (Expo)"), ("electron", "desktop app (Electron)"),
                           ("vite", "web app (Vite)"), ("react", "web app (React)"), ("svelte", "web app (Svelte)"), ("vue", "web app (Vue)"), ("express", "web service")):
            if dep in deps:
                kind = "a " + label
                break
        detail = (pkg.get("description") or "")[:160]
    if not detail:
        for rd in ("README.md", "README", "readme.md", "Readme.md"):
            if rd in names:
                try:
                    for line in (folder / rd).read_text(errors="ignore").splitlines()[:40]:
                        line = line.strip().lstrip("#").strip()
                        if len(line) > 12 and not line.startswith(("![", "<", "[!", "```", "|")):
                            detail = line[:160]
                            break
                except OSError:
                    pass
                break
    return {"name": folder.name, "path": str(folder), "kind": kind, "detail": detail, "git": ".git" in names}


def places_from_hint(hint):
    """Turn what the person typed into (folders to look in, a name to look for, whether to look everywhere).

    "look through my dev folder and see what should come in" names a place and nothing else. A name is only taken from
    words that plainly are one: "called recipes", "named X", or something in quotes.
    """
    hint = (hint or "").strip()
    home = Path.home()
    direct = Path(hint).expanduser()
    if hint.startswith(("/", "~")) and direct.is_dir():
        return [direct], "", False
    name = ""
    m = re.search(r"(?i)\b(?:called|named|name is)\s+[\"“']?([\w][\w .-]{1,40}?)[\"”']?(?:[,.;]|\s+(?:in|on|under|inside|somewhere)\b|$)", hint) or re.search(r"[\"“]([^\"”]{2,40})[\"”]", hint)
    if m:
        name = m.group(1).strip().lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9_-]+", hint.lower()))
    places = []
    try:
        tops = {p.name.lower(): p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")}
    except OSError:
        tops = {}
    for w in words:
        target = tops.get(w) or tops.get(PLACE_WORDS.get(w, "").lower())
        if target and target not in places and target.name not in HOME_SKIP and target != storage.HOME:
            places.append(target)
    for m2 in re.finditer(r"(~|/Users/)[^\s,;]+", hint):
        p = Path(m2.group(0)).expanduser()
        if p.is_dir() and p not in places:
            places.append(p)
    everywhere = bool(EVERYWHERE.search(hint)) or not places
    return (places if places and not EVERYWHERE.search(hint) else [home]), name, everywhere


def walk(roots, depth=6, on_look=None, stop=lambda: False):
    """Breadth-first through the folders, yielding each thing that could be an app. An app's own subfolders belong to it
    and are not searched."""
    home, seen = Path.home(), set()
    queue = [(Path(r), 0) for r in roots]
    while queue and not stop():
        folder, d = queue.pop(0)
        key = os.path.realpath(folder)
        if key in seen:
            continue
        seen.add(key)
        if on_look:
            on_look(folder, d)
        info = describe(folder) if folder != home else None
        if info and d > 0 or (info and len(roots) == 1 and folder == Path(roots[0]) and info["kind"] != "a folder with version history" and _is_single_app(folder)):
            yield info
            continue
        if d >= depth:
            continue
        try:
            subs = sorted((e for e in os.scandir(folder) if e.is_dir(follow_symlinks=False)), key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in subs:
            n = e.name
            if n.startswith(".") or n in SKIP or (folder == home and (n in HOME_SKIP or Path(e.path) == storage.HOME)):
                continue
            if "." in n and n.rsplit(".", 1)[1].lower() in BUNDLES:
                continue
            queue.append((Path(e.path), d + 1))


def _is_single_app(folder):
    """The person pointed straight at one app (not at a folder of apps): it has app files and no sub-apps."""
    try:
        subs = [e for e in os.scandir(folder) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".") and e.name not in SKIP]
    except OSError:
        return True
    return not any(describe(e.path) for e in subs[:60])


# ---- step 2: read ------------------------------------------------------------------------------------
ENTRY_GUESSES = ["src/app/page.tsx", "src/app/page.jsx", "app/page.tsx", "app/page.jsx", "pages/index.tsx", "pages/index.jsx", "pages/index.js", "src/App.tsx", "src/App.jsx",
                 "src/main.tsx", "src/main.ts", "src/index.tsx", "src/index.ts", "src/index.js", "index.js", "index.ts", "server.js", "main.py", "app.py", "manage.py",
                 "Sources/main.swift", "main.swift", "index.html"]


def read(info):
    """Open one candidate and gather the evidence: what is in it, who wrote it, where it lives, a look at its code."""
    folder, who = Path(info["path"]), me()
    d = dict(info)
    exts, files, newest, docs = {}, 0, 0, 0
    for dp, dns, fns in os.walk(folder):
        dns[:] = [x for x in dns if x not in SKIP and not x.startswith(".")]
        for fn in fns:
            files += 1
            ext = os.path.splitext(fn)[1].lower()
            if ext in CODE:
                exts[ext] = exts.get(ext, 0) + 1
            elif ext in DOCS:
                docs += 1
            if files <= 3000:
                try:
                    newest = max(newest, os.stat(os.path.join(dp, fn)).st_mtime)
                except OSError:
                    pass
        if files > 6000:
            break
    code = sum(exts.values())
    d.update(files=files, code_files=code, doc_files=docs, languages=[CODE[x] for x, _ in sorted(exts.items(), key=lambda kv: -kv[1])[:3]],
             changed=ago(newest) if newest else "", changed_ts=newest)
    try:
        d["top"] = sorted(e.name for e in os.scandir(folder) if not e.name.startswith(".") and e.name not in SKIP)[:28]
    except OSError:
        d["top"] = []
    # who wrote it, and is it a second copy
    gitp = folder / ".git"
    d.update(commits=0, mine=None, remote="", remote_owner="", copy_of="")
    if gitp.is_file():                                         # a git worktree: a second working copy of another folder
        m = re.search(r"gitdir:\s*(.+?)/\.git/worktrees/", gitp.read_text(errors="ignore"))
        if m:
            d["copy_of"] = Path(m.group(1)).name
    if gitp.exists():
        _, remote, _ = _run(["git", "remote", "get-url", "origin"], cwd=folder)
        d["remote"] = remote
        m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", remote)
        if m:
            d["remote_owner"], d["remote_slug"] = m.group(1).lower(), f"{m.group(1)}/{m.group(2)}"
        rc, log, _ = _run(["git", "shortlog", "-sne", "HEAD"], cwd=folder)
        total = mine = 0
        for line in log.splitlines():
            m = re.match(r"\s*(\d+)\s+(.*?)\s*<([^>]*)>", line)
            if not m:
                continue
            n, name, email = int(m.group(1)), m.group(2).lower(), m.group(3).lower()
            total += n
            if email in who["emails"] or (who["name"] and name == who["name"]) or (who["login"] and who["login"] in email):
                mine += n
        d["commits"], d["mine"] = total, (mine / total if total else None)
    d["live"] = [n for n in ("vercel.json", "netlify.toml", "fly.toml", "render.yaml", "firebase.json") if (folder / n).exists()] + ([".vercel"] if (folder / ".vercel").exists() else [])
    entry = next((e for e in ENTRY_GUESSES if (folder / e).is_file()), None)
    if not entry:
        for ext in (".swift", ".py", ".ts", ".js", ".sh"):
            c = sorted(folder.glob("*" + ext))[:1] or sorted(folder.glob("*/*" + ext))[:1]
            if c:
                entry = str(c[0].relative_to(folder))
                break
    d["entry"], d["entry_head"] = entry or "", ""
    if entry:
        try:
            d["entry_head"] = "\n".join((folder / entry).read_text(errors="ignore").splitlines()[:34])[:1400]
        except OSError:
            pass
    d["already"] = bool(next((a for a in storage.apps() if a.get("origin_path") == str(folder)), None))
    d["looked_at"] = [x for x in (("package.json" if (folder / "package.json").exists() else ""), ("README" if any((folder / r).exists() for r in ("README.md", "README")) else ""), entry or "", ("version history" if gitp.exists() else "")) if x]
    d["readme"] = ""
    for rd in ("README.md", "README", "readme.md", "Readme.md"):
        if (folder / rd).is_file():
            try:
                d["readme"] = re.sub(r"\n{3,}", "\n\n", (folder / rd).read_text(errors="ignore"))[:900]
            except OSError:
                pass
            break
    d["verdict"], d["why"] = local_verdict(d)
    d["what"] = d["detail"] or d["kind"].capitalize()
    d["label"], d["summary"] = plain_name(d["name"]), ""      # a first name from the folder; Claude suggests a better one
    pic = find_picture(folder)
    d["picture"], d["picture_kind"], d["thumb"] = pic, "", ""
    if pic and pic["kind"] in ("screenshot", "icon"):          # these are quick; a page is drawn later, while Claude reads
        d["thumb"] = make_thumb(pic["path"], pic["kind"])
        d["picture_kind"] = pic["kind"] if d["thumb"] else ""
    return d


# ---- a picture of each one ---------------------------------------------------------------------------
# Only real pictures: a screenshot already in the project, the page itself drawn without running anything, or its icon.
# Never an invented one. Running an app to photograph it (installing its libraries, starting it) is left for when the
# person asks about that app.
SHOT = re.compile(r"(?i)(screenshot|screen[-_ ]shot|preview|hero|og[-_]?image|demo|mockup)")
ICON = re.compile(r"(?i)^(icon|app-?icon|logo|apple-touch-icon)[-_.@0-9x]*\.(png|icns)$")
PICTURE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".icns")
NEEDS_BUILDING = ("vite", "next", "react-scripts", "@sveltejs/kit", "astro", "parcel", "webpack", "nuxt", "gatsby")


def static_page(folder):
    """A page in the project that can simply be opened: a built site, or plain HTML that needs no build step."""
    for built in ("dist", "build", "out", "_site", "docs", "site"):
        f = folder / built / "index.html"
        if f.is_file() and f.stat().st_size > 300:
            return f
    pkg = storage.read_json(folder / "package.json")
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if (folder / "index.html").is_file() and not any(d in deps for d in NEEDS_BUILDING):
        return folder / "index.html"
    if not (folder / "package.json").exists():
        try:
            pages = sorted((f for f in folder.glob("*.html") if f.stat().st_size > 1500), key=lambda f: -f.stat().st_size)
        except OSError:
            pages = []
        if pages:
            return pages[0]
    return None


def find_picture(folder):
    """The best real picture source in a project: {"kind": screenshot | page | icon, "path": ...}, plus an icon to fall back on."""
    folder = Path(folder)
    shots, icons = [], []
    for dp, dns, fns in os.walk(folder):
        depth = len(Path(dp).relative_to(folder).parts)
        dns[:] = [] if depth >= 4 else [x for x in dns if x not in SKIP and not x.startswith(".")]
        for fn in fns:
            low = fn.lower()
            if not low.endswith(PICTURE_EXT):
                continue
            path = os.path.join(dp, fn)
            try:
                size = os.stat(path).st_size
            except OSError:
                continue
            if SHOT.search(low) and not low.endswith(".icns") and size > 15_000:
                shots.append((depth, -size, path))
            elif "appicon" in dp.lower() and low.endswith(".png"):
                icons.append((0, -size, path))
            elif ICON.match(low) or low.endswith(".icns"):
                icons.append((1, -size, path))
    icon = min(icons)[2] if icons else ""
    if shots:
        return {"kind": "screenshot", "path": min(shots)[2], "icon": icon}
    page = static_page(folder)
    if page:
        return {"kind": "page", "path": str(page), "icon": icon}
    if icon:
        return {"kind": "icon", "path": icon, "icon": icon}
    return None


def snapshot_tool():
    b = storage.SRC / "build" / "App Magic.app" / "Contents" / "MacOS" / "AppMagic"
    return b if b.exists() else None


def looks_blank(png):
    """True when a drawn page is one flat colour (an empty or broken page). Read through a tiny uncompressed copy."""
    tiny = Path(str(png) + ".bmp")
    try:
        rc, _, _ = _run(["/usr/bin/sips", "-s", "format", "bmp", "-z", "24", "36", str(png), "--out", str(tiny)])
        data = tiny.read_bytes()
        px = data[int.from_bytes(data[10:14], "little"):]
        step = 4 if int.from_bytes(data[28:30], "little") == 32 else 3
        lum = [(px[i] + px[i + 1] + px[i + 2]) // 3 for i in range(0, len(px) - step, step)]
        if len(lum) < 200:
            return False
        mean = sum(lum) / len(lum)
        return (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5 < 3.0
    except Exception:
        return False
    finally:
        tiny.unlink(missing_ok=True)


def make_thumb(path, kind):
    """A small PNG of a picture source, kept in .magic/thumbs. Returns its id, or "" when no picture could be made."""
    try:
        stamp = os.stat(path).st_mtime
    except OSError:
        return ""
    tid = hashlib.sha1(f"{path}|{stamp}|{kind}".encode()).hexdigest()[:16]
    out = storage.INTERNAL / "thumbs" / f"{tid}.png"
    if out.exists():
        return tid
    out.parent.mkdir(parents=True, exist_ok=True)
    if kind == "page":
        tool = snapshot_tool()
        if not tool:
            return ""
        raw = out.with_suffix(".raw.png")
        # Open it the way a browser would reach a real site: through a short-lived local web server on this Mac only.
        # Opened straight from the disk, most built sites show nothing, because their own scripts refuse to load.
        import functools, http.server
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        handler = functools.partial(Quiet, directory=str(Path(path).parent))
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError:
            return ""
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            from urllib.parse import quote
            rc, _, _ = _run([str(tool), "--snapshot", f"http://127.0.0.1:{srv.server_address[1]}/{quote(Path(path).name)}", str(raw)], timeout=20)
        finally:
            srv.shutdown(); srv.server_close()
        ok = rc == 0 and raw.exists() and not looks_blank(raw)              # an empty or broken page is not a picture
        if ok:
            _run(["/usr/bin/sips", "-Z", "720", str(raw), "--out", str(out)])
        raw.unlink(missing_ok=True)
        return tid if ok and out.exists() else ""
    rc, _, _ = _run(["/usr/bin/sips", "-s", "format", "png", "-Z", "720", str(path), "--out", str(out)])
    return tid if rc == 0 and out.exists() else ""


def plain_name(folder_name):
    """'task-list-standalone' -> 'Task List Standalone'. Only a stand-in until Claude has read the project."""
    words = [w for w in re.split(r"[-_.\s]+", folder_name) if w]
    return " ".join(w if (w.isupper() or any(c.isdigit() for c in w)) else w.capitalize() for w in words) or folder_name


def local_verdict(d):
    """A first judgment from the evidence alone. Verdicts: yours, not-yours, copy, not-an-app."""
    who = me()
    name = d["name"].lower()
    if d.get("copy_of"):
        return "copy", f"A second working copy of {d['copy_of']}."
    if re.search(r"(?:[-_. ](?:copy|backup|bak|old|archive)\b|\.bak\b|\bbackup\b)", name):
        return "copy", "The name says it is a backup or an old copy."
    if d.get("remote_owner") and d["remote_owner"] != who["login"] and d.get("commits") and (d.get("mine") or 0) < 0.2:
        return "not-yours", f"It comes from {d['remote_owner']}'s GitHub, and almost none of the work in it is yours."
    if d["code_files"] == 0:
        return "not-an-app", "There is no code in it." if not d["doc_files"] else "It is documents, not code."
    if d["code_files"] <= 3 and d["doc_files"] > 6 * d["code_files"] and d["kind"] in ("a folder with version history", "a code project", "Python scripts", "shell scripts"):
        return "not-an-app", "It is mostly documents with a script or two."
    if d["kind"] in ("Python scripts", "shell scripts", "a folder with version history") and d["code_files"] <= 4 and not d.get("live"):
        return "not-an-app", "It is a few scripts rather than an app."
    bits = []
    if d.get("mine"):
        bits.append("you wrote it" if d["mine"] > 0.8 else "you wrote much of it")
    elif not d.get("git"):
        bits.append("it only exists on this Mac")
    if d.get("live"):
        bits.append("it is set up to be live on the web")
    return "yours", (", ".join(bits).capitalize() + ".") if bits else "It is laid out like an app."


def mark_copies(items):
    """Several folders of the same repository: keep the main, most recently changed one; the others are copies."""
    groups = {}
    for it in items:
        if it.get("remote_slug") and it["verdict"] in ("yours", "copy"):
            groups.setdefault(it["remote_slug"], []).append(it)
    for slug, group in groups.items():
        if len(group) < 2:
            continue
        mains = [g for g in group if not g.get("copy_of")] or group
        keep = max(mains, key=lambda g: g.get("changed_ts", 0))
        for g in group:
            if g is not keep and g["verdict"] != "copy":
                g["verdict"], g["why"] = "copy", f"Another copy of {keep['name']} (the same project on GitHub)."
    return items


# ---- step 3: judge -----------------------------------------------------------------------------------
JUDGE_SYSTEM = "You sort folders on someone's Mac into their own apps and everything else. You reply only with JSON lines, one per folder, nothing before or after."
JUDGE_RULES = """For each numbered folder below, reply with exactly one line of JSON, in the same order:
{"i": <number>, "verdict": "yours" | "not-yours" | "copy" | "not-an-app", "name": "<a good name for it>", "what": "<what it does>", "summary": "<two or three sentences>", "why": "<the evidence>"}

verdict:
- "yours": an app, site, tool or dashboard this person made or had made for them, worth having in one place.
- "not-yours": someone else's project they downloaded, a library, a template or example they did not build on.
- "copy": a backup, an old version, or a second working copy of another folder in this list.
- "not-an-app": documents, data, notes, one-off scripts, exports, or config; not something you would run as an app.
name: what this project should be called, 1 to 4 words in Title Case, the way the person would say it out loud. If the project
  names itself (a title in its readme, an app name in its code or settings), use that. Otherwise name it for what it does
  ("Water Bill Splitter"). Never the folder name with dashes, never words like "app", "project", "standalone" or "v2" as filler.
what: one plain sentence a non-developer understands, at most 14 words, saying what it does (not how it is built).
summary: two or three plain sentences telling the person about this project: what it does and who or what it is for, the main
  things in it, and what state it looks to be in (finished and in use, half built, an experiment, long untouched).
why: one plain sentence, at most 16 words, giving the evidence for the verdict. No jargon: say "saved work", not "commits".
Write to the person directly: "you", never "they" or "the user" ("You wrote all of it", not "Written by them").
Each folder comes with a first guess ("guess"); change it when the code or description says otherwise. Read "readme" and "code" (the top of its main file).
Only JSON lines. No tools, no markdown, no commentary."""


def _for_judge(i, d):
    return {"i": i, "folder": short(d["path"]), "kind": d["kind"], "description": d["detail"], "contains": d["top"][:22], "languages": d["languages"],
            "code_files": d["code_files"], "document_files": d["doc_files"], "last_changed": d["changed"], "saved_versions": d["commits"],
            "share_written_by_them": None if d["mine"] is None else round(d["mine"], 2), "github": d.get("remote_slug", ""), "set_up_for_web": bool(d["live"]),
            "second_copy_of": d.get("copy_of", ""), "guess": d["verdict"], "readme": d.get("readme", "")[:520], "main_file": d["entry"], "code": d["entry_head"][:800]}


def judge(items, hint="", on_verdict=None, stop=lambda: False, timeout=240):
    """Ask Claude (the person's Claude account, through Claude Code) to read the evidence and judge each folder. Verdicts
    are reported one by one as Claude writes them. Returns how many it judged; 0 means the first guesses stand."""
    claude = claude_cli.bin()
    if os.environ.get("APP_MAGIC_NO_AI") or not claude or not items or not claude_cli.auth_status()["signed_in"]:
        return 0
    prompt = JUDGE_RULES + (f"\n\nThe person said: \"{hint}\"\n" if hint else "\n") + "\nFolders:\n" + "\n".join(json.dumps(_for_judge(i, d), ensure_ascii=False) for i, d in items)
    env = claude_cli.env()
    cmd = [claude, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages", "--setting-sources", "project,local", "--strict-mcp-config",
           "--mcp-config", '{"mcpServers":{}}', "--system-prompt", JUDGE_SYSTEM, "--max-turns", "1", "--model", storage.config().get("scan_model") or "claude-sonnet-5-5"]
    scratch = storage.where("scratch")
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=str(scratch), env=env, start_new_session=True)
    except OSError:
        return 0
    killer = threading.Timer(timeout, lambda: p.poll() is None and p.kill())
    killer.start()
    by_i, done, buf = dict(items), 0, ""

    def take(line):
        nonlocal done
        line = line.strip().strip("`").strip()
        if not line.startswith("{"):
            return
        try:
            o = json.loads(line)
        except Exception:
            return
        d = by_i.get(o.get("i"))
        if d is None or o.get("verdict") not in ("yours", "not-yours", "copy", "not-an-app"):
            return
        d["verdict"], d["judged_by"] = o["verdict"], "claude"
        if o.get("what"): d["what"] = str(o["what"])[:160]
        if o.get("why"): d["why"] = str(o["why"])[:200]
        if o.get("name"): d["label"] = str(o["name"])[:48]
        if o.get("summary"): d["summary"] = str(o["summary"])[:520]
        done += 1
        if on_verdict:
            on_verdict(o["i"], d)
    try:
        p.stdin.write(prompt); p.stdin.close()
        for raw in p.stdout:
            if stop():
                p.kill(); break
            try:
                o = json.loads(raw)
            except Exception:
                continue
            ev = o.get("event") or {}
            if o.get("type") == "stream_event" and ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                buf += ev["delta"].get("text", "")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    take(line)
            elif o.get("type") == "rate_limit_event":
                try:
                    import accounts
                    accounts.record_rate(o.get("rate_limit_info") or {})
                except Exception:
                    pass
        take(buf)
    finally:
        killer.cancel()
        if p.poll() is None:
            p.kill()
    return done


def claude_text(prompt, system, timeout=90):
    """One short question to Claude on the person's account; the answer as plain text ('' when Claude is not available)."""
    claude = claude_cli.bin()
    if os.environ.get("APP_MAGIC_NO_AI") or not claude or not claude_cli.auth_status()["signed_in"]:
        return ""
    env = claude_cli.env()
    cmd = [claude, "-p", "--output-format", "json", "--setting-sources", "project,local", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--system-prompt", system, "--max-turns", "1", "--model", storage.config().get("scan_model") or "claude-sonnet-5-5"]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(storage.where("scratch")), env=env)
        return (json.loads(r.stdout).get("result") or "").strip()
    except Exception:
        return ""


def overview(items):
    """A few sentences about the whole set: what kinds of things this person makes."""
    lines = [f"- {i.get('label') or i['name']} [{i['verdict']}]: {i.get('what', '')}" for i in items]
    return claude_text("Here is everything found on someone's Mac that could be one of their apps, with a verdict for each "
                       "(yours / not-yours / copy / not-an-app):\n" + "\n".join(lines) + "\n\nIn three plain sentences and at most 80 words, addressed to them as 'you', tell them what you found: "
                       "what kinds of things they make and for what parts of their life or work, which few look most alive or most used, and what you set aside and why. "
                       "No list, no jargon, no preamble.", "You describe what you found, plainly and briefly, to a non-developer.")[:1400]


# ---- the whole search, as one job --------------------------------------------------------------------
class Search:
    """One search, reported step by step through `emit`. `pace` adds a breath between steps so the eye can follow."""
    def __init__(self, hint="", everywhere=False, emit=lambda **e: None, pace=True, ai=True, render=True):
        self.hint, self.emit, self.pace, self.ai, self.render = (hint or "").strip(), emit, pace, ai, render
        places, self.name, wide = places_from_hint(self.hint)
        self.places = [Path.home()] if everywhere else places
        self.everywhere = self.places == [Path.home()]
        self.items, self.looked, self.status, self.phase, self.stopped, self.overview = [], 0, "looking", "", False, ""
        self.id = str(int(time.time() * 1000))

    def public(self):
        keys = ("name", "label", "path", "kind", "what", "summary", "why", "verdict", "changed", "already", "state", "looked_at", "judged_by", "thumb", "picture_kind")
        return {"id": self.id, "status": self.status, "phase": self.phase, "hint": self.hint, "everywhere": self.everywhere, "looked": self.looked,
                "overview": getattr(self, "overview", ""), "places": [short(p) for p in self.places], "items": [{k: it.get(k) for k in keys} | {"i": i, "source": it["path"]} for i, it in enumerate(self.items)]}

    def say(self, phase):
        self.phase = phase
        self.emit(t="scan", k="phase", id=self.id, text=phase, status=self.status)

    def run(self):
        try:
            self._run()
        except Exception as e:
            import problems
            problems.from_exception("search", e, {"hint": self.hint, "places": [str(p) for p in self.places]})
            self.status = "done"
            self.emit(t="scan", k="done", id=self.id, error=str(e), search=self.public())

    def _run(self):
        where = "your whole computer" if self.places == [Path.home()] else " and ".join(short(p) for p in self.places)
        self.emit(t="scan", k="start", id=self.id, search=self.public())
        self.say(f"Looking through {where}")
        last = [0.0]

        def on_look(folder, depth):
            self.looked += 1
            if time.time() - last[0] > 0.05:
                last[0] = time.time()
                self.emit(t="scan", k="look", id=self.id, path=short(folder), looked=self.looked)
            if self.pace:
                time.sleep(0.03 if depth <= 1 else 0.002)

        for info in walk(self.places, on_look=on_look, stop=lambda: self.stopped):
            if self.name and self.name not in (info["name"] + " " + info["detail"]).lower():
                continue
            if Path(info["path"]) == storage.SRC or Path(info["path"]) in storage.SRC.parents:
                continue                                       # App Magic's own code is not one of their apps
            info.update(state="found", verdict="", what="", why="", changed="", already=False, looked_at=[])
            self.items.append(info)
            self.emit(t="scan", k="found", id=self.id, i=len(self.items) - 1, item={"name": info["name"], "path": info["path"], "kind": info["kind"]}, looked=self.looked)
        if self.stopped:
            return self.finish()
        self.status = "reading"
        self.say(f"Found {len(self.items)} thing{'s' if len(self.items) != 1 else ''} that could be {'an app' if len(self.items) == 1 else 'apps'}. Opening each one")
        for i, info in enumerate(self.items):
            if self.stopped:
                return self.finish()
            self.emit(t="scan", k="reading", id=self.id, i=i, path=short(info["path"]))
            full = read(info)
            full["state"] = "read"
            self.items[i] = full
            if self.pace:
                time.sleep(0.06)
            self.emit(t="scan", k="read", id=self.id, i=i, item=self.public()["items"][i])
        mark_copies(self.items)
        painter = threading.Thread(target=self.draw_pages, daemon=True)     # pictures of plain pages, drawn while Claude reads
        painter.start()
        if self.ai and self.items and not self.stopped:
            self.status = "judging"
            self.say("Reading the code to see which ones are really yours")

            def on_verdict(i, d):
                d["state"] = "judged"
                self.emit(t="scan", k="judged", id=self.id, i=i, item=self.public()["items"][i])
            todo = list(enumerate(self.items))
            threads = []
            for start in range(0, len(todo), 20):               # twenty at a time, two batches at once
                batch = todo[start:start + 20]
                th = threading.Thread(target=judge, args=(batch, self.hint, on_verdict, lambda: self.stopped), daemon=True)
                threads.append(th)
            for n in range(0, len(threads), 2):
                for th in threads[n:n + 2]: th.start()
                for th in threads[n:n + 2]: th.join()
            mark_copies(self.items)
            if not self.stopped and len(self.items) >= 3 and any(i.get("judged_by") for i in self.items):
                self.say("Putting together what I found")
                self.overview = overview(self.items)
        painter.join(timeout=150 if self.render else 1)
        self.finish()

    def draw_pages(self):
        """Draw the apps that are just a page, one at a time, and fall back to the icon when a page comes out blank."""
        drawn = 0
        for i, it in enumerate(self.items):
            pic = it.get("picture")
            if self.stopped or not pic or pic["kind"] != "page" or it.get("thumb"):
                continue
            tid = ""
            if self.render and drawn < 40 and it.get("verdict") != "not-yours":
                drawn += 1
                tid = make_thumb(pic["path"], "page")
            kind = "page"
            if not tid and pic.get("icon"):
                tid, kind = make_thumb(pic["icon"], "icon"), "icon"
            if tid:
                it["thumb"], it["picture_kind"] = tid, kind
                self.emit(t="scan", k="thumb", id=self.id, i=i, thumb=tid, picture_kind=kind)

    def finish(self):
        for it in self.items:
            it["state"] = "judged" if it.get("verdict") else it.get("state", "found")
        self.status = "stopped" if self.stopped else "done"
        yours = len([i for i in self.items if i.get("verdict") == "yours"])
        self.phase = (f"Stopped. {yours} look like yours so far." if self.stopped else
                      f"Done. {yours} look like your app{'s' if yours != 1 else ''}" + (f", {len(self.items) - yours} don't." if len(self.items) - yours else "."))
        self.emit(t="scan", k="done", id=self.id, search=self.public())


def find(hint, everywhere=False):
    """The same search without the live reporting or Claude's judgment: used by the in-app Claude's own find_apps tool."""
    s = Search(hint, everywhere, pace=False, ai=False, render=False)
    s.run()
    out = s.public()
    return {"looked_in": out["places"], "folders_looked_through": out["looked"], "apps": out["items"]}
