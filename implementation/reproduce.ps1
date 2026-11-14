$ErrorActionPreference = 'Stop'
$somaRoot = Split-Path -Parent $PSScriptRoot
$somaPython = if ($env:SOMA_PYTHON) { $env:SOMA_PYTHON } else { Join-Path $somaRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $somaPython)) {
    & py -3.12 -m venv (Join-Path $somaRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required' }
}
# Always reconcile exact pins, including when an existing venv is reused.
& $somaPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed' }
& $somaPython (Join-Path $PSScriptRoot 'verify_dependencies.py')
if ($LASTEXITCODE -ne 0) { throw 'Dependency pin verification failed' }
$somaPgBin = if ($env:SOMA_PG_BIN) { $env:SOMA_PG_BIN } else { Join-Path $somaRoot 'tools\postgresql\pgsql\bin' }
if (-not (Test-Path -LiteralPath (Join-Path $somaPgBin 'postgres.exe'))) {
    $somaTools = Join-Path $somaRoot 'tools'
    New-Item -ItemType Directory -Force -Path $somaTools | Out-Null
    $somaArchive = Join-Path $somaTools 'postgresql-18.7-windows.zip'
    Invoke-WebRequest -Uri 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260664' -OutFile $somaArchive -UseBasicParsing
    if ((Get-FileHash -LiteralPath $somaArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'ead0bbc27fb678fa24dcf86b738981323042abd073c744103bf7f3aed23f9a33') { throw 'PostgreSQL archive hash mismatch' }
    Expand-Archive -LiteralPath $somaArchive -DestinationPath (Join-Path $somaTools 'postgresql')
}
$env:SOMA_PG_BIN = $somaPgBin
& $somaPython (Join-Path $PSScriptRoot 'run_r4.py')
if ($LASTEXITCODE -ne 0) { throw 'R4 reproduction failed; preserve .runtime/failures and results' }
& $somaPython (Join-Path $PSScriptRoot 'publication_audit.py') --write-manifest
if ($LASTEXITCODE -ne 0) { throw 'Public allowlist or leakage audit failed' }
& $somaPython (Join-Path $PSScriptRoot 'publication_audit.py') --verify-manifest
if ($LASTEXITCODE -ne 0) { throw 'Public manifest verification failed' }
