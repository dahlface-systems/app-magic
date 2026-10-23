#!/usr/bin/env python3
"""Tests for App Magic's service. They run against a throwaway home folder and never start Claude or open a window.

    python3 tests/test_service.py
"""
import json, os, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="appmagic-test-"))
os.environ["APP_MAGIC_HOME"] = str(TMP / "home")
os.environ["APP_MAGIC_DRY"] = "1"
os.environ["APP_MAGIC_NO_AI"] = "1"                      # never reach the real Claude from the tests
os.environ["APP_MAGIC_DEV_FAKE"] = json.dumps({"xcode": True, "xcode_account": False, "certificate": False, "gcloud": False, "gcloud_account": False})
sys.path.insert(0, str(SRC / "service"))
import apps, audit, devaccounts, finder, storage  # noqa: E402

finder._ME = {"emails": {"t@t"}, "name": "t", "login": "tester"}      # the test author is "the person"

FAKE_KEY = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0" * 2          # shaped like a key, not one


def make_app(name, leaky=False, git=True):
    d = TMP / "projects" / name
    d.mkdir(parents=True)
    (d / "package.json").write_text(json.dumps({"name": name, "description": "A recipe site for the family", "dependencies": {"next": "15.0.0"}}))
    (d / "README.md").write_text(f"# {name}\n\nA recipe site for the family.\n")
    (d / "index.js").write_text("export default function Home(){ return 'hi' }\n")
    (d / "node_modules" / "left-pad").mkdir(parents=True)
    (d / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1\n")
    (d / ".env").write_text("DATABASE_URL=postgres://user:hunter2hunter2@db.example.com/app\n")
    (d / ".gitignore").write_text("node_modules\n" + ("" if leaky else ".env\n"))
    if leaky:
        (d / "config.js").write_text(f"const key = '{FAKE_KEY}'\n")
        (d / "vercel.json").write_text("{}")
    if git:
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first"]):
            subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    return d


class Storage(unittest.TestCase):
    def test_layout_and_rules(self):
        home = storage.ensure()
        for folder in ("Context", "Designs", "Apps", "Capabilities", "Accounts", "Changes", "Rules", "Audits", ".magic"):
            self.assertTrue((home / folder).is_dir(), folder)
        self.assertIn("where_to_store", (home / "CLAUDE.md").read_text())
        self.assertEqual(oct(home.stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct((home / ".magic" / "token").stat().st_mode & 0o777), "0o600")
        self.assertEqual(storage.where("designs", "My Orders!").name, "my-orders")
        with self.assertRaises(KeyError):
            storage.where("nonsense")

    def test_context_roundtrip(self):
        storage.ensure()
        f = TMP / "note.txt"; f.write_text("green rows are paid")
        m = storage.context_add("Orders sheet", "Green rows are paid.", "file", path=str(f))
        self.assertTrue((storage.where("context") / m["id"] / "original.txt").exists())
        self.assertIn(m["id"], [x["id"] for x in storage.context_list()])
        self.assertTrue(storage.context_remove(m["id"]))


class Audit(unittest.TestCase):
    def test_clean_app_passes_keys_and_versions(self):
        storage.ensure()
        r = audit.audit_app(make_app("clean-recipes"))
        by = {c["key"]: c for c in r["checks"]}
        self.assertEqual(len(r["checks"]), 5)
        self.assertEqual(by["keys"]["status"], "pass", by["keys"])
        self.assertEqual(by["versions"]["status"], "pass")
        self.assertEqual(by["private"]["status"], "pass")

    def test_leaky_app_is_caught_and_key_is_masked(self):
        storage.ensure()
        r = audit.save_report(audit.audit_app(make_app("leaky-recipes", leaky=True)))
        by = {c["key"]: c for c in r["checks"]}
        self.assertEqual(by["keys"]["status"], "fail")
        text = json.dumps(r) + Path(r["saved"]).read_text()
        self.assertNotIn(FAKE_KEY, text, "the report must never contain the secret itself")
        self.assertNotIn("hunter2hunter2", text)
        self.assertEqual(by["lock"]["status"], "fail")          # set up for the web, no sign-in
        self.assertGreaterEqual(r["fails"], 2)

    def test_no_history_fails_versions(self):
        storage.ensure()
        by = {c["key"]: c for c in audit.audit_app(make_app("no-git", git=False))["checks"]}
        self.assertEqual(by["versions"]["status"], "fail")

    def test_self_audit(self):
        storage.ensure()
        r = audit.audit_self()
        self.assertEqual({c["key"] for c in r["checks"]}, {"door", "folder", "keys", "power", "versions"})
        by = {c["key"]: c for c in r["checks"]}
        self.assertEqual(by["folder"]["status"], "pass")
        self.assertEqual(by["power"]["status"], "unknown")       # bypass mode is reported, never hidden


class Apps(unittest.TestCase):
    def test_bring_in(self):
        storage.ensure()
        src = make_app("family-recipes", leaky=True)
        r = apps.bring_in(str(src))
        self.assertTrue(r["ok"], r)
        dst = Path(r["app"]["path"])
        self.assertTrue((dst / "index.js").exists())
        self.assertFalse((dst / "node_modules").exists(), "libraries are left out")
        self.assertFalse((dst / ".env").exists(), "secret files are left out")
        self.assertIn(".env", r["app"]["left_out"]["secrets"])
        self.assertTrue((dst / ".git").exists(), "version history comes along")
        self.assertTrue((src / ".env").exists(), "the original is untouched")
        self.assertEqual(storage.app_get("family-recipes")["source"], "a folder on this Mac")
        self.assertFalse(apps.bring_in(str(src))["ok"], "bringing it in twice is refused")


def git(d, *args, author="t <t@t>"):
    name, email = author.split(" <")[0], author.split("<")[1].rstrip(">")
    subprocess.run(["git", "-c", f"user.email={email}", "-c", f"user.name={name}", *args], cwd=d, check=True, capture_output=True)


class Finder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.ensure()
        cls.root = TMP / "finder" / "stuff"
        mine = cls.root / "orders-dashboard"; mine.mkdir(parents=True)
        (mine / "package.json").write_text(json.dumps({"name": "orders", "description": "Orders that fill themselves in", "dependencies": {"vite": "5"}}))
        (mine / "src").mkdir(); (mine / "src" / "main.ts").write_text("// the orders list\nconsole.log('orders')\n")
        git(mine, "init", "-q"); git(mine, "add", "-A"); git(mine, "commit", "-qm", "first")
        tool = cls.root / "nested" / "deeper" / "quit-everything"; tool.mkdir(parents=True)      # found two levels down, no package file
        (tool / "Makefile").write_text("all:\n"); (tool / "quit.swift").write_text("import AppKit\n"); (tool / "README.md").write_text("# Quit everything\n\nQuits every open app at once.\n")
        (tool / "helper.swift").write_text("// helper\n")
        theirs = cls.root / "somebody-elses-library"; theirs.mkdir()
        (theirs / "package.json").write_text(json.dumps({"name": "lib", "description": "A date library"})); (theirs / "index.js").write_text("module.exports = {}\n")
        git(theirs, "init", "-q"); git(theirs, "add", "-A")
        for n in range(3):
            (theirs / f"f{n}.js").write_text("1\n"); git(theirs, "add", "-A"); git(theirs, "commit", "-qm", f"c{n}", author="Stranger <s@elsewhere.example>")
        git(theirs, "remote", "add", "origin", "https://github.com/stranger/lib.git")
        backup = cls.root / "orders-dashboard-backup"; backup.mkdir()
        (backup / "package.json").write_text(json.dumps({"name": "orders"})); (backup / "index.js").write_text("1\n")
        docs = cls.root / "tax-papers"; docs.mkdir(); git(docs, "init", "-q")
        for n in range(8):
            (docs / f"doc{n}.md").write_text("notes\n")
        (cls.root / "holiday-photos").mkdir(); (cls.root / "holiday-photos" / "a.jpg").write_text("x")
        (mine / "node_modules" / "dep").mkdir(parents=True); (mine / "node_modules" / "dep" / "package.json").write_text("{}")

    def test_finds_several_and_sorts_them(self):
        r = finder.find(str(self.root))
        by = {a["name"]: a for a in r["apps"]}
        self.assertEqual(set(by), {"orders-dashboard", "quit-everything", "somebody-elses-library", "orders-dashboard-backup", "tax-papers"})
        self.assertEqual(by["orders-dashboard"]["verdict"], "yours")
        self.assertEqual(by["quit-everything"]["verdict"], "yours", "a tool with no package file, two folders down, still counts")
        self.assertEqual(by["somebody-elses-library"]["verdict"], "not-yours")
        self.assertEqual(by["orders-dashboard-backup"]["verdict"], "copy")
        self.assertEqual(by["tax-papers"]["verdict"], "not-an-app")
        self.assertTrue(all(a["why"] for a in r["apps"]))
        self.assertGreater(r["folders_looked_through"], 5)

    def test_what_they_typed(self):
        places, name, everywhere = finder.places_from_hint("look through my dev folder and see what should come in")
        self.assertEqual(name, "", "ordinary words are not a name to filter by")
        places, name, everywhere = finder.places_from_hint("it's in Documents somewhere, I think it's called recipes")
        self.assertEqual(name, "recipes")
        places, name, everywhere = finder.places_from_hint("look all over my computer")
        self.assertTrue(everywhere); self.assertEqual(places, [Path.home()])
        self.assertTrue(finder.places_from_hint("")[2])
        places, name, everywhere = finder.places_from_hint(str(self.root))
        self.assertEqual(places, [self.root]); self.assertFalse(everywhere)

    def test_search_reports_each_step(self):
        seen = []
        s = finder.Search(str(self.root), emit=lambda **e: seen.append(e), pace=False, ai=False)
        s.run()
        kinds = [e["k"] for e in seen]
        self.assertEqual(kinds[0], "start"); self.assertEqual(kinds[-1], "done")
        for k in ("look", "found", "reading", "read", "phase"):
            self.assertIn(k, kinds)
        self.assertEqual(kinds.count("found"), 5); self.assertEqual(kinds.count("read"), 5)
        self.assertLess(kinds.index("found"), kinds.index("reading"))
        self.assertIn("look like your app", s.phase)

    def test_names_and_summaries(self):
        self.assertEqual(finder.plain_name("task-list-standalone"), "Task List Standalone")
        self.assertEqual(finder.plain_name("211-213-water_tracker"), "211 213 Water Tracker")
        r = finder.find(str(self.root))
        self.assertTrue(all(a["label"] for a in r["apps"]), "every find has a name to show before Claude suggests one")
        src = self.root / "nested" / "deeper" / "quit-everything"
        b = apps.bring_in(str(src), "Quit Everything", "Quits every open app at once. A small Mac tool you wrote.", "Quits every open app at once.")
        self.assertTrue(b["ok"], b)
        a = storage.app_get("Quit Everything")
        self.assertEqual(a["name"], "Quit Everything"); self.assertEqual(a["folder_name"], "quit-everything")
        self.assertIn("small Mac tool", a["summary"])
        self.assertTrue(Path(a["path"]).name == "quit-everything" and Path(a["path"]).is_dir())

    def test_pictures_are_real_or_absent(self):
        app = TMP / "finder" / "pics" / "with-shot"; (app / "docs").mkdir(parents=True)
        (app / "package.json").write_text("{}")
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082")
        (app / "docs" / "screenshot-home.png").write_bytes(png + b"\0" * 20000)
        (app / "icon.png").write_bytes(png)
        self.assertEqual(finder.find_picture(app)["kind"], "screenshot")
        page = TMP / "finder" / "pics" / "plain-page"; page.mkdir(parents=True)
        (page / "index.html").write_text("<h1>Hello</h1>" + "x" * 400); (page / "icon.png").write_bytes(png)
        got = finder.find_picture(page)
        self.assertEqual(got["kind"], "page"); self.assertTrue(got["icon"].endswith("icon.png"))
        needs = TMP / "finder" / "pics" / "needs-building"; needs.mkdir(parents=True)
        (needs / "package.json").write_text(json.dumps({"devDependencies": {"vite": "5"}})); (needs / "index.html").write_text("<div id=app></div>" + "x" * 400)
        self.assertIsNone(finder.find_picture(needs), "a page that needs a build step is not drawn, and nothing is invented")
        self.assertIsNone(finder.find_picture(TMP / "finder" / "stuff" / "tax-papers"))
        tid = finder.make_thumb(str(app / "icon.png"), "icon")
        self.assertRegex(tid, r"^[0-9a-f]{16}$")
        self.assertTrue((Path(os.environ["APP_MAGIC_HOME"]) / ".magic" / "thumbs" / f"{tid}.png").exists())

    def test_pointing_at_one_app(self):
        r = finder.find(str(self.root / "orders-dashboard"))
        self.assertEqual([a["name"] for a in r["apps"]], ["orders-dashboard"])


class Designs(unittest.TestCase):
    def test_only_a_published_canvas_makes_a_card(self):
        import claude_session
        c = claude_session.Conversation("designs-test")
        use = lambda i, args: c.handle({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": "Artifact", "input": args}]}})
        result = lambda i, text: c.handle({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": text}]}})
        cards = lambda: [x for x in c.items() if x.get("k") == "design"]
        use("q", {"action": "quickstart", "intent": "design"}); result("q", "Design system: https://claude.ai/artifact/SystemLink")
        c.handle({"type": "assistant", "message": {"content": [{"type": "text", "text": "See https://claude.ai/artifact/MentionedLink"}]}})
        self.assertEqual(cards(), [])                                    # links Claude only read or mentioned are not designs
        use("p", {"type_url": "https://claude.ai/artifact/T", "title": "Orders"}); result("p", "Published https://claude.ai/artifact/Canvas1")
        use("p2", {"file_path": "x.html", "url": "https://claude.ai/artifact/Canvas1"}); result("p2", "Updated https://claude.ai/artifact/Canvas1")
        self.assertEqual([x["url"] for x in cards()], ["https://claude.ai/artifact/Canvas1"])   # a change to it is no second card
        self.assertEqual(c.design_url, "https://claude.ai/artifact/Canvas1")


class DevAccounts(unittest.TestCase):
    def test_optional_choices_ticks_and_capabilities(self):
        storage.ensure()
        storage.set_config(dev_accounts={})
        a = {x["key"]: x for x in devaccounts.status()}
        self.assertEqual(set(a), {"apple", "google"})
        self.assertIsNone(a["apple"]["want"])                                    # nothing chosen until they say
        xcode = next(s for s in a["apple"]["steps"] if s["id"] == "xcode")
        self.assertTrue(xcode["done"] and xcode["seen"])                         # seen on the Mac, no tick needed
        self.assertEqual(devaccounts.capability_state("apple")[0], "not-yet")
        devaccounts.choose("apple", want=True)
        state, note = devaccounts.capability_state("apple")
        self.assertEqual((state, note), ("needs-you", "4 steps left"))
        r = devaccounts.choose("apple", step="enroll", done=True)
        self.assertTrue(r["ok"])
        self.assertEqual(devaccounts.capability_state("apple")[1], "3 steps left")
        devaccounts.choose("apple", step="enroll", done=False)                   # a tick can be taken back
        self.assertEqual(devaccounts.capability_state("apple")[1], "4 steps left")
        devaccounts.choose("google", want=False)
        self.assertEqual(devaccounts.capability_state("google")[0], "not-yet")
        self.assertFalse(devaccounts.choose("apple", step="nope", done=True)["ok"])
        self.assertFalse(devaccounts.choose("android", want=True)["ok"])
        caps = {c["name"]: c for c in apps.capabilities()}
        self.assertEqual(caps["make-iphone-and-mac-apps"]["state"], "needs-you")
        self.assertIn("use-google", caps)
        old = storage.where("capabilities", "make-iphone-and-mac-apps") / "capability.json"   # an older copy is moved on
        storage.write_json(old, {**storage.read_json(old), "check": "xcodebuild"})
        storage.ensure()
        self.assertEqual(storage.read_json(old)["check"], "apple")


class Activity(unittest.TestCase):
    def test_background_jobs_and_helpers(self):
        import claude_session
        c = claude_session.Conversation("activity-test")
        sent = []
        c.status = (lambda orig: (lambda doing=None: (orig(doing), sent.append(c.doing))))(c.status)
        c.begin_turn("Thinking")
        c.handle({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Write"}}})
        c.block_pub = 0
        c.handle({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"file_path": "/a/b/page.html", "content": "' + "x" * 4000}}})
        self.assertEqual(c.doing, "Writing page.html · 3 KB so far")
        c.handle({"type": "system", "subtype": "task_started", "task_id": "fg", "description": "Build it", "is_backgrounded": False, "task_type": "local_bash"})
        self.assertEqual(c.tasks, {})                                                # a command Claude waits for is just the step
        c.handle({"type": "system", "subtype": "task_started", "task_id": "h", "tool_use_id": "ag", "description": "Look around", "is_backgrounded": True, "task_type": "local_agent"})
        c.handle({"type": "assistant", "parent_tool_use_id": "ag", "message": {"content": [{"type": "text", "text": "helper words"}, {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "/a/x.png"}}]}})
        self.assertEqual(c.tasks["h"]["now"], "Reading x.png")
        self.assertFalse([x for x in c.items() if x.get("k") == "magic"])           # a helper's words are not the reply
        c.handle({"type": "result", "subtype": "success", "result": "", "queued_turn_count": 0})
        self.assertFalse(c.busy)
        self.assertEqual(len(c.activity()["tasks"]), 1)                             # still running after the reply
        c.handle({"type": "system", "subtype": "task_notification", "task_id": "h", "status": "completed"})
        self.assertEqual(c.tasks, {})
        c.handle({"type": "system", "subtype": "status", "status": "requesting"})   # a turn nobody asked for
        self.assertTrue(c.busy)
        self.assertEqual(c.doing, "Picking up: Look around")
        c.handle({"type": "result", "subtype": "success", "result": "", "queued_turn_count": 0, "origin": {"kind": "task-notification"}})
        self.assertFalse(c.busy)
        self.assertEqual([x["state"] for x in c.items() if x.get("k") == "task"], ["started", "done"])


class Tools(unittest.TestCase):
    def rpc(self, p, obj):
        p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()
        return json.loads(p.stdout.readline())

    def test_mcp_handshake_and_calls(self):
        env = dict(os.environ, APP_MAGIC_PORT="")
        p = subprocess.Popen([sys.executable, str(SRC / "service" / "tools_mcp.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        try:
            r = self.rpc(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
            self.assertEqual(r["result"]["serverInfo"]["name"], "app-magic")
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); p.stdin.flush()
            tools = self.rpc(p, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
            names = {t["name"] for t in tools}
            for must in ("where_to_store", "context_add", "remember", "security_audit", "bring_in_app", "design_register", "ask_user", "request_app_change"):
                self.assertIn(must, names)
            self.assertTrue(all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools))
            r = self.rpc(p, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "where_to_store", "arguments": {"kind": "designs", "name": "Orders"}}})
            self.assertTrue(json.loads(r["result"]["content"][0]["text"])["path"].endswith("Designs/orders"))
            r = self.rpc(p, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "where_to_store", "arguments": {"kind": "nope"}}})
            self.assertTrue(r["result"]["isError"])
            r = self.rpc(p, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "remember", "arguments": {"fact": "Likes large text"}}})
            self.assertIn("Likes large text", (Path(os.environ["APP_MAGIC_HOME"]) / "Rules" / "about-you.md").read_text())
        finally:
            p.kill()


class Service(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.ensure()
        cls.p = subprocess.Popen([sys.executable, str(SRC / "service" / "magicd.py")], stdout=subprocess.PIPE, text=True,
                                 env=dict(os.environ, APP_MAGIC_PORT="8805", APP_MAGIC_FAST="1"))
        line = cls.p.stdout.readline()
        cls.base = line.split()[2].split("/?t=")[0]
        cls.tok = line.strip().split("?t=")[1]

    @classmethod
    def tearDownClass(cls):
        cls.p.terminate()

    def call(self, path, body=None, token=True, host=None):
        req = urllib.request.Request(self.base + path, data=(json.dumps(body).encode() if body is not None else None))
        if token: req.add_header("X-Magic-Token", self.tok)
        if host: req.add_header("Host", host)
        if body is not None: req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_locked_door(self):
        self.assertEqual(self.call("/api/state", token=False)[0], 401)
        self.assertEqual(self.call("/api/state", host="evil.example.com")[0], 403)
        self.assertEqual(self.call("/api/health", token=False)[0], 200)

    def test_state_and_pages(self):
        code, s = self.call("/api/state")
        self.assertEqual(code, 200)
        for k in ("accounts", "apps", "context", "capabilities", "home"):
            self.assertIn(k, s)
        html = urllib.request.urlopen(self.base + "/").read().decode()
        self.assertIn("App Magic", html)
        self.assertEqual(self.call("/../service/magicd.py")[0], 404)

    def test_dev_accounts_routes(self):
        code, r = self.call("/api/setup/dev")
        self.assertEqual(code, 200)
        self.assertEqual([a["key"] for a in r["accounts"]], ["apple", "google"])
        code, r = self.call("/api/setup/dev", {"account": "google", "want": True})
        self.assertTrue(r["ok"])
        g = next(a for a in r["accounts"] if a["key"] == "google")
        self.assertTrue(g["want"])
        code, r = self.call("/api/setup/dev", {"account": "google", "step": "google-account", "done": True})
        g = next(a for a in r["accounts"] if a["key"] == "google")
        self.assertTrue(next(s for s in g["steps"] if s["id"] == "google-account")["done"])

    def test_audit_self_and_workshop_dry(self):
        code, r = self.call("/api/apps/audit", {"target": "self"})
        self.assertEqual(code, 200)
        self.assertEqual(len(r["report"]["checks"]), 5)
        self.assertEqual(r["report"]["checks"][0]["status"], "pass")     # only this Mac can talk to it
        code, r = self.call("/api/workshop", {"request": "Add a weekly summary button"})
        self.assertTrue(r["ok"] and r["dry"])
        self.assertIn("workshop", r["command"])
        self.assertIn("weekly summary", Path(r["record"]).read_text())

    def test_search_job(self):
        code, r = self.call("/api/apps/scan", {"where": str(TMP / "finder" / "stuff")})
        self.assertTrue(r["ok"])
        for _ in range(100):
            time.sleep(0.2)
            s = self.call("/api/apps/scan")[1]["search"]
            if s["status"] == "done": break
        self.assertEqual(s["status"], "done")
        self.assertEqual(len(s["items"]), 5)
        self.assertEqual(len([i for i in s["items"] if i["verdict"] == "yours"]), 2)

    def test_upload_goes_to_context(self):
        req = urllib.request.Request(self.base + "/api/context/upload?name=orders.csv", data=b"name,amount\nA,1\n", headers={"X-Magic-Token": self.tok})
        r = json.loads(urllib.request.urlopen(req).read())
        self.assertTrue(Path(r["path"]).exists())
        self.assertIn("Context", r["path"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
