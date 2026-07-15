[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$Install
)

$ErrorActionPreference = "Stop"

function Resolve-Python {
    param([string]$Requested)
    if ($Requested) {
        if (-not (Test-Path $Requested)) { throw "Python 路径不存在：$Requested" }
        return (Resolve-Path $Requested).Path
    }
    if (Test-Path ".venv\Scripts\python.exe") {
        return (Resolve-Path ".venv\Scripts\python.exe").Path
    }
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return "py" }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "未找到 Python。请让 Codex 根据项目现状安装或选择解释器。"
}

$pythonExe = Resolve-Python $Python
Write-Host "Python command: $pythonExe"

if ($pythonExe -eq "py") {
    & py -3 --version
    if ($Install -and -not (Test-Path ".venv\Scripts\python.exe")) {
        & py -3 -m venv .venv
        $pythonExe = (Resolve-Path ".venv\Scripts\python.exe").Path
    }
} else {
    & $pythonExe --version
    if ($Install -and -not (Test-Path ".venv\Scripts\python.exe")) {
        & $pythonExe -m venv .venv
        $pythonExe = (Resolve-Path ".venv\Scripts\python.exe").Path
    }
}

if ($Install) {
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install mistralai
}

& $pythonExe -c "from mistralai.client import Mistral; import importlib.metadata as m; print('mistralai import OK', m.version('mistralai'), Mistral.__name__)"

if ([string]::IsNullOrWhiteSpace($env:MISTRAL_API_KEY)) {
    Write-Host "MISTRAL_API_KEY: absent"
} else {
    Write-Host "MISTRAL_API_KEY: present"
}

Write-Host "No OCR request was sent."
