$ErrorActionPreference = "Stop"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
$Root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $Root "src") + $(if ($env:PYTHONPATH) { ";" + $env:PYTHONPATH } else { "" })
$script = Join-Path $Root "src\grctl.py"
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $script @args
} else {
    & py -3 $script @args
}
exit $LASTEXITCODE
