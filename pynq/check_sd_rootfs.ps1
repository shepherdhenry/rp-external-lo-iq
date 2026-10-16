# Read the ext4 superblock of the PYNQ SD card's root partition (partition 2) and report whether it looks valid.
# Run from an elevated (Administrator) PowerShell:   & "Y:\Users\Henry\code\RP_IQ\pynq\check_sd_rootfs.ps1"
param([int]$DiskNumber = -1)

if ($DiskNumber -lt 0) {
    $d = Get-Disk | Where-Object { $_.BusType -in 'USB', 'SD' -and $_.PartitionStyle -eq 'MBR' } | Select-Object -First 1
    if (-not $d) { Write-Host "no USB/SD MBR disk found"; exit 1 }
    $DiskNumber = $d.Number
}
$parts = Get-Partition -DiskNumber $DiskNumber | Sort-Object PartitionNumber
Write-Host "disk $DiskNumber partitions:"
$parts | Select-Object PartitionNumber, DriveLetter, @{n = 'OffsetMB'; e = { [math]::Round($_.Offset / 1MB, 1) } }, @{n = 'SizeMB'; e = { [math]::Round($_.Size / 1MB, 1) } }, MbrType | Format-Table -AutoSize | Out-String | Write-Host
$p2 = $parts | Where-Object PartitionNumber -eq 2
if (-not $p2) { Write-Host "no partition 2"; exit 1 }

$fs = New-Object IO.FileStream("\\.\PhysicalDrive$DiskNumber", [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
try {
    # ext4 superblock lives 1024 bytes into the partition
    $fs.Position = $p2.Offset + 1024
    $b = New-Object byte[] 1024
    [void]$fs.Read($b, 0, 1024)
    $magic = [BitConverter]::ToUInt16($b, 0x38)
    $inodes = [BitConverter]::ToUInt32($b, 0)
    $blocks = [BitConverter]::ToUInt32($b, 4)
    $bs = 1024 -shl [BitConverter]::ToUInt32($b, 24)
    $state = [BitConverter]::ToUInt16($b, 0x3A)
    $label = [Text.Encoding]::ASCII.GetString($b, 0x78, 16).Trim([char]0)
    $incompat = [BitConverter]::ToUInt32($b, 0x60)
    $rocompat = [BitConverter]::ToUInt32($b, 0x64)
    Write-Host ("superblock magic     : 0x{0:X4}   (valid ext2/3/4 = 0xEF53)" -f $magic)
    if ($magic -eq 0xEF53) {
        Write-Host ("filesystem size      : {0} MB in {1} blocks of {2} bytes   (partition is {3} MB)" -f [math]::Round([double]$blocks * $bs / 1MB), $blocks, $bs, [math]::Round($p2.Size / 1MB))
        Write-Host ("inodes               : {0}" -f $inodes)
        Write-Host ("state                : 0x{0:X}   (1 = cleanly unmounted, 2 = errors detected)" -f $state)
        Write-Host ("label                : '{0}'" -f $label)
        Write-Host ("feature_incompat     : 0x{0:X}   feature_ro_compat: 0x{1:X}" -f $incompat, $rocompat)
        # sanity: does the filesystem claim more space than the partition?
        if ([double]$blocks * $bs -gt $p2.Size + 1MB) { Write-Host "WARNING: filesystem is larger than the partition -> image was truncated when written" }
        # check the backup superblock in block group 1 (block 32768 for 4k blocks) and a few group descriptors
        $fs.Position = $p2.Offset + 32768 * $bs + (1024 * [int]($bs -eq 1024))
        $b2 = New-Object byte[] 1024
        [void]$fs.Read($b2, 0, 1024)
        Write-Host ("backup superblock    : magic 0x{0:X4}" -f [BitConverter]::ToUInt16($b2, 0x38))
    } else {
        Write-Host "The root partition does NOT contain a valid ext4 superblock. First 64 bytes:"
        Write-Host ((($b[0..63]) | ForEach-Object { '{0:X2}' -f $_ }) -join ' ')
    }
    # how much of the partition is non-zero? sample 64 points
    $nonzero = 0
    $buf = New-Object byte[] 4096
    for ($i = 0; $i -lt 64; $i++) {
        $fs.Position = $p2.Offset + [int64]($p2.Size / 64 * $i)
        [void]$fs.Read($buf, 0, 4096)
        if (($buf | Where-Object { $_ -ne 0 } | Select-Object -First 1)) { $nonzero++ }
    }
    Write-Host ("data present         : {0}/64 sampled 4 KB blocks across the partition are non-zero" -f $nonzero)
} finally { $fs.Close() }
