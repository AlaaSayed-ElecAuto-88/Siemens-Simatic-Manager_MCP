# SIMATIC Manager MCP

MCP server for Siemens STEP 7 V5.x (SIMATIC Manager, "classic"). It lets an MCP client such as Claude
browse and edit STEP 7 projects, work online with the CPU, and read or write live values.

## How it works

```
                           +-- bridge/s7bridge.ps1 (32-bit PowerShell) -- COM ------- STEP 7 (projects, download, ...)
MCP client -- server.py ---+
 (stdio)   (64-bit Python) +-- s7client.py --+-- bridge/s7online_link.ps1 -- S7ONLINE -- PLCSIM or the real PLC
                                             +-- TCP port 102 ------------------------- real PLC (route "direct")
```

- STEP 7's automation interface (`Simatic.Simatic`) and its `S7ONLINE` driver (`s7onlinx.dll`) are 32-bit
  only, so 64-bit Python cannot load them. The server keeps 32-bit Windows PowerShell workers alive and
  sends them one JSON request at a time.
- Live values use a small built-in S7 protocol client. Through `S7ONLINE` it reaches whatever STEP 7's
  PG/PC interface reaches, which is S7-PLCSIM whenever the simulator is running.

| File | Purpose |
| --- | --- |
| `server.py` | MCP tools |
| `worker.py` | Runs a PowerShell worker and exchanges JSON lines with it |
| `bridge/s7bridge.ps1` | STEP 7 command interface (projects, blocks, compile, download, CPU control) |
| `bridge/s7online_link.ps1` | Carries S7 messages over the `S7ONLINE` access point |
| `s7client.py` | S7 protocol client: read/write memory, system status lists |
| `plc_values.py` | Operand addresses and data types |
| `ladder.py` | Draws STL networks as text ladder rungs |
| `s7online_gateway.py` | Optional standalone PLCSIM gateway for other S7 client programs |

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
   claude mcp add simatic-manager --scope user -- python "C:\path\to\Siemens-Simatic-Manager_MCP\server.py"
   ```

   Or copy `.mcp.example.json` to `.mcp.json` (project scope) or into `claude_desktop_config.json`
   (Claude Desktop) and fix the path.
4. Start a new session and call `step7_status`. If it fails with a DLL initialisation error, apply the
   permissions fix above.

## Tools

`project` is a project name or path. `program` is a program name or path and can be omitted when the
project has only one program.

### Read a project (nothing is changed)

| Tool | Purpose |
| --- | --- |
| `step7_status` | Check STEP 7 is reachable, report version |
| `list_projects` | Projects and libraries known to SIMATIC Manager |
| `get_project_tree` | Stations, S7 programs and their containers |
| `list_blocks` | Blocks of a program with header info |
| `get_block_source` | Decompile blocks to STL text (symbolic or absolute) |
| `get_block_ladder` | Blocks as text-drawn ladder rungs |
| `list_sources` / `get_source` | Read the Sources folder |
| `get_symbol_table` | Symbols, optionally filtered |
| `get_station_config` | Hardware configuration as HW Config export text |
| `get_station_hardware` | Racks, modules, addresses and DP slaves as a tree |
| `get_module_parameters` | Parameters of one module or DP slave |

### Change the offline project

| Tool | Purpose |
| --- | --- |
| `import_source` | Store STL/SCL text as a source file |
| `compile_source` | Compile a source into blocks, returns the compiler log |
| `import_symbols` | Add or update symbols |
| `copy_blocks` | Copy blocks between programs or projects |
| `create_project` / `add_program` | New empty project / S7 program |
| `update_module` | Change a module in place: name, parameters, IP / MPI / PROFIBUS address |
| `add_module` / `remove_module` | Insert or remove a module in an existing station |
| `import_station_config` | Create a station from (edited) HW Config text |
| `compile_station` | Compile a station's hardware configuration, or only check its consistency |
| `upload_blocks` | Copy blocks from the CPU into the project |

### Read from the PLC

| Tool | Purpose |
| --- | --- |
| `get_cpu_state` | RUN / STOP / HALT / STARTUP / DEFECT |
| `list_online_blocks` | Blocks loaded in the CPU |
| `compare_online_offline` | Per block: identical / different / not in CPU |
| `read_plc_values` | Live inputs, outputs, memory bits, timers, counters, DB values; by address or symbol name |
| `get_cpu_diagnostics` | State, order number, firmware, diagnostic buffer |

### Change the PLC (disabled by default)

| Tool | Purpose |
| --- | --- |
| `download_blocks` | Download offline blocks to the CPU |
| `download_system_data` | Download the compiled hardware configuration (CPU in STOP) |
| `start_cpu` / `stop_cpu` | Warm/hot restart, or STOP |
| `write_plc_values` | Write memory bits, outputs, DB values |
| `memory_reset` | Erase the CPU's program (CPU in STOP) |
| `compress_memory` | Compress the CPU's load memory |

These refuse to run unless the server is started with `S7_MCP_ALLOW_PLC_WRITES=1` (see the `env` block in
`.mcp.example.json`). They act on whatever STEP 7's PG/PC interface reaches: the real CPU, or S7-PLCSIM
when the simulator is running. Test with PLCSIM first.

## Live values

`read_plc_values` and `write_plc_values` take the CPU's `address` (MPI/DP station number such as `"2"`, or
an IP address) and a list of items:

- operand addresses: `I 0.0`, `QB 4`, `MW 10`, `MD 20:REAL`, `DB10.DBX0.1`, `DB10.DBW4:INT`, `T 5`, `C 3`
  (German mnemonics `E`, `A`, `Z` also work); `:TYPE` overrides the data type
- symbol names from the program's symbol table, when `project` is given

`route="step7"` (default) goes through STEP 7's PG/PC interface, so it follows the same path as SIMATIC
Manager and lands in PLCSIM when the simulator is running. `route="direct"` opens TCP port 102 on the IP
address without STEP 7; it never reaches PLCSIM.

## PLCSIM gateway for other programs

Classic S7-PLCSIM has no network port. `s7online_gateway.py` gives it one, so that other S7 client
software (python-snap7, an HMI, a SCADA test) can talk to the simulator:

```
python s7online_gateway.py --address 2 --port 1102
```

Then connect the client to `127.0.0.1`, port 1102. The server itself does not need the gateway.

## Editing the hardware configuration

In place, in the existing station (the program is untouched): `get_station_hardware` to find a module's
path, `get_module_parameters`, then `update_module`, `add_module` or `remove_module`, and finally
`compile_station`. Values STEP 7 does not accept are rejected and the old value is kept.

I/O addresses are the exception: STEP 7 accepts an address change through this interface but never saves
it. To change addresses, use the text route, which always creates a new station with an empty program:
`get_station_config`, edit the text, `import_station_config`, `compile_station`, then `copy_blocks` and
`get_symbol_table` / `import_symbols` to bring the program across. The original station is left untouched.

## Notes

- Close the project's objects in the STEP 7 editors (LAD/STL/FBD, symbol editor) before writing; open
  editors lock them.
- `start_cpu` and `stop_cpu` fail when the CPU's mode switch is in STOP (in PLCSIM: tick RUN-P).
- While PLCSIM is running it answers for every MPI address, so a wrong `address` still gets values.
- Blocks uploaded from a CPU only decompile with `symbolic=True`.
- `get_block_ladder` draws bit logic, edges, coils, timers and counters; other networks stay as STL.
- Diagnostic buffer texts are included for common events only; the event ID is always returned.
- A call that gets no answer within `S7_MCP_TIMEOUT` seconds (default 180) restarts the worker.
- Worker errors outside a request are written to `%TEMP%\s7mcp_*_stderr.log`.
- Not possible: forcing values, opening know-how-protected blocks, writing timers and counters.

## Test status

Tested against S7-PLCSIM V5.4: project browsing, source import and compile, block download/upload/compare,
CPU start/stop, live read/write, diagnostics, station import and compile, block copy, in-place hardware
edits on a central rack. Not yet tested: anything on a real CPU, `route="direct"` against a real CPU,
`download_system_data`, `memory_reset`, `compress_memory`, hardware edits on DP slaves.

SCL sources need a working S7-SCL package. On the development PC (STEP 7 V5.6 with S7-SCL V5.7) STEP 7
refused the import with "software package 'S7-SCL' ... not installed or exists in an earlier version".

## Credits

The `S7ONLINE` request block layout follows the open-source NetToPLCsim project by Thomas Wiens.
