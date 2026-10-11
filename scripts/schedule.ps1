<#
.SYNOPSIS
    Register, inspect or remove the job-search scheduled tasks.

.DESCRIPTION
    Two tasks:

      jobsearch-platform Cycle   every 15 minutes - watch once, then triage,
                        rank and redraw the dashboard for every profile. This is the one that matters: triage has
                        nothing to rank until the watcher has been running.

      jobsearch-platform Verify  weekly - re-check every board is still alive and still
                        belongs to the company we think it does. Discovery is a
                        slow-moving problem; polling it every 15 minutes would
                        be 96x the requests for no extra signal.

    Both run under the current user, only while logged on, with no stored
    password. Both use pythonw.exe so nothing flashes on screen.

    Output goes to var/_shared/logs/cycle-YYYYMM.log, not to a console.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\schedule.ps1 -Action install
    powershell -ExecutionPolicy Bypass -File scripts\schedule.ps1 -Action status
    powershell -ExecutionPolicy Bypass -File scripts\schedule.ps1 -Action remove
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'remove', 'status')]
    [string]$Action = 'status',

    [int]$IntervalMinutes = 15
)

$ErrorActionPreference = 'Stop'

$Root      = Split-Path -Parent $PSScriptRoot
$PythonW   = Join-Path $Root '.venv\Scripts\pythonw.exe'
$CycleName = 'jobsearch-platform Cycle'
$VerifyName = 'jobsearch-platform Verify'

function Assert-Setup {
    if (-not (Test-Path $PythonW)) {
        throw "Not found: $PythonW`nCreate the venv first: python -m venv .venv; .venv\Scripts\python.exe -m pip install -e `".[dev]`""
    }
}

function Show-Status {
    foreach ($name in @($CycleName, $VerifyName)) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host ("  {0,-18} not installed" -f $name)
            continue
        }
        $info = Get-ScheduledTaskInfo -TaskName $name
        $last = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 1999) { $info.LastRunTime } else { 'never' }
        $result = switch ($info.LastTaskResult) {
            0       { 'ok' }
            267011  { 'not yet run' }
            default { "exit $($info.LastTaskResult)" }
        }
        Write-Host ("  {0,-18} {1,-9} last: {2}  ({3})  next: {4}" -f `
            $name, $task.State, $last, $result, $info.NextRunTime)
    }

    $log = Join-Path $Root ('var\_shared\logs\cycle-{0:yyyyMM}.log' -f (Get-Date))
    if (Test-Path $log) {
        Write-Host "`n  last 5 log lines ($log):"
        Get-Content $log -Tail 5 | ForEach-Object { Write-Host "    $_" }
    } else {
        Write-Host "`n  no log yet: $log"
    }
}

function Remove-Tasks {
    foreach ($name in @($CycleName, $VerifyName)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "  removed $name"
        } else {
            Write-Host "  $name was not installed"
        }
    }
}

function Install-Tasks {
    Assert-Setup
    Remove-Tasks | Out-Null

    # Runs only while logged on, so no password is stored anywhere.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    # StartWhenAvailable catches up a run missed while the machine was asleep.
    # ExecutionTimeLimit stops a hung fetch from blocking every later run.
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopIfGoingOnBatteries `
        -AllowStartIfOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -Hidden

    # ── cycle, every N minutes, indefinitely ─────────────────────────────────
    $cycleAction = New-ScheduledTaskAction -Execute $PythonW `
        -Argument '-m jobsearch.cli cycle --quiet' -WorkingDirectory $Root
    $cycleTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
    Register-ScheduledTask -TaskName $CycleName -Action $cycleAction `
        -Trigger $cycleTrigger -Principal $principal -Settings $settings `
        -Description "Poll job boards once, then triage, rank and redraw for every profile." | Out-Null
    Write-Host "  installed $CycleName  (every $IntervalMinutes min)"

    # ── verify, weekly ───────────────────────────────────────────────────────
    $verifyAction = New-ScheduledTaskAction -Execute $PythonW `
        -Argument '-m jobsearch.cli verify' -WorkingDirectory $Root
    $verifyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
    Register-ScheduledTask -TaskName $VerifyName -Action $verifyAction `
        -Trigger $verifyTrigger -Principal $principal -Settings $settings `
        -Description "Re-check every job board is alive and still belongs to its company." | Out-Null
    Write-Host "  installed $VerifyName  (Sundays 09:00)"

    Write-Host "`n  First cycle runs in about a minute. Check with:"
    Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\schedule.ps1 -Action status"
}

switch ($Action) {
    'install' { Install-Tasks }
    'remove'  { Remove-Tasks }
    'status'  { Show-Status }
}
