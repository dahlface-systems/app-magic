"""Security audit.

Two audits share one report shape:
  audit_app(path)  the five checks every app gets before anything leaves the Mac
  audit_self()     the same idea turned on App Magic itself

A report is plain data: five checks, each "pass", "fail" or "unknown", with a sentence for the person and the technical
findings underneath for whoever fixes it. Nothing here changes any file.
"""
import json, os, re, stat, subprocess, time
from pathlib import Path

import storage

SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", ".build", "DerivedData", "Pods", "venv", ".venv", "__pycache__",
             "target", ".turbo", ".vercel", "coverage", ".cache", "vendor", ".svelte-kit", "out", ".expo", ".gradle"}
TEXT_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".py", ".rb", ".go", ".rs", ".swift", ".java", ".kt", ".php", ".html", ".css",
            ".json", ".yml", ".yaml", ".toml", ".env", ".sh", ".sql", ".md", ".txt", ".plist", ".xml", ".ini", ".cfg", ".conf", ""}
SECRET_FILES = re.compile(r"(^\.env(\..*)?$|\.pem$|\.p12$|\.key$|^id_(rsa|ed25519|ecdsa)$|credentials\.json$|service[-_]?account.*\.json$|\.keystore$|^\.npmrc$|^\.netrc$)", re.I)
ENV_EXAMPLE = re.compile(r"\.(example|sample|template|dist)$", re.I)

# (label, pattern). Kept specific on purpose: a noisy audit gets ignored.
SECRET_PATTERNS = [
    ("An Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("An OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("A GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("An AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("A Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b")),
    ("A Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("A Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("A private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("A Supabase service key", re.compile(r"service_role[\"'\s:=]{1,6}[\"']?eyJ[A-Za-z0-9_\-]{20,}\.")),
    ("A database address with a password in it", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/\"']+:[^\s@/\"']{4,}@")),
    ("A password written in the code", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|auth[_-]?token)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")),
]
PLACEHOLDER = re.compile(r"(?i)(your[_-]|example|changeme|placeholder|xxxx|<[^>]+>|\$\{|process\.env|os\.environ|\*{4,}|dummy|test[_-]?key|fake)")
AUTH_HINTS = re.compile(r"(?i)(next-auth|@auth/|@clerk/|supabase\.auth|auth0|firebase/auth|passport|lucia|better-auth|iron-session|express-session|"
                        r"django\.contrib\.auth|flask[_-]login|devise|basic[_-]?auth|withAuth|getServerSession|middleware\.(ts|js)|"
                        r"Sign in with Apple|ASAuthorization|LocalAuthentication)")
RISKY = [
    ("Runs text as code (eval)", re.compile(r"\beval\s*\(|new Function\s*\(")),
    ("Lets any website call it (CORS *)", re.compile(r"Access-Control-Allow-Origin[\"']?\s*[,:]\s*[\"']\*[\"']|cors\(\s*\)|origin:\s*[\"']\*[\"']")),
    ("Turns off a database safety (row level security)", re.compile(r"(?i)disable\s+row\s+level\s+security")),
    ("Turns off certificate checks", re.compile(r"rejectUnauthorized\s*:\s*false|verify\s*=\s*False|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*[\"']?0")),
    ("Builds a database command from text (possible injection)", re.compile(r"(?i)(execute|query)\(\s*f[\"'].*(select|insert|update|delete)\b.*\{")),
    ("Runs shell commands built from text", re.compile(r"shell\s*=\s*True|child_process\.exec\(\s*[`\"'].*\$\{")),
]


def _run(cmd, cwd=None, timeout=20):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 127, "", str(e)


def _files(root, limit=6000):
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            n += 1
            if n > limit:
                return
            yield Path(dp) / fn


def _size(f):
    try:
        return f.stat().st_size
    except OSError:
        return 10**12


def _check(key, title, status, say, findings=None, fix=""):
    return {"key": key, "title": title, "status": status, "say": say, "findings": findings or [], "fix": fix}


def _mask(s):
    s = s.strip()
    return s if len(s) <= 12 else s[:6] + "…" + s[-3:]


def scan_secrets(root):
    """Secrets written into files, and secret files sitting in the folder."""
    root = Path(root)
    rc, tracked, _ = _run(["git", "ls-files"], cwd=root)
    tracked = set(tracked.splitlines()) if rc == 0 else None
    rc, ignored, _ = _run(["git", "ls-files", "--others", "--ignored", "--exclude-standard"], cwd=root)
    ignored = set(ignored.splitlines()) if rc == 0 else set()
    in_code, secret_files = [], []
    for f in _files(root):
        rel = str(f.relative_to(root))
        if SECRET_FILES.search(f.name) and not ENV_EXAMPLE.search(f.name):
            state = "saved in version history" if (tracked is not None and rel in tracked) else ("kept out of version history" if rel in ignored else "in the folder")
            secret_files.append({"file": rel, "state": state, "tracked": bool(tracked is not None and rel in tracked)})
            continue
        if f.suffix.lower() not in TEXT_EXT:
            continue
        try:
            if f.stat().st_size > 400_000:
                continue
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if len(line) > 600:
                continue
            for label, pat in SECRET_PATTERNS:
                m = pat.search(line)
                if m and not PLACEHOLDER.search(line):
                    in_code.append({"file": rel, "line": i, "what": label, "sample": _mask(m.group(0))})
                    break
    return in_code[:50], secret_files


def audit_app(path, name=None, quick=False):
    root = Path(path).expanduser()
    name = name or root.name
    if not root.is_dir():
        return {"target": name, "path": str(root), "when": storage.now(), "error": "That folder does not exist.", "checks": [], "summary": "Could not check"}
    checks, extra = [], []
    files = list(_files(root))
    names = {f.name for f in files}
    blob = ""
    for f in files:
        if f.name in ("package.json", "requirements.txt", "pyproject.toml", "Gemfile", "Podfile", "Package.swift", "middleware.ts", "middleware.js"):
            try:
                blob += f.read_text(errors="ignore")[:20000]
            except Exception:
                pass
    is_git = (root / ".git").exists()
    is_web = bool(names & {"package.json", "index.html", "vercel.json", "next.config.js", "next.config.mjs", "next.config.ts", "manage.py", "app.py"})
    is_apple = any(f.suffix in (".xcodeproj", ".swift") or f.name == "Package.swift" for f in files) or any(p.suffix == ".xcodeproj" for p in root.iterdir())

    # 1. Private by default: is the code itself public, and is the site open?
    findings, status, say = [], "unknown", "I can't tell whether anyone else can see this yet."
    remote = ""
    if is_git:
        _, remote, _ = _run(["git", "remote", "get-url", "origin"], cwd=root)
    if remote and "github.com" in remote:
        slug_ = re.sub(r"\.git$", "", remote.split("github.com")[-1].lstrip(":/"))
        rc, out, _ = _run(["gh", "repo", "view", slug_, "--json", "visibility,isPrivate"], timeout=25)
        if rc == 0:
            vis = json.loads(out)
            if vis.get("isPrivate"):
                status, say = "pass", "The code is private on GitHub. Only you can see it."
            else:
                status, say = "fail", "The code is public on GitHub. Anyone can read it."
                findings.append({"what": f"{slug_} is {vis.get('visibility', 'PUBLIC').lower()}"})
        else:
            findings.append({"what": f"Could not ask GitHub about {slug_}"})
    elif not remote:
        status, say = "pass", "It only exists on this Mac. Nobody else can see it."
    deploy = [n for n in ("vercel.json", "netlify.toml", "fly.toml", "render.yaml", "Procfile", "firebase.json") if n in names] + ([".vercel"] if (root / ".vercel").exists() else [])
    if deploy:
        findings.append({"what": "It is set up to go on the web (" + ", ".join(deploy) + "). Whether the live site is private is checked under the next item."})
    checks.append(_check("private", "Private by default", status, say, findings, "Make the GitHub repository private." if status == "fail" else ""))

    # 2. A lock on the front door: sign-in.
    auth_where = []
    for f in files:
        if f.suffix.lower() in (".js", ".jsx", ".ts", ".tsx", ".py", ".rb", ".swift", ".json", ".toml", ".txt") and _size(f) < 300_000:
            try:
                if AUTH_HINTS.search(f.read_text(errors="ignore")):
                    auth_where.append(str(f.relative_to(root)))
            except Exception:
                pass
        if len(auth_where) >= 5:
            break
    if not is_web and not deploy:
        checks.append(_check("lock", "A lock on the front door", "pass", "It runs on your own devices, so there is no front door on the internet to lock."))
    elif auth_where:
        checks.append(_check("lock", "A lock on the front door", "pass", "It has sign-in.", [{"what": "Sign-in found in " + w} for w in auth_where[:3]]))
    else:
        checks.append(_check("lock", "A lock on the front door", "fail" if deploy else "unknown",
                             "It can go on the web but I found no sign-in. Anyone with the link could open it." if deploy else "I found no sign-in. That matters as soon as it goes on the web.",
                             [], "Add sign-in before it is shared."))

    # 3. Keys locked away.
    in_code, secret_files = scan_secrets(root)
    findings = [{"what": f"{s['what']} in {s['file']}, line {s['line']}", "sample": s["sample"]} for s in in_code]
    tracked = [s for s in secret_files if s["tracked"]]
    findings += [{"what": f"{s['file']} is a secrets file and is {s['state']}"} for s in secret_files]
    gi = (root / ".gitignore").read_text(errors="ignore") if (root / ".gitignore").exists() else ""
    if is_git and ".env" not in gi and any(n.startswith(".env") for n in names):
        findings.append({"what": ".gitignore does not keep .env files out of version history"})
    if in_code or tracked:
        n = len(in_code) + len(tracked)
        checks.append(_check("keys", "Keys locked away", "fail", f"I found {n} key{'s' if n != 1 else ''} or password{'s' if n != 1 else ''} written where they should not be.", findings,
                             "Move each one to the Mac keychain, replace it in the code with a name, and change the key itself, because it has been exposed."))
    else:
        checks.append(_check("keys", "Keys locked away", "pass", "No passwords or keys are written into the code." + (" Secret files are kept out of version history." if secret_files else ""), findings))

    # 4. Every version saved.
    if not is_git:
        checks.append(_check("versions", "Every version saved", "fail", "Nothing is saving versions of this. One bad change and the old one is gone.", [], "Start version history (git) in this folder."))
    else:
        _, count, _ = _run(["git", "rev-list", "--count", "HEAD"], cwd=root)
        _, dirty, _ = _run(["git", "status", "--porcelain"], cwd=root)
        _, last, _ = _run(["git", "log", "-1", "--format=%cr"], cwd=root)
        f = []
        if dirty:
            f.append({"what": f"{len(dirty.splitlines())} changed file(s) not saved as a version yet"})
        if not remote:
            f.append({"what": "Versions are only on this Mac; there is no copy anywhere else"})
        ok = count.isdigit() and int(count) > 0
        checks.append(_check("versions", "Every version saved", "pass" if ok else "fail",
                             (f"{count} versions saved, the last one {last}." if ok else "Version history is switched on but nothing has been saved yet."), f,
                             "" if ok and not dirty else "Save the current state as a version."))

    # 5. Built the standard way.
    f, bad = [], 0
    if not any(n.lower().startswith("readme") for n in names):
        f.append({"what": "No README explaining what it is and how to run it"})
    if "package.json" in names and not (names & {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock"}):
        f.append({"what": "No lock file, so a fresh install could fetch different versions"}); bad += 1
    if (root / "node_modules").exists() and is_git:
        rc, out, _ = _run(["git", "ls-files", "node_modules", "--error-unmatch"], cwd=root)
        if rc == 0 and out:
            f.append({"what": "Downloaded libraries (node_modules) are saved in version history"}); bad += 1
    risky = []
    for fp in files:
        if fp.suffix.lower() in (".js", ".jsx", ".ts", ".tsx", ".py", ".rb", ".php", ".sql") and _size(fp) < 300_000:
            try:
                text = fp.read_text(errors="ignore")
            except Exception:
                continue
            for label, pat in RISKY:
                m = pat.search(text)
                if m:
                    risky.append({"what": f"{label} in {fp.relative_to(root)}, line {text[:m.start()].count(chr(10)) + 1}"})
    f += risky[:12]
    if (root / "package-lock.json").exists() and not quick:      # the slow one; skipped when many apps come in at once
        rc, out, _ = _run(["npm", "audit", "--json", "--omit=dev"], cwd=root, timeout=45)
        try:
            v = (json.loads(out).get("metadata") or {}).get("vulnerabilities") or {}
            serious = v.get("high", 0) + v.get("critical", 0)
            if serious:
                f.append({"what": f"{serious} library problem(s) rated high or critical (npm audit)"}); bad += 1
            extra.append(f"npm audit: {v}")
        except Exception:
            pass
    status = "fail" if (bad or len(risky) >= 3) else "pass"
    kind = "a web app" if is_web else ("an Apple app" if is_apple else "a project")
    checks.append(_check("standard", "Built the standard way", status,
                         f"It is laid out like {kind} normally is." if status == "pass" else "A few things are not how a careful developer would leave them.", f,
                         "" if status == "pass" else "Fix the items listed."))

    fails = [c for c in checks if c["status"] == "fail"]
    unknown = [c for c in checks if c["status"] == "unknown"]
    summary = "Passed all five checks" if not fails and not unknown else (f"{len(fails)} thing{'s' if len(fails) != 1 else ''} to fix" if fails else f"{len(unknown)} thing{'s' if len(unknown) != 1 else ''} I could not check")
    return {"target": name, "path": str(root), "when": storage.now(), "checks": checks, "summary": summary, "fails": len(fails), "unknown": len(unknown), "notes": extra}


def audit_self(port=None):
    """App Magic checking itself: what it can reach, what it stores, and what is switched on."""
    home, checks = storage.HOME, []

    # 1. The door between the window and the service.
    f, ok = [], True
    if port:
        rc, out, _ = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
        if out and "127.0.0.1" not in out and "localhost" not in out:
            ok = False; f.append({"what": f"The service is listening on more than this Mac: {out.splitlines()[-1]}"})
        else:
            f.append({"what": f"The service only answers on this Mac (127.0.0.1:{port})"})
    tok = storage.INTERNAL / "token"
    if tok.exists():
        mode = stat.S_IMODE(tok.stat().st_mode)
        if mode & 0o077:
            ok = False; f.append({"what": f"The service's token file can be read by other users (mode {oct(mode)})"})
        else:
            f.append({"what": "Every request needs a token only this user can read"})
    checks.append(_check("door", "Only this Mac can talk to it", "pass" if ok else "fail",
                         "Nothing outside this Mac can reach App Magic." if ok else "Something outside this Mac may be able to reach App Magic.", f))

    # 2. Its folder.
    f, ok = [], True
    mode = stat.S_IMODE(home.stat().st_mode)
    if mode & 0o077:
        ok = False; f.append({"what": f"{home} can be opened by other users of this Mac (mode {oct(mode)})"})
    else:
        f.append({"what": f"{home} is readable only by you"})
    checks.append(_check("folder", "Its folder is yours alone", "pass" if ok else "fail",
                         "Only your user can open the App Magic folder." if ok else "Other users on this Mac could read the App Magic folder.", f,
                         "" if ok else f"chmod 700 '{home}'"))

    # 3. No secrets in the folder.
    in_code, secret_files = [], []
    for sub in ("Accounts", "Capabilities", "Rules", "Changes", "Context"):
        a, b = scan_secrets(home / sub) if (home / sub).exists() else ([], [])
        in_code += [dict(x, file=f"{sub}/{x['file']}") for x in a]
        secret_files += [dict(x, file=f"{sub}/{x['file']}") for x in b]
    f = [{"what": f"{s['what']} in {s['file']}, line {s['line']}", "sample": s["sample"]} for s in in_code] + [{"what": f"{s['file']} is a secrets file"} for s in secret_files]
    checks.append(_check("keys", "Keys locked away", "fail" if f else "pass",
                         "No passwords or keys are stored in App Magic's own files; sign-ins stay in the Mac keychain." if not f else f"I found {len(f)} secret(s) in App Magic's files.", f,
                         "" if not f else "Move them to the keychain."))

    # 4. What Claude is allowed to do. Stated plainly because it is the biggest fact about this app.
    s = storage.read_json(home / ".claude" / "settings.json")
    bypass = (s.get("permissions") or {}).get("defaultMode") == "bypassPermissions"
    checks.append(_check("power", "What it is allowed to do", "unknown" if bypass else "pass",
                         "Claude runs here without asking permission for each step. That is by design, so it can build without interrupting you, and it means the rules in Rules/safety.md are promises, not locks." if bypass
                         else "Claude asks before each risky step.",
                         [{"what": "Claude Code permission mode: " + ((s.get("permissions") or {}).get("defaultMode") or "default")},
                          {"what": "The change-App-Magic workshop window always runs with permissions bypassed"}]))

    # 5. Its own code and change history.
    src = Path(storage.config().get("source_dir") or storage.SRC)
    f, ok = [], True
    if (src / ".git").exists() or (src.parent / ".git").exists():
        _, dirty, _ = _run(["git", "status", "--porcelain", "."], cwd=src)
        _, last, _ = _run(["git", "log", "-1", "--format=%cr: %s", "."], cwd=src)
        f.append({"what": "Last saved version of App Magic's code: " + (last or "none yet")})
        if not last:
            ok = False
        if dirty:
            f.append({"what": f"{len(dirty.splitlines())} file(s) of App Magic's code changed since the last saved version"})
    else:
        ok = False; f.append({"what": f"App Magic's code at {src} has no version history, so a bad change to it cannot be undone"})
    n = len(list(storage.where("changes").glob("*.md")))
    f.append({"what": f"{n} recorded change(s) to App Magic in Changes/"})
    checks.append(_check("versions", "Changes to App Magic can be undone", "pass" if ok else "fail",
                         "Every change to App Magic itself is saved and can be rolled back." if ok else "No version of App Magic's own code has been saved yet, so a bad change to it could not be undone.", f,
                         "" if ok else "Save a first version of App Magic's source folder (git commit)."))

    fails = [c for c in checks if c["status"] == "fail"]
    return {"target": "App Magic", "path": str(home), "when": storage.now(), "checks": checks,
            "summary": "App Magic is in good order" if not fails else f"{len(fails)} thing{'s' if len(fails) != 1 else ''} to fix",
            "fails": len(fails), "unknown": len([c for c in checks if c["status"] == "unknown"]), "notes": []}


def save_report(report):
    d = storage.where("audits", "app-magic" if report["target"] == "App Magic" else report["target"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    storage.write_json(d / f"{stamp}.json", report)
    lines = [f"# Security audit: {report['target']}", "", f"{report['when']}  ·  {report['path']}", "", f"**{report['summary']}**", ""]
    for c in report["checks"]:
        mark = {"pass": "OK", "fail": "FIX", "unknown": "?"}[c["status"]]
        lines += [f"## [{mark}] {c['title']}", "", c["say"], ""]
        lines += [f"- {x['what']}" + (f" (`{x['sample']}`)" if x.get("sample") else "") for x in c["findings"]]
        if c["fix"]:
            lines += ["", f"To fix: {c['fix']}"]
        lines.append("")
    (d / f"{stamp}.md").write_text("\n".join(lines))
    report["saved"] = str(d / f"{stamp}.md")
    return report


def latest(target):
    d = storage.where("audits", "app-magic" if target == "App Magic" else target)
    files = sorted(d.glob("*.json"))
    return storage.read_json(files[-1]) if files else None


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "self"
    storage.ensure()
    r = audit_self(storage.config().get("port")) if t == "self" else audit_app(t)
    print(json.dumps(save_report(r), indent=2))
