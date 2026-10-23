"""Finding apps the person already has, bringing them in, and knowing what capabilities are switched on."""
import json, os, re, shutil, subprocess, time
from pathlib import Path

import audit, finder, storage

SKIP = audit.SKIP_DIRS
MARKERS = [("package.json", "a web app"), ("Package.swift", "a Swift app"), ("pyproject.toml", "a Python app"), ("requirements.txt", "a Python app"),
           ("manage.py", "a Django site"), ("Cargo.toml", "a Rust program"), ("go.mod", "a Go program"), ("Gemfile", "a Ruby app"),
           ("pubspec.yaml", "a Flutter app"), ("index.html", "a web page"), ("vercel.json", "a web app")]
LEAVE_OUT = SKIP | {".DS_Store"}


def _run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 127, "", str(e)


def _ago(ts):
    d = time.time() - ts
    for n, unit in ((365 * 86400, "year"), (30 * 86400, "month"), (7 * 86400, "week"), (86400, "day"), (3600, "hour")):
        if d >= n:
            k = int(d // n)
            return f"{k} {unit}{'s' if k != 1 else ''} ago"
    return "just now"


describe = finder.describe


def find_by_hint(hint, everywhere=False):
    """Kept for the tools and tests: the search in finder.py, without live reporting or Claude's judgment."""
    return finder.find(hint, everywhere)


def github_repos(limit=100):
    rc, out, err = _run(["gh", "repo", "list", "--limit", str(limit), "--json", "name,nameWithOwner,description,updatedAt,isPrivate,isArchived,isFork,primaryLanguage,url"], timeout=40)
    if rc != 0:
        return {"connected": False, "error": err or "GitHub is not connected.", "repos": []}
    repos, have = [], {a.get("remote_slug") for a in storage.apps()}
    for r in json.loads(out or "[]"):
        try:
            ts = time.mktime(time.strptime(r["updatedAt"][:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            ts = 0
        lang = (r.get("primaryLanguage") or {}).get("name") or ""
        stale = (time.time() - ts) > 540 * 86400
        guess = r.get("description") or (f"{lang} project" if lang else "No description")
        if r.get("isArchived"):
            guess += " Archived."
        repos.append({"name": r["name"], "slug": r["nameWithOwner"], "what": guess, "changed": _ago(ts) if ts else "", "changed_ts": ts,
                      "private": r.get("isPrivate"), "suggest": not (stale or r.get("isArchived") or r.get("isFork") or r["name"].lower() in ("dotfiles", ".github")),
                      "already": r["nameWithOwner"] in have})
    repos.sort(key=lambda r: -r["changed_ts"])
    return {"connected": True, "repos": repos}


def _copy_app(src, dst):
    left_out = {"secrets": [], "libraries": []}

    def ignore(d, names):
        skip = []
        for n in names:
            if n in LEAVE_OUT:
                skip.append(n)
                if n in ("node_modules", "Pods", "venv", ".venv", "vendor", "DerivedData"):
                    left_out["libraries"].append(str(Path(d).relative_to(src) / n))
            elif audit.SECRET_FILES.search(n) and not audit.ENV_EXAMPLE.search(n):
                skip.append(n)
                left_out["secrets"].append(str(Path(d).relative_to(src) / n))
        return skip
    shutil.copytree(src, dst, ignore=ignore, symlinks=True)
    if (Path(src) / ".git").exists():                         # keep every saved version too
        shutil.copytree(Path(src) / ".git", Path(dst) / ".git", symlinks=True)
    return left_out


def bring_in(source, name=None, summary="", what="", quick=False, thumb="", picture_kind=""):
    """Copy an app into Apps/ (from a folder) or clone it (from GitHub "owner/name"). The original is never touched.
    name is what the person will call it (the suggested name from the search); summary and what describe it."""
    source = (source or "").strip()
    is_path = source.startswith(("/", "~")) or Path(source).expanduser().is_dir()
    if is_path:
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            return {"ok": False, "error": f"There is no folder at {src}."}
        name = name or src.name
        dst = storage.where("apps") / storage.slug(name)
        if dst.exists():
            return {"ok": False, "error": f"{name} is already in App Magic.", "app": storage.app_get(name)}
        left_out = _copy_app(src, dst)
        info = describe(dst) or {}
        _, remote, _ = _run(["git", "remote", "get-url", "origin"], cwd=dst, timeout=8) if (dst / ".git").exists() else (1, "", "")
        entry = {"name": name, "path": str(dst), "source": "a folder on this Mac", "origin_path": str(src), "folder_name": src.name, "kind": info.get("kind", "a project"),
                 "summary": info.get("detail", ""), "left_out": left_out, "remote": remote}
    else:
        slug_ = re.sub(r"^https://github\.com/|\.git$", "", source).strip("/")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", slug_):
            return {"ok": False, "error": f"I could not read {source!r} as a folder or a GitHub repository."}
        name = name or slug_.split("/")[1]
        dst = storage.where("apps") / storage.slug(name)
        if dst.exists():
            return {"ok": False, "error": f"{name} is already in App Magic.", "app": storage.app_get(name)}
        rc, _, err = _run(["gh", "repo", "clone", slug_, str(dst)], timeout=300)
        if rc != 0:
            return {"ok": False, "error": err or "GitHub would not hand that over."}
        info = describe(dst) or {}
        entry = {"name": name, "path": str(dst), "source": "GitHub", "remote_slug": slug_, "kind": info.get("kind", "a project"), "summary": info.get("detail", ""),
                 "left_out": {"secrets": [], "libraries": []}, "remote": f"https://github.com/{slug_}"}
    if entry["remote"] and not entry.get("remote_slug") and "github.com" in entry["remote"]:
        entry["remote_slug"] = re.sub(r"\.git$", "", entry["remote"].split("github.com")[-1].lstrip(":/"))
    deploys = [n for n in ("vercel.json", ".vercel", "netlify.toml", "fly.toml") if (dst / n).exists()]
    entry["lives"] = "on the web" if deploys else ("on your iPhone or Mac" if "iPhone" in entry["kind"] else "on this Mac")
    if summary:
        entry["summary"] = summary
    if what:
        entry["what"] = what
    if thumb and re.fullmatch(r"[0-9a-f]{16}", thumb):
        entry["thumb"], entry["picture_kind"] = thumb, picture_kind
    report = audit.save_report(audit.audit_app(dst, name, quick=quick))
    entry["audit"] = {"summary": report["summary"], "fails": report["fails"], "when": report["when"]}
    entry["status"] = "fine" if not report["fails"] else report["summary"]
    storage.app_save(entry)
    storage.log_action(f"Brought in {name}", f"from {entry['source']}; {report['summary']}")
    return {"ok": True, "app": entry, "audit": report}


# ---- capabilities ------------------------------------------------------------------------------------
def _has(cmd):
    return shutil.which(cmd) is not None


def capability_state(cap):
    """Work out the real state of a capability: not-yet, signing-in, on, needs-you."""
    check = cap.get("check")
    if check == "always":
        return "on", ""
    if check == "gh":
        if not _has("gh"):
            return "not-yet", "The GitHub tool is not installed yet."
        rc, out, err = _run(["gh", "auth", "status"], timeout=15)
        return ("on", "") if rc == 0 else ("needs-you", "GitHub needs you to sign in again.")
    if check == "vercel":
        if _has("vercel"):
            rc, out, _ = _run(["vercel", "whoami"], timeout=20)
            return ("on", out.splitlines()[-1] if out else "") if rc == 0 else ("needs-you", "Vercel needs you to sign in.")
        return "not-yet", ""
    if check == "supabase":
        return ("on", "") if _has("supabase") else ("not-yet", "")
    if check in ("apple", "google"):
        import devaccounts
        return devaccounts.capability_state(check)
    if check == "xcodebuild":
        rc, out, _ = _run(["xcodebuild", "-version"], timeout=15)
        return ("on", out.splitlines()[0] if out else "") if rc == 0 else ("not-yet", "The Apple kit is not installed yet.")
    return cap.get("state", "not-yet"), cap.get("note", "")


def capabilities(refresh=True):
    out = []
    for d in sorted(storage.where("capabilities").iterdir()):
        cap = storage.read_json(d / "capability.json")
        if not cap:
            continue
        if refresh and cap.get("state") != "signing-in":
            state, note = capability_state(cap)
            if state != cap.get("state") or (note and note != cap.get("note")):
                cap["state"], cap["note"] = state, note
                storage.write_json(d / "capability.json", cap)
        out.append(cap)
    order = {"needs-you": 0, "signing-in": 1, "on": 2, "not-yet": 3}
    out.sort(key=lambda c: (order.get(c.get("state"), 9), c.get("title", "")))
    return out
