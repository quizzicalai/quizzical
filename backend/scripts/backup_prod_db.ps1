<#
.SYNOPSIS
  Nightly backup of the production Quizzical PostgreSQL database.

.DESCRIPTION
  Pulls the production connection string from Azure Key Vault (or a
  local environment variable fallback), runs `pg_dump` in custom
  format, writes the resulting `.dump` file under `backend/backups/`,
  and prunes dumps older than the retention window (default 7 days).

  Designed to be invoked by Windows Task Scheduler. Idempotent and
  side-effect-light: every run produces exactly one new dump file plus
  a sibling `.log` file describing the run.

.PARAMETER VaultName
  Azure Key Vault name. Defaults to `quizzical-shared-kv`.

.PARAMETER SecretName
  Name of the secret in Key Vault that holds the SQLAlchemy URL.
  Defaults to `database-url`.

.PARAMETER BackupDir
  Directory in which to place the dump files. Defaults to
  `<repo>/backend/backups`.

.PARAMETER RetentionDays
  Number of days to keep dumps before deletion. Defaults to 7.

.PARAMETER PgDumpPath
  Optional explicit path to the `pg_dump` executable. If not set the
  script searches `PATH` and falls back to the bundled installation
  under `C:\Program Files\PostgreSQL\*\bin`.

.PARAMETER ConnectionString
  Optional override. If set, used directly and Key Vault is skipped.
  Useful for ad-hoc backups; never commit a real value.

.EXAMPLE
  pwsh -File scripts/backup_prod_db.ps1

.EXAMPLE
  pwsh -File scripts/backup_prod_db.ps1 -RetentionDays 14
#>
[CmdletBinding()]
param(
    [string]$VaultName = "quizzical-shared-kv",
    [string]$SecretName = "database-url",
    [string]$BackupDir,
    [int]$RetentionDays = 7,
    [string]$PgDumpPath,
    [string]$ConnectionString
)

$ErrorActionPreference = "Stop"

# ---- Resolve paths ---------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Split-Path -Parent $ScriptDir
if (-not $BackupDir) {
    $BackupDir = Join-Path $BackendDir "backups"
}
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DumpPath = Join-Path $BackupDir "quizzical_$Timestamp.dump"
$LogPath = Join-Path $BackupDir "quizzical_$Timestamp.log"

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "o"), $Level, $Message
    Write-Host $line
    Add-Content -Path $LogPath -Value $line
}

# ---- Locate pg_dump --------------------------------------------------------
function Resolve-PgDump {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path $Explicit)) { return $Explicit }
    $cmd = Get-Command pg_dump -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = Get-ChildItem `
        -Path "C:\Program Files\PostgreSQL\*\bin\pg_dump.exe" `
        -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    if ($candidates) { return $candidates[0].FullName }
    throw "pg_dump not found. Install PostgreSQL client tools or pass -PgDumpPath."
}

# ---- Fetch connection string ----------------------------------------------
function Get-ProdConnectionString {
    param(
        [string]$Override,
        [string]$Vault,
        [string]$Secret
    )
    if ($Override) {
        Write-Log "Using -ConnectionString override (Key Vault skipped)."
        return $Override
    }
    $envValue = $env:PROD_DATABASE_URL
    if ($envValue) {
        Write-Log "Using PROD_DATABASE_URL from environment."
        return $envValue
    }
    Write-Log "Fetching '$Secret' from Key Vault '$Vault'..."
    $az = Get-Command az -ErrorAction SilentlyContinue
    if (-not $az) {
        throw "Azure CLI ('az') not found and PROD_DATABASE_URL not set."
    }
    $value = & az keyvault secret show `
        --vault-name $Vault `
        --name $Secret `
        --query value `
        -o tsv 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        throw "Failed to fetch secret '$Secret' from vault '$Vault': $value"
    }
    return $value.Trim()
}

# ---- Parse SQLAlchemy URL into pg_dump-friendly parts ---------------------
function ConvertFrom-SqlAlchemyUrl {
    param([string]$Url)
    # Accept postgresql+asyncpg://user:pass@host:port/db, postgresql://...,
    # postgres://...
    $clean = $Url -replace "^postgresql\+[a-zA-Z0-9]+://", "postgresql://"
    $clean = $clean -replace "^postgres://", "postgresql://"
    if ($clean -notmatch "^postgresql://") {
        throw "Unsupported URL scheme: $Url"
    }
    $uri = [Uri]$clean
    $userInfo = $uri.UserInfo
    if (-not $userInfo) {
        throw "Connection URL is missing user:password component."
    }
    $userParts = $userInfo.Split(":", 2)
    $user = [Uri]::UnescapeDataString($userParts[0])
    $password = if ($userParts.Length -eq 2) {
        [Uri]::UnescapeDataString($userParts[1])
    } else { "" }
    $database = $uri.AbsolutePath.TrimStart("/").Split("?", 2)[0]
    $port = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }
    return @{
        Host     = $uri.Host
        Port     = $port
        User     = $user
        Password = $password
        Database = $database
    }
}

# ---- Run ------------------------------------------------------------------
try {
    Write-Log "Backup starting. Target file: $DumpPath"

    $pgDump = Resolve-PgDump -Explicit $PgDumpPath
    Write-Log "Using pg_dump at: $pgDump"

    $connStr = Get-ProdConnectionString `
        -Override $ConnectionString `
        -Vault $VaultName `
        -Secret $SecretName

    $parts = ConvertFrom-SqlAlchemyUrl -Url $connStr
    Write-Log ("Connecting host={0} port={1} db={2} user={3}" -f `
        $parts.Host, $parts.Port, $parts.Database, $parts.User)

    $env:PGPASSWORD = $parts.Password
    try {
        & $pgDump `
            --host=$($parts.Host) `
            --port=$($parts.Port) `
            --username=$($parts.User) `
            --dbname=$($parts.Database) `
            --format=custom `
            --no-owner `
            --no-privileges `
            --file=$DumpPath
        $exit = $LASTEXITCODE
    }
    finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }

    if ($exit -ne 0) {
        throw "pg_dump exited with code $exit"
    }

    $size = (Get-Item $DumpPath).Length
    if ($size -lt 1024) {
        throw "Dump file is suspiciously small ($size bytes); aborting."
    }
    Write-Log ("Dump complete. Size: {0:N0} bytes" -f $size)

    # ---- Prune ----------------------------------------------------------
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    $stale = Get-ChildItem -Path $BackupDir -Filter "quizzical_*.dump" |
        Where-Object { $_.LastWriteTime -lt $cutoff }
    foreach ($f in $stale) {
        Write-Log "Pruning old dump: $($f.Name)"
        Remove-Item $f.FullName -Force
        $sidecar = [IO.Path]::ChangeExtension($f.FullName, ".log")
        if (Test-Path $sidecar) { Remove-Item $sidecar -Force }
    }

    Write-Log "Backup completed successfully."
    exit 0
}
catch {
    Write-Log -Level "ERROR" -Message $_.Exception.Message
    if (Test-Path $DumpPath) {
        Write-Log -Level "ERROR" -Message "Removing partial dump: $DumpPath"
        Remove-Item $DumpPath -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
