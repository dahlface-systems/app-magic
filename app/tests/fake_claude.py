#!/usr/bin/env python3
"""A stand-in for the claude program, for the end-to-end tests. It speaks the same stream-json as Claude Code, so the
service and the window cannot tell the difference, and it never uses anyone's Claude account.

It understands a few words in a message, so a test can make it do things:
  TOOL:<name> <json>   call one of App Magic's tools through the real MCP server, the way Claude would
  DESIGN               answer with a design canvas link
  CRASH                die in the middle of a reply
  SIGNEDOUT            answer the way Claude Code does when nobody is signed in
  SLOW                 take a few seconds, so Stop can be tested
Anything else gets "Fake Claude heard: <message>".

Signed-in state lives in $FAKE_CLAUDE_STATE (a folder): `auth login` signs in after two seconds.
"""
import json, os, re, subprocess, sys, time, uuid
from pathlib import Path

STATE = Path(os.environ.get("FAKE_CLAUDE_STATE", "/tmp/fake-claude"))
STATE.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]


def out(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()


def signed_in():
    return (STATE / "signed-in").exists() or not (STATE / "signed-out").exists()


if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": signed_in(), "authMethod": "claude.ai", "email": "sarah@example.com" if signed_in() else None}))
    sys.exit(0)
if args[:2] == ["auth", "login"]:
    print("Opening your browser to sign in…", flush=True)
    time.sleep(2)
    (STATE / "signed-in").write_text("yes")
    (STATE / "signed-out").unlink(missing_ok=True)
    print("Login successful.")
    sys.exit(0)
if args[:1] == ["--version"]:
    print("9.9.9 (Fake Claude)")
    sys.exit(0)


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default


# ---- the search's judge: plain text in, verdict lines out ----
if "-p" in args and "--input-format" not in args:
    prompt = sys.stdin.read()
    if opt("--output-format") == "json":                          # the overview
        print(json.dumps({"type": "result", "result": "You make a few small tools for your own work. The fake judge looked at each one."}))
        sys.exit(0)
    lines = []
    for line in prompt.split("Folders:", 1)[-1].splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        d = json.loads(line)
        name = d["folder"].rstrip("/").split("/")[-1]
        lines.append(json.dumps({"i": d["i"], "verdict": d["guess"], "name": "Fake " + name.replace("-", " ").title(),
                                 "what": f"Does what {name} does.", "summary": f"A summary of {name}, written by the fake judge.",
                                 "why": "The fake judge agreed with the first look."}))
    out({"type": "system", "subtype": "init", "model": "fake"})
    for line in lines:
        out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": line + "\n"}}})
        time.sleep(0.05)
    out({"type": "result", "subtype": "success", "result": "\n".join(lines)})
    sys.exit(0)


# ---- a conversation: stream-json both ways ----
class Tools:
    """App Magic's real tool server, started the way Claude Code would start it."""
    def __init__(self):
        self.p = None
        cfg = opt("--mcp-config")
        if not cfg:
            return
        server = json.loads(Path(cfg).read_text())["mcpServers"]["magic"]
        self.p = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                  env={**os.environ, **server.get("env", {})})
        self.call_raw("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})

    def call_raw(self, method, params):
        rid = uuid.uuid4().hex[:6]
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def call(self, name, arguments):
        r = self.call_raw("tools/call", {"name": name, "arguments": arguments})
        return r["result"]["content"][0]["text"]


tools, sid, first = Tools(), opt("--session-id") or opt("--resume") or str(uuid.uuid4()), True
if signed_in():
    out({"type": "system", "subtype": "init", "model": "fake", "session_id": sid, "tools": ["Bash", "Artifact", "mcp__magic__where_to_store"],
         "mcp_servers": [{"name": "magic", "status": "connected" if tools.p else "none"}]})


def say(text, steps=6):
    out({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text"}}})
    words = re.findall(r"\S+\s*", text)
    n = max(1, len(words) // steps)
    for i in range(0, len(words), n):
        out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "".join(words[i:i + n])}}})
        time.sleep(0.04)
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


for raw in sys.stdin:
    try:
        msg = json.loads(raw)
    except Exception:
        continue
    if msg.get("type") == "control_request":
        continue
    text = " ".join(b.get("text", "") for b in msg["message"]["content"] if b.get("type") == "text")
    if not signed_in() or "SIGNEDOUT" in text:
        out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Not logged in · Please run /login"})
        continue
    if "CRASH" in text:
        sys.stderr.write("Traceback (most recent call last):\n  fake crash for the tests\nRuntimeError: the fake Claude fell over\n")
        sys.stderr.flush()
        say("Starting, then", 1)
        sys.exit(3)
    if "BACKGROUND" in text:
        # Like Claude Code: a tool call written out bit by bit, a helper whose own messages carry parent_tool_use_id, a
        # background command, a reply that ends the turn, and later a turn nobody asked for when the jobs finish.
        out({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "Write"}}})
        for bit in ['{"file_path": "/x/index', '.html", "content": "', "a" * 3000, '"}']:
            out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": bit}}})
            time.sleep(0.5)
        out({"type": "stream_event", "event": {"type": "content_block_stop"}})
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "/x/index.html"}}]}})
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "w1", "content": "ok"}]}})
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "ag", "name": "Agent", "input": {"description": "Check the pictures", "run_in_background": True}}]}})
        out({"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "h1", "task_type": "local_agent", "description": "Check the pictures"}]})
        out({"type": "system", "subtype": "task_started", "task_id": "h1", "tool_use_id": "ag", "description": "Check the pictures", "is_backgrounded": True, "task_type": "local_agent"})
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "ag", "content": "started"}]}})
        out({"type": "system", "subtype": "task_started", "task_id": "b1", "tool_use_id": "bb", "description": "Render the clips", "is_backgrounded": True, "task_type": "local_bash"})
        say("Both are running. I will tell you when they finish.")
        out({"type": "result", "subtype": "success", "is_error": False, "result": "", "queued_turn_count": 0})
        time.sleep(1)
        out({"type": "assistant", "parent_tool_use_id": "ag", "message": {"content": [{"type": "tool_use", "id": "hr", "name": "Read", "input": {"file_path": "/x/sheetA.jpg"}}]}})
        out({"type": "assistant", "parent_tool_use_id": "ag", "message": {"content": [{"type": "text", "text": "HELPER-ONLY words"}]}})
        time.sleep(1.5)
        out({"type": "system", "subtype": "task_notification", "task_id": "h1", "status": "completed"})
        out({"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "b1", "task_type": "local_bash", "description": "Render the clips"}]})
        out({"type": "system", "subtype": "status", "status": "requesting"})
        say("The helper is done: the pictures look fine.")
        out({"type": "result", "subtype": "success", "is_error": False, "result": "", "queued_turn_count": 0, "origin": {"kind": "task-notification"}})
        time.sleep(1)
        out({"type": "system", "subtype": "task_updated", "task_id": "b1", "patch": {"status": "completed"}})
        out({"type": "system", "subtype": "background_tasks_changed", "tasks": []})
        out({"type": "system", "subtype": "status", "status": "requesting"})
        say("The clips are rendered too.")
        out({"type": "result", "subtype": "success", "is_error": False, "result": "", "queued_turn_count": 0, "origin": {"kind": "task-notification"}})
        continue
    if "SLOW" in text:
        for _ in range(20):
            out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "…"}}})
            time.sleep(0.3)
    for m in re.finditer(r"TOOL:(\w+)\s+(\{.*?\})(?=\s*(?:TOOL:|$))", text, re.S):
        name, a = m.group(1), json.loads(m.group(2))
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": f"mcp__magic__{name}", "input": a}]}})
        result = tools.call(name, a) if tools.p else "no tools"
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": result[:2000]}]}})
    if "DESIGN" in text:
        # Like Claude Design: the quickstart answer carries other artifacts' links (a design system), the publish carries the canvas.
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a1", "name": "Artifact", "input": {"action": "quickstart", "intent": "design"}}]}})
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a1", "content": "Design system: https://claude.ai/artifact/FakeDesignSystem9"}]}})
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a2", "name": "Artifact", "input": {"type_url": "https://claude.ai/artifact/FakeType", "title": "Recipe box"}}]}})
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a2", "content": "Published https://claude.ai/artifact/FakeCanvas123"}]}})
        say("I drew three screens. Here is the canvas: https://claude.ai/artifact/FakeCanvas123")
    else:
        say(f"Fake Claude heard: {text.strip()[:200]}")
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.3, "resetsAt": time.time() + 7200},
                                                                                              "seven_day": {"utilization": 0.1, "resetsAt": time.time() + 400000}}}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": ""})
