"""When something goes wrong inside App Magic.

Every error the window, the service, Claude or the Mac shell runs into is written down here once, with enough detail
for someone (or the workshop agent) to fix it. The window shows a quiet banner with "Fix it with Claude", which opens
the workshop with a request already written from the record.
"""
import json, secrets, time, traceback

import storage

LOG = lambda: storage.INTERNAL / "logs" / "problems.jsonl"     # noqa: E731
_recent = {}                                                    # what -> (id, time), to fold repeats together


def _publish(p):
    try:
        from claude_session import BUS
        BUS.publish(t="problem", problem=public(p))
    except Exception:
        pass


def public(p):
    return {k: p.get(k) for k in ("id", "when", "where", "what", "say", "count", "fixing", "dismissed")}


def record(where, what, detail="", context=None, say=""):
    """Write one problem down and tell the window. Repeats of the same problem within two minutes are counted, not
    added again. Returns the record."""
    what = (what or "Something went wrong").strip()[:400]
    key = f"{where}|{what}"
    seen = _recent.get(key)
    if seen and time.time() - seen[1] < 120:
        items = all_problems()
        for p in items:
            if p["id"] == seen[0]:
                p["count"] = p.get("count", 1) + 1
                p["last"] = storage.now()
                _rewrite(items)
                _publish(p)
                return p
    p = {"id": time.strftime("%Y%m%d%H%M%S-") + secrets.token_hex(2), "when": storage.now(), "where": where, "what": what,
         "say": say or plain(where, what), "detail": (detail or "")[-6000:], "context": context or {}, "count": 1}
    storage.append_line(LOG(), json.dumps(p, ensure_ascii=False, default=str))
    _recent[key] = (p["id"], time.time())
    _publish(p)
    return p


def from_exception(where, exc, context=None, say=""):
    return record(where, f"{type(exc).__name__}: {exc}", traceback.format_exc(), context, say)


def plain(where, what):
    return {"window": "Something on this screen didn't work.", "service": "Something inside App Magic didn't work.",
            "claude": "Claude stopped unexpectedly.", "search": "The search for your apps ran into a problem.",
            "shell": "App Magic's window ran into a problem.", "workshop": "The workshop could not open."}.get(where, "Something went wrong.")


def all_problems():
    p = LOG()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines()[-300:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _rewrite(items):
    path = LOG()
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text("".join(json.dumps(p, ensure_ascii=False, default=str) + "\n" for p in items[-300:]))
    tmp.replace(path)


def get(pid):
    return next((p for p in all_problems() if p["id"] == pid), None)


def update(pid, **fields):
    items = all_problems()
    for p in items:
        if p["id"] == pid:
            p.update(fields)
            _rewrite(items)
            return p
    return None


def open_ones():
    return [public(p) for p in all_problems() if not p.get("dismissed") and not p.get("fixed")][-20:][::-1]


def fix_request(p):
    """What the workshop is asked to do about a problem. Written for Claude Code working in App Magic's source."""
    ctx = json.dumps(p.get("context") or {}, ensure_ascii=False, default=str)[:1500]
    return (f"App Magic ran into a problem and the person asked you to fix it.\n\n"
            f"Where: {p['where']}\nWhat: {p['what']}\nWhen: {p['when']} (seen {p.get('count', 1)} time(s))\n"
            f"Context: {ctx}\n\nDetails:\n{(p.get('detail') or '(none)')[-3500:]}\n\n"
            f"The full record is problem {p['id']} in {LOG()}. The service log is {storage.INTERNAL / 'logs' / 'service.log'}.\n"
            "Find the cause in App Magic's code, fix it, add a test that would have caught it, run ./magic test, then put the fix "
            "in front of the person (./magic reload, or ./magic rebuild if you changed the Mac shell). Explain in two plain "
            "sentences what was wrong and what you changed.")
