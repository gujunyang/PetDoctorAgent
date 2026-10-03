# Portable PostgreSQL control script (no admin / no Windows service).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 start|stop|status|restart
param(
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'status'
)

# Install root; override with env var PET_PG_ROOT.
$PgRoot = if ($env:PET_PG_ROOT) { $env:PET_PG_ROOT } else { Join-Path $env:USERPROFILE 'pgsql' }
$Bin = Join-Path $PgRoot 'pgsql\bin'
$Data = Join-Path $PgRoot 'data'
$Log = Join-Path $PgRoot 'server.log'
$PgCtl = Join-Path $Bin 'pg_ctl.exe'

if (-not (Test-Path $PgCtl)) {
    Write-Error "pg_ctl not found: $PgCtl (set PET_PG_ROOT to override)"
    exit 1
}

function Test-PgRunning {
    & $PgCtl -D $Data status 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}

switch ($Action) {
    'start' {
        if (Test-PgRunning) { Write-Output "PostgreSQL already running"; break }
        $pidFile = Join-Path $Data 'postmaster.pid'
        if (Test-Path $pidFile) {
            $listening = Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue
            if (-not $listening) { Remove-Item $pidFile -Force }
        }
        Start-Process -FilePath $PgCtl -ArgumentList @('-D', $Data, '-l', $Log, 'start') -WindowStyle Hidden
        Start-Sleep -Seconds 4
        if (Test-PgRunning) { Write-Output "PostgreSQL started (port 5432, log: $Log)" }
        else { Write-Error "start failed; see $Log"; exit 1 }
    }
    'stop' {
        & $PgCtl -D $Data -m fast stop
    }
    'restart' {
        & $PgCtl -D $Data -m fast restart -l $Log
    }
    'status' {
        & $PgCtl -D $Data status
        $listening = Get-NetTCPConnection -LocalPort 5432 -State Listen -ErrorAction SilentlyContinue
        Write-Output ("port 5432 listening: {0}" -f [bool]$listening)
    }
}
