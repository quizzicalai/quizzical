# Nightly Production DB Backup — Scheduled Task Setup

This document describes how to register the
[`backup_prod_db.ps1`](./backup_prod_db.ps1) script as a nightly Windows
Scheduled Task on this machine.

## What it does

1. Reads the production `database-url` secret from Azure Key Vault
   `quizzical-shared-kv` (or, if set, the `PROD_DATABASE_URL`
   environment variable).
2. Runs `pg_dump --format=custom` against the prod PostgreSQL server.
3. Writes `backend/backups/quizzical_<YYYYMMDD_HHMMSS>.dump` plus a
   sibling `.log` file with the per-run trace.
4. Prunes any dumps older than 7 days (configurable via
   `-RetentionDays`).

The output directory `backend/backups/` is git-ignored.

## Prerequisites

- **PostgreSQL client tools** with `pg_dump` on `PATH`, or pass
  `-PgDumpPath`.  The script auto-detects installations under
  `C:\Program Files\PostgreSQL\*\bin`.
- **Azure CLI (`az`)** signed in with read access to
  `quizzical-shared-kv` (`az login` once, with the principal that has
  `Key Vault Secrets User` on the vault).  Alternatively, set
  `PROD_DATABASE_URL` for the user that runs the task and the script
  will skip Key Vault entirely.
- **PowerShell 5.1+** (built-in) or PowerShell 7+ (`pwsh`).

## Quick manual test

From the repo root (or anywhere — the script self-locates):

```powershell
cd "c:\Users\Yeyian PC\Desktop\quizzical\quizzical\backend"
powershell -ExecutionPolicy Bypass -File scripts\backup_prod_db.ps1
```

Confirm a `.dump` file appears under `backend/backups/` and the
sibling `.log` ends with `Backup completed successfully.`

## Register the Scheduled Task (recommended: PowerShell)

Run this once **as the user that should own the task** (typically your
own Windows account).  The trigger fires nightly at 03:00 local time.

```powershell
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"c:\Users\Yeyian PC\Desktop\quizzical\quizzical\backend\scripts\backup_prod_db.ps1`""

$Trigger = New-ScheduledTaskTrigger -Daily -At 3:00am

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName "Quizzical Prod DB Backup" `
    -Description "Nightly pg_dump of production Quizzical DB into backend/backups/" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal
```

To run it on demand for verification:

```powershell
Start-ScheduledTask -TaskName "Quizzical Prod DB Backup"
Get-ScheduledTaskInfo -TaskName "Quizzical Prod DB Backup"
```

To remove it:

```powershell
Unregister-ScheduledTask -TaskName "Quizzical Prod DB Backup" -Confirm:$false
```

## Alternative: `schtasks.exe`

If you prefer the classic command-line tool:

```powershell
schtasks /Create `
    /SC DAILY /ST 03:00 `
    /TN "Quizzical Prod DB Backup" `
    /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"c:\Users\Yeyian PC\Desktop\quizzical\quizzical\backend\scripts\backup_prod_db.ps1\"" `
    /RL HIGHEST /F
```

## Operational notes

- **Idempotency**: each run produces a new file; nothing is overwritten.
- **Failure handling**: on error the script removes the partial dump,
  writes a stack trace into the sidecar `.log`, and exits with code 1
  — Task Scheduler will mark the run as failed.
- **Restore**: `pg_restore --no-owner --no-privileges --clean --dbname=<target> <file>.dump`
- **Retention override**: pass `-RetentionDays 30` to keep a month of
  history.  The trigger arguments can be edited in
  Task Scheduler → Properties → Actions.
- **No secrets in logs**: only host/port/db/user are logged; the
  password is shipped to `pg_dump` via `PGPASSWORD` and unset
  immediately afterwards.
