# SIMATIC Manager MCP

MCP server for Siemens STEP 7 V5.x (SIMATIC Manager, "classic"). It lets an MCP client such as Claude
browse and edit STEP 7 projects, and optionally work online with the CPU.

## How it works

```
MCP client  <--stdio-->  server.py (64-bit Python)  <--JSON lines-->  bridge/s7bridge.ps1 (32-bit PowerShell)  <--COM-->  STEP 7
```

STEP 7's automation interface (`Simatic.Simatic`, `S7ABATCX.DLL`) is a 32-bit in-process COM server, so
64-bit Python cannot load it. `server.py` keeps one 32-bit Windows PowerShell worker alive and sends it
one request at a time.

## Requirements

- Windows with STEP 7 V5.x installed and licensed (developed against V5.6 + HF1)
- Python 3.10+
- The Windows "Users" group needs write access to the STEP 7 folder and registry key, otherwise STEP 7
  refuses to start with message 256:131 ("Your user rights do not include the use of STEP 7"). Fix once
  from an elevated PowerShell:

  ```powershell
  icacls "C:\Program Files (x86)\Siemens\Step7" /grant "*S-1-5-32-545:(OI)(CI)M" /T /C /Q
  $k='HKLM:\SOFTWARE\WOW6432Node\Siemens\STEP7'; $a=Get-Acl $k; $a.AddAccessRule((New-Object Security.AccessControl.RegistryAccessRule('BUILTIN\Users','FullControl','ContainerInherit,ObjectInherit','None','Allow'))); Set-Acl $k $a
  ```

## Install

Instructions for a person, or for Claude Code when given this repository's link:

1. Clone or download the repository to a permanent folder.
2. Install the dependency: `pip install -r requirements.txt`
3. Register the server with the absolute path to `server.py`:

   ```
   claude mcp add simatic-manager --scope user -- python "C:\path\to\simatic-manager-mcp\server.py"
   ```

   Or copy `.mcp.example.json` to `.mcp.json` (project scope) or into `claude_desktop_config.json`
   (Claude Desktop) and fix the path.
4. Start a new session and call `step7_status`. If it fails with a DLL initialisation error, apply the
   permissions fix above.

## Tools

| Tool | Changes | Purpose |
| --- | --- | --- |
| `step7_status` | – | Check STEP 7 is reachable, report version |
| `list_projects` | – | Projects and libraries known to SIMATIC Manager |
| `get_project_tree` | – | Stations, S7 programs and their containers |
| `list_blocks` | – | Blocks of a program with header info |
| `get_block_source` | – | Decompile blocks to STL text (symbolic or absolute) |
| `list_sources` / `get_source` | – | Read the Sources folder |
| `get_symbol_table` | – | Symbols, optionally filtered |
| `get_station_config` | – | Hardware configuration as HW Config export text |
| `import_source` | offline project | Store STL/SCL text as a source file |
| `compile_source` | offline project | Compile a source into blocks, returns the compiler log |
| `import_symbols` | offline project | Add or update symbols |
| `create_project` / `add_program` | offline project | New empty project / S7 program |
| `compile_station` | offline project | Compile a station's hardware configuration |
| `get_cpu_state` | – (online, read) | RUN / STOP / HALT / STARTUP / DEFECT |
| `list_online_blocks` | – (online, read) | Blocks loaded in the CPU |
| `compare_online_offline` | – (online, read) | Per block: identical / different / not in CPU |
| `upload_blocks` | offline project | Copy blocks from the CPU into the project |
| `download_blocks` | **PLC** | Download offline blocks to the CPU |
| `start_cpu` / `stop_cpu` | **PLC** | Warm/hot restart, or STOP |

`project` is a project name or path. `program` is a program name or path and can be omitted when the
project has only one program.

### Enabling tools that change the PLC

`download_blocks`, `start_cpu` and `stop_cpu` refuse to run unless the server is started with
`S7_MCP_ALLOW_PLC_WRITES=1` (see the `env` block in `.mcp.example.json`). They act on whatever the STEP 7
PG/PC interface reaches: the real CPU, or S7-PLCSIM when the simulator is running. Test with PLCSIM first.

## Notes

- Close the project's objects in the STEP 7 editors (LAD/STL/FBD, symbol editor) before writing; open
  editors lock them.
- `start_cpu` and `stop_cpu` fail when the CPU's mode switch is in STOP (in PLCSIM: tick RUN-P).
- Blocks uploaded from a CPU only decompile with `symbolic=True`.
- A call that gets no answer within `S7_MCP_TIMEOUT` seconds (default 180) restarts the worker.
- Worker errors outside a request are written to `%TEMP%\s7mcp_bridge_stderr.log`.
- Not implemented: downloading hardware configuration / system data, memory reset, monitoring values,
  editing the hardware configuration.
