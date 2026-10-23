#!/usr/bin/env python3
"""App Magic's tools, offered to Claude Code as an MCP server over stdio.

Claude Code starts this next to every App Magic conversation (see claude_session.py). Each tool is a small, plainly named
action on App Magic's own folder or window. Tools that should show something in the window tell the service, which passes
it to the page.
"""
import json, os, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accounts, apps, audit, storage

PORT, TOKEN, CONV = os.environ.get("APP_MAGIC_PORT", ""), os.environ.get("APP_MAGIC_TOKEN", ""), os.environ.get("APP_MAGIC_CONVERSATION", "home")


def tell_window(**event):
    """Pass an event to the window through the service. Quietly does nothing when the service is not there (tests)."""
    if not PORT:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/internal/event", data=json.dumps(dict(event, c=CONV)).encode(),
                                     headers={"Content-Type": "application/json", "X-Magic-Token": TOKEN})
        urllib.request.urlopen(req, timeout=4).read()
        return True
    except Exception:
        return False


def call_service(path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Magic-Token": TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


# ---- the tools ---------------------------------------------------------------------------------------
def where_to_store(kind, name=None):
    p = storage.where(kind, name)
    return {"path": str(p), "rule": storage.MAP[kind][1]}


def storage_map():
    return {"home": str(storage.HOME), "places": {k: {"path": str(storage.HOME / v[0]), "holds": v[1]} for k, v in storage.MAP.items()},
            "app_magic_code": str(storage.SRC), "secrets": "Mac keychain only, never in a file"}


def context_add(title, summary, kind="note", path=None, text=None):
    m = storage.context_add(title, summary, kind, path, text)
    tell_window(t="refresh", what="context")
    return m


def context_list(query=None):
    items = storage.context_list()
    if query:
        q = query.lower()
        items = [m for m in items if q in (m.get("title", "") + " " + m.get("summary", "") + " " + m.get("kind", "")).lower()]
    return {"count": len(items), "items": items[:80]}


def context_describe(id, summary, title=None):
    f = {"summary": summary}
    if title:
        f["title"] = title
    m = storage.context_update(id, **f)
    tell_window(t="refresh", what="context")
    return m


def remember(fact, about="you"):
    """about: 'you' | 'lesson' | 'app:<name>' | 'capability:<name>'"""
    fact = fact.strip()
    if about == "you":
        p = storage.where("rules") / "about-you.md"
        if p.read_text().strip().endswith("or delete any line."):
            p.write_text("# What App Magic knows about you\n\n")
    elif about == "lesson":
        p = storage.where("rules") / "lessons.md"
    elif about.startswith("app:"):
        p = storage.where("apps") / (storage.slug(about[4:]) + ".notes.md")
    elif about.startswith("capability:"):
        p = storage.where("capabilities", about[11:]) / "lessons.md"
    else:
        return {"error": "about must be 'you', 'lesson', 'app:<name>' or 'capability:<name>'"}
    storage.append_line(p, f"- {fact}  ({storage.now()[:10]})")
    tell_window(t="refresh", what="context")
    return {"saved_in": str(p)}


def recall(query=None):
    out = {}
    for p in list(storage.where("rules").glob("*.md")) + list(storage.where("apps").glob("*.notes.md")) + list(storage.where("capabilities").glob("*/lessons.md")):
        text = p.read_text()
        if query:
            lines = [ln for ln in text.splitlines() if query.lower() in ln.lower()]
            if lines:
                out[str(p.relative_to(storage.HOME))] = lines[:30]
        else:
            out[str(p.relative_to(storage.HOME))] = text[:3000]
    return out


def apps_list():
    return {"apps": storage.apps()}


def app_save(name, path="", source="made-here", kind="", summary="", lives="", status=""):
    a = storage.app_get(name) or {"name": name}
    for k, v in (("path", path), ("source", source), ("kind", kind), ("summary", summary), ("lives", lives), ("status", status)):
        if v:
            a[k] = v
    a = storage.app_save(a)
    tell_window(t="refresh", what="apps")
    return a


def find_apps(where="", everywhere=False):
    return apps.find_by_hint(where, everywhere)


def github_repos():
    return apps.github_repos()


def bring_in_app(source, name=None, summary=""):
    r = apps.bring_in(source, name, summary)
    tell_window(t="refresh", what="apps")
    return r


def design_register(app, url="", folder="", screens=None):
    rec = storage.design_save(app, url, folder, screens)
    tell_window(t="design", url=rec.get("url", ""), app=app)
    tell_window(t="refresh", what="apps")
    return rec


def designs_list():
    return {"designs": storage.designs()}


def security_audit(target):
    """target: an app's name, a folder path, or 'self' for App Magic itself."""
    if target in ("self", "App Magic", "app-magic"):
        rep = audit.audit_self(storage.config().get("port"))
    else:
        a = storage.app_get(target)
        path = (a or {}).get("path") or target
        rep = audit.audit_app(path, (a or {}).get("name"))
        if a and not rep.get("error"):
            a["audit"] = {"summary": rep["summary"], "fails": rep["fails"], "when": rep["when"]}
            a["status"] = "fine" if not rep["fails"] else rep["summary"]
            storage.app_save(a)
    rep = audit.save_report(rep)
    tell_window(t="audit", report=rep)
    tell_window(t="refresh", what="apps")
    return rep


def capability_list():
    return {"capabilities": apps.capabilities()}


def capability_set(name, state, note="", title=None, service=None, instructions=None):
    if state not in ("not-yet", "signing-in", "on", "needs-you"):
        return {"error": "state must be one of: not-yet, signing-in, on, needs-you"}
    d = storage.where("capabilities", name)
    cap = storage.read_json(d / "capability.json") or {"name": storage.slug(name), "title": title or name, "service": service or "", "check": "manual"}
    cap.update(state=state, note=note)
    if title: cap["title"] = title
    if service: cap["service"] = service
    storage.write_json(d / "capability.json", cap)
    if instructions:
        (d / "instructions.md").write_text(f"# {cap['title']}\n\n{instructions.strip()}\n")
    if not (d / "lessons.md").exists():
        (d / "lessons.md").write_text(f"# Lessons: {cap['title']}\n")
    tell_window(t="refresh", what="capabilities")
    return cap


def account_status():
    return accounts.all_accounts()


def show_user(title, body="", bullets=None, actions=None):
    """Put a card in the conversation: a heading, a sentence or two, numbered points, and up to four reply buttons."""
    tell_window(t="show", title=title, body=body, bullets=bullets or [], actions=(actions or [])[:4])
    return {"shown": True}


def ask_user(question, choices=None):
    tell_window(t="ask", question=question, choices=(choices or [])[:4])
    return {"asked": True, "now": "Stop here and end your turn. Their answer arrives as the next message."}


def notify(text):
    tell_window(t="note", text=text)
    storage.log_action("Note", text)
    return {"ok": True}


def open_in_window(url):
    tell_window(t="open", url=url)
    return {"ok": True}


def log_action(what, detail=""):
    storage.log_action(what, detail)
    return {"ok": True}


def request_app_change(request):
    """The person wants App Magic itself changed. Opens the workshop: a separate terminal window running Claude Code on
    App Magic's own code, which can rebuild and reopen the app."""
    try:
        return call_service("/api/workshop", {"request": request})
    except Exception as e:
        return {"ok": False, "error": f"Could not open the workshop: {e}"}


S, A, O = {"type": "string"}, {"type": "array", "items": {"type": "string"}}, "object"
TOOLS = [
    (where_to_store, "The one correct folder for a kind of thing. Call this before saving anything. kind is one of: " + ", ".join(storage.MAP) + ". Give a name to get that item's own folder.", {"kind": S, "name": S}, ["kind"]),
    (storage_map, "The whole layout of App Magic's home folder and what each place holds.", {}, []),
    (context_add, "Add something the person showed or told you to the context library, with a one or two sentence plain summary of what it tells you about how they work. kind: recording, photo, video, file, note, app.", {"title": S, "summary": S, "kind": S, "path": S, "text": S}, ["title", "summary"]),
    (context_list, "What is in the context library, newest first. Optional query filters by words.", {"query": S}, []),
    (context_describe, "Write or replace the plain summary of a context item after you have looked at it.", {"id": S, "summary": S, "title": S}, ["id", "summary"]),
    (remember, "Write down something learned so the next session knows it. about: 'you' (the person), 'lesson' (a general lesson), 'app:<name>', 'capability:<name>'.", {"fact": S, "about": S}, ["fact"]),
    (recall, "Read back what has been remembered (rules, what App Magic knows about the person, lessons, app notes). Optional query.", {"query": S}, []),
    (apps_list, "Every app App Magic knows: made here or brought in, where it lives, its last safety check.", {}, []),
    (app_save, "Create or update an app's entry in the registry (name is the key).", {"name": S, "path": S, "source": S, "kind": S, "summary": S, "lives": S, "status": S}, ["name"]),
    (find_apps, "Look for apps the person already has. where is a folder path or their own rough words ('my dev folder', 'in Documents, something called recipes'); everywhere=true searches the whole home folder. Each result has a first verdict (yours, not-yours, copy, not-an-app) and why. Read the code yourself before telling them which are theirs.", {"where": S, "everywhere": {"type": "boolean"}}, []),
    (github_repos, "List the person's GitHub repositories with a guess at what each is and which look like live apps.", {}, []),
    (bring_in_app, "Bring an existing app into App Magic. source is a folder path or a GitHub 'owner/name'. name is what the person will call it (a plain name, not the folder name); summary is two or three sentences about it. Copies it (the original is untouched), leaves out libraries and secret files, and runs the safety checks.", {"source": S, "name": S, "summary": S}, ["source"]),
    (design_register, "Record a design: the app it is for, the Claude Design canvas link, the folder holding its sources, and its screen names. It appears in the conversation as a card the person can open.", {"app": S, "url": S, "folder": S, "screens": A}, ["app"]),
    (designs_list, "Every saved design and its canvas link.", {}, []),
    (security_audit, "Run the five safety checks on an app (by name or folder path), or on App Magic itself with target 'self'. Saves the report and shows it.", {"target": S}, ["target"]),
    (capability_list, "What App Magic can do right now and the state of each capability: not-yet, signing-in, on, needs-you.", {}, []),
    (capability_set, "Create a capability or change its state. Give instructions when creating one: when and how to use it.", {"name": S, "state": S, "note": S, "title": S, "service": S, "instructions": S}, ["name", "state"]),
    (account_status, "The AI accounts on this Mac and how full each one is.", {}, []),
    (show_user, "Show a card in the conversation: a title, a short body, numbered points, and up to four reply buttons. Use for 'Here's what I saw' and similar.", {"title": S, "body": S, "bullets": A, "actions": A}, ["title"]),
    (ask_user, "Ask the person a question with up to four answer buttons, then stop and wait. Required before spending money, publishing, deleting, or sending anything as them.", {"question": S, "choices": A}, ["question"]),
    (notify, "A quiet one-line note that appears in the window and the menu bar.", {"text": S}, ["text"]),
    (open_in_window, "Open a web address in a pop-up browser over the conversation (a design canvas, a live app). Only when the person asks to see it now: finished designs already show up as a card they can open.", {"url": S}, ["url"]),
    (log_action, "Add a line to the plain log of what App Magic did on its own.", {"what": S, "detail": S}, ["what"]),
    (request_app_change, "The person wants App Magic ITSELF changed (its screens, wording, behavior, a new built-in ability). Opens the workshop terminal with their request. Do not edit App Magic's code yourself.", {"request": S}, ["request"]),
]
BY_NAME = {f.__name__: f for f, *_ in TOOLS}


def tool_list():
    return [{"name": f.__name__, "description": d, "inputSchema": {"type": O, "properties": props, "required": req}} for f, d, props, req in TOOLS]


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                                                        "capabilities": {"tools": {}}, "serverInfo": {"name": "app-magic", "version": "0.1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list()}}
    if method == "tools/call":
        p = msg.get("params") or {}
        fn = BY_NAME.get(p.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"No tool named {p.get('name')}"}], "isError": True}}
        try:
            out = fn(**{k: v for k, v in (p.get("arguments") or {}).items() if v is not None})
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(out, indent=1, ensure_ascii=False, default=str)[:60000]}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}}
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
    return None                                            # a notification: nothing to answer


def main():
    storage.ensure()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except Exception as e:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
