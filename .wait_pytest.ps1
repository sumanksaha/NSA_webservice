param([string]$File, [int]$Seconds = 25)

$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
    $busy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*no:cacheprovider*' }
    if (-not $busy) { Write-Output "DONE"; break }
    Start-Sleep -Milliseconds 1500
}

Write-Output "--- tail of $File ---"
Get-Content $File -Tail 8
