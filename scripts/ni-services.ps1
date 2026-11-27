<#
.SYNOPSIS
  Stop, start or check the Native Instruments background services.

.DESCRIPTION
  NIHardwareService and NIHostIntegrationAgent drive the keyboard for Komplete
  Kontrol / DAW integration. If they keep overwriting your light guide colours,
  or kkleds cannot open the device, stop them while you use kkleds and start
  them again when you're done. Stopping/starting needs an elevated (admin)
  PowerShell.

.EXAMPLE
  .\scripts\ni-services.ps1 status
  .\scripts\ni-services.ps1 stop
  .\scripts\ni-services.ps1 start
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'stop', 'start')]
    [string]$Action = 'status'
)

$names = @('NIHostIntegrationAgent', 'NIHardwareService')

switch ($Action) {
    'stop' {
        foreach ($n in $names) { Stop-Service -Name $n -Force -ErrorAction Continue }
    }
    'start' {
        # Start in reverse order: the hardware service first.
        foreach ($n in ($names[($names.Count - 1)..0])) { Start-Service -Name $n -ErrorAction Continue }
    }
}

Get-Service -Name $names -ErrorAction SilentlyContinue | Format-Table Status, Name, StartType -AutoSize
