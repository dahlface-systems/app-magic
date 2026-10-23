"""First run: what this Mac already has, and putting in what it doesn't.

The window walks the person through it: Claude (installed and signed in), the plan, and the plain statement of what
App Magic does on their Mac. Everything here reports progress on the bus so the window can show it happening.
"""
import os, shutil, subprocess, threading, time

import claude_cli, storage

_jobs = {}


def _bus(**e):
    from claude_session import BUS
    BUS.publish(t="setup", **e)


def has(cmd):
    return shutil.which(cmd, path=claude_cli.path_env()) is not None


def status(fresh=False):
    st = claude_cli.auth_status(fresh)
    import accounts
    plan = accounts.claude().get("plan", "") if st["signed_in"] else ""
    gh_in = False
    if has("gh"):
        try:
            gh_in = subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=10, env=claude_cli.env()).returncode == 0
        except Exception:
            pass
    cfg = storage.config()
    return {"claude": {"installed": st["installed"], "signed_in": st["signed_in"], "email": st["email"], "plan": plan, "version": claude_cli.version() if fresh else ""},
            "tools": {"git": has("git"), "gh": has("gh"), "gh_signed_in": gh_in, "brew": has("brew"), "swift": has("swift")},
            "onboarded": bool(cfg.get("onboarded")), "plan_seen": bool(cfg.get("plan_seen")), "permission_seen": bool(cfg.get("permission_seen")),
            "jobs": {k: v.get("state") for k, v in _jobs.items()}}


def install_claude():
    """Anthropic's own installer, run in the background, its output passed to the window line by line."""
    if _jobs.get("install", {}).get("state") == "running":
        return {"ok": True, "already": True}
    if claude_cli.bin():
        return {"ok": True, "installed": True}
    _jobs["install"] = {"state": "running", "started": time.time()}

    def work():
        cmd = ["/bin/bash", "-c", f"set -o pipefail; /usr/bin/curl -fsSL {claude_cli.INSTALL_SCRIPT} | /bin/bash"]
        lines = []
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=claude_cli.env(), cwd=str(storage.where("scratch")))
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    lines.append(line)
                    _bus(k="log", job="install", line=line[-200:])
            ok = p.wait(timeout=900) == 0
        except Exception as e:
            ok, lines = False, lines + [str(e)]
        claude_cli.forget_status()
        ok = ok and bool(claude_cli.bin())
        _jobs["install"] = {"state": "done" if ok else "failed"}
        if not ok:
            import problems
            problems.record("service", "Installing Claude Code failed", "\n".join(lines[-60:]), {"step": "install-claude"},
                            say="Putting Claude on this Mac didn't work.")
        _bus(k="done", job="install", ok=ok)
    threading.Thread(target=work, daemon=True).start()
    return {"ok": True, "started": True}


def sign_in():
    """Sign-in happens in a small Terminal window (`magic signin`): Claude's own sign-in is made for a terminal, may ask
    the person to paste a code, and opens their browser. Here we only watch for it to finish."""
    if not claude_cli.bin():
        return {"ok": False, "error": "Claude isn't installed yet."}
    if os.environ.get("APP_MAGIC_DRY"):
        started = True
    else:
        r = subprocess.run([str(storage.SRC / "magic"), "signin"], capture_output=True, text=True, timeout=30, env=claude_cli.env())
        started = r.returncode == 0
        if not started:
            import problems
            err = (r.stderr or r.stdout)
            if "-1743" in err or "not allowed" in err.lower():          # macOS said no to App Magic controlling Terminal
                return {"ok": False, "error": "Your Mac didn't let App Magic open Terminal for the sign-in. Open System Settings, Privacy & Security, Automation, turn on Terminal under App Magic, then try again."}
            p = problems.record("service", "Could not open the sign-in window", err[-2000:], {"step": "sign-in"})
            return {"ok": False, "error": "The sign-in window didn't open.", "problem": p["id"]}
    if _jobs.get("signin", {}).get("state") != "waiting":
        _jobs["signin"] = {"state": "waiting", "started": time.time()}

        def watch():
            end = time.time() + 900
            while time.time() < end:
                time.sleep(2)
                if claude_cli.auth_status(fresh=True)["signed_in"]:
                    _jobs["signin"] = {"state": "done"}
                    _bus(k="done", job="signin", ok=True, email=claude_cli.auth_status()["email"])
                    return
            _jobs["signin"] = {"state": "timed-out"}
            _bus(k="done", job="signin", ok=False)
        threading.Thread(target=watch, daemon=True).start()
    return {"ok": True, "started": started}


def mark(**flags):
    storage.set_config(**{k: bool(v) for k, v in flags.items() if k in ("onboarded", "plan_seen", "permission_seen")})
    return status()
