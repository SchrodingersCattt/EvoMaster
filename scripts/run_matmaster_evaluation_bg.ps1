param(
  [string]$EvalConfig = "playground/mat_master/evaluation/config.yaml",
  [string]$MatConfig = "configs/mat_master/config.yaml",
  [string]$OutputDir = "runs/mat_master_eval",
  [string]$RunLabel = "mat_master_eval",
  [string[]]$Levels = @(),
  [string[]]$Questions = @(),
  [int]$K = 0,
  [string[]]$Modes = @(),
  [switch]$UseSeedPrompt
)

$ErrorActionPreference = "Stop"

# Run from repo root
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $repoRoot "runs/logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir ("eval_{0}_{1}.log" -f $RunLabel, $ts)

$pyArgs = @(
  "-m", "playground.mat_master.evaluation.cli",
  "--eval-config", $EvalConfig,
  "--mat-config", $MatConfig,
  "--output-dir", $OutputDir,
  "--run-label", $RunLabel
)

if ($K -gt 0) { $pyArgs += @("--k", "$K") }
if ($Modes.Count -gt 0) { $pyArgs += @("--modes") + $Modes }
if ($UseSeedPrompt) { $pyArgs += "--use-seed-prompt" }
if ($Levels.Count -gt 0) { $pyArgs += @("--levels") + $Levels }
if ($Questions.Count -gt 0) { $pyArgs += @("--questions") + $Questions }

Write-Host "Starting evaluation..."
Write-Host "  Log: $logPath"
Write-Host "  Cmd: python $($pyArgs -join ' ')"

$proc = Start-Process -FilePath "python" -ArgumentList $pyArgs -NoNewWindow -PassThru -RedirectStandardOutput $logPath -RedirectStandardError $logPath

$meta = [PSCustomObject]@{
  pid = $proc.Id
  started_at = (Get-Date).ToString("o")
  run_label = $RunLabel
  log_path = $logPath
  output_dir = $OutputDir
  args = $pyArgs
}

$metaPath = Join-Path $logDir ("eval_{0}_{1}.json" -f $RunLabel, $ts)
$meta | ConvertTo-Json -Depth 5 | Out-File -FilePath $metaPath -Encoding utf8

Write-Host "Started PID $($proc.Id)"
Write-Host "Meta: $metaPath"