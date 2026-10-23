"""The AI layer: one windowless Claude Code per conversation, on the person's Claude account.

No API key is ever used. Claude Code runs signed in with the Claude subscription already on this Mac, in App Magic's home
folder, with App Magic's rules and App Magic's tools. Personal Claude Code settings (hooks, plugins) are left out with
--setting-sources so App Magic behaves the same on every Mac.

The launch recipe (stream-json both ways, Artifact tool switched on so Claude Design works headless) is the one Claude
Desk uses for its cards.
"""
import json, os, queue, re, signal, subprocess, sys, threading, time, uuid
from pathlib import Path

import accounts, claude_cli, problems, storage

RULES = storage.SRC / "service" / "rules" / "app-magic.md"
ARTIFACT_URL = re.compile(r"https://claude\.ai/(?:code/)?artifact/[\w-]+")
IDLE_SECONDS = 20 * 60


class Bus:
    """Everything the window needs to hear about, fanned out to every open page."""
    def __init__(self):
        self.subs, self.lock, self.recent = [], threading.Lock(), []

    def subscribe(self):
        q = queue.Queue(maxsize=2000)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def publish(self, **event):
        event.setdefault("at", time.time())
        with self.lock:
            if event.get("t") in ("note", "ask", "show"):
                self.recent = (self.recent + [event])[-30:]
            for q in list(self.subs):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass


BUS = Bus()


def tool_label(name, inp):
    """A few plain words for what Claude is doing right now."""
    inp = inp or {}
    if name == "Bash": return "Running: " + (inp.get("description") or (inp.get("command") or "")[:70])
    if name in ("Read", "Write", "Edit"): return {"Read": "Reading ", "Write": "Writing ", "Edit": "Changing "}[name] + Path(inp.get("file_path", "")).name
    if name == "Artifact": return "Working on the design canvas"
    if name in ("WebFetch", "WebSearch"): return "Looking something up"
    if name == "Agent": return "Handing part of this to a helper"
    if name == "Skill": return "Using a skill: " + str(inp.get("skill", ""))
    if name in ("ToolSearch", "TodoWrite"): return None
    if name.startswith("mcp__magic__"):
        return {"where_to_store": "Checking where this goes", "context_add": "Adding to what I know", "remember": "Remembering that", "security_audit": "Running the safety checks",
                "find_apps": "Looking for your apps", "bring_in_app": "Bringing an app in", "github_repos": "Asking GitHub what you have", "design_register": "Saving the design",
                "ask_user": "Asking you", "show_user": "Showing you", "request_app_change": "Opening the workshop"}.get(name.split("__")[-1], name.split("__")[-1].replace("_", " ").capitalize())
    if name.startswith("mcp__"):
        bits = name.split("__")
        return (bits[1].replace("claude_ai_", "").replace("_", " ") + ": " + bits[-1].replace("_", " "))[:80]
    return name


def preparing_label(name, js):
    """While Claude is still writing out a tool call (a big file can take a minute or more), a few words for what is coming,
    from the part of the call written so far."""
    def field(k):
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % k, js)
        return m.group(1) if m else ""
    kb = f" · {len(js) // 1024} KB so far" if len(js) >= 2048 else ""
    if name in ("Write", "Edit"):
        return ("Writing " if name == "Write" else "Changing ") + (Path(field("file_path")).name or "a file") + kb
    if name == "Artifact": return "Putting the design together" + kb
    if name == "Agent": return "Briefing a helper"
    if name == "Bash" and not (field("description") or field("command")): return "Getting a command ready"
    return (tool_label(name, {k: field(k) for k in ("description", "command", "file_path", "skill")}) or "Working") + kb


TASK_KIND = {"local_agent": "helper", "local_bash": "command"}
TASK_ENDED = ("completed", "failed", "killed", "stopped", "cancelled")


class Conversation:
    def __init__(self, cid):
        self.id = cid
        self.dir = storage.INTERNAL / "conversations"
        self.meta_path = self.dir / f"{cid}.json"
        self.log_path = self.dir / f"{cid}.jsonl"
        m = storage.read_json(self.meta_path)
        self.sid = m.get("sid") or str(uuid.uuid4())
        self.started = bool(m.get("started"))
        self.design_url = m.get("design_url", "")
        self.p, self.last, self.lock, self.pending = None, time.time(), threading.Lock(), 0
        self.stream, self.said_this_turn, self.interrupted = "", False, False
        self.tool_calls = {}                                             # tool_use id -> (name, input), to know what a result came from
        # What the window shows while work goes on: a turn running (`turn`), what it is doing and since when, the tool call
        # Claude is still writing out (`block`), and helpers and commands still running, in the background or not (`tasks`).
        self.turn, self.turn_since, self.doing, self.doing_since = False, 0.0, "", 0.0
        self.block, self.block_pub, self.tasks, self.just_finished = None, 0.0, {}, ("", 0.0)
        self.save_meta()

    @property
    def busy(self):
        return self.turn

    def activity(self):
        return {"busy": self.turn, "doing": self.doing if self.turn else "", "since": self.turn_since, "doing_since": self.doing_since,
                "tasks": list(self.tasks.values())}

    def status(self, doing=None):
        """Tell the window what is happening now. Sent on every change, so the window never has to guess."""
        if doing is not None and doing != self.doing:
            self.doing, self.doing_since = doing, time.time()
        BUS.publish(t="busy", c=self.id, **self.activity())

    def begin_turn(self, doing):
        self.turn, self.turn_since = True, time.time()
        self.stream, self.said_this_turn, self.interrupted = "", False, False
        self.status(doing)

    def save_meta(self):
        storage.write_json(self.meta_path, {"id": self.id, "sid": self.sid, "started": self.started, "design_url": self.design_url})

    def items(self, limit=200):
        if not self.log_path.exists():
            return []
        out = []
        for line in self.log_path.read_text().splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out

    def add(self, **item):
        item["at"] = time.time()
        storage.append_line(self.log_path, json.dumps(item, ensure_ascii=False))
        BUS.publish(t="item", c=self.id, item=item)

    # ---- the process ----
    def spawn(self):
        cfg = storage.config()
        mcp = storage.INTERNAL / "mcp.json"
        storage.write_json(mcp, {"mcpServers": {"magic": {"command": sys.executable, "args": [str(storage.SRC / "service" / "tools_mcp.py")],
                                                           "env": {"APP_MAGIC_HOME": str(storage.HOME), "APP_MAGIC_PORT": str(cfg.get("port", "")), "APP_MAGIC_TOKEN": storage.token(),
                                                                   "APP_MAGIC_CONVERSATION": self.id}}}})
        os.chmod(mcp, 0o600)
        claude = claude_cli.bin()
        if not claude:
            raise FileNotFoundError("Claude Code is not installed")
        cmd = [claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--dangerously-skip-permissions", "--setting-sources", "project,local", "--mcp-config", str(mcp)]
        if RULES.exists():
            cmd += ["--append-system-prompt-file", str(RULES)]
        if cfg.get("model"):
            cmd += ["--model", cfg["model"]]
        if cfg.get("effort"):
            cmd += ["--effort", cfg["effort"]]
        cmd += ["--resume", self.sid] if self.started else ["--session-id", self.sid]
        # The Claude account only (claude_cli.env drops anything pointing at an API key or another setup). The Artifact
        # tool, which Claude Design's canvas needs, is off by default in claude -p and is switched back on here.
        env = claude_cli.env(CLAUDE_CODE_ARTIFACT=1, CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF=1, APP_MAGIC_HOME=storage.HOME)
        self.err_path = storage.INTERNAL / "logs" / f"claude-{self.id}.log"
        with open(self.err_path, "a") as err:
            err.write(f"\n--- {storage.now()} start\n")
            self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, cwd=str(storage.HOME), env=env,
                                      start_new_session=True, text=True, bufsize=1)
        threading.Thread(target=self.reader, args=(self.p,), daemon=True).start()

    def write(self, obj):
        try:
            self.p.stdin.write(json.dumps(obj) + "\n")
            self.p.stdin.flush()
            return True
        except Exception:
            return False

    def send(self, text, shown=None):
        """One message from the person. A slash command (/design ...) must be the whole message to be recognised."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Nothing to send."}
        with self.lock:
            # A message sent while Claude is still working is handed over straight away: Claude Code queues it and
            # takes it up when the current turn ends. `pending` counts turns not yet finished.
            if self.p is None or self.p.poll() is not None:
                self.pending = 0
                try:
                    self.spawn()
                except FileNotFoundError:
                    BUS.publish(t="setup-needed", why="claude-missing")
                    return {"ok": False, "error": "Claude isn't installed on this Mac yet. Setup will put it in.", "setup": True}
                except Exception as e:
                    p = problems.from_exception("claude", e, {"conversation": self.id})
                    return {"ok": False, "error": "Claude could not start.", "problem": p["id"]}
            queued = self.turn                     # a turn is running (the person's own, or one a finished background job started)
            self.pending += 1
            self.last = time.time()
        self.add(k="you", text=shown or text)
        if queued:
            self.status()
        else:
            self.begin_turn("Thinking")
        if not self.write({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}):
            self.pending = max(0, self.pending - 1)
            self.turn = self.pending > 0
            self.status()
            return {"ok": False, "error": "Claude did not start. Try again in a moment."}
        return {"ok": True}

    def interrupt(self):
        self.interrupted = True
        if self.p and self.p.poll() is None:
            self.write({"type": "control_request", "request_id": "int-" + uuid.uuid4().hex[:8], "request": {"subtype": "interrupt"}})
        return {"ok": True}

    def stop(self):
        if self.p and self.p.poll() is None:
            try:
                self.p.stdin.close()
            except Exception:
                pass
            try:
                os.killpg(self.p.pid, signal.SIGTERM)
            except Exception:
                pass
        self.p, self.turn, self.pending, self.tasks, self.block = None, False, 0, {}, None

    def reader(self, p):
        for line in p.stdout:
            try:
                o = json.loads(line)
            except Exception:
                continue
            try:
                self.handle(o)
            except Exception as e:
                storage.append_line(storage.INTERNAL / "logs" / "service.log", f"{storage.now()} handle error {e!r}")
        if self.p is p:                                    # the process ended
            was_busy, self.p, self.turn, self.pending, self.tasks, self.block = self.turn, None, False, 0, {}, None
            if was_busy and not self.interrupted:
                tail = self.stderr_tail()
                said = self.explain(tail)
                if said:
                    self.add(k="error", text=said)
                else:
                    pr = problems.record("claude", "Claude stopped in the middle of a reply", tail, {"conversation": self.id, "exit": p.poll()})
                    self.add(k="error", text="Claude stopped before finishing. Send your message again to carry on.", problem=pr["id"])
            self.status()

    def stderr_tail(self):
        try:
            return Path(self.err_path).read_text(errors="ignore")[-3000:]
        except Exception:
            return ""

    @staticmethod
    def explain(text):
        """A plain sentence for the problems that are not bugs: not signed in, out of usage. "" for anything else."""
        t = (text or "").lower()
        if any(k in t for k in ("not logged in", "/login", "please log in", "invalid api key", "oauth token has expired", "authentication_error", "login required")):
            BUS.publish(t="setup-needed", why="signed-out")
            return "Claude isn't signed in on this Mac. Open Setup (top right) to sign in, then send this again."
        if any(k in t for k in ("usage limit", "hit your limit", "rate limit", "limit reached", "out of usage")):
            return "Your Claude account has used up its allowance for now. It comes back on its own; the Accounts page shows when."
        if "overloaded" in t:
            return "Claude is very busy right now. Wait a minute and send this again."
        return ""

    def handle(self, o):
        t, sub = o.get("type"), o.get("subtype")
        self.last = time.time()
        if o.get("parent_tool_use_id"):                    # a helper's own messages: its progress, never App Magic's reply
            return self.helper_message(o)
        # Claude Code starts a turn by itself when a background job it was waiting on finishes. Nobody sent a message, so
        # the first sign of it is what makes the window show it is working again.
        if not self.turn and (t in ("assistant", "stream_event") or (t == "system" and sub == "status")):
            title, at = self.just_finished
            self.begin_turn(f"Picking up: {title}" if title and time.time() - at < 120 else "Working")
        if t == "system" and sub == "init":
            if not self.started:
                self.started = True
                self.save_meta()
            magic = next((m for m in o.get("mcp_servers", []) if m.get("name") == "magic"), {})
            BUS.publish(t="ready", c=self.id, model=o.get("model"), tools=len(o.get("tools", [])), magic_tools=magic.get("status"),
                        design="Artifact" in o.get("tools", []))
        elif t == "system" and sub and sub.startswith(("task_", "background_tasks")):
            self.task_event(o)
        elif t == "stream_event":
            ev = o.get("event") or {}
            et, cb, d = ev.get("type"), ev.get("content_block") or {}, ev.get("delta") or {}
            if et == "content_block_delta" and d.get("type") == "text_delta":
                self.stream += d.get("text", "")
                BUS.publish(t="delta", c=self.id, text=d.get("text", ""))
            elif et == "content_block_delta" and d.get("type") == "input_json_delta" and self.block:
                self.block["json"] += d.get("partial_json", "")
                if time.time() - self.block_pub > 0.8:     # often enough to see a big file growing, not for every few letters
                    self.block_pub = time.time()
                    self.status(preparing_label(self.block["name"], self.block["json"]))
            elif et == "content_block_start" and cb.get("type") == "text":
                self.stream = ""
                BUS.publish(t="stream-start", c=self.id)
                self.status("Answering")
            elif et == "content_block_start" and cb.get("type") in ("thinking", "redacted_thinking"):
                self.status("Thinking")
            elif et == "content_block_start" and cb.get("type") == "tool_use":
                self.block, self.block_pub = {"name": cb.get("name", ""), "json": ""}, time.time()
                self.status(preparing_label(self.block["name"], ""))
            elif et == "content_block_stop":
                self.block = None
        elif t == "assistant":
            for b in (o.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    self.said_this_turn = True
                    self.stream = ""
                    self.add(k="magic", text=b["text"])
                elif b.get("type") == "tool_use":
                    self.tool_calls[b.get("id")] = (b.get("name", ""), b.get("input") or {})
                    label = tool_label(b.get("name", ""), b.get("input"))
                    if label:
                        self.status(label)
                        self.add(k="step", text=label, tool=b.get("name", ""))
        elif t == "user":
            for b in (o.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self.tool_result(b)
                    if self.turn:
                        self.status("Thinking")
        elif t == "rate_limit_event":
            rec = accounts.record_rate(o.get("rate_limit_info") or {})
            BUS.publish(t="usage", usage=rec)
        elif t == "result":
            text = (o.get("result") or "").strip()
            if o.get("is_error") and not self.interrupted:
                said = self.explain(text)
                if said:
                    self.add(k="error", text=said)
                else:
                    pr = problems.record("claude", "Claude finished with an error", text[:2000], {"conversation": self.id, "subtype": o.get("subtype")})
                    self.add(k="error", text=(text or "Something went wrong.")[:600], problem=pr["id"])
            elif not self.said_this_turn and text:
                self.add(k="magic", text=text)
            # A turn a background job started answers no message of the person's. Claude Code says how many of their
            # messages are still waiting, which is the surest count when it folds one into the turn that was running.
            if o.get("queued_turn_count") == 0:
                self.pending = 0
            elif ((o.get("origin") or {}).get("kind") or "") != "task-notification":
                self.pending = max(0, self.pending - 1)
            self.turn, self.block = self.pending > 0, None
            self.said_this_turn, self.interrupted, self.stream = False, False, ""
            if self.turn:
                self.turn_since = time.time()
            self.status("Thinking" if self.turn else "")

    def tool_result(self, b):
        c = b.get("content")
        text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in (c or []) if isinstance(x, dict))
        name, args = self.tool_calls.pop(b.get("tool_use_id"), ("", {}))
        # Only a canvas Claude just published is a design. Quickstart, list and read results carry other
        # artifacts' links (design systems, references) and must not show up as the person's design.
        if name == "Artifact" and args.get("action", "publish") == "publish" and not args.get("asset") and not b.get("is_error"):
            m = ARTIFACT_URL.search(text[:4000])
            if m:
                self.design_card(m.group(0))

    def helper_message(self, o):
        """A helper (Claude's Agent tool) at work. What it does shows on its line in the window; its words stay out of the
        conversation, because Claude reads them and answers in its own."""
        task = next((x for x in self.tasks.values() if x.get("tool_use_id") == o.get("parent_tool_use_id")), None)
        for b in (o.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                self.tool_calls[b.get("id")] = (b.get("name", ""), b.get("input") or {})
                label = tool_label(b.get("name", ""), b.get("input"))
                if task is not None and label:
                    task["now"], task["now_since"] = label, time.time()
                    self.status()
            elif b.get("type") == "tool_result":
                self.tool_result(b)

    def task_event(self, o):
        """Helpers and commands that run alongside the conversation, Claude Code's task events. Each one gets a line in
        the window while it runs, and a line in the conversation when it starts in the background and when it ends."""
        sub, tid = o.get("subtype"), o.get("task_id")
        if sub == "background_tasks_changed":
            running = {x.get("task_id"): x for x in o.get("tasks") or []}
            for gone in [k for k, v in self.tasks.items() if v["background"] and k not in running]:
                self.tasks.pop(gone)
            for k, x in running.items():
                if k not in self.tasks:
                    self.tasks[k] = self.new_task(x, True)
        elif sub == "task_started":
            parent = self.tasks.get(o.get("parent_task_id"))
            if o.get("owned_by_subagent") or parent:
                if parent:
                    parent["now"], parent["now_since"] = "Running: " + (o.get("description") or "a command"), time.time()
            elif o.get("is_backgrounded") or o.get("task_type") == "local_agent":
                # A command Claude waits for is already the step on screen. It gets its own line only once it moves to
                # the background (background_tasks_changed then lists it).
                had = self.tasks.get(tid)
                self.tasks[tid] = {**self.new_task(o, bool(o.get("is_backgrounded"))), **({"since": had["since"]} if had else {})}
                if self.tasks[tid]["background"]:
                    self.add(k="task", state="started", kind=self.tasks[tid]["kind"], text=self.tasks[tid]["title"])
        elif sub == "task_progress" and tid in self.tasks:
            x = self.tasks[tid]
            x["tools"] = (o.get("usage") or {}).get("tool_uses", x["tools"])
            if o.get("description") and not x["now"]:
                x["now"], x["now_since"] = o["description"], time.time()
        elif sub in ("task_updated", "task_notification") and tid in self.tasks:
            state = (o.get("patch") or {}).get("status") if sub == "task_updated" else o.get("status")
            if state in TASK_ENDED:
                x = self.tasks.pop(tid)
                self.just_finished = (x["title"], time.time())
                if x["background"]:
                    self.add(k="task", state="done" if state == "completed" else "failed", kind=x["kind"], text=x["title"],
                             took=round(time.time() - x["since"]))
        else:
            return
        self.status()

    @staticmethod
    def new_task(x, background):
        return {"id": x.get("task_id"), "kind": TASK_KIND.get(x.get("task_type"), "job"), "title": (x.get("description") or "Something")[:120],
                "tool_use_id": x.get("tool_use_id"), "background": background, "since": time.time(), "now": "", "now_since": 0.0, "tools": 0}

    def design_card(self, url, app=""):
        """A design shows up in the conversation as a card the person opens when they want to. It never takes over the
        window by itself. The same canvas published again (a change to it) gets no second card."""
        if not url:
            return
        if url != self.design_url:
            self.design_url = url
            self.save_meta()
            self.add(k="design", url=url, app=app)
        BUS.publish(t="design", c=self.id, url=url, app=app)


class Sessions:
    def __init__(self):
        self.convs, self.lock = {}, threading.Lock()
        threading.Thread(target=self.reaper, daemon=True).start()

    def get(self, cid):
        cid = re.sub(r"[^\w-]", "", cid or "home") or "home"
        with self.lock:
            if cid not in self.convs:
                self.convs[cid] = Conversation(cid)
            return self.convs[cid]

    def fresh(self, cid):
        """Forget a conversation's Claude session (the written history stays on disk under a dated name)."""
        c = self.get(cid)
        c.stop()
        if c.log_path.exists():
            c.log_path.rename(c.log_path.with_name(f"{cid}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"))
        c.meta_path.unlink(missing_ok=True)
        with self.lock:
            self.convs.pop(c.id, None)
        return self.get(cid)

    def busy(self):
        return [{"c": c.id, "busy": c.busy, "background": len(c.tasks)} for c in self.convs.values() if c.busy or c.tasks]

    def reaper(self):
        while True:
            time.sleep(60)
            for c in list(self.convs.values()):
                quiet = time.time() - c.last
                # Not while a helper or command is still running in the background: stopping would kill it. A job that
                # has said nothing for three hours is taken to be lost.
                if c.p and not c.busy and quiet > IDLE_SECONDS and (not c.tasks or quiet > 3 * 3600):
                    c.stop()                               # sleeps; the next message resumes the same session

    def shutdown(self):
        for c in list(self.convs.values()):
            c.stop()
