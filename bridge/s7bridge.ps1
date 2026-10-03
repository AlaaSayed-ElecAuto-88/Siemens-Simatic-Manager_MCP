# STEP 7 (SIMATIC Manager) command-interface bridge.
#
# Must run in 32-bit Windows PowerShell (SysWOW64): the Simatic.Simatic COM
# server (S7ABATCX.DLL) is a 32-bit in-process DLL. server.py starts this
# script and talks to it with one JSON object per line:
#   request : {"id": 1, "op": "list_projects", "args": {...}}
#   response: @@S7@@{"id": 1, "ok": true, "result": ...}
# Only lines starting with the marker are protocol; anything else is noise.

$ErrorActionPreference = 'Stop'

$MARK = '@@S7@@'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$ansi = [System.Text.Encoding]::Default   # STEP 7 reads/writes sources in the ANSI code page
$stdin = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), $utf8)
$stdout = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), $utf8)
$stdout.AutoFlush = $true

$WORK = Join-Path $env:TEMP 's7mcp'
[void](New-Item -ItemType Directory -Force $WORK)
$VERBLOG = Join-Path $WORK ("verb_{0}.log" -f $PID)

$BLOCK_CONTAINER = 1138689
$SOURCE_CONTAINER = 1122308
$SWTYPE_SOURCE = 65
$PROGRAM_S7 = 1327361

$BLOCK_TYPES = @{ 1138945 = 'FB'; 1138946 = 'FC'; 1138947 = 'DB'; 1138948 = 'OB'; 1138949 = 'SDB'; 1138955 = 'SDBs'
                  1138950 = 'UDT'; 1138951 = 'SFC'; 1138952 = 'SFB'; 1138953 = 'VAT' }
$SOURCE_TYPES = @{ 1122309 = 'STL'; 1122310 = 'SCL'; 1122311 = 'GRAPH'; 1122312 = 'SCLMake'; 1122313 = 'GG'
                   1122314 = 'ZG'; 1122315 = 'NET'; 1139210 = 'STL (encrypted)'; 1139211 = 'SCL (encrypted)' }
$SOURCE_EXTS = @{ 1122309 = 'awl'; 1122310 = 'scl'; 1122311 = 'gr7' }
$CPU_STATES = @{ 256 = 'RUN'; 512 = 'STOP'; 1024 = 'HALT'; 2048 = 'DEFECT'; 4096 = 'STARTUP' }
$USER_BLOCK_TYPES = @(1138945, 1138946, 1138947, 1138948, 1138950)   # FB, FC, DB, OB, UDT
$SYSTEM_DATA = 1138955
$OVERWRITE_ALL = 2
$OVERWRITE_NONE = 4
$PROJECT_TYPES = @{ 1122305 = 'project'; 1122306 = 'library' }

$script:S7 = $null

function Get-S7 {
    if ($null -eq $script:S7) {
        $s = New-Object -ComObject Simatic.Simatic
        $s.UnattendedServerMode = $true   # never pop up modal dialogs
        $s.AutomaticSave = 1
        try { $s.VerbLogFile = $VERBLOG } catch { }
        $script:S7 = $s
    }
    return , $script:S7
}

function Send($obj) {
    $stdout.WriteLine($MARK + (ConvertTo-Json $obj -Compress -Depth 12))
}

function TypeName($map, $value) {
    $k = [int]$value
    if ($map.ContainsKey($k)) { return $map[$k] }
    return "unknown($k)"
}

function IsoDate($d) {
    try { return ([datetime]$d).ToString('s') } catch { return $null }
}

function Arg($a, $name, $default = $null) {
    if ($null -ne $a -and ($a.PSObject.Properties.Name -contains $name) -and $null -ne $a.$name) { return $a.$name }
    return $default
}

function Need($a, $name) {
    $v = Arg $a $name
    if ($null -eq $v -or "$v" -eq '') { throw "Missing required argument '$name'." }
    return $v
}

function TempFile($ext) {
    return (Join-Path $WORK ("{0}.{1}" -f [guid]::NewGuid().ToString('N'), $ext))
}

function ReadShared($path, $enc) {
    $fs = New-Object System.IO.FileStream($path, 'Open', 'Read', 'ReadWrite')
    try { $r = New-Object System.IO.StreamReader($fs, $enc); return $r.ReadToEnd() } finally { $fs.Dispose() }
}

function VerbLogLength {
    if (Test-Path $VERBLOG) { return (Get-Item $VERBLOG).Length }
    return 0
}

function VerbLogSince($offset) {
    try {
        if (-not (Test-Path $VERBLOG)) { return '' }
        $all = ReadShared $VERBLOG $ansi
        $bytes = $ansi.GetByteCount($all)
        if ($offset -ge $bytes) { return '' }
        return $ansi.GetString($ansi.GetBytes($all), $offset, $bytes - $offset).Trim()
    } catch { return '' }
}

function Find-Project($key) {
    $s = Get-S7
    $hits = New-Object System.Collections.ArrayList
    foreach ($p in $s.Projects) {
        if ($p.Name -eq $key -or $p.LogPath -eq $key) { [void]$hits.Add($p) }
    }
    if ($hits.Count -eq 0) { throw "Project '$key' not found. Call list_projects for valid names/paths." }
    if ($hits.Count -gt 1) { throw "Project name '$key' is ambiguous ($($hits.Count) matches); pass the project path instead." }
    return , $hits[0]
}

function Find-Program($proj, $key) {
    $all = New-Object System.Collections.ArrayList
    foreach ($g in $proj.Programs) { [void]$all.Add($g) }
    if ($all.Count -eq 0) { throw "Project '$($proj.Name)' contains no programs." }
    if ($null -eq $key -or "$key" -eq '') {
        if ($all.Count -eq 1) { return , $all[0] }
        $names = ($all | ForEach-Object { $_.LogPath }) -join '; '
        throw "Project has $($all.Count) programs; specify one of: $names"
    }
    $hits = @($all | Where-Object { $_.LogPath -eq $key })
    if ($hits.Count -eq 0) { $hits = @($all | Where-Object { $_.Name -eq $key }) }
    if ($hits.Count -eq 0) { throw "Program '$key' not found in project '$($proj.Name)'." }
    if ($hits.Count -gt 1) {
        $names = ($hits | ForEach-Object { $_.LogPath }) -join '; '
        throw "Program name '$key' is ambiguous; pass its path instead: $names"
    }
    return , $hits[0]
}

function Find-Container($prog, $concreteType, $label) {
    foreach ($c in $prog.Next) {
        if ([int]$c.ConcreteType -eq $concreteType) { return , $c }
    }
    throw "Program '$($prog.Name)' has no $label container."
}

function Resolve-Program($a) {
    $proj = Find-Project (Need $a 'project')
    return , (Find-Program $proj (Arg $a 'program'))
}

function Find-Item($container, $name, $label) {
    try { $i = $container.Next.Item([string]$name) } catch { $i = $null }
    if ($null -eq $i) { throw "$label '$name' not found." }
    return , $i
}

function BlockInfo($b) {
    $o = [ordered]@{ name = $b.Name; type = (TypeName $BLOCK_TYPES $b.ConcreteType) }
    foreach ($p in 'SymbolicName', 'Language', 'Size', 'Family', 'HeaderName', 'HeaderVersion', 'KnowHowProtection', 'Comment') {
        try { $o[$p.Substring(0, 1).ToLower() + $p.Substring(1)] = $b.$p } catch { }
    }
    $o['modified'] = IsoDate $b.Modified
    return $o
}

# ---------------------------------------------------------------- operations

function Op-Ping($a) {
    [void](Get-S7)
    $reg = Get-ItemProperty 'HKLM:\SOFTWARE\Siemens\AUTSW\STEP7' -ErrorAction SilentlyContinue
    return [ordered]@{ step7 = $reg.ProdName; version = $reg.VersionString; bridgeBits = [IntPtr]::Size * 8; workDir = $WORK }
}

function Op-ListProjects($a) {
    $s = Get-S7
    $out = New-Object System.Collections.ArrayList
    foreach ($p in $s.Projects) {
        [void]$out.Add([ordered]@{ name = $p.Name; path = $p.LogPath; kind = (TypeName $PROJECT_TYPES $p.Type) })
    }
    return , $out
}

function Op-ProjectTree($a) {
    $proj = Find-Project (Need $a 'project')
    $stations = New-Object System.Collections.ArrayList
    foreach ($st in $proj.Stations) {
        [void]$stations.Add($st.Name)
    }
    $programs = New-Object System.Collections.ArrayList
    foreach ($g in $proj.Programs) {
        $containers = New-Object System.Collections.ArrayList
        foreach ($c in $g.Next) {
            $kind = 'other'
            if ([int]$c.ConcreteType -eq $BLOCK_CONTAINER) { $kind = 'blocks' }
            elseif ([int]$c.ConcreteType -eq $SOURCE_CONTAINER) { $kind = 'sources' }
            [void]$containers.Add([ordered]@{ name = $c.Name; kind = $kind; count = $c.Next.Count })
        }
        $module = $null
        try { $module = $g.Module.Name } catch { }
        [void]$programs.Add([ordered]@{ name = $g.Name; path = $g.LogPath; cpu = $module; containers = $containers })
    }
    return [ordered]@{
        name = $proj.Name; path = $proj.LogPath; kind = (TypeName $PROJECT_TYPES $proj.Type)
        comment = $proj.Comment; creator = $proj.Creator
        created = (IsoDate $proj.Created); modified = (IsoDate $proj.Modified)
        stations = $stations; programs = $programs
    }
}

function Op-ListBlocks($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $BLOCK_CONTAINER 'Blocks'
    $out = New-Object System.Collections.ArrayList
    foreach ($b in $c.Next) { [void]$out.Add((BlockInfo $b)) }
    return , $out
}

function Op-BlockSource($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $BLOCK_CONTAINER 'Blocks'
    $flags = [int](Arg $a 'flags' 0)
    $out = New-Object System.Collections.ArrayList
    foreach ($name in @(Need $a 'blocks')) {
        $tmp = TempFile 'awl'
        $logStart = VerbLogLength
        try {
            $b = Find-Item $c $name 'Block'
            $b.GenerateSource($tmp, $flags)
            [void]$out.Add([ordered]@{ block = $name; source = [IO.File]::ReadAllText($tmp, $ansi) })
        } catch {
            [void]$out.Add([ordered]@{ block = $name; error = $_.Exception.Message; log = (VerbLogSince $logStart) })
        } finally {
            if (Test-Path $tmp) { Remove-Item $tmp -Force }
        }
    }
    return , $out
}

function Op-ListSources($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $SOURCE_CONTAINER 'Sources'
    $out = New-Object System.Collections.ArrayList
    foreach ($s in $c.Next) {
        $o = [ordered]@{ name = $s.Name }
        try { $o['language'] = TypeName $SOURCE_TYPES $s.ConcreteType } catch { }
        try { $o['size'] = $s.Size } catch { }
        $o['modified'] = IsoDate $s.Modified
        [void]$out.Add($o)
    }
    return , $out
}

function Op-GetSource($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $SOURCE_CONTAINER 'Sources'
    $src = Find-Item $c (Need $a 'name') 'Source'
    # Export only succeeds when the file extension matches the source language.
    $ext = 'awl'
    if ($SOURCE_EXTS.ContainsKey([int]$src.ConcreteType)) { $ext = $SOURCE_EXTS[[int]$src.ConcreteType] }
    $tmp = TempFile $ext
    try {
        $src.Export($tmp)
        return [ordered]@{ name = $src.Name; language = (TypeName $SOURCE_TYPES $src.ConcreteType); source = [IO.File]::ReadAllText($tmp, $ansi) }
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }
}

function Op-ImportSource($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $SOURCE_CONTAINER 'Sources'
    $name = Need $a 'name'
    $ext = ([string](Arg $a 'language' 'awl')).ToLower()
    $existing = $null
    try { $existing = $c.Next.Item([string]$name) } catch { }
    if ($null -ne $existing) {
        if (-not [bool](Arg $a 'overwrite' $false)) { throw "Source '$name' already exists; pass overwrite=true to replace it." }
        $existing.Remove()
    }
    # STEP 7 derives the source language from the file extension.
    $tmp = Join-Path $WORK ("{0}.{1}" -f [guid]::NewGuid().ToString('N'), $ext)
    try {
        [IO.File]::WriteAllText($tmp, [string](Need $a 'text'), $ansi)
        $src = $c.Next.Add([string]$name, $SWTYPE_SOURCE, $tmp)
        return [ordered]@{ name = $src.Name; language = (TypeName $SOURCE_TYPES $src.ConcreteType) }
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }
}

function Op-CompileSource($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $SOURCE_CONTAINER 'Sources'
    $src = Find-Item $c (Need $a 'name') 'Source'
    $logStart = VerbLogLength
    $blocks = New-Object System.Collections.ArrayList
    $err = $null
    try {
        $res = $src.Compile()
        if ($null -ne $res) { foreach ($b in $res) { [void]$blocks.Add($b.Name) } }
    } catch { $err = $_.Exception.Message }
    $o = [ordered]@{ source = $src.Name; ok = ($null -eq $err); blocks = $blocks; log = (VerbLogSince $logStart) }
    if ($null -ne $err) { $o['error'] = $err }
    return $o
}

function Op-ExportSymbols($a) {
    $prog = Resolve-Program $a
    $tmp = TempFile 'sdf'
    try {
        $prog.SymbolTable.Export($tmp)
        return [ordered]@{ sdf = [IO.File]::ReadAllText($tmp, $ansi) }
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }
}

function Op-ImportSymbols($a) {
    $prog = Resolve-Program $a
    $tmp = TempFile 'sdf'
    try {
        [IO.File]::WriteAllText($tmp, [string](Need $a 'sdf'), $ansi)
        $n = $prog.SymbolTable.Import($tmp, [int](Arg $a 'mode' 0))
        return [ordered]@{ result = $n }
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }
}

function Op-StationConfig($a) {
    $proj = Find-Project (Need $a 'project')
    $name = Need $a 'station'
    $st = $null
    try { $st = $proj.Stations.Item([string]$name) } catch { }
    if ($null -eq $st) { throw "Station '$name' not found in project '$($proj.Name)'." }
    $tmp = TempFile 'cfg'
    try {
        $st.Export($tmp)
        return [ordered]@{ station = $st.Name; config = [IO.File]::ReadAllText($tmp, $ansi) }
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }
}

function Op-CreateProject($a) {
    $s = Get-S7
    $name = Need $a 'name'
    $dir = Need $a 'directory'
    if (-not (Test-Path $dir)) { throw "Directory '$dir' does not exist." }
    $p = $s.Projects.Add([string]$name, [string]$dir)
    return [ordered]@{ name = $p.Name; path = $p.LogPath }
}

function Op-AddProgram($a) {
    $proj = Find-Project (Need $a 'project')
    $g = $proj.Programs.Add([string](Need $a 'name'), $PROGRAM_S7)
    return [ordered]@{ name = $g.Name; path = $g.LogPath }
}

# ----------------------------------------------------- online (PLC) operations

function CpuState($prog) {
    return (TypeName $CPU_STATES $prog.ModuleState)
}

function Select-Blocks($coll, $names, $label) {
    # Explicit names, or every user block (system data, SFC/SFB and VATs never go to/from the CPU this way).
    $out = New-Object System.Collections.ArrayList
    if ($null -ne $names -and @($names).Count -gt 0) {
        foreach ($n in @($names)) {
            $b = $null
            try { $b = $coll.Item([string]$n) } catch { }
            [void]$out.Add(@{ name = [string]$n; block = $b; error = $(if ($null -eq $b) { "$label '$n' not found." } else { $null }) })
        }
    } else {
        foreach ($b in $coll) {
            if ($USER_BLOCK_TYPES -contains [int]$b.ConcreteType) { [void]$out.Add(@{ name = $b.Name; block = $b; error = $null }) }
        }
    }
    return , $out
}

function Op-CpuState($a) {
    $prog = Resolve-Program $a
    return [ordered]@{ program = $prog.LogPath; state = (CpuState $prog) }
}

function Op-CpuControl($a) {
    $prog = Resolve-Program $a
    $before = CpuState $prog
    switch ([string](Need $a 'action')) {
        'warm_restart' { $prog.NewStart() }
        'hot_restart' { $prog.Restart() }
        'stop' { $prog.Stop() }
        'memory_reset' { $prog.Reset() }
        'compress' { $prog.Compress() }
        default { throw "Unknown action '$($a.action)'." }
    }
    Start-Sleep -Milliseconds 1500
    return [ordered]@{ program = $prog.LogPath; before = $before; state = (CpuState $prog) }
}

function Op-ListOnlineBlocks($a) {
    $prog = Resolve-Program $a
    $out = New-Object System.Collections.ArrayList
    foreach ($b in $prog.OnlineBlocks) {
        [void]$out.Add([ordered]@{ name = $b.Name; type = (TypeName $BLOCK_TYPES $b.ConcreteType) })
    }
    return , $out
}

function Op-Download($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $BLOCK_CONTAINER 'Blocks'
    $flag = $OVERWRITE_NONE
    if ([bool](Arg $a 'overwrite' $true)) { $flag = $OVERWRITE_ALL }
    $out = New-Object System.Collections.ArrayList
    foreach ($e in (Select-Blocks $c.Next (Arg $a 'blocks') 'Block')) {
        $r = [ordered]@{ block = $e.name }
        if ($null -ne $e.error) { $r['error'] = $e.error }
        else { try { $e.block.Download($flag); $r['downloaded'] = $true } catch { $r['error'] = $_.Exception.Message } }
        [void]$out.Add($r)
    }
    return [ordered]@{ program = $prog.LogPath; cpuState = (CpuState $prog); blocks = $out }
}

function Op-Upload($a) {
    $prog = Resolve-Program $a
    $overwrite = [bool](Arg $a 'overwrite' $false)
    $flag = $OVERWRITE_NONE
    if ($overwrite) { $flag = $OVERWRITE_ALL }
    $offline = (Find-Container $prog $BLOCK_CONTAINER 'Blocks').Next
    $out = New-Object System.Collections.ArrayList
    foreach ($e in (Select-Blocks $prog.OnlineBlocks (Arg $a 'blocks') 'Online block')) {
        $r = [ordered]@{ block = $e.name }
        # STEP 7 skips existing blocks silently with S7OverwriteNone, so report that here.
        $exists = $null
        try { $exists = $offline.Item([string]$e.name) } catch { }
        if ($null -ne $e.error) { $r['error'] = $e.error }
        elseif ($null -ne $exists -and -not $overwrite) { $r['skipped'] = 'already exists offline; pass overwrite=true to replace it' }
        else { try { $e.block.Upload($flag); $r['uploaded'] = $true } catch { $r['error'] = $_.Exception.Message } }
        [void]$out.Add($r)
    }
    return [ordered]@{ program = $prog.LogPath; blocks = $out }
}

function Op-Compare($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $BLOCK_CONTAINER 'Blocks'
    $out = New-Object System.Collections.ArrayList
    foreach ($e in (Select-Blocks $c.Next (Arg $a 'blocks') 'Block')) {
        $r = [ordered]@{ block = $e.name }
        if ($null -ne $e.error) { $r['error'] = $e.error }
        else {
            try { $rc = 0; $e.block.CompareOnlineOffline([ref]$rc); $r['code'] = [int]$rc } catch { $r['error'] = $_.Exception.Message }
        }
        [void]$out.Add($r)
    }
    return [ordered]@{ program = $prog.LogPath; blocks = $out }
}

function Op-CompileStation($a) {
    $proj = Find-Project (Need $a 'project')
    $name = Need $a 'station'
    $st = $null
    try { $st = $proj.Stations.Item([string]$name) } catch { }
    if ($null -eq $st) { throw "Station '$name' not found in project '$($proj.Name)'." }
    $logStart = VerbLogLength
    $rc = 0
    $st.Compile([ref]$rc)
    return [ordered]@{ station = $st.Name; code = [int]$rc; log = (VerbLogSince $logStart) }
}

function Op-DownloadSystemData($a) {
    $prog = Resolve-Program $a
    $c = Find-Container $prog $BLOCK_CONTAINER 'Blocks'
    $sdb = $null
    foreach ($b in $c.Next) { if ([int]$b.ConcreteType -eq $SYSTEM_DATA) { $sdb = $b } }
    if ($null -eq $sdb) { throw "Program '$($prog.Name)' has no System Data; compile the station first." }
    # Download returns without error when the CPU is unreachable, so check first.
    if ([int]$prog.ModuleState -eq 0) { throw "The CPU of program '$($prog.Name)' cannot be reached online." }
    $sdb.Download($OVERWRITE_ALL)
    return [ordered]@{ program = $prog.LogPath; downloaded = $sdb.Name; cpuState = (CpuState $prog) }
}

function Op-ImportStation($a) {
    $proj = Find-Project (Need $a 'project')
    $tmp = TempFile 'cfg'
    $logStart = VerbLogLength
    try {
        [IO.File]::WriteAllText($tmp, [string](Need $a 'config'), $ansi)
        $st = $proj.Stations.Import($tmp)
        return [ordered]@{ station = $st.Name; log = (VerbLogSince $logStart) }
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }
}

function Op-CopyBlocks($a) {
    $proj = Find-Project (Need $a 'project')
    $src = Find-Container (Find-Program $proj (Need $a 'source_program')) $BLOCK_CONTAINER 'Blocks'
    $targetProj = Find-Project (Arg $a 'target_project' (Need $a 'project'))
    $dst = Find-Container (Find-Program $targetProj (Need $a 'target_program')) $BLOCK_CONTAINER 'Blocks'
    $overwrite = [bool](Arg $a 'overwrite' $false)
    $out = New-Object System.Collections.ArrayList
    foreach ($e in (Select-Blocks $src.Next (Arg $a 'blocks') 'Block')) {
        $r = [ordered]@{ block = $e.name }
        $exists = $null
        try { $exists = $dst.Next.Item([string]$e.name) } catch { }
        if ($null -ne $e.error) { $r['error'] = $e.error }
        elseif ($null -ne $exists -and -not $overwrite) { $r['skipped'] = 'already exists in target; pass overwrite=true to replace it' }
        else {
            try {
                if ($null -ne $exists) { $exists.Remove() }
                [void]$e.block.Copy($dst)
                $r['copied'] = $true
            } catch { $r['error'] = $_.Exception.Message }
        }
        [void]$out.Add($r)
    }
    return , $out
}

$OPS = @{
    copy_blocks = 'Op-CopyBlocks'
    download_system_data ='Op-DownloadSystemData'; import_station = 'Op-ImportStation'
    cpu_state = 'Op-CpuState'; cpu_control = 'Op-CpuControl'; list_online_blocks = 'Op-ListOnlineBlocks'
    download = 'Op-Download'; upload = 'Op-Upload'; compare = 'Op-Compare'; compile_station = 'Op-CompileStation'
    ping = 'Op-Ping'; list_projects = 'Op-ListProjects'; project_tree = 'Op-ProjectTree'
    list_blocks = 'Op-ListBlocks'; block_source = 'Op-BlockSource'
    list_sources = 'Op-ListSources'; get_source = 'Op-GetSource'
    import_source = 'Op-ImportSource'; compile_source = 'Op-CompileSource'
    export_symbols = 'Op-ExportSymbols'; import_symbols = 'Op-ImportSymbols'
    station_config = 'Op-StationConfig'; create_project = 'Op-CreateProject'; add_program = 'Op-AddProgram'
}

# ----------------------------------------------------------------- main loop

try {
    while ($true) {
        $line = $stdin.ReadLine()
        if ($null -eq $line) { break }
        if ($line.Trim() -eq '') { continue }
        $id = $null
        $logStart = VerbLogLength
        try {
            $req = ConvertFrom-Json $line
            $id = $req.id
            if (-not $OPS.ContainsKey([string]$req.op)) { throw "Unknown op '$($req.op)'." }
            $result = & $OPS[[string]$req.op] $req.args
            Send @{ id = $id; ok = $true; result = $result }
        } catch {
            Send @{ id = $id; ok = $false; error = $_.Exception.Message; log = (VerbLogSince $logStart) }
        }
    }
} finally {
    if ($null -ne $script:S7) {
        try { $script:S7.Save() } catch { }
        try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($script:S7) } catch { }
    }
    if (Test-Path $VERBLOG) { Remove-Item $VERBLOG -Force -ErrorAction SilentlyContinue }
}
