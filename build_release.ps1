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
$sourceFiles = @(
    'VERSION', 'README.md', 'versions/README.md', 'requirements.txt', 'app.py',
    'GestorTracabilitat.spec', 'build_exe.bat', 'build_linux.sh', 'build_release.ps1',
    'launch_programa.bat', 'run_and_verify.bat', 'verify_programa.bat'
)
$sourceFiles += @(Get-ChildItem -LiteralPath 'src', 'tests' -Filter '*.py' -File | ForEach-Object {
    $_.FullName.Substring($PSScriptRoot.Length + 1)
})
$sourceFiles += @(Get-ChildItem -LiteralPath 'icon' -Filter '*.png' -File | ForEach-Object {
    $_.FullName.Substring($PSScriptRoot.Length + 1)
})
foreach ($file in $sourceFiles) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw "Falta el fitxer necessari: $file"
    }
}
if ($ValidateOnly) {
    Write-Output "Versio: $version"
    Write-Output "Desti: $destination"
    Write-Output "Fitxers al ZIP: $($sourceFiles.Count)"
    return
}

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Les proves han fallat; no es genera la versio.' }

$staging = Join-Path $PSScriptRoot ("versions\.staging-" + [guid]::NewGuid().ToString('N'))
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("Erola-release-" + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path "$staging\windows", "$staging\linux", $work -Force | Out-Null
    & $python -m PyInstaller --noconfirm --distpath "$staging\windows" --workpath $work GestorTracabilitat.spec
    if ($LASTEXITCODE -ne 0) { throw "Ha fallat la construccio de l'executable." }
    $builtExe = Get-ChildItem -LiteralPath "$staging\windows" -Filter '*.exe' -File
    if (@($builtExe).Count -ne 1) { throw "S'esperava exactament un executable." }
    $exeName = "erola-produccio-$version-windows.exe"
    Rename-Item -LiteralPath $builtExe.FullName -NewName $exeName

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zipName = "erola-produccio-$version-linux-fonts.zip"
    $archive = [System.IO.Compression.ZipFile]::Open("$staging\linux\$zipName", 'Create')
    try {
        foreach ($file in $sourceFiles) {
            $entryName = "erola-produccio-$version/" + $file.Replace('\', '/')
            $entry = [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive, (Join-Path $PSScriptRoot $file), $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            )
            $entry.ExternalAttributes = if ($file.EndsWith('.sh')) { 0x81ED -shl 16 } else { 0x81A4 -shl 16 }
        }
    }
    finally { $archive.Dispose() }

    $artifacts = @("windows/$exeName", "linux/$zipName")
    $checksums = foreach ($artifact in $artifacts) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $staging $artifact) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $artifact"
    }
    $checksums | Set-Content -LiteralPath "$staging\SHA256SUMS.txt" -Encoding ASCII
    [ordered]@{
        version = $version
        phase = if ($version -match '-alpha\.') { 'alpha' } elseif ($version -match '-') { 'prerelease' } else { 'stable' }
        built_at_utc = [DateTime]::UtcNow.ToString('o')
        windows = $artifacts[0]
        linux_sources = $artifacts[1]
        linux_native_verified = $false
        excludes_user_data = $true
    } | ConvertTo-Json | Set-Content -LiteralPath "$staging\release.json" -Encoding UTF8
    Move-Item -LiteralPath $staging -Destination $destination
    Write-Output "Versio arxivada a: $destination"
}
finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
}