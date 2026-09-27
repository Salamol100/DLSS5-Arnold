# Sweep RenoDX settings found in renodx-dlss5.addon64 that the ini did not list:
# NRAutoMask, NRUICorrection, NRDepthMode, NRTransferStrength, NRColorStrength, NRPaperWhiteScale.
# Each variant = baseline (Style 0, intensity 0.98) + one key. Reports change vs baseline and detail.
$root = Split-Path -Parent $PSScriptRoot
$rt   = Join-Path $root "runtime"
$src  = Join-Path $root "test\screen"          # clean ACES test frame: c.pfm, z.pfm, input.png
$out  = Join-Path $root "test\hidden"
$oiio = "C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
$py   = "C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe"
New-Item -ItemType Directory -Force $out | Out-Null

$ini = Join-Path $rt "ReShade.ini"; $iniOrig = Get-Content $ini
$preset = Join-Path $rt "ReShadePreset.ini"; $presetOrig = Get-Content $preset -Raw
"`nTechniques=DLSS5_Feed@DLSS5_Feed.fx`n`nTechniqueSorting=DLSS5_Feed@DLSS5_Feed.fx`n`nPreprocessorDefinitions=DLSS5_MV_PROVIDER=0`n`n[DLSS5_Feed.fx]`nPreprocessorDefinitions=DLSS5_MV_PROVIDER=0`n" | Set-Content $preset -Encoding ascii

$variants = [ordered]@{
  "base"        = @{}
  "automask0"   = @{ NRAutoMask = "0" }
  "uicorr0"     = @{ NRUICorrection = "0" }
  "uicorr1"     = @{ NRUICorrection = "1" }
  "depthmode0"  = @{ NRDepthMode = "0" }
  "depthmode1"  = @{ NRDepthMode = "1" }
  "depthmode2"  = @{ NRDepthMode = "2" }
  "transfer0"   = @{ NRTransferStrength = "0" }
  "transfer1"   = @{ NRTransferStrength = "1" }
  "transfer2"   = @{ NRTransferStrength = "2" }
  "color0"      = @{ NRColorStrength = "0" }
  "color1"      = @{ NRColorStrength = "1" }
  "color2"      = @{ NRColorStrength = "2" }
}
try {
  foreach ($name in $variants.Keys) {
    $set = @{ NeuralUplift = "1"; NRIntensity = "0.98"; NRStyle = "0"; NRLocalStructure = "2" } + $variants[$name]
    $lines = New-Object System.Collections.Generic.List[string]
    $inSec = $false; $seen = @{}
    foreach ($l in $iniOrig) {
      if ($l -match '^\[') {
        if ($inSec) { foreach ($k in $set.Keys) { if (-not $seen[$k]) { $lines.Add("$k=$($set[$k])") } } }
        $inSec = ($l -eq '[RenoDX.DLSS5]')
      }
      $k = ($l -split '=', 2)[0]
      if ($inSec -and $set.ContainsKey($k)) { $lines.Add("$k=$($set[$k])"); $seen[$k] = $true } else { $lines.Add($l) }
    }
    $lines | Set-Content $ini -Encoding ascii
    Push-Location $rt
    & .\dlss5_host.exe --color "$src\c.pfm" --depth "$src\z.pfm" --out "$out\$name.ppm" --display-referred 1 | Out-Null
    Pop-Location
    & $oiio "$out\$name.ppm" -o "$out\$name.png"; Remove-Item "$out\$name.ppm"
  }
} finally {
  $iniOrig | Set-Content $ini -Encoding ascii
  Set-Content $preset $presetOrig -Encoding ascii -NoNewline
}
& $py (Join-Path $PSScriptRoot "detail_over_time.py") "$src\input.png" (Get-ChildItem "$out\*.png" | Sort Name | % FullName) 2>&1 | Select-String "source|png"
"--- change vs base (mean abs):"
foreach ($name in $variants.Keys) { if ($name -ne "base") { "{0,-12} {1}" -f $name, ((& $oiio "$out\base.png" "$out\$name.png" --diff 2>&1 | Select-String "Mean error").Line.Trim()) } }
