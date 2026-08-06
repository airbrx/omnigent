<#
.SYNOPSIS
  Control the omnigent dev box (EC2 i-099d66548b496d876).

.DESCRIPTION
  devbox status    - instance state, uptime, idle counter
  devbox start     - wake it and wait until the omnigent host reconnects
  devbox stop      - put it to sleep (EBS volume and all setup persist)
  devbox connect   - open an SSM shell as the michael user
  devbox idle      - show idle-watcher state and recent decisions
  devbox cost      - rough month-to-date running cost

  Stopping only halts compute; the 50GB gp3 volume (~$4/mo) persists, which is
  what makes waking fast and lossless.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('status', 'start', 'wake', 'stop', 'sleep', 'connect', 'ssh', 'idle', 'cost')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'
$InstanceId = 'i-099d66548b496d876'
$Region = 'us-east-1'

# The AWS CLI writes its own stdout as cp1252 on this machine and dies on any
# non-ASCII byte in a response. Force UTF-8 for every child process.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Get-State {
    (& aws ec2 describe-instances --instance-ids $InstanceId --region $Region `
        --query 'Reservations[].Instances[].State.Name' --output text).Trim()
}

function Invoke-OnBox {
    param([string[]]$Commands)
    $json = @{ commands = $Commands } | ConvertTo-Json -Compress
    # -AsArray isn't available in PS 5.1; a single-element array collapses to a
    # bare string, which SSM rejects. Force the array shape.
    if ($Commands.Count -eq 1) { $json = '{"commands":' + (ConvertTo-Json @($Commands) -Compress) + '}' }
    $tmp = New-TemporaryFile
    # NOT Set-Content -Encoding utf8: PS 5.1 writes a BOM, and the AWS CLI's
    # JSON parser chokes on it ("Expected: '=', received: '<BOM>'").
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
    try {
        $out = & aws ssm send-command --instance-ids $InstanceId --region $Region `
            --document-name AWS-RunShellScript --parameters "file://$tmp" --output json
        $cid = ($out | ConvertFrom-Json).Command.CommandId
        & aws ssm wait command-executed --command-id $cid --instance-id $InstanceId `
            --region $Region 2>$null
        $inv = & aws ssm get-command-invocation --command-id $cid `
            --instance-id $InstanceId --region $Region --output json | ConvertFrom-Json
        return $inv.StandardOutputContent
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

switch ($Command) {

    { $_ -in 'start', 'wake' } {
        $state = Get-State
        if ($state -eq 'running') {
            Write-Host "already running" -ForegroundColor Green
            break
        }
        Write-Host "starting $InstanceId (was: $state) ..." -ForegroundColor Cyan
        & aws ec2 start-instances --instance-ids $InstanceId --region $Region --output text | Out-Null
        & aws ec2 wait instance-running --instance-ids $InstanceId --region $Region
        Write-Host "instance running; waiting for SSM agent ..." -ForegroundColor Cyan

        # The omnigent host unit is lingering with Restart=always, so it
        # reconnects on its own; SSM Online is the proxy for "boot finished".
        $deadline = (Get-Date).AddMinutes(4)
        do {
            Start-Sleep -Seconds 5
            $ping = (& aws ssm describe-instance-information --region $Region `
                --filters "Key=InstanceIds,Values=$InstanceId" `
                --query 'InstanceInformationList[].PingStatus' --output text).Trim()
        } while ($ping -ne 'Online' -and (Get-Date) -lt $deadline)

        if ($ping -ne 'Online') {
            Write-Host "SSM did not come Online within 4 min - check the console" -ForegroundColor Red
            break
        }
        Write-Host "SSM Online. Checking the omnigent host ..." -ForegroundColor Cyan
        $svc = Invoke-OnBox @(
            "runuser -l michael -c 'export XDG_RUNTIME_DIR=/run/user/1001; systemctl --user is-active omnigent-host.service'"
        )
        Write-Host ("omnigent-host: " + $svc.Trim()) -ForegroundColor Green
        Write-Host "ready - https://omnigent.airbrx.ai (host: omnigent-devbox)"
    }

    { $_ -in 'stop', 'sleep' } {
        $state = Get-State
        if ($state -ne 'running') {
            Write-Host "not running (state: $state)" -ForegroundColor Yellow
            break
        }
        Write-Host "stopping $InstanceId ..." -ForegroundColor Cyan
        & aws ec2 stop-instances --instance-ids $InstanceId --region $Region --output text | Out-Null
        & aws ec2 wait instance-stopped --instance-ids $InstanceId --region $Region
        Write-Host "stopped. compute billing halted; the 50GB volume persists (~`$4/mo)." -ForegroundColor Green
    }

    { $_ -in 'connect', 'ssh' } {
        if ((Get-State) -ne 'running') {
            Write-Host "box is not running - 'devbox start' first" -ForegroundColor Yellow
            break
        }
        Write-Host "opening SSM session (you land as ssm-user; 'sudo su - michael')" -ForegroundColor Cyan
        & aws ssm start-session --target $InstanceId --region $Region
    }

    'idle' {
        if ((Get-State) -ne 'running') {
            Write-Host "box is not running" -ForegroundColor Yellow
            break
        }
        Write-Output (Invoke-OnBox @('devbox-report idle'))
    }

    'cost' {
        $start = (Get-Date -Day 1).ToString('yyyy-MM-dd')
        $end = (Get-Date).AddDays(1).ToString('yyyy-MM-dd')
        Write-Host "month-to-date EC2 + EBS (whole account, us-east-1):" -ForegroundColor Cyan
        & aws ce get-cost-and-usage --region us-east-1 `
            --time-period "Start=$start,End=$end" --granularity MONTHLY --metrics UnblendedCost `
            --filter '{"Dimensions":{"Key":"SERVICE","Values":["Amazon Elastic Compute Cloud - Compute","EC2 - Other"]}}' `
            --query 'ResultsByTime[].Total.UnblendedCost.Amount' --output text
    }

    default {
        $state = Get-State
        $color = if ($state -eq 'running') { 'Green' } else { 'Yellow' }
        Write-Host "omnigent-devbox ($InstanceId): $state" -ForegroundColor $color
        if ($state -eq 'running') {
            Write-Output (Invoke-OnBox @('devbox-report status'))
        } else {
            Write-Host "wake it with: devbox start"
        }
    }
}
