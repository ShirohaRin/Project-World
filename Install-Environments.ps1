$ErrorActionPreference = 'Stop'
$environmentRoot = Join-Path $PSScriptRoot 'Environments'
$env:ELECTRON_CACHE = Join-Path $environmentRoot 'Cache\Electron'
$env:electron_config_cache = $env:ELECTRON_CACHE
if ($env:HTTP_PROXY -or $env:HTTPS_PROXY) {
 $env:ELECTRON_GET_USE_PROXY = '1'
 $env:GLOBAL_AGENT_HTTP_PROXY = $env:HTTP_PROXY
 $env:GLOBAL_AGENT_HTTPS_PROXY = $env:HTTPS_PROXY
}
$env:ELECTRON_BUILDER_CACHE = Join-Path $environmentRoot 'Cache\ElectronBuilder'
Push-Location (Join-Path $environmentRoot 'Node')
try {
 & npm.cmd install --no-audit --no-fund --cache (Join-Path $environmentRoot 'Cache\npm') --prefer-online
 if ($LASTEXITCODE -ne 0) { throw "依赖安装失败：$LASTEXITCODE" }
 if (-not (Test-Path "node_modules\electron\dist\electron.exe")) {
  & node node_modules/electron/install.js
  if ($LASTEXITCODE -ne 0) { throw "Electron 运行时下载失败" }
 }
} finally { Pop-Location }
