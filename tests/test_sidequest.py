import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("sidequest", str(ROOT / "bin" / "sidequest"))
spec = importlib.util.spec_from_loader("sidequest", loader)
sq = importlib.util.module_from_spec(spec)
loader.exec_module(sq)


def ns(**kw):
    base = dict(request="look into the flaky login test", brief=None, mode="brief", session=None,
                cwd=None, name=None, backend="auto", direction="right", ratio=0.5, focus=True,
                claude_bin="claude", claude_arg=None, start_timeout_ms=1000, dry_run=False,
                shell_ready_attempts=3)
    base.update(kw)
    return Namespace(**base)


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        rc, out, err = self.responses.pop(0) if self.responses else (0, "", "")
        return subprocess.CompletedProcess(cmd, rc, out, err)


class Helpers(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(sq.slugify("Look into the FLAKY login test, please!"), "look-into-the-flaky-login")
        self.assertEqual(sq.slugify("!!!"), "sidequest")

    def test_make_slug_prefers_name(self):
        import datetime
        now = datetime.datetime(2026, 11, 8, 9, 5, 7)
        self.assertEqual(sq.make_slug("auth spike", "whatever", now), "auth-spike-1108-090507")
        self.assertTrue(sq.make_slug(None, "fix the thing", now).startswith("fix-the-thing-"))

    def test_parent_session_env_and_override(self):
        self.assertEqual(sq.parent_session({"CLAUDE_CODE_SESSION_ID": "abc"}), "abc")
        self.assertEqual(sq.parent_session({"CLAUDE_CODE_SESSION_ID": "abc"}, "xyz"), "xyz")
        self.assertIsNone(sq.parent_session({}))

    def test_detect_backend(self):
        which = lambda name: f"/bin/{name}"
        self.assertEqual(sq.detect_backend({"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/s"}, which), "herdr")
        self.assertEqual(sq.detect_backend({"TMUX": "/tmp/x"}, which), "tmux")
        self.assertEqual(sq.detect_backend({}, which), "print")
        # herdr env but binary missing -> not herdr
        self.assertEqual(sq.detect_backend({"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/s", "TMUX": "x"},
                                           lambda n: None if n == "herdr" else "/bin/tmux"), "tmux")

    def test_compose_brief(self):
        text = sq.compose_brief("do X", "parent is building Y\n", parent="sess1", cwd="/w",
                               report_path=Path("/r/report.md"), slug="do-x-1")
        self.assertIn("# Sidequest: do-x-1", text)
        self.assertIn("## Context from the parent session\n\nparent is building Y", text)
        self.assertIn("`sess1`", text)
        self.assertIn("/r/report.md", text)
        self.assertIn("sidequest report do-x-1", text)
        self.assertTrue(text.rstrip().endswith("do X"))

    def test_compose_brief_without_context_omits_section(self):
        text = sq.compose_brief("do X", "", parent=None, cwd="/w", report_path=Path("/r"), slug="s")
        self.assertNotIn("## Context from the parent session", text)
        self.assertIn("(unknown)", text)

    def test_build_claude_args_brief(self):
        args = sq.build_claude_args(mode="brief", brief_path=Path("/b.md"), request=" hi ", session="s")
        self.assertEqual(args, ["claude", "--append-system-prompt-file", "/b.md", "hi"])

    def test_build_claude_args_full(self):
        args = sq.build_claude_args(mode="full", brief_path=Path("/b.md"), request="hi", session="s1",
                                    extra=["--model", "opus"])
        self.assertEqual(args, ["claude", "--resume", "s1", "--fork-session",
                                "--append-system-prompt-file", "/b.md", "--model", "opus", "hi"])

    def test_build_claude_args_full_requires_session(self):
        with self.assertRaises(SystemExit):
            sq.build_claude_args(mode="full", brief_path=Path("/b"), request="hi", session=None)

    def test_parse_herdr_pane_id(self):
        out = '{"id":"cli:pane:split","result":{"pane":{"pane_id":"w8:pJ"},"type":"pane_info"}}'
        self.assertEqual(sq.parse_herdr_pane_id(out), "w8:pJ")
        with self.assertRaises(RuntimeError):
            sq.parse_herdr_pane_id('{"result":{}}')

    def test_tmux_command(self):
        cmd = sq.tmux_command(["claude", "a b"], cwd="/w", direction="down", ratio=0.4, focus=False)
        self.assertEqual(cmd[:2], ["tmux", "split-window"])
        self.assertIn("-v", cmd)
        self.assertIn("-d", cmd)
        self.assertIn("40%", cmd)
        self.assertEqual(cmd[-1], "claude 'a b'")


class SpawnFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"SIDEQUEST_STATE_DIR": self.tmp.name, "CLAUDE_CODE_SESSION_ID": "parent-1",
                    "HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/sock"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_writes_brief_and_meta(self):
        brief_src = Path(self.tmp.name) / "ctx.md"
        brief_src.write_text("We are mid-refactor of auth.\n")
        out = StringIO()
        meta = sq.spawn(ns(brief=str(brief_src), dry_run=True, cwd="/w"), env=self.env,
                        run=FakeRunner([]), out=out)
        d = Path(self.tmp.name) / meta["slug"]
        self.assertTrue((d / "brief.md").is_file())
        self.assertTrue((d / "meta.json").is_file())
        brief = (d / "brief.md").read_text()
        self.assertIn("We are mid-refactor of auth.", brief)
        self.assertIn("look into the flaky login test", brief)
        self.assertIn("`parent-1`", brief)
        self.assertEqual(meta["parent_session"], "parent-1")
        self.assertEqual(meta["cwd"], "/w")
        self.assertIn("[dry-run]", out.getvalue())

    def test_herdr_spawn_splits_starts_agent_then_prompts(self):
        runner = FakeRunner([
            (0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""),
            (0, '{"result":{"type":"ok"}}', ""),
            (0, '{"result":{"agent":{"agent_status":"working"}}}', ""),
        ])
        meta = sq.spawn(ns(cwd="/w", ratio=0.4, focus=False), env=self.env, run=runner, out=StringIO())
        self.assertEqual(meta["pane_id"], "w8:pZ")
        self.assertEqual(meta["backend"], "herdr")
        self.assertTrue(meta["agent_registered"])
        split, start, prompt = runner.calls
        self.assertEqual(split[:3], ["herdr", "pane", "split"])
        self.assertIn("--no-focus", split)
        self.assertIn("0.4", split)
        self.assertEqual(start[:3], ["herdr", "agent", "start"])
        self.assertEqual(start[3], meta["slug"])
        self.assertIn("w8:pZ", start)
        dd = start.index("--")
        self.assertEqual(start[dd + 1:], ["--append-system-prompt-file", meta["brief"]])
        for a in start:
            self.assertNotIn("\n", a)  # herdr refuses multi-line argv
        self.assertEqual(prompt[:4], ["herdr", "agent", "prompt", meta["slug"]])
        self.assertTrue(prompt[4].startswith("look into the flaky login test\n\n[sidequest "))
        self.assertIn(meta["report"], prompt[4])
        self.assertEqual(prompt[5:], ["--wait", "--until", "working", "--timeout", "20000"])
        self.assertTrue(meta["prompt_delivery"]["verified"])
        self.assertEqual(meta["prompt_delivery"]["attempts"], 1)
        self.assertNotIn("start_fallback", meta)

    def test_herdr_prompt_stall_rescued_by_enter(self):
        stalled = '{"error":{"code":"agent_prompt_stalled"}}'
        runner = FakeRunner([
            (0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""),
            (0, "", ""),                 # agent start
            (1, stalled, ""),            # prompt --wait stalls
            (0, "", ""),                 # send-keys enter
            (0, '{"result":{"agent":{"agent_status":"working"}}}', ""),  # agent wait working
        ])
        meta = sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=StringIO(), sleep=lambda s: None)
        self.assertEqual(runner.calls[3], ["herdr", "agent", "send-keys", meta["slug"], "enter"])
        self.assertEqual(runner.calls[4][:4], ["herdr", "agent", "wait", meta["slug"]])
        self.assertTrue(meta["prompt_delivery"]["verified"])
        self.assertIn("enter rescued it", " ".join(meta["prompt_delivery"]["log"]))

    def test_herdr_prompt_stall_then_repaste(self):
        stalled = '{"error":{"code":"agent_prompt_stalled"}}'
        runner = FakeRunner([
            (0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""),
            (0, "", ""),
            (1, stalled, ""), (0, "", ""), (1, '{"error":{"code":"timeout"}}', ""),  # stall, enter, still not
            (0, "", ""),                 # second prompt --wait succeeds
        ])
        meta = sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=StringIO(), sleep=lambda s: None)
        self.assertEqual(runner.calls[5][:3], ["herdr", "agent", "prompt"])
        self.assertTrue(meta["prompt_delivery"]["verified"])
        self.assertEqual(meta["prompt_delivery"]["attempts"], 2)

    def test_herdr_prompt_never_verified_warns_but_does_not_exit(self):
        stalled = '{"error":{"code":"agent_prompt_stalled"}}'
        runner = FakeRunner([
            (0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""), (0, "", ""),
            (1, stalled, ""), (0, "", ""), (1, "timeout", ""),
            (1, stalled, ""), (0, "", ""), (1, "timeout", ""),
        ])
        out = StringIO()
        meta = sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=out, sleep=lambda s: None)
        self.assertFalse(meta["prompt_delivery"]["verified"])
        self.assertIn("warning", out.getvalue())

    def test_herdr_prompt_real_error_stops_retrying(self):
        runner = FakeRunner([
            (0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""), (0, "", ""),
            (1, '{"error":{"code":"agent_not_found"}}', ""),
        ])
        meta = sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=StringIO(), sleep=lambda s: None)
        self.assertEqual(len(runner.calls), 3)
        self.assertFalse(meta["prompt_delivery"]["verified"])

    def test_herdr_agent_start_failure_falls_back_to_pane_run(self):
        runner = FakeRunner([
            (0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""),
            (1, "", "agent not detected"),
            (0, "", ""),
        ])
        meta = sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=StringIO(), sleep=lambda s: None)
        self.assertEqual(runner.calls[2][:3], ["herdr", "pane", "run"])
        self.assertEqual(runner.calls[2][3], "w8:pZ")
        self.assertEqual(runner.calls[2][4], "claude")
        self.assertTrue(runner.calls[2][-1].startswith("look into the flaky login test\n"))
        self.assertIn("agent not detected", meta["start_fallback"])
        self.assertFalse(meta["agent_registered"])

    def test_herdr_agent_start_retries_while_pane_busy(self):
        busy = '{"error":{"code":"agent_pane_busy","message":"not an available shell"}}'
        runner = FakeRunner([
            (0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""),
            (1, busy, ""), (1, busy, ""), (0, '{"result":{"type":"ok"}}', ""), (0, "", ""),
        ])
        slept = []
        meta = sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=StringIO(), sleep=slept.append)
        self.assertEqual(len(runner.calls), 5)
        self.assertEqual(slept, [0.5, 0.5])
        self.assertNotIn("start_fallback", meta)

    def test_herdr_agent_start_gives_up_after_attempts(self):
        busy = '{"error":{"code":"agent_pane_busy"}}'
        runner = FakeRunner([(0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', "")] +
                            [(1, busy, "")] * 3 + [(0, "", "")])
        meta = sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=StringIO(), sleep=lambda s: None)
        self.assertEqual(runner.calls[4][:3], ["herdr", "pane", "run"])
        self.assertIn("agent_pane_busy", meta["start_fallback"])

    def test_herdr_split_failure_exits(self):
        runner = FakeRunner([(1, "", "boom")])
        with self.assertRaises(SystemExit):
            sq.spawn(ns(cwd="/w"), env=self.env, run=runner, out=StringIO())

    def test_tmux_backend(self):
        env = {"SIDEQUEST_STATE_DIR": self.tmp.name, "TMUX": "/tmp/t"}
        runner = FakeRunner([(0, "%7\n", "")])
        meta = sq.spawn(ns(cwd="/w", backend="tmux"), env=env, run=runner, out=StringIO())
        self.assertEqual(meta["pane_id"], "%7")
        self.assertEqual(runner.calls[0][:2], ["tmux", "split-window"])

    def test_print_backend_outputs_manual_command(self):
        env = {"SIDEQUEST_STATE_DIR": self.tmp.name}
        out = StringIO()
        meta = sq.spawn(ns(cwd="/w", backend="print"), env=env, run=FakeRunner([]), out=out)
        self.assertEqual(meta["pane_id"], "(manual)")
        self.assertIn("cd /w && claude --append-system-prompt-file", out.getvalue())

    def test_full_mode_forks_parent(self):
        runner = FakeRunner([(0, '{"result":{"pane":{"pane_id":"w8:pZ"}}}', ""), (0, "", ""), (0, "", "")])
        sq.spawn(ns(cwd="/w", mode="full"), env=self.env, run=runner, out=StringIO())
        start = runner.calls[1]
        self.assertIn("--resume", start)
        self.assertIn("parent-1", start)
        self.assertIn("--fork-session", start)

    def test_empty_request_rejected(self):
        with self.assertRaises(SystemExit):
            sq.spawn(ns(request="   ", dry_run=True), env=self.env, run=FakeRunner([]), out=StringIO())

    def test_list_report_show_rm(self):
        meta = sq.spawn(ns(cwd="/w", dry_run=True, name="auth"), env=self.env, run=FakeRunner([]),
                        out=StringIO())
        out = StringIO()
        sq.cmd_list(Namespace(json=False), env=self.env, out=out)
        self.assertIn("pending", out.getvalue())
        self.assertIn(meta["slug"], out.getvalue())
        # report pending -> rc 1
        out = StringIO()
        self.assertEqual(sq.cmd_report(Namespace(slug="auth"), env=self.env, out=out), 1)
        self.assertIn("no report yet", out.getvalue())
        # child writes report
        Path(meta["report"]).write_text("Done: fixed it.\n")
        out = StringIO()
        self.assertEqual(sq.cmd_report(Namespace(slug=meta["slug"]), env=self.env, out=out), 0)
        self.assertIn("Done: fixed it.", out.getvalue())
        out = StringIO()
        sq.cmd_list(Namespace(json=False), env=self.env, out=out)
        self.assertIn("reported", out.getvalue())
        out = StringIO()
        sq.cmd_show(Namespace(slug="auth"), env=self.env, out=out)
        self.assertIn("--- brief ---", out.getvalue())
        sq.cmd_rm(Namespace(slug="auth"), env=self.env, out=StringIO())
        self.assertEqual(sq.load_all(self.env), [])

    def test_prompt_stamp(self):
        p = sq.prompt_with_stamp(" do it ", "s-1", Path("/r/report.md"))
        self.assertTrue(p.startswith("do it\n\n[sidequest s-1:"))
        self.assertIn("/r/report.md]", p)

    def test_wait_polls_report_and_prints_it(self):
        meta = sq.spawn(ns(cwd="/w", dry_run=True, name="w"), env=self.env, run=FakeRunner([]),
                        out=StringIO())
        idle = (0, '{"result":{"agent":{"agent_status":"idle"}}}', "")
        runner = FakeRunner([idle, idle, idle])
        def sleeper(_):
            if len(runner.calls) >= 2:
                Path(meta["report"]).write_text("all good\n")
        out = StringIO()
        rc = sq.cmd_wait(Namespace(slug="w", timeout_ms=60000), env=self.env, out=out, run=runner,
                         sleep=sleeper, clock=lambda: 0.0)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.calls[0][:3], ["herdr", "agent", "get"])
        self.assertIn("all good", out.getvalue())

    def test_wait_bails_when_herdr_agent_blocked(self):
        meta = sq.spawn(ns(cwd="/w", dry_run=True, name="b"), env=self.env, run=FakeRunner([]),
                        out=StringIO())
        runner = FakeRunner([(0, '{"result":{"agent":{"agent_status":"blocked"}}}', "")])
        out = StringIO()
        rc = sq.cmd_wait(Namespace(slug="b", timeout_ms=60000), env=self.env, out=out, run=runner,
                         sleep=lambda s: None, clock=lambda: 0.0)
        self.assertEqual(rc, 3)
        self.assertIn("blocked", out.getvalue())

    def test_wait_non_herdr_polls_and_times_out(self):
        env2 = {"SIDEQUEST_STATE_DIR": self.tmp.name}
        m2 = sq.spawn(ns(cwd="/w", backend="print", name="pp"), env=env2, run=FakeRunner([]), out=StringIO())
        runner = FakeRunner([])
        def sleeper(_):
            Path(m2["report"]).write_text("late but fine\n")
        out = StringIO()
        rc = sq.cmd_wait(Namespace(slug="pp", timeout_ms=10000), env=env2, out=out, run=runner,
                         sleep=sleeper, clock=lambda: 0.0)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.calls, [])  # no herdr calls for non-herdr backends
        self.assertIn("late but fine", out.getvalue())
        m3 = sq.spawn(ns(cwd="/w", backend="print", name="qq"), env=env2, run=FakeRunner([]), out=StringIO())
        ticks = iter([0.0, 5.0, 11.0])
        rc = sq.cmd_wait(Namespace(slug="qq", timeout_ms=10000), env=env2, out=StringIO(), run=FakeRunner([]),
                         sleep=lambda s: None, clock=lambda: next(ticks))
        self.assertEqual(rc, 1)
        # herdr sidequest that fell back (agent not registered) skips status checks
        m4 = sq.spawn(ns(cwd="/w", dry_run=True, name="fb"), env=self.env, run=FakeRunner([]), out=StringIO())
        mf = Path(m4["brief"]).parent / "meta.json"; d = json.loads(mf.read_text()); d["agent_registered"] = False
        mf.write_text(json.dumps(d)); Path(m4["report"]).write_text("fb ok\n")
        runner = FakeRunner([]); out = StringIO()
        rc = sq.cmd_wait(Namespace(slug="fb", timeout_ms=10), env=self.env, out=out, run=runner,
                         sleep=lambda s: None, clock=lambda: 0.0)
        self.assertEqual(rc, 0); self.assertIn("fb ok", out.getvalue()); self.assertEqual(runner.calls, [])

    def test_find_ambiguous_and_missing(self):
        sq.spawn(ns(dry_run=True, name="alpha one"), env=self.env, run=FakeRunner([]), out=StringIO())
        sq.spawn(ns(dry_run=True, name="alpha two"), env=self.env, run=FakeRunner([]), out=StringIO())
        with self.assertRaises(SystemExit):
            sq.find("alpha", self.env)
        with self.assertRaises(SystemExit):
            sq.find("nope", self.env)
        self.assertEqual(sq.find("alpha-one", self.env)["slug"].split("-")[1], "one")


class CLI(unittest.TestCase):
    def test_help_and_version(self):
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "sidequest"), "--version"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn(sq.__version__, r.stdout)
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "sidequest"), "spawn", "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--mode", r.stdout)

    def test_end_to_end_dry_run_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, SIDEQUEST_STATE_DIR=tmp, CLAUDE_CODE_SESSION_ID="p9")
            r = subprocess.run([sys.executable, str(ROOT / "bin" / "sidequest"), "spawn",
                                "--request", "write release notes", "--dry-run", "--name", "notes"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("[dry-run]", r.stdout)
            r = subprocess.run([sys.executable, str(ROOT / "bin" / "sidequest"), "list"],
                               capture_output=True, text=True, env=env)
            self.assertIn("notes-", r.stdout)


if __name__ == "__main__":
    unittest.main()
