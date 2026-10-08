$projectRoot = $PSScriptRoot
$runtime = Join-Path $projectRoot '..\Environments\Node\node_modules\electron\dist\electron.exe'
if (-not (Test-Path -LiteralPath $runtime)) { throw '请先在 Project World 根目录执行 .\Install-Environments.ps1' }
Start-Process -FilePath $runtime -ArgumentList ('"' + $projectRoot + '"') -WorkingDirectory $projectRoot -WindowStyle Normal
