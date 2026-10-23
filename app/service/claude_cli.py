"""Finding and starting Claude Code, the one way App Magic reaches Claude.

Every part of App Magic that runs Claude (the conversation, the search, the workshop) goes through here, so it always
runs the same program, on the person's own Claude account, with the same clean environment.
"""
import json, os, shutil, subprocess, threading, time
from pathlib import Path

HOME = Path.home()
INSTALL_SCRIPT = "https://claude.ai/install.sh"          # Anthropic's native installer: puts claude in ~/.local/bin


def path_env():
    return f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")


def bin():
    """The claude program, or "" when it is not installed. APP_MAGIC_CLAUDE points the tests at a stand-in."""
    if os.environ.get("APP_MAGIC_CLAUDE"):
        return os.environ["APP_MAGIC_CLAUDE"]
    found = shutil.which("claude", path=path_env())
    if found:
        return found
    p = HOME / ".local" / "bin" / "claude"
    return str(p) if p.exists() else ""


def env(**extra):
    """The environment Claude runs in: the person's Claude account and nothing else. Variables that would point Claude
    Code at an API key, another configuration or the caller's own session are removed."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC")) and k not in ("CODEX_THREAD_ID", "CODEX_SESSION_ID")}
    e["PATH"] = path_env()
    e.update({k: str(v) for k, v in extra.items()})
    return e


_status = {"at": 0.0, "value": None}
_lock = threading.Lock()


def auth_status(fresh=False):
    """{"installed", "signed_in", "email", "org", "method"} from `claude auth status`, remembered for ten seconds."""
    with _lock:
        if not fresh and _status["value"] and time.time() - _status["at"] < 10:
            return _status["value"]
    b = bin()
    out = {"installed": bool(b), "signed_in": False, "email": "", "org": "", "method": "", "version": ""}
    if b:
        try:
            r = subprocess.run([b, "auth", "status", "--json"], capture_output=True, text=True, timeout=15, env=env())
            d = json.loads(r.stdout or "{}")
            out.update(signed_in=bool(d.get("loggedIn")), email=d.get("email") or "", org=d.get("orgName") or "", method=d.get("authMethod") or "")
        except Exception:
            pass
    with _lock:
        _status.update(at=time.time(), value=out)
    return out


def version():
    b = bin()
    if not b:
        return ""
    try:
        return subprocess.run([b, "--version"], capture_output=True, text=True, timeout=15, env=env()).stdout.strip().split(" ")[0]
    except Exception:
        return ""


def forget_status():
    with _lock:
        _status["at"] = 0.0
