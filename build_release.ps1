param(
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$version = (Get-Content -LiteralPath 'VERSION' -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+-(alpha|beta|rc)\.[1-9]\d*$|^\d+\.\d+\.\d+$') {
    throw 'El fitxer VERSION no conte una versio valida, per exemple 0.1.0-alpha.1.'
}
$destination = Join-Path $PSScriptRoot "versions\$version"
if (Test-Path -LiteralPath $destination) {
    throw "La versio $version ja existeix. No se sobreescriura; actualitza VERSION."
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "No s'ha trobat .venv\Scripts\python.exe."
}
if ($ValidateOnly) {
    Write-Output "Versio: $version"
    Write-Output "Desti: $destination"
    return
}

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Les proves han fallat; no es genera la versio.' }

$staging = Join-Path $PSScriptRoot ("versions\.staging-" + [guid]::NewGuid().ToString('N'))
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("Erola-release-" + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path "$staging\windows", $work -Force | Out-Null
    & $python -m PyInstaller --noconfirm --distpath "$staging\windows" --workpath $work GestorTracabilitat.spec
    if ($LASTEXITCODE -ne 0) { throw "Ha fallat la construccio de l'executable." }
    $builtExe = Get-ChildItem -LiteralPath "$staging\windows" -Filter '*.exe' -File
    if (@($builtExe).Count -ne 1) { throw "S'esperava exactament un executable." }
    $exeName = "erola-produccio-$version-windows.exe"
    Rename-Item -LiteralPath $builtExe.FullName -NewName $exeName

    $artifacts = @("windows/$exeName")
    $checksums = foreach ($artifact in $artifacts) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $staging $artifact) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $artifact"
    }
    $checksums | Set-Content -LiteralPath "$staging\SHA256SUMS.txt" -Encoding ASCII
    [ordered]@{
        version = $version
        phase = if ($version -match '-alpha\.') { 'alpha' } elseif ($version -match '-') { 'prerelease' } else { 'stable' }
        built_at_utc = [DateTime]::UtcNow.ToString('o')
        platform = 'windows'
        windows = $artifacts[0]
        excludes_user_data = $true
    } | ConvertTo-Json | Set-Content -LiteralPath "$staging\release.json" -Encoding UTF8
    Move-Item -LiteralPath $staging -Destination $destination
    Write-Output "Versio arxivada a: $destination"
}
finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}