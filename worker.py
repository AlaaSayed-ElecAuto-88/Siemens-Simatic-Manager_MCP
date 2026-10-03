"""Runs a 32-bit Windows PowerShell worker script and exchanges JSON lines with it."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

BRIDGE_DIR = Path(__file__).parent / "bridge"
POWERSHELL_X86 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
MARK = "@@S7@@"
DEFAULT_TIMEOUT = float(os.environ.get("S7_MCP_TIMEOUT", "180"))


class BridgeError(RuntimeError):
    pass


class Bridge:
    """Owns one worker process; one request at a time."""

    def __init__(self, script: str, label: str = "STEP 7") -> None:
        self._script = BRIDGE_DIR / script
        self._label = label
        self._stderr_log = Path(tempfile.gettempdir()) / f"s7mcp_{self._script.stem}_stderr.log"
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0

    def _start(self) -> None:
        if not POWERSHELL_X86.exists():
            raise BridgeError(f"32-bit PowerShell not found at {POWERSHELL_X86}")
        self._proc = subprocess.Popen(
            [str(POWERSHELL_X86), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(self._script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=open(self._stderr_log, "w", encoding="utf-8"),
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc, self._lines), daemon=True).start()

    @staticmethod
    def _pump(proc: subprocess.Popen[str], lines: queue.Queue[str | None]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith(MARK):
                lines.put(line[len(MARK):])
        lines.put(None)

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc = None

    def call(self, op: str, timeout: float = DEFAULT_TIMEOUT, **args: Any) -> Any:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            assert self._proc is not None and self._proc.stdin is not None
            self._next_id += 1
            request = {"id": self._next_id, "op": op, "args": {k: v for k, v in args.items() if v is not None}}
            try:
                self._proc.stdin.write(json.dumps(request) + "\n")
                self._proc.stdin.flush()
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                self.stop()
                raise BridgeError(
                    f"{self._label} did not answer '{op}' within {timeout:.0f}s; the worker was restarted. "
                    "Check for an open STEP 7 dialog or a project locked by SIMATIC Manager."
                ) from None
            except OSError as e:
                self.stop()
                raise BridgeError(f"Lost connection to the {self._label} worker: {e}") from None
            if line is None:
                self.stop()
                raise BridgeError(f"The {self._label} worker exited unexpectedly; see {self._stderr_log}.")
            reply = json.loads(line)
            if not reply.get("ok"):
                detail = reply.get("error") or "unknown error"
                if reply.get("log"):
                    detail += "\nSTEP 7 log:\n" + reply["log"]
                raise BridgeError(detail)
            return reply.get("result")
