# Start the pet store MCP server (streamable_http, 127.0.0.1:8000) on Windows.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$venvPython = Join-Path $PWD '.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }

& $python -m petdoctor.mcp_server.server
