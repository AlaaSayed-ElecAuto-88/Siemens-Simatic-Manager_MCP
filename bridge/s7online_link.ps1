# S7ONLINE link: carries S7 protocol messages to whatever STEP 7's PG/PC interface
# reaches (S7-PLCSIM when the simulator is running, otherwise the real adapter).
#
# Must run in 32-bit Windows PowerShell: s7onlinx.dll only exists as a 32-bit DLL.
# s7online_gateway.py starts this script and talks to it with one JSON object per
# line, same framing as s7bridge.ps1:
#   {"id": 1, "op": "open", "args": {"address": "2", "rack": 0, "slot": 2}}
#   {"id": 2, "op": "exchange", "args": {"pdu": "320100..."}}   -> {"pdu": "3203..."}
#   {"id": 3, "op": "close"}
# The request block layout follows the open-source NetToPLCsim project.

$ErrorActionPreference = 'Stop'

$MARK = '@@S7@@'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$stdin = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), $utf8)
$stdout = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), $utf8)
$stdout.AutoFlush = $true

Add-Type -Namespace S7o -Name Native -MemberDefinition @'
[DllImport("s7onlinx.dll", CharSet = CharSet.Ansi)] public static extern int SCP_open(string name);
[DllImport("s7onlinx.dll")] public static extern int SCP_close(int handle);
[DllImport("s7onlinx.dll")] public static extern int SCP_send(int handle, ushort length, byte[] data);
[DllImport("s7onlinx.dll")] public static extern int SCP_receive(int handle, ushort timeout, int[] receivedLength, ushort length, byte[] data);
[DllImport("s7onlinx.dll")] public static extern int SCP_get_errno();
'@

$HDR = 80                 # request block header length
$SEG = 1024               # user data area
$LEN = $HDR + $SEG
$RECEIVE_TIMEOUT = 10000

# Request block field offsets
$O_USER = 5; $O_OPCODE = 13; $O_RESPONSE = 14; $O_FILL = 16; $O_SEGLEN = 19; $O_OFFSET = 21
$O_AB_OPCODE = 34; $O_AB_SUBSYSTEM = 35; $O_AB_SSAP = 42; $O_AB_REMOTE_STATION = 44

$script:Handle = -1
$script:AbOpcode = 0
$script:AbSubsystem = 0
$script:ReceiveArmed = $false
$script:User = 1

function Send($obj) { $stdout.WriteLine($MARK + (ConvertTo-Json $obj -Compress -Depth 6)) }
function PutU16($b, $o, $v) { $b[$o] = $v -band 0xFF; $b[$o + 1] = ($v -shr 8) -band 0xFF }
function GetU16($b, $o) { return [int]$b[$o] + ([int]$b[$o + 1] -shl 8) }

function New-Block($user, $opcode, $response, $fill, $seg, $offset) {
    $b = New-Object byte[] $LEN
    $b[4] = $HDR; PutU16 $b $O_USER $user; $b[7] = 2; $b[12] = 0x40; $b[$O_OPCODE] = $opcode
    PutU16 $b $O_RESPONSE $response; PutU16 $b $O_FILL $fill; PutU16 $b $O_SEGLEN $seg; PutU16 $b $O_OFFSET $offset
    $b[$O_AB_OPCODE] = $script:AbOpcode; $b[$O_AB_SUBSYSTEM] = $script:AbSubsystem
    return , $b
}

function Send-Block($b) {
    $n = (GetU16 $b $O_SEGLEN) + $HDR
    $rc = [S7o.Native]::SCP_send($script:Handle, [uint16]$n, $b)
    if ($rc -ne 0) { throw "SCP_send failed (errno $([S7o.Native]::SCP_get_errno()))." }
}

function Receive-Block {
    $b = New-Object byte[] $LEN
    $got = New-Object int[] 1
    $rc = [S7o.Native]::SCP_receive($script:Handle, [uint16]$RECEIVE_TIMEOUT, $got, [uint16]$LEN, $b)
    if ($rc -ne 0) { throw "No answer from the PLC (SCP_receive errno $([S7o.Native]::SCP_get_errno()))." }
    return , $b
}

function Close-Link {
    if ($script:Handle -ge 0) { [void][S7o.Native]::SCP_close($script:Handle) }
    $script:Handle = -1
    $script:ReceiveArmed = $false
}

function Op-Open($a) {
    Close-Link
    $h = [S7o.Native]::SCP_open('S7ONLINE')
    if ($h -lt 0) { throw "Cannot open the S7ONLINE access point (errno $([S7o.Native]::SCP_get_errno())). Check Set PG/PC Interface." }
    $script:Handle = $h
    try {
        $script:AbOpcode = 0; $script:AbSubsystem = 0
        Send-Block (New-Block 1 0 0xFF 0 0 0)
        $rec = Receive-Block
        $script:AbOpcode = $rec[$O_AB_OPCODE]; $script:AbSubsystem = $rec[$O_AB_SUBSYSTEM]

        $b = New-Block 1 1 0xFF 126 126 $HDR
        $b[$O_AB_SSAP] = 2; $b[$O_AB_REMOTE_STATION] = 114
        $u = $HDR
        $b[$u + 1] = 0x02; $b[$u + 2] = 0x01; $b[$u + 4] = 0x0C; $b[$u + 5] = 0x01
        # Destination: an MPI/PROFIBUS station number (one byte) or an IPv4 address (four bytes).
        $parts = ([string]$a.address).Split('.')
        if ($parts.Length -ne 1 -and $parts.Length -ne 4) { throw "Address '$($a.address)' must be an MPI/DP station number or an IPv4 address." }
        for ($i = 0; $i -lt $parts.Length; $i++) { $b[$u + 9 + $i] = [byte]$parts[$i] }
        $b[$u + 15] = 0x01; $b[$u + 17] = 0x02
        $b[$u + 18] = 1                                  # connection type: PG
        $b[$u + 19] = [int]$a.slot + [int]$a.rack * 32
        Send-Block $b
        $rec = Receive-Block
        if ((GetU16 $rec $O_RESPONSE) -ne 1) { throw "The PLC at address $($a.address) refused the connection (response $(GetU16 $rec $O_RESPONSE))." }
    } catch { Close-Link; throw }
    return @{ connected = $true }
}

function Op-Exchange($a) {
    if ($script:Handle -lt 0) { throw 'Link is not open.' }
    $hex = [string]$a.pdu
    $pdu = New-Object byte[] ($hex.Length / 2)
    for ($i = 0; $i -lt $pdu.Length; $i++) { $pdu[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16) }
    if ($pdu.Length -gt $SEG) { throw "PDU of $($pdu.Length) bytes is too large." }

    $script:User = ($script:User % 31999) + 1
    $b = New-Block $script:User 6 0xFF $pdu.Length $pdu.Length $HDR
    [Array]::Copy($pdu, 0, $b, $HDR, $pdu.Length)
    Send-Block $b
    for ($i = 0; $i -lt 8; $i++) {
        $rec = Receive-Block
        $opcode = $rec[$O_OPCODE]; $response = GetU16 $rec $O_RESPONSE
        if ($opcode -eq 6 -and $response -eq 1) {
            # Send confirmation. Post the receive request once per connection.
            if (-not $script:ReceiveArmed) { Send-Block (New-Block 0 7 0xFF 0 $SEG $HDR); $script:ReceiveArmed = $true }
        } elseif ($opcode -eq 7 -and $response -eq 3) {
            $n = GetU16 $rec $O_FILL
            $sb = New-Object System.Text.StringBuilder
            for ($k = 0; $k -lt $n; $k++) { [void]$sb.Append($rec[$HDR + $k].ToString('X2')) }
            Send-Block (New-Block 0 7 0 0 $SEG $HDR)      # re-arm the receive request
            return @{ pdu = $sb.ToString() }
        } elseif ($opcode -eq 6) {
            throw "The PLC connection was lost (send response $response)."
        }
    }
    throw 'No S7 response received.'
}

try {
    while ($true) {
        $line = $stdin.ReadLine()
        if ($null -eq $line) { break }
        if ($line.Trim() -eq '') { continue }
        $id = $null
        try {
            $req = ConvertFrom-Json $line
            $id = $req.id
            switch ([string]$req.op) {
                'open' { $result = Op-Open $req.args }
                'exchange' { $result = Op-Exchange $req.args }
                'close' { Close-Link; $result = @{ closed = $true } }
                default { throw "Unknown op '$($req.op)'." }
            }
            Send @{ id = $id; ok = $true; result = $result }
        } catch {
            Send @{ id = $id; ok = $false; error = $_.Exception.Message }
        }
    }
} finally {
    Close-Link
}
