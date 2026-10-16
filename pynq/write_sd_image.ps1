# Write a raw disk image to the Red Pitaya's SD card and verify it.  DESTROYS everything on the target disk.
#
#   Run from an elevated (Administrator) PowerShell:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\Dycontrol4\rp_iq_sd\write_sd_image.ps1" -Image "<image.img>" -DiskNumber 1
#
# Safety: the target must be a removable USB/SD disk, must not be the system disk, and you must confirm.
# Method: the partition table is cleared first, everything AFTER the first chunk is written and verified, and the
# first chunk (which holds the MBR) is written last. Otherwise Windows notices the new partitions mid-write, mounts
# them and blocks further raw writes ("a device which does not exist was specified" / "access denied").
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][int]$DiskNumber,
    [int]$BufferMB = 4,
    [switch]$SkipBodyWrite      # body already on the card: only verify it, then write the first chunk (MBR)
)
$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Please run this script from an Administrator PowerShell." -ForegroundColor Red; exit 1
}
if (-not (Test-Path $Image)) { Write-Host "image not found: $Image" -ForegroundColor Red; exit 1 }
$imgLen = (Get-Item $Image).Length

$disk = Get-Disk -Number $DiskNumber
if ($disk.IsSystem -or $disk.IsBoot) { Write-Host "disk $DiskNumber is the system/boot disk - refusing." -ForegroundColor Red; exit 1 }
if ($disk.BusType -notin 'USB', 'SD') { Write-Host "disk $DiskNumber bus type is $($disk.BusType), not USB/SD - refusing." -ForegroundColor Red; exit 1 }
if ($disk.Size -lt $imgLen) { Write-Host "disk is smaller than the image - refusing." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "TARGET DISK $DiskNumber : $($disk.FriendlyName)  $([math]::Round($disk.Size/1GB,1)) GB  bus $($disk.BusType)" -ForegroundColor Yellow
Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue | ForEach-Object {
    $v = Get-Volume -Partition $_ -ErrorAction SilentlyContinue
    Write-Host ("   partition {0}: {1,8:N0} MB  drive {2}  label '{3}'" -f $_.PartitionNumber, ($_.Size / 1MB), $_.DriveLetter, $v.FileSystemLabel) -ForegroundColor Yellow
}
Write-Host "IMAGE: $Image  ($([math]::Round($imgLen/1MB,1)) MB)" -ForegroundColor Yellow
Write-Host ""
$ans = Read-Host "Everything on disk $DiskNumber will be erased. Type YES to continue"
if ($ans -ne 'YES') { Write-Host "aborted"; exit 1 }

if (-not $SkipBodyWrite) {
# remove the partition table so Windows mounts nothing on this disk while we write
Write-Host "clearing partition table..."
try { Clear-Disk -Number $DiskNumber -RemoveData -RemoveOEM -Confirm:$false -ErrorAction Stop } catch { Write-Host "  (Clear-Disk: $($_.Exception.Message.Trim()))" }
try { Set-Disk -Number $DiskNumber -IsReadOnly $false -ErrorAction Stop } catch {}
Start-Sleep -Seconds 3
# make sure the first sector really is blank now (otherwise Windows may re-detect partitions during the write)
$zero = New-Object byte[] 1MB
$chk = New-Object IO.FileStream("\\.\PhysicalDrive$DiskNumber", [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 1MB, [IO.FileOptions]::WriteThrough)
try { $chk.Write($zero, 0, $zero.Length); $chk.Flush($true) } finally { $chk.Close() }
Start-Sleep -Seconds 2
}

$chunk = $BufferMB * 1MB
$buf = New-Object byte[] $chunk
$first = New-Object byte[] $chunk
$src = [IO.File]::OpenRead($Image)
$dst = New-Object IO.FileStream("\\.\PhysicalDrive$DiskNumber", [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 1MB, [IO.FileOptions]::WriteThrough)
try {
    # keep the first chunk (MBR + start of the boot partition) for the end
    $firstLen = $src.Read($first, 0, $chunk)
    if ($firstLen % 512 -ne 0) { $pad = 512 - ($firstLen % 512); [Array]::Clear($first, $firstLen, $pad); $firstLen += $pad }

    $t0 = Get-Date; $done = [int64]$firstLen; $lastPct = -1
    $dst.Position = $firstLen
    if ($SkipBodyWrite) { Write-Host "skipping body write (verify only)"; $done = [int64]$imgLen; if ($done % 512 -ne 0) { $done += 512 - ($done % 512) } }
    else { Write-Host "writing (first $BufferMB MB deferred to the end)..." }
    while (-not $SkipBodyWrite -and ($n = $src.Read($buf, 0, $chunk)) -gt 0) {
        if ($n % 512 -ne 0) { $pad = 512 - ($n % 512); [Array]::Clear($buf, $n, $pad); $n += $pad }
        $dst.Write($buf, 0, $n)
        $done += $n
        $pct = [int](100 * $done / $imgLen)
        if ($pct -ne $lastPct -and $pct % 5 -eq 0) {
            $rate = ($done - $firstLen) / 1MB / ((Get-Date) - $t0).TotalSeconds
            Write-Host ("  {0,3}%  {1,6:N0} MB  {2,5:N1} MB/s" -f $pct, ($done / 1MB), $rate); $lastPct = $pct
        }
    }
    $dst.Flush($true)
    $total = $done
    Write-Host ("body written: {0:N0} MB in {1:N0} s" -f (($total - $firstLen) / 1MB), ((Get-Date) - $t0).TotalSeconds)

    # verify the body (everything after the first chunk) by reading it back
    Write-Host "verifying body..."
    $src.Position = $firstLen; $dst.Position = $firstLen
    $shaA = [Security.Cryptography.SHA256]::Create(); $shaB = [Security.Cryptography.SHA256]::Create()
    $buf2 = New-Object byte[] $chunk
    $remaining = $total - $firstLen
    while ($remaining -gt 0) {
        $want = [int][math]::Min([int64]$chunk, [int64]$remaining)
        $nA = $src.Read($buf, 0, $want); if ($nA -lt $want) { [Array]::Clear($buf, $nA, $want - $nA) }
        $nB = 0; while ($nB -lt $want) { $r = $dst.Read($buf2, $nB, $want - $nB); if ($r -le 0) { break }; $nB += $r }
        [void]$shaA.TransformBlock($buf, 0, $want, $null, 0)
        [void]$shaB.TransformBlock($buf2, 0, $want, $null, 0)
        $remaining -= $want
    }
    [void]$shaA.TransformFinalBlock($buf, 0, 0); [void]$shaB.TransformFinalBlock($buf2, 0, 0)
    $hA = [BitConverter]::ToString($shaA.Hash) -replace '-', ''
    $hB = [BitConverter]::ToString($shaB.Hash) -replace '-', ''
    if ($hA -ne $hB) { Write-Host "BODY VERIFY FAILED - card content differs from the image. MBR not written." -ForegroundColor Red; exit 2 }
    Write-Host "body verified OK (SHA256 $hA)"

    # now the first chunk, MBR included
    Write-Host "writing first chunk (MBR)..."
    $dst.Position = 0
    $dst.Write($first, 0, $firstLen)
    $dst.Flush($true)
    $dst.Position = 0
    $rb = New-Object byte[] $firstLen
    $nB = 0; while ($nB -lt $firstLen) { $r = $dst.Read($rb, $nB, $firstLen - $nB); if ($r -le 0) { break }; $nB += $r }
    $same = $true
    for ($i = 0; $i -lt $firstLen; $i++) { if ($rb[$i] -ne $first[$i]) { $same = $false; break } }
    if ($same) { Write-Host "VERIFY OK - the whole image is on the card." -ForegroundColor Green } else { Write-Host "FIRST CHUNK VERIFY FAILED" -ForegroundColor Red; exit 2 }
} finally {
    $dst.Close(); $src.Close()
    Update-Disk -Number $DiskNumber -ErrorAction SilentlyContinue
}
Write-Host "done. Eject the card (Windows may now show a PYNQ drive and ask to format a second one - say no) and put it in the Red Pitaya."
