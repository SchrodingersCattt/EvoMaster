param(
  [Parameter(Mandatory=$true)][string]$RunDir,
  [string]$LogPath = "",
  [int]$TailLines = 200
)

$ErrorActionPreference = "Stop"

function Show-Progress {
  param([string]$dir)
  $raw = Join-Path $dir "raw_runs.jsonl"
  if (Test-Path $raw) {
    $n = (Get-Content $raw -ReadCount 0 | Measure-Object -Line).Lines
    Write-Host ("raw_runs.jsonl lines: {0}" -f $n)
  } else {
    Write-Host "raw_runs.jsonl: (not found yet)"
  }

  $final = Join-Path $dir "final_report.md"
  $sbq = Join-Path $dir "scores_by_question.json"
  $sbl = Join-Path $dir "scores_by_level.json"

  foreach ($p in @($final, $sbq, $sbl)) {
    if (Test-Path $p) {
      $info = Get-Item $p
      Write-Host ("{0}: {1} bytes, mtime {2}" -f (Split-Path $p -Leaf), $info.Length, $info.LastWriteTime)
    } else {
      Write-Host ("{0}: (missing)" -f (Split-Path $p -Leaf))
    }
  }
}

Write-Host "RunDir: $RunDir"
Show-Progress -dir $RunDir

if ($LogPath -ne "") {
  Write-Host "\n--- tail log ($TailLines lines) ---"
  Get-Content $LogPath -Tail $TailLines
  Write-Host "\n--- follow log (Ctrl+C to stop) ---"
  Get-Content $LogPath -Wait
}
