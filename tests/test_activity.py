import copy
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from codex_activity import CodexActivityMonitor, RolloutActivity, writer_is_live
from dashboard_core import DEFAULT_CONFIG, render_frame


def event(kind):
    return (json.dumps({"timestamp":"2026-10-13T00:00:00Z", "type":"event_msg",
                      "payload":{"type":kind}}, separators=(",", ":")) + "\n").encode()


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.home = ROOT / "runtime" / ("activity-test-" + uuid.uuid4().hex)
        self.home.mkdir(parents=True)
        (self.home / "thread-writer-locks").mkdir()
        self.metadata = sqlite3.connect(self.home / "state_5.sqlite")
        self.metadata.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,rollout_path TEXT,archived INTEGER)")
        self.history = sqlite3.connect(self.home / "thread_history_1.sqlite")
        self.history.execute("CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,status TEXT,completed_at INTEGER,rollout_ordinal INTEGER)")
        self.live = set()
        self.monitor = CodexActivityMonitor(self.home, lock_probe=lambda p: p.stem in self.live)

    def tearDown(self):
        self.metadata.close()
        self.history.close()
        self.assertTrue(self.home.resolve().is_relative_to(ROOT))
        for path in self.home.rglob("*"):
            if path.is_file():
                path.unlink()
        (self.home / "thread-writer-locks").rmdir()
        self.home.rmdir()

    def session(self, identifier, status=None, ordinal=1):
        path = self.home / (identifier + ".jsonl")
        path.write_bytes(b"")
        self.metadata.execute("INSERT OR REPLACE INTO threads VALUES(?,?,0)", (identifier,str(path)))
        self.metadata.commit()
        self.live.add(identifier)
        if status:
            self.history.execute("INSERT INTO thread_turns VALUES(?,?,?,?,?)",
                (identifier,str(ordinal),status,None if status == "inProgress" else 1,ordinal))
            self.history.commit()
        return path

    def test_idle_open_sessions_and_quota_helper_are_not_busy(self):
        self.session("open-window", "completed")
        self.assertEqual(self.monitor.poll(), {"status":"idle","active_sessions":0})

    def test_any_active_session_sets_green_and_completion_clears(self):
        self.session("first", "inProgress")
        self.session("second", "inProgress")
        self.assertEqual(self.monitor.poll()["active_sessions"],2)
        self.history.execute("UPDATE thread_turns SET status='completed',completed_at=1 WHERE thread_id='first'")
        self.history.commit()
        self.assertEqual(self.monitor.poll(),{"status":"active","active_sessions":1})
        self.history.execute("UPDATE thread_turns SET status='completed',completed_at=1")
        self.history.commit()
        self.assertEqual(self.monitor.poll()["status"],"idle")

    def test_crashed_writer_is_not_stuck_active(self):
        self.session("crashed", "inProgress")
        self.live.clear()
        self.assertEqual(self.monitor.poll()["status"],"idle")

    def test_newer_completed_turn_overrides_old_unfinished_turn(self):
        self.session("reopened", "inProgress")
        self.history.execute("INSERT INTO thread_turns VALUES('reopened','2','completed',2,2)")
        self.history.commit()
        self.assertEqual(self.monitor.poll()["status"],"idle")

    def test_legacy_session_tail_is_cached_and_interrupt_clears(self):
        path = self.session("legacy")
        path.write_bytes(event("task_started"))
        self.assertEqual(self.monitor.poll()["status"],"active")
        reader = self.monitor.rollouts["legacy"]
        reads = reader.bytes_read
        self.assertEqual(self.monitor.poll()["status"],"active")
        self.assertEqual(reader.bytes_read,reads)
        with path.open("ab") as stream:
            stream.write(event("turn_aborted"))
        self.assertEqual(self.monitor.poll()["status"],"idle")

    def test_partial_appends_and_log_truncation(self):
        path = self.session("split")
        reader = RolloutActivity(path)
        self.assertIsNone(reader.read())
        record = event("task_started")
        with path.open("ab") as stream:
            stream.write(record[:52])
        self.assertIsNone(reader.read())
        with path.open("ab") as stream:
            stream.write(record[52:])
        self.assertTrue(reader.read())
        path.write_bytes(event("turn_aborted"))
        self.assertFalse(reader.read())

    def test_unrecognized_metadata_is_amber_not_false_idle(self):
        self.session("unknown")
        self.assertEqual(self.monitor.poll()["status"],"unknown")

    def test_archived_sessions_are_ignored(self):
        self.session("archived", "inProgress")
        self.metadata.execute("UPDATE threads SET archived=1")
        self.metadata.commit()
        self.assertEqual(self.monitor.poll()["status"],"idle")

    def test_monitor_does_not_modify_codex_files(self):
        path = self.session("read-only", "inProgress")
        files = [self.home / "state_5.sqlite",self.home / "thread_history_1.sqlite",path]
        before = {p:p.read_bytes() for p in files}
        self.monitor.poll()
        self.assertEqual(before,{p:p.read_bytes() for p in files})

    def test_broken_metadata_reports_unknown(self):
        self.metadata.execute("DROP TABLE threads")
        self.metadata.commit()
        self.assertEqual(self.monitor.poll()["status"],"unknown")

    def test_indicator_colors_are_rendered_and_can_be_disabled(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        for status in ("active","idle","unknown"):
            image = render_frame({"codex_activity":{"status":status}},config)
            expected = tuple(bytes.fromhex(config["codex_activity"][status+"_color"].lstrip("#")))
            self.assertEqual(image.getpixel((240,53)),expected)
        config["codex_activity"]["enabled"] = False
        image = render_frame({"codex_activity":{"status":"active"}},config)
        self.assertEqual(image.getpixel((240,53)),tuple(bytes.fromhex(config["theme"]["bg"].lstrip("#"))))

    @unittest.skipUnless(os.name == "nt", "Windows writer locks")
    def test_real_writer_lock_released_on_process_exit(self):
        path = self.home / "thread-writer-locks/real.lock"
        script = "import sys,msvcrt; f=open(sys.argv[1],'w+b'); f.write(b'0'); f.flush(); f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); print('ready',flush=True); sys.stdin.readline()"
        process = subprocess.Popen([sys.executable,"-c",script,str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(),"ready")
            self.assertTrue(writer_is_live(path))
            process.terminate()
            process.wait(timeout=5)
            self.assertFalse(writer_is_live(path))
            self.assertEqual(path.read_bytes(),b"0")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
