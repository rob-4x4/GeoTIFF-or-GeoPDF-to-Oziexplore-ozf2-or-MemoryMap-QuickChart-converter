$ErrorActionPreference = 'Stop'

$appRoot = Join-Path $env:LOCALAPPDATA 'GeoTIFFQuickChart'
$mambaPath = Join-Path $appRoot 'micromamba.exe'
$envPath = Join-Path $appRoot 'environment'
$env:MAMBA_ROOT_PREFIX = Join-Path $appRoot 'mamba-root'
$archivePath = Join-Path $env:TEMP ('quickchart-micromamba-' + [guid]::NewGuid().ToString('N') + '.tar.bz2')
$extractPath = Join-Path $env:TEMP ('quickchart-micromamba-' + [guid]::NewGuid().ToString('N'))

try {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'The automatic setup currently requires 64-bit Windows.'
    }
    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null

    if (-not (Test-Path -LiteralPath $mambaPath -PathType Leaf)) {
        Write-Host 'Downloading the official micromamba Windows package...'
        Invoke-WebRequest -Uri 'https://micro.mamba.pm/api/micromamba/win-64/latest' `
            -OutFile $archivePath -UseBasicParsing
        New-Item -ItemType Directory -Path $extractPath -Force | Out-Null
        & tar.exe -xjf $archivePath -C $extractPath 'Library/bin/micromamba.exe'
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract micromamba from its official package.' }
        $downloadedMamba = Join-Path $extractPath 'Library\bin\micromamba.exe'
        if (-not (Test-Path -LiteralPath $downloadedMamba -PathType Leaf)) {
            throw 'The micromamba package did not contain the expected Windows executable.'
        }
        Copy-Item -LiteralPath $downloadedMamba -Destination $mambaPath
    }

    Write-Host 'Creating or repairing the isolated app environment (Python, Pillow, GDAL, and GDAL PDF plugin).'
    Write-Host 'This can take several minutes and downloads packages from conda-forge.'
    & $mambaPath create --yes --prefix $envPath --channel conda-forge --strict-channel-priority `
        'python=3.12' pillow gdal libgdal-pdf
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check the network connection and try again.' }

    & $mambaPath run --prefix $envPath python -c 'from PIL import Image; print("Python and Pillow are ready.")'
    if ($LASTEXITCODE -ne 0) { throw 'The installed Python/Pillow environment failed its verification check.' }
    & $mambaPath run --prefix $envPath gdalinfo --version
    if ($LASTEXITCODE -ne 0) { throw 'GDAL failed its verification check.' }
    foreach ($tool in @('gdal_translate', 'gdaltransform')) {
        & $mambaPath run --prefix $envPath $tool --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "$tool failed its verification check." }
    }
    $formats = & $mambaPath run --prefix $envPath gdalinfo --formats
    if ($LASTEXITCODE -ne 0 -or -not [regex]::IsMatch(
            ($formats -join "`n"), '^\s*GTiff\b', [System.Text.RegularExpressions.RegexOptions]::Multiline)) {
        throw 'The installed GDAL environment does not expose its required GeoTIFF driver.'
    }
    Write-Host 'Setup complete. Start GeoTIFF QuickChart again.'
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extractPath -Recurse -Force -ErrorAction SilentlyContinue
}
