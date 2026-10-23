#!/usr/bin/env python3
"""magicd: App Magic's local service.

The window, the menu bar item and the tools all talk to this one process. It answers only on 127.0.0.1, every API call
needs the token in ~/App Magic/.magic/token, and the Host header must be this Mac, so nothing else on the network and no
web page in a browser can drive it.

Run by the app (or `./magic service` while developing). Standard library only.
"""
import json, mimetypes, os, queue, re, signal, socket, subprocess, sys, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accounts, apps, audit, claude_cli, devaccounts, finder, onboarding, problems, storage
from claude_session import BUS, Sessions

VERSION = (storage.SRC / "VERSION").read_text().strip() if (storage.SRC / "VERSION").exists() else "0.2.0"
UI = storage.SRC / "ui"
SESSIONS = None
SEARCH = None                        # the app search in progress, or the last one finished
PORT = 0
BOOT = str(int(time.time()))


def log(*a):
    storage.append_line(storage.INTERNAL / "logs" / "service.log", storage.now() + " " + " ".join(str(x) for x in a))


def free_port(preferred):
    """The port App Magic used last time, so an open window finds the service again after a restart. The old service
    may still be letting go of it for a moment, so it is given a few seconds before another port is taken."""
    def ok(p):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", int(p)))
                return True
            except OSError:
                return False
    if preferred:
        for _ in range(40):
            if ok(preferred):
                return int(preferred)
            time.sleep(0.15)
    for p in range(8791, 8811):
        if ok(p):
            return p
    raise SystemExit("no free port for App Magic's service")


def state():
    ctx = storage.context_list()
    kinds = {}
    for m in ctx:
        kinds[m.get("kind", "note")] = kinds.get(m.get("kind", "note"), 0) + 1
    about = [ln for ln in (storage.where("rules") / "about-you.md").read_text().splitlines() if ln.startswith("- ")]
    a = accounts.all_accounts()
    app_list = storage.apps()
    return {"version": VERSION, "boot": BOOT, "home": str(storage.HOME), "source_dir": str(storage.SRC), "accounts": a,
            "apps": app_list, "context": {"count": len(ctx), "kinds": kinds, "recent": ctx[:6], "about_you": len(about), "brought_in": len([x for x in app_list if x.get("source") != "made-here"])},
            "capabilities": apps.capabilities(refresh=False), "busy": SESSIONS.busy(), "recent": BUS.recent[-8:],
            "claude_ready": a["accounts"][0]["signed_in"] and a["accounts"][0]["installed"], "onboarded": bool(storage.config().get("onboarded")),
            "problems": problems.open_ones()[:5]}


def kind_of(name):
    ext = Path(name).suffix.lower()
    if ext in (".mov", ".mp4", ".m4v", ".webm"): return "video"
    if ext in (".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".tiff"): return "photo"
    return "file"


def open_workshop(request):
    """Open the separate terminal window where Claude Code changes App Magic itself."""
    request = (request or "").strip()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rec = storage.where("changes") / f"{stamp}-{storage.slug(request)[:40] or 'open'}.md"
    rec.write_text(f"# {request or 'Opened the workshop'}\n\nAsked: {storage.now()}\nStatus: asked\n\n## What was asked\n\n{request or '(no request typed; the workshop was opened to talk it through)'}\n\n## What changed\n\n(the workshop fills this in)\n\n## How to undo\n\n(the workshop fills this in)\n")
    cmd = [str(storage.SRC / "magic"), "workshop", "--record", str(rec), request]
    if os.environ.get("APP_MAGIC_DRY"):
        return {"ok": True, "dry": True, "command": cmd, "record": str(rec)}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        storage.log_action("Opened the workshop", request[:200])
        if not ok:
            err = (r.stderr or r.stdout).strip()
            hint = " macOS may be asking whether App Magic can control Terminal: say OK, then try again." if "-1743" in err or "not allowed" in err.lower() else ""
            problems.record("workshop", "The workshop window did not open", err[-2000:], {"request": request[:300]})
            return {"ok": False, "record": str(rec), "error": "The workshop window didn't open." + hint}
        return {"ok": True, "record": str(rec)}
    except Exception as e:
        problems.from_exception("workshop", e, {"request": request[:300]})
        return {"ok": False, "error": "The workshop window didn't open."}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "magicd"

    def log_message(self, *a):
        pass

    # ---- plumbing ----
    def send_json(self, obj, code=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return False
        origin = self.headers.get("Origin")
        return (not origin) or origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    def authed(self):
        tok = storage.token()
        if self.headers.get("X-Magic-Token") == tok:
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "magic" and v == tok:
                return True
        return False

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def json_body(self):
        try:
            return json.loads(self.body() or b"{}")
        except Exception:
            return {}

    # ---- every request goes through one of these two, so nothing fails silently ----
    def do_GET(self):
        self._guard(self._get)

    def do_POST(self):
        self._guard(self._post)

    def _guard(self, fn):
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            p = problems.record("service", f"{type(e).__name__}: {e}", traceback.format_exc(), {"request": self.command + " " + self.path.split("?")[0]})
            log("error", self.path, repr(e))
            try:
                self.send_json({"error": "Something inside App Magic didn't work.", "problem": p["id"]}, 500)
            except Exception:
                pass

    # ---- GET ----
    def _get(self):
        if not self.host_ok():
            return self.send_json({"error": "not this Mac"}, 403)
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/api/health":
            return self.send_json({"ok": True, "version": VERSION, "home": str(storage.HOME), "boot": BOOT})
        if u.path in ("/", "/menubar") and q.get("t"):
            if q["t"] != storage.token():
                return self.send_json({"error": "wrong token"}, 403)
            self.send_response(302)
            self.send_header("Set-Cookie", f"magic={q['t']}; Path=/; HttpOnly; SameSite=Strict")
            rest = urllib.parse.urlencode({k: v for k, v in q.items() if k != "t"})
            self.send_header("Location", u.path + ("?" + rest if rest else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if u.path.startswith("/api/"):
            if not self.authed():
                return self.send_json({"error": "no token"}, 401)
            m = re.fullmatch(r"/api/thumb/([0-9a-f]{16})\.png", u.path)
            if m:
                f = storage.INTERNAL / "thumbs" / f"{m.group(1)}.png"
                if not f.is_file():
                    return self.send_json({"error": "not found"}, 404)
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=86400, immutable")
                self.end_headers()
                self.wfile.write(data)
                return
            return self.api_get(u.path, q)
        return self.static(u.path)

    def static(self, path):
        rel = {"/": "index.html", "/menubar": "menubar.html"}.get(path, path.lstrip("/"))
        f = (UI / rel).resolve()
        if UI.resolve() not in f.parents or not f.is_file():
            return self.send_json({"error": "not found"}, 404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(f.name)[0] or "application/octet-stream") + ("; charset=utf-8" if f.suffix in (".html", ".js", ".css") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def api_get(self, path, q):
        if path == "/api/state": return self.send_json(state())
        if path == "/api/conversation":
            c = SESSIONS.get(q.get("c", "home"))
            return self.send_json({"id": c.id, "items": c.items(), **c.activity(), "stream": c.stream, "design_url": c.design_url})
        if path == "/api/context": return self.send_json({"items": storage.context_list()})
        if path == "/api/accounts": return self.send_json(accounts.all_accounts())
        if path == "/api/capabilities": return self.send_json({"capabilities": apps.capabilities()})
        if path == "/api/github": return self.send_json(apps.github_repos())
        if path == "/api/apps/scan": return self.send_json({"search": SEARCH.public() if SEARCH else None})
        if path == "/api/audit":
            return self.send_json({"report": audit.latest("App Magic" if q.get("target") in ("self", None, "") else q["target"])})
        if path == "/api/about": return self.send_json({"text": (storage.where("rules") / "about-you.md").read_text()})
        if path == "/api/setup": return self.send_json(onboarding.status(fresh=q.get("fresh") == "1"))
        if path == "/api/setup/dev": return self.send_json({"accounts": devaccounts.status()})
        if path == "/api/problems": return self.send_json({"problems": problems.open_ones()})
        if path == "/api/actions":
            p = storage.INTERNAL / "logs" / "actions.log"
            return self.send_json({"lines": p.read_text().splitlines()[-200:] if p.exists() else []})
        if path == "/api/events": return self.events()
        return self.send_json({"error": "not found"}, 404)

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = BUS.subscribe()
        try:
            self.wfile.write(b": hello\n\n"); self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False, default=str).encode() + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": still here\n\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            BUS.unsubscribe(q)

    # ---- POST ----
    def _post(self):
        if not self.host_ok():
            return self.send_json({"error": "not this Mac"}, 403)
        if not self.authed():
            return self.send_json({"error": "no token"}, 401)
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        path = u.path
        if path == "/api/context/upload":
            name = re.sub(r"[^\w.\- ]", "_", q.get("name", "file"))[:120] or "file"
            tmp = storage.INTERNAL / "uploads" / f"{int(time.time() * 1000)}-{name}"
            tmp.write_bytes(self.body())
            m = storage.context_add(name, "", kind_of(name), path=str(tmp), source="dropped in")
            tmp.unlink(missing_ok=True)
            BUS.publish(t="refresh", what="context")
            return self.send_json({"ok": True, "item": m, "path": str(storage.where("context") / m["id"] / m["original"])})
        b = self.json_body()
        if path == "/api/chat":
            text = (b.get("text") or "").strip()
            c = SESSIONS.get(b.get("c", "home"))
            return self.send_json(c.send(text, shown=b.get("shown")))
        if path == "/api/stop": return self.send_json(SESSIONS.get(b.get("c", "home")).interrupt())
        if path == "/api/fresh":
            SESSIONS.fresh(b.get("c", "home"))
            return self.send_json({"ok": True})
        if path == "/api/context/add":
            m = storage.context_add(b.get("title", ""), b.get("summary", ""), b.get("kind", "note"), path=b.get("path"), text=b.get("text"), source=b.get("source", ""))
            BUS.publish(t="refresh", what="context")
            return self.send_json({"ok": True, "item": m})
        if path == "/api/context/remove":
            ok = storage.context_remove(re.sub(r"[^\w-]", "", b.get("id", "")))
            BUS.publish(t="refresh", what="context")
            return self.send_json({"ok": ok})
        if path == "/api/apps/find": return self.send_json(apps.find_by_hint(b.get("where", ""), bool(b.get("everywhere"))))
        if path == "/api/apps/scan":
            global SEARCH
            if SEARCH and SEARCH.status not in ("done", "stopped"):
                SEARCH.stopped = True
            SEARCH = finder.Search(b.get("where", ""), bool(b.get("everywhere")), emit=BUS.publish,
                                   pace=not os.environ.get("APP_MAGIC_FAST"), ai=not os.environ.get("APP_MAGIC_NO_AI"),
                                   render=not os.environ.get("APP_MAGIC_FAST"))
            threading.Thread(target=SEARCH.run, daemon=True).start()
            return self.send_json({"ok": True, "search": SEARCH.public()})
        if path == "/api/apps/scan/stop":
            if SEARCH: SEARCH.stopped = True
            return self.send_json({"ok": True})
        if path == "/api/apps/bring":
            items = [{"source": x} if isinstance(x, str) else x for x in (b.get("items") or b.get("sources") or [])][:300]
            if b.get("wait") or len(items) <= 1:                 # one app (or a caller that wants the answer): do it now
                results = [apps.bring_in(i.get("source", ""), i.get("name") or None, i.get("summary", ""), i.get("what", ""), thumb=i.get("thumb", ""), picture_kind=i.get("picture_kind", "")) for i in items]
                BUS.publish(t="refresh", what="apps")
                return self.send_json({"results": results})

            def work():
                results = []
                for n, i in enumerate(items, 1):
                    BUS.publish(t="bring", k="one", n=n, total=len(items), name=i.get("name") or Path(i.get("source", "")).name)
                    try:
                        results.append(apps.bring_in(i.get("source", ""), i.get("name") or None, i.get("summary", ""), i.get("what", ""), quick=True, thumb=i.get("thumb", ""), picture_kind=i.get("picture_kind", "")))
                    except Exception as e:
                        problems.from_exception("service", e, {"bring_in": i.get("source", "")})
                        results.append({"ok": False, "error": f"{i.get('name') or i.get('source')} could not be brought in."})
                BUS.publish(t="refresh", what="apps")
                BUS.publish(t="bring", k="done", total=len(items), ok=[r["app"]["name"] for r in results if r.get("ok")],
                            errors=[r.get("error", "") for r in results if not r.get("ok")],
                            to_fix=[r["app"]["name"] for r in results if r.get("ok") and r["audit"]["fails"]])
            threading.Thread(target=work, daemon=True).start()
            return self.send_json({"started": True, "total": len(items)})
        if path == "/api/apps/audit":
            if b.get("target") == "self":
                rep = audit.audit_self(PORT)
            else:
                a = storage.app_get(b.get("name", ""))
                if not a or not a.get("path"):
                    return self.send_json({"error": "That app has no code on this Mac yet, so there is nothing to check."}, 400)
                rep = audit.audit_app(a["path"], a["name"])
                a["audit"] = {"summary": rep["summary"], "fails": rep["fails"], "when": rep["when"]}
                a["status"] = "fine" if not rep["fails"] else rep["summary"]
                storage.app_save(a)
            rep = audit.save_report(rep)
            BUS.publish(t="refresh", what="apps")
            return self.send_json({"report": rep})
        if path == "/api/workshop": return self.send_json(open_workshop(b.get("request", "")))
        if path == "/api/setup/install-claude": return self.send_json(onboarding.install_claude())
        if path == "/api/setup/signin": return self.send_json(onboarding.sign_in())
        if path == "/api/setup/dev":
            r = devaccounts.choose(str(b.get("account", "")), want=b.get("want") if isinstance(b.get("want"), bool) else None,
                                   step=b.get("step"), done=bool(b.get("done")))
            BUS.publish(t="refresh", what="capabilities")
            return self.send_json(r)
        if path == "/api/setup/mark": return self.send_json(onboarding.mark(**{k: v for k, v in b.items() if isinstance(v, bool)}))
        if path == "/api/problem":                       # the window reporting something that broke on its side
            p = problems.record(b.get("where") or "window", str(b.get("what", ""))[:400], str(b.get("detail", ""))[:6000], b.get("context") or {})
            return self.send_json({"ok": True, "problem": problems.public(p)})
        if path == "/api/problems/fix":
            p = problems.get(b.get("id", ""))
            if not p:
                return self.send_json({"ok": False, "error": "That problem is no longer on record."}, 404)
            r = open_workshop(problems.fix_request(p))
            if r.get("ok"):
                problems.update(p["id"], fixing=storage.now())
                BUS.publish(t="problem", problem=problems.public(problems.get(p["id"])))
            return self.send_json(r)
        if path == "/api/problems/dismiss":
            ids = [x["id"] for x in problems.open_ones()] if b.get("all") else [b.get("id", "")]
            for i in ids:
                problems.update(i, dismissed=storage.now())
            BUS.publish(t="problems-changed")
            return self.send_json({"ok": True})
        if path == "/api/internal/event":
            if b.get("t") in ("show", "ask"):
                c = SESSIONS.get(b.get("c", "home"))
                c.add(k=b["t"], title=b.get("title", ""), text=b.get("body") or b.get("question") or "", bullets=b.get("bullets") or [], actions=b.get("actions") or b.get("choices") or [])
            elif b.get("t") == "design" and b.get("url"):
                SESSIONS.get(b.get("c", "home")).design_card(b["url"], b.get("app", ""))
            else:
                BUS.publish(**b)
            return self.send_json({"ok": True})
        return self.send_json({"error": "not found"}, 404)


def main():
    global SESSIONS, PORT
    storage.ensure()
    PORT = free_port(os.environ.get("APP_MAGIC_PORT") or storage.config().get("port"))
    storage.set_config(port=PORT, pid=os.getpid(), started=storage.now())
    (storage.INTERNAL / "port").write_text(str(PORT))
    SESSIONS = Sessions()
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True

    def bye(*_):
        log("stopping")
        SESSIONS.shutdown()
        os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    threading.Thread(target=lambda: apps.capabilities(refresh=True), daemon=True).start()
    threading.Thread(target=storage.version_source, daemon=True).start()
    log(f"magicd {VERSION} on 127.0.0.1:{PORT}, home {storage.HOME}")
    print(f"magicd on http://127.0.0.1:{PORT}/?t={storage.token()}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
