"""Which AI logins exist on this Mac and how full each one is.

App Magic never holds a password or a token. It reads what each tool already says about itself, and it learns usage from
the rate-limit notes Claude Code sends while it works (saved by claude_session.py in .magic/usage.json).
"""
import base64, json, shutil, time
from pathlib import Path

import claude_cli, storage

HOME = Path.home()


def _plan(tier):
    t = (tier or "").lower()
    if "max_20x" in t: return "Max (20x)"
    if "max_5x" in t: return "Max (5x)"
    if "max" in t: return "Max"
    if "pro" in t: return "Pro"
    if "team" in t: return "Team"
    return "Free" if t else ""


def _when(ts):
    if not ts:
        return ""
    d = ts - time.time()
    if d <= 0: return "now"
    if d < 3600: return f"in {int(d // 60) or 1} min"
    if d < 86400: return f"in {int(d // 3600)} h"
    return f"in {int(d // 86400)} day{'s' if d >= 172800 else ''}"


def claude():
    st = claude_cli.auth_status()
    acct = (storage.read_json(HOME / ".claude.json").get("oauthAccount") or {})
    email = st["email"]
    same = (acct.get("emailAddress") or "").lower() == email.lower()      # the plan is only read when it is the same login
    out = {"service": "Claude", "name": email or "Claude", "email": email, "installed": st["installed"], "signed_in": st["signed_in"],
           "plan": _plan(acct.get("organizationRateLimitTier") or acct.get("userRateLimitTier")) if same else "", "in_use": st["signed_in"],
           "used": None, "say": "", "state": "ready"}
    if not out["installed"]:
        out.update(state="needs-you", say="Claude Code is not installed yet.")
        return out
    if not st["signed_in"]:
        out.update(state="needs-you", say="Sign in with Claude to start.")
        return out
    u = (storage.read_json(storage.INTERNAL / "usage.json").get(email) or {})
    five, week = u.get("five_hour") or {}, u.get("seven_day") or {}
    if five.get("utilization") is not None:
        out["used"] = max(0.0, min(1.0, float(five["utilization"])))
        out["windows"] = [{"label": "This session", "used": out["used"], "resets": _when(five.get("resetsAt"))}]
        if week.get("utilization") is not None:
            out["windows"].append({"label": "This week", "used": max(0.0, min(1.0, float(week["utilization"]))), "resets": _when(week.get("resetsAt"))})
        worst = max(w["used"] for w in out["windows"])
        if u.get("status") == "rejected" or worst >= 1:
            out.update(state="full", say="Full. Fresh again " + (_when(five.get("resetsAt")) or "soon") + ".")
        elif worst >= 0.85:
            out.update(state="nearly-full", say="Nearly full. Fresh again " + (_when(five.get("resetsAt")) or "soon") + ".")
        else:
            out.update(state="in-use", say="Plenty left." if worst < 0.5 else "About half used." if worst < 0.7 else "Getting there.")
        out["as_of"] = u.get("at", "")
    else:
        out.update(state="in-use", say="No usage figure yet. It appears after the first job.")
    return out


def _jwt_payload(tok):
    try:
        part = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def codex():
    out = {"service": "Codex", "name": "Codex", "email": "", "installed": shutil.which("codex", path=claude_cli.path_env()) is not None, "signed_in": False, "plan": "", "in_use": False,
           "used": None, "say": "", "state": "ready"}
    if not out["installed"]:
        out.update(state="not-yet", say="Not installed.")
        return out
    auth = storage.read_json(HOME / ".codex" / "auth.json")
    p = _jwt_payload(((auth.get("tokens") or {}).get("id_token")) or "")
    if p:
        info = p.get("https://api.openai.com/auth") or {}
        out.update(signed_in=True, email=p.get("email", ""), plan=(info.get("chatgpt_plan_type") or "").title())
        until = info.get("chatgpt_subscription_active_until")
        out["say"] = "Ready." + (f" Subscription runs to {str(until)[:10]}." if until else "")
    else:
        out.update(state="needs-you", say="Codex needs you to sign in.")
    return out


def grok():
    ok = shutil.which("grok", path=claude_cli.path_env()) is not None
    return {"service": "Grok", "name": "Grok", "email": "", "installed": ok, "signed_in": None, "plan": "", "in_use": False, "used": None,
            "state": "ready" if ok else "not-yet", "say": "Installed. I can't see its usage yet." if ok else "Not installed."}


def all_accounts():
    accts = [claude(), codex(), grok()]
    need = [a for a in accts if a["state"] == "needs-you"]
    fine = [a for a in accts if a["state"] in ("ready", "in-use", "nearly-full")]
    line = (f"{len(fine)} account{'s' if len(fine) != 1 else ''} fine" if fine else "No accounts yet") + (f", {need[0]['service']} needs you" if need else "")
    summary = [{k: a[k] for k in ("service", "email", "plan", "state", "say", "used")} for a in accts]
    path = storage.where("accounts") / "accounts.json"
    if storage.read_json(path).get("accounts") != summary:                    # only written when something changed
        storage.write_json(path, {"updated": storage.now(), "accounts": summary})
    return {"accounts": accts, "line": line, "needs_you": bool(need)}


def record_rate(info):
    """Save what a rate_limit_event from Claude Code says about the signed-in account."""
    email = claude_cli.auth_status()["email"] or "claude"
    path = storage.INTERNAL / "usage.json"
    d = storage.read_json(path)
    rec = d.get(email) or {}
    for k, v in (info.get("unifiedWindows") or {}).items():
        if isinstance(v, dict):
            rec[k] = {"utilization": v.get("utilization"), "resetsAt": v.get("resetsAt")}
    if "five_hour" not in rec and info.get("rateLimitType") == "five_hour":
        rec["five_hour"] = {"utilization": None, "resetsAt": info.get("resetsAt")}
    rec["status"] = info.get("status")
    rec["at"] = storage.now()
    d[email] = rec
    storage.write_json(path, d)
    return rec
