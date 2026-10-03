"""MCP server for Siemens SIMATIC Manager (STEP 7 V5.x classic).

STEP 7's automation interface (Simatic.Simatic) is a 32-bit in-process COM
server, so it cannot be loaded into 64-bit Python. This server keeps a 32-bit
Windows PowerShell worker (bridge/s7bridge.ps1) alive and exchanges one JSON
object per line with it.
"""

from __future__ import annotations

import csv
import io
import json
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

BRIDGE_SCRIPT = Path(__file__).parent / "bridge" / "s7bridge.ps1"
POWERSHELL_X86 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
MARK = "@@S7@@"
DEFAULT_TIMEOUT = float(os.environ.get("S7_MCP_TIMEOUT", "180"))

# Block.GenerateSource flag; not in the type library, found by probing STEP 7 V5.6.
GENERATE_SOURCE_ABSOLUTE = 32
ALLOW_PLC_WRITES = os.environ.get("S7_MCP_ALLOW_PLC_WRITES", "") == "1"
# Block.CompareOnlineOffline return codes, found by probing against S7-PLCSIM.
COMPARE_RESULTS = {0: "identical", -1: "not in CPU"}
SYMBOL_IMPORT_MODES = {"insert": 0, "overwrite_by_name": 1, "overwrite_by_address": 2}


class BridgeError(RuntimeError):
    pass


class Bridge:
    """Owns the 32-bit PowerShell worker; one request at a time."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0

    def _start(self) -> None:
        if not POWERSHELL_X86.exists():
            raise BridgeError(f"32-bit PowerShell not found at {POWERSHELL_X86}")
        stderr_log = open(Path(tempfile.gettempdir()) / "s7mcp_bridge_stderr.log", "w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [str(POWERSHELL_X86), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(BRIDGE_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_log,
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

    def _kill(self) -> None:
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
                self._kill()
                raise BridgeError(
                    f"STEP 7 did not answer '{op}' within {timeout:.0f}s; the worker was restarted. "
                    "Check for an open STEP 7 dialog or a project locked by SIMATIC Manager."
                ) from None
            except OSError as e:
                self._kill()
                raise BridgeError(f"Lost connection to the STEP 7 worker: {e}") from None
            if line is None:
                self._kill()
                raise BridgeError("The STEP 7 worker exited unexpectedly; see %TEMP%\\s7mcp_bridge_stderr.log.")
            reply = json.loads(line)
            if not reply.get("ok"):
                detail = reply.get("error") or "unknown error"
                if reply.get("log"):
                    detail += "\nSTEP 7 log:\n" + reply["log"]
                raise BridgeError(detail)
            return reply.get("result")


bridge = Bridge()
mcp = FastMCP(
    "simatic-manager",
    instructions=(
        "Offline engineering access to Siemens STEP 7 V5.x (SIMATIC Manager) projects. "
        "Start with list_projects, then get_project_tree to find programs. "
        "A 'project' argument is a project name or its path; 'program' is a program name or path and may be "
        "omitted when the project has exactly one program. To change logic: get_block_source, edit the STL/SCL "
        "text, import_source, then compile_source. All tools work on the offline project only; nothing is "
        "downloaded to a PLC."
    ),
)


def _parse_sdf(text: str) -> list[dict[str, str]]:
    symbols = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        cells = [c.strip() for c in row] + [""]
        symbols.append({"name": cells[0], "address": " ".join(cells[1].split()), "data_type": " ".join(cells[2].split()),"comment": cells[3]})
    return symbols


def _format_sdf(symbols: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for s in symbols:
        writer.writerow([s["name"], s["address"], s["data_type"], s.get("comment", "")])
    return out.getvalue()


@mcp.tool()
def step7_status() -> dict:
    """Check that STEP 7 is reachable and report its version."""
    return bridge.call("ping")


@mcp.tool()
def list_projects() -> list[dict]:
    """List all projects and libraries registered in SIMATIC Manager (name, path, kind)."""
    return bridge.call("list_projects")


@mcp.tool()
def get_project_tree(project: str) -> dict:
    """Show a project's stations and S7 programs, with the block/source containers of each program."""
    return bridge.call("project_tree", project=project)


@mcp.tool()
def list_blocks(project: str, program: str | None = None, block_type: str | None = None) -> list[dict]:
    """List the blocks of an S7 program (OB/FB/FC/DB/UDT/VAT/SFC/SFB) with symbol, language, size and header info.

    block_type optionally filters by type, e.g. "FC" or "DB".
    """
    blocks = bridge.call("list_blocks", project=project, program=program)
    if block_type:
        blocks = [b for b in blocks if b["type"].upper() == block_type.upper()]
    return blocks


@mcp.tool()
def get_block_source(project: str, blocks: list[str], program: str | None = None, symbolic: bool = True) -> list[dict]:
    """Decompile blocks (e.g. ["OB1", "FC5"]) into STL source text.

    LAD/FBD blocks are returned as their STL equivalent. By default operands that have a symbol are shown
    by symbol name; symbolic=False shows absolute addresses instead. Know-how-protected blocks return an
    error entry.
    """
    flags = 0 if symbolic else GENERATE_SOURCE_ABSOLUTE
    results = bridge.call("block_source", project=project, program=program, blocks=blocks, flags=flags)
    if not symbolic:
        for r in results:
            if "error" in r and "not found" not in r["error"]:
                # Seen on blocks uploaded from a CPU: STEP 7 only decompiles them with symbolic=True.
                r["hint"] = "Retry with symbolic=True."
    return results


@mcp.tool()
def list_sources(project: str, program: str | None = None) -> list[dict]:
    """List the STL/SCL source files in a program's Sources folder."""
    return bridge.call("list_sources", project=project, program=program)


@mcp.tool()
def get_source(project: str, name: str, program: str | None = None) -> dict:
    """Read the text of a source file from a program's Sources folder."""
    return bridge.call("get_source", project=project, program=program, name=name)


@mcp.tool()
def import_source(
    project: str,
    name: str,
    text: str,
    program: str | None = None,
    language: Literal["awl", "scl"] = "awl",
    overwrite: bool = False,
) -> dict:
    """Create a source file in a program's Sources folder from STL ("awl") or SCL text.

    Modifies the offline project. This only stores the source; call compile_source to generate blocks.
    """
    return bridge.call("import_source", project=project, program=program, name=name, text=text, language=language, overwrite=overwrite)


@mcp.tool()
def compile_source(project: str, name: str, program: str | None = None) -> dict:
    """Compile a source file into blocks, overwriting existing offline blocks with the same numbers.

    Returns the generated block names and the STEP 7 compiler log.
    """
    return bridge.call("compile_source", project=project, program=program, name=name, timeout=max(DEFAULT_TIMEOUT, 600))


@mcp.tool()
def get_symbol_table(project: str, program: str | None = None, filter: str | None = None) -> list[dict]:
    """Read a program's symbol table (name, address, data_type, comment).

    filter is an optional case-insensitive substring matched against name, address and comment.
    """
    symbols = _parse_sdf(bridge.call("export_symbols", project=project, program=program)["sdf"])
    if filter:
        needle = filter.lower()
        symbols = [s for s in symbols if needle in s["name"].lower() or needle in s["address"].lower() or needle in s["comment"].lower()]
    return symbols


@mcp.tool()
def import_symbols(
    project: str,
    symbols: list[dict[str, str]],
    program: str | None = None,
    mode: Literal["insert", "overwrite_by_name", "overwrite_by_address"] = "insert",
) -> dict:
    """Add or update symbols in a program's symbol table. Modifies the offline project.

    Each symbol needs name, address (e.g. "I 0.0", "MW 10", "FC 5") and data_type (e.g. "BOOL", "INT", "FC 5");
    comment is optional. mode "insert" only adds new symbols; the overwrite modes replace existing entries
    matched by name or by address.
    """
    for s in symbols:
        missing = [k for k in ("name", "address", "data_type") if not s.get(k)]
        if missing:
            raise ValueError(f"Symbol {s!r} is missing {', '.join(missing)}")
    return bridge.call("import_symbols", project=project, program=program, sdf=_format_sdf(symbols), mode=SYMBOL_IMPORT_MODES[mode])


@mcp.tool()
def get_station_config(project: str, station: str) -> dict:
    """Export a station's hardware configuration (racks, modules, addresses, parameters) as HW Config text."""
    return bridge.call("station_config", project=project, station=station)


@mcp.tool()
def create_project(name: str, directory: str) -> dict:
    """Create a new empty STEP 7 project inside an existing directory."""
    return bridge.call("create_project", name=name, directory=directory)


@mcp.tool()
def add_program(project: str, name: str) -> dict:
    """Add an empty S7 program (with Blocks and Sources folders) to a project."""
    return bridge.call("add_program", project=project, name=name)


# ------------------------------------------------------------------ online tools


def _require_plc_writes() -> None:
    if not ALLOW_PLC_WRITES:
        raise PermissionError(
            "Tools that change the PLC (download, start, stop) are disabled. "
            "Set the environment variable S7_MCP_ALLOW_PLC_WRITES=1 in the server's MCP configuration to enable them."
        )


def _describe_compare(entry: dict) -> dict:
    if "code" in entry:
        entry["result"] = COMPARE_RESULTS.get(entry["code"], "different")
    return entry


@mcp.tool()
def get_cpu_state(project: str, program: str | None = None) -> dict:
    """Read the operating state of the CPU a program is assigned to (RUN, STOP, HALT, STARTUP, DEFECT).

    Connects through the PG/PC interface configured in STEP 7. "unknown(0)" means the CPU could not be reached.
    """
    return bridge.call("cpu_state", project=project, program=program)


@mcp.tool()
def list_online_blocks(project: str, program: str | None = None, include_system_blocks: bool = False) -> list[dict]:
    """List the blocks currently loaded in the CPU. SFCs/SFBs are hidden unless include_system_blocks is true."""
    blocks = bridge.call("list_online_blocks", project=project, program=program)
    if not include_system_blocks:
        blocks = [b for b in blocks if b["type"] not in ("SFC", "SFB")]
    return blocks


@mcp.tool()
def compare_online_offline(project: str, program: str | None = None, blocks: list[str] | None = None) -> dict:
    """Compare offline blocks with the blocks in the CPU. Read-only.

    blocks defaults to every OB/FB/FC/DB/UDT of the program. Each block is reported as "identical",
    "different" or "not in CPU".
    """
    result = bridge.call("compare", project=project, program=program, blocks=blocks, timeout=max(DEFAULT_TIMEOUT, 900))
    result["blocks"] = [_describe_compare(b) for b in result["blocks"]]
    return result


@mcp.tool()
def upload_blocks(project: str, program: str | None = None, blocks: list[str] | None = None, overwrite: bool = False) -> dict:
    """Copy blocks from the CPU into the offline project. Does not change the PLC.

    blocks defaults to every OB/FB/FC/DB in the CPU. Existing offline blocks are kept unless overwrite is true.
    """
    return bridge.call("upload", project=project, program=program, blocks=blocks, overwrite=overwrite, timeout=max(DEFAULT_TIMEOUT, 900))


@mcp.tool()
def download_blocks(project: str, program: str | None = None, blocks: list[str] | None = None, overwrite: bool = True) -> dict:
    """Download offline blocks to the CPU. CHANGES THE RUNNING PLC PROGRAM.

    blocks defaults to every OB/FB/FC/DB/UDT of the program. With overwrite false, blocks that already exist
    in the CPU are not replaced. Always confirm with the user before calling this on a real plant.
    """
    _require_plc_writes()
    return bridge.call("download", project=project, program=program, blocks=blocks, overwrite=overwrite, timeout=max(DEFAULT_TIMEOUT, 900))


@mcp.tool()
def start_cpu(project: str, program: str | None = None, mode: Literal["warm_restart", "hot_restart"] = "warm_restart") -> dict:
    """Switch the CPU to RUN. STARTS THE MACHINE'S CONTROL PROGRAM; confirm with the user first.

    Fails if the CPU's mode switch is in STOP.
    """
    _require_plc_writes()
    return bridge.call("cpu_control", project=project, program=program, action=mode)


@mcp.tool()
def stop_cpu(project: str, program: str | None = None) -> dict:
    """Switch the CPU to STOP. HALTS THE MACHINE'S CONTROL PROGRAM; confirm with the user first."""
    _require_plc_writes()
    return bridge.call("cpu_control", project=project, program=program, action="stop")


@mcp.tool()
def compile_station(project: str, station: str) -> dict:
    """Compile a station's hardware configuration (regenerates the offline System Data). Modifies the offline project."""
    return bridge.call("compile_station", project=project, station=station, timeout=max(DEFAULT_TIMEOUT, 600))


if __name__ == "__main__":
    mcp.run()
