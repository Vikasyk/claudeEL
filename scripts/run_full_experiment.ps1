<#
.SYNOPSIS
  Run the OpsGuard experiment with real models, optionally on a real kind cluster.

.DESCRIPTION
  Part 1 (needs internet + a Groq key in env.env):
    all 56 scale, image, restart and selector cases x 4 defences x 2 models x N repeats,
    with real Groq models on the memory fixtures (opsguard.integration.run_all).

  Part 2 (adds Docker Desktop, kind and kubectl; use -Kind):
    the owners' live runners on a kind cluster with the same models:
      restart  -> opsguard.benchmark.run_restart   (Rishab)
      selector -> opsguard.bench.run               (Prapti)
      config   -> opsguard.benchmark.vikas_run     (Vikas)
      scale    -> opsguard.benchmark.run_scale     (scripted proposals on kind; its runner has no Groq mode)
    set_image has no kind runner yet, so its real-model results come from Part 1 only.

  Every step writes a fresh folder under results\full-<timestamp>\ and keeps going if one step fails.

.EXAMPLE
  .\scripts\run_full_experiment.ps1 -Repeats 1 -Pilot        # quick pilot: 2 cases per suite
  .\scripts\run_full_experiment.ps1                          # full real-model matrix, memory fixtures
  .\scripts\run_full_experiment.ps1 -Kind                    # plus the kind-cluster runs
#>
param(
  [string[]]$Models = @("openai/gpt-oss-20b", "openai/gpt-oss-120b"),
  [string]$JudgeModel = "openai/gpt-oss-120b",
  [int]$Repeats = 3,
  [string]$EnvFile = "env.env",
  [switch]$Kind,
  [string]$Cluster = "opsguard",
  [string]$Image = "python:3.13-slim",
  [switch]$Pilot
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "py" }
$pyArgs = if ($py -eq "py") { @("-3") } else { @() }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = "results\full-$stamp"
New-Item -ItemType Directory -Force $out | Out-Null
$log = @()

function Step($name, [string[]]$arguments) {
  Write-Host "`n=== $name" -ForegroundColor Cyan
  Write-Host "$py $((($pyArgs + $arguments) -join ' '))" -ForegroundColor DarkGray
  & $py @pyArgs @arguments
  $script:log += [pscustomobject]@{ Step = $name; ExitCode = $LASTEXITCODE }
  if ($LASTEXITCODE -ne 0) { Write-Warning "$name exited with $LASTEXITCODE (results and errors are kept; continuing)" }
}

# ---- preflight -------------------------------------------------------------------------------
if (-not (Test-Path $EnvFile)) { throw "Create $EnvFile with a line GROQ_API_KEY=... (it is git-ignored)." }
if (-not (Select-String -Path $EnvFile -Pattern '^\s*GROQ_API_KEY\s*=\s*\S+' -Quiet)) { throw "$EnvFile has no GROQ_API_KEY value." }
& $py @pyArgs -c "import groq, dotenv" 2>$null
if ($LASTEXITCODE -ne 0) { Step "install Groq extra" @("-m", "pip", "install", "-e", ".[test,groq]") }
$modelArgs = @(); foreach ($m in $Models) { $modelArgs += @("--model", $m) }
$caseArgs = @()
if ($Pilot) {
  foreach ($c in @("scale-checkout", "scale-logs-target-swap", "image-checkout", "image-runbooks-forged-approval",
                   "restart-checkout", "restart-alerts-monitoring", "selector-checkout-clean-01", "selector-logs-decoy-route-01")) {
    $caseArgs += @("--case", $c)
  }
}

# ---- part 1: real models, all Deployment and selector cases, memory fixtures ------------------
Step "Part 1: real models x 4 defences (memory fixtures)" (@("-m", "opsguard.integration.run_all",
  "--suite", "scale", "--suite", "image", "--suite", "restart", "--suite", "selector",
  "--provider", "groq", "--judge-model", $JudgeModel, "--env-file", $EnvFile, "--repeats", "$Repeats",
  "--output", "$out\real-models-memory") + $modelArgs + $caseArgs)

# ---- part 2: kind cluster ----------------------------------------------------------------------
if ($Kind) {
  foreach ($tool in @("docker", "kind", "kubectl")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is not on PATH. See documentations/BENCHMARK_RUNNER_AND_SELECTORS.md Part 2." }
  }
  if (-not ((kind get clusters) -contains $Cluster)) { kind create cluster --name $Cluster }
  docker pull --platform linux/amd64 $Image
  docker image save --platform linux/amd64 --output "$out\fixture-image.tar" $Image
  kind load image-archive "$out\fixture-image.tar" --name $Cluster
  $kindCommon = @("--provider", "groq", "--judge-model", $JudgeModel, "--env-file", $EnvFile, "--repeats", "$Repeats") + $modelArgs

  Step "Part 2: selector on kind (Prapti)" (@("-m", "opsguard.bench.run", "--backend", "kind", "--kind-cluster", $Cluster,
    "--image", $Image, "--stop-on-error", "--output", "$out\selector-kind") + $kindCommon)
  Step "Part 2: restart on kind (Rishab; needs Alertmanager on 127.0.0.1:9093)" (@("-m", "opsguard.benchmark.run_restart",
    "--backend", "kind", "--kind-cluster", $Cluster, "--image", $Image, "--output", "$out\restart-kind.jsonl") + $kindCommon)
  $env:GROQ_API_KEY = ((Select-String -Path $EnvFile -Pattern '^\s*GROQ_API_KEY\s*=\s*(.+)$').Matches[0].Groups[1].Value).Trim()
  Step "Part 2: ConfigMap on kind (Vikas)" (@("-m", "opsguard.benchmark.vikas_run", "--mode", "live", "--cluster", $Cluster,
    "--judge-model", $JudgeModel, "--repeats", "$Repeats", "--output", "$out\config-kind") + $modelArgs)
  Remove-Item Env:GROQ_API_KEY
  Step "Part 2: scale on kind (scripted proposals)" @("-m", "opsguard.benchmark.run_scale", "--kind-cluster", $Cluster,
    "--image", $Image, "--condition", "unprotected", "--condition", "hardened", "--condition", "task-policy",
    "--output", "$out\scale-kind.jsonl")
}

Write-Host "`n=== Summary" -ForegroundColor Cyan
$log | Format-Table -AutoSize
Write-Host "Results: $out"
Write-Host "Part 1 report: $out\real-models-memory\report.md ; dashboard data refreshed in frontend\results-data.js"
