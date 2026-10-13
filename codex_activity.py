"""Read local Codex turn metadata without launching or modifying Codex."""
import ctypes
import os
import pathlib
import re
import sqlite3
from ctypes import wintypes


_LIFECYCLE = re.compile(
    rb'(?:^|\n)\s*\{"timestamp"\s*:\s*"[^"\n]+"\s*,\s*"type"\s*:\s*"event_msg"'
    rb'\s*,\s*"payload"\s*:\s*\{\s*"type"\s*:\s*"'
    rb'(task_started|task_complete|turn_aborted|turn_cancelled|session_end)"'
)


def writer_is_live(path):
    """A read blocked by Codex's byte-range lock proves a live writer owns it.

    This only opens and reads one byte. It never acquires a lock or changes the
    file. Windows releases the writer lock when its host process exits.
    """
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        return True if error in (32, 33) else (False if error in (2, 3) else None)
    try:
        buffer = ctypes.create_string_buffer(1)
        count = wintypes.DWORD()
        if kernel.ReadFile(handle, buffer, 1, ctypes.byref(count), None):
            return False
        return True if ctypes.get_last_error() in (32, 33) else None
    finally:
        kernel.CloseHandle(handle)


class RolloutActivity:
    """Fallback for sessions not indexed in Codex's newer turn-history DB.

    Bootstrap reads backwards until the most recent lifecycle marker. Later
    checks read only appended bytes; only a bounded partial-line prefix is kept.
    """
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.offset = None
        self.identity = None
        self.modified = None
        self.pending = b""
        self.active = None
        self.bytes_read = 0

    def _markers(self, data):
        markers = list(_LIFECYCLE.finditer(data))
        if markers:
            self.active = markers[-1].group(1) == b"task_started"
            return True
        return False

    def read(self):
        stat = self.path.stat()
        identity = stat.st_dev, stat.st_ino
        with self.path.open("rb") as stream:
            rewritten = stat.st_size == self.offset and stat.st_mtime_ns != self.modified
            if self.offset is None or identity != self.identity or stat.st_size < self.offset or rewritten:
                self.active = None
                self.pending = b""
                position = stat.st_size
                lower = max(0, position - 8 * 1024 * 1024)
                while position > lower:
                    begin = max(lower, position - 65536)
                    stream.seek(begin)
                    block = stream.read(min(stat.st_size - begin, position - begin + 1024))
                    self.bytes_read += len(block)
                    if self._markers(block):
                        break
                    position = begin
                # The writer normally appends complete lines. Keep an incomplete
                # metadata prefix so a split lifecycle record is handled too.
                stream.seek(max(0, stat.st_size - 1024))
                tail = stream.read()
                self.bytes_read += len(tail)
                final = tail.rsplit(b"\n", 1)[-1]
                self.pending = final if len(final) < 1024 else b"X"
                self.offset = stat.st_size
                self.identity = identity
            elif stat.st_size > self.offset:
                stream.seek(self.offset)
                while self.offset < stat.st_size:
                    block = stream.read(min(65536, stat.st_size - self.offset))
                    if not block:
                        break
                    self.offset += len(block)
                    self.bytes_read += len(block)
                    data = self.pending + block
                    self._markers(data)
                    final = data.rsplit(b"\n", 1)[-1]
                    self.pending = final if len(final) <= 1024 else b"X"
        self.modified = stat.st_mtime_ns
        return self.active


class CodexActivityMonitor:
    def __init__(self, home=None, lock_probe=writer_is_live):
        self.home = pathlib.Path(home or os.environ.get("CODEX_HOME") or pathlib.Path.home() / ".codex")
        self.lock_probe = lock_probe
        self.rollouts = {}

    def _database(self, pattern):
        paths = [p for p in self.home.glob(pattern) if p.stem.rsplit("_", 1)[-1].isdigit()]
        return max(paths, key=lambda p: int(p.stem.rsplit("_", 1)[-1])) if paths else None

    @staticmethod
    def _connect(path):
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.1)
        connection.execute("PRAGMA query_only=ON")
        return connection

    def poll(self):
        metadata = self._database("state_*.sqlite")
        if metadata is None:
            return {"status": "idle", "active_sessions": 0}
        try:
            with self._connect(metadata) as connection:
                sessions = dict(connection.execute("SELECT id,rollout_path FROM threads WHERE archived=0"))
        except (sqlite3.Error, OSError, ValueError):
            return {"status": "unknown", "active_sessions": 0}
        finally:
            if "connection" in locals():
                connection.close()
        history = self._database("thread_history_*.sqlite")
        indexed, active = set(), set()
        if history:
            connection = None
            try:
                connection = self._connect(history)
                indexed = {row[0] for row in connection.execute("SELECT DISTINCT thread_id FROM thread_turns")}
                active = {row[0] for row in connection.execute("""
                    SELECT t.thread_id FROM thread_turns t
                    WHERE t.status='inProgress' AND t.completed_at IS NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM thread_turns newer WHERE newer.thread_id=t.thread_id
                        AND newer.rollout_ordinal > t.rollout_ordinal
                    )
                """)}
            except sqlite3.Error:
                # Older Codex builds may not have the same history schema.
                indexed.clear()
                active.clear()
            finally:
                if connection is not None:
                    connection.close()
        count, uncertain = 0, False
        relevant = (active | (sessions.keys() - indexed)) & sessions.keys()
        for identifier in relevant:
            live = self.lock_probe(self.home / "thread-writer-locks" / (identifier + ".lock"))
            if live is None:
                uncertain = True
                continue
            if not live:
                continue
            if identifier in indexed:
                count += 1
                continue
            path = pathlib.Path(sessions[identifier])
            reader = self.rollouts.get(identifier)
            if reader is None or reader.path != path:
                reader = self.rollouts[identifier] = RolloutActivity(path)
            try:
                running = reader.read()
                count += int(running is True)
                uncertain |= running is None
            except OSError:
                uncertain = True
        self.rollouts = {key: value for key, value in self.rollouts.items() if key in relevant}
        return {"status": "active" if count else ("unknown" if uncertain else "idle"),
                "active_sessions": count}
