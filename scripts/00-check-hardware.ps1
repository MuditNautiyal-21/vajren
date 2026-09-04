# 00 - Ground truth. Run this first and read the output before anything else.
$ErrorActionPreference = "Continue"
Write-Host "`n=== VAJREN hardware check ===`n" -ForegroundColor Cyan

$cpu = Get-CimInstance Win32_Processor
$cs  = Get-CimInstance Win32_ComputerSystem
$os  = Get-CimInstance Win32_OperatingSystem
Write-Host ("CPU     : {0} ({1}c/{2}t)" -f $cpu.Name.Trim(), $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors)
Write-Host ("RAM     : {0} GB" -f [math]::Round($cs.TotalPhysicalMemory/1GB,1))
Write-Host ("OS      : {0} build {1}" -f $os.Caption, $os.Version)

Write-Host "`nGPUs:"
Get-CimInstance Win32_VideoController | ForEach-Object {
  Write-Host ("  {0}  driver {1}" -f $_.Name, $_.DriverVersion)
}
Write-Host "  NOTE: Win32 reports VRAM wrong above 4 GB. RX 6750 XT is 12 GB."

Write-Host "`nDisk (drive TYPE matters - a 20 GB model loads in ~26s off NVMe vs ~58s off SATA):"
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -ne $null } | ForEach-Object {
  Write-Host ("  {0}:  {1} GB free" -f $_.Name, [math]::Round($_.Free/1GB,1))
}
try {
  Get-PhysicalDisk | ForEach-Object {
    $d = $_
    $letters = (Get-Partition -DiskNumber $d.DeviceId -ErrorAction SilentlyContinue |
                Where-Object DriveLetter | ForEach-Object { $_.DriveLetter }) -join ","
    Write-Host ("  disk {0}  {1}  bus={2}  media={3}  drives={4}" -f `
      $d.DeviceId, $d.FriendlyName, $d.BusType, $d.MediaType, $letters)
  }
  Write-Host "  -> Put C:\Users\ytdek\vajren\models on the NVMe disk. If F: is SATA," -ForegroundColor Yellow
  Write-Host "     every model swap costs you an extra 30 seconds." -ForegroundColor Yellow
} catch {
  Write-Host "  (could not read physical disk info - run as Administrator)" -ForegroundColor DarkGray
}

Write-Host "`nVulkan runtime:"
$vk = Get-ChildItem "C:\Windows\System32\vulkan-1.dll" -ErrorAction SilentlyContinue
if ($vk) { Write-Host "  OK - vulkan-1.dll present" -ForegroundColor Green }
else     { Write-Host "  MISSING - update your AMD Adrenalin driver" -ForegroundColor Red }

Write-Host "`nPower settings (an always-on assistant must never sleep):"
powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String "Current AC Power Setting"

Write-Host "`nIf standby is not 0x00000000, run:" -ForegroundColor Yellow
Write-Host "  powercfg /change standby-timeout-ac 0"
Write-Host "  powercfg /change hibernate-timeout-ac 0"
Write-Host "  powercfg /hibernate off"
Write-Host "  powercfg /setactive SCHEME_MIN`n"
