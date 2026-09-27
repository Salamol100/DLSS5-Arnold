# Is what dlss5_host saves the same as what its window actually displays?
# Starts the host on the clean test frame, screen-grabs the window's client area while DLSS 5
# is running, then compares the grab with the saved read-back.
param([int]$GrabAfterSec = 9)

$root = Split-Path -Parent $PSScriptRoot
$rt   = Join-Path $root "runtime"
$src  = Join-Path $root "test\noise\work_ref"
$out  = Join-Path $root "test\screen"
$oiio = "C:\Program Files\Autodesk\Arnold\maya2024\bin\oiiotool.exe"
New-Item -ItemType Directory -Force $out | Out-Null

$png = (Get-ChildItem $src -Filter "display.*.png")[0].FullName
$exr = (Get-ChildItem $src -Filter "arnold.*.exr")[0].FullName
& $oiio $png --ch R,G,B -d float -o "$out\c.pfm"
& $oiio $exr --ch Z -o "$out\z.pfm"
Copy-Item $png "$out\input.png"

Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
Add-Type -AssemblyName System.Drawing
[W]::SetProcessDPIAware() | Out-Null

$p = Start-Process -FilePath (Join-Path $rt "dlss5_host.exe") -WorkingDirectory $rt -PassThru -WindowStyle Normal `
     -ArgumentList "--color `"$out\c.pfm`" --depth `"$out\z.pfm`" --out `"$out\readback.ppm`" --display-referred 1 --warmup-sec 18"
Start-Sleep -Seconds 2
$p.Refresh(); $h = $p.MainWindowHandle
[W]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Seconds $GrabAfterSec

$r = New-Object W+RECT; [W]::GetClientRect($h, [ref]$r) | Out-Null
$pt = New-Object W+POINT; [W]::ClientToScreen($h, [ref]$pt) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($pt.X, $pt.Y, 0, 0, $bmp.Size)
$bmp.Save("$out\screen.png", [System.Drawing.Imaging.ImageFormat]::Png)
"grabbed client area {0}x{1} at {2},{3}" -f $bmp.Width, $bmp.Height, $pt.X, $pt.Y

$p.WaitForExit()
& $oiio "$out\readback.ppm" -o "$out\readback.png"
"--- screen vs readback";  & $oiio "$out\screen.png" "$out\readback.png" --diff 2>&1 | Select-String "Mean error|RMS"
"--- screen vs input";     & $oiio "$out\screen.png" "$out\input.png"    --diff 2>&1 | Select-String "Mean error|RMS"
"--- readback vs input";   & $oiio "$out\readback.png" "$out\input.png"  --diff 2>&1 | Select-String "Mean error|RMS"
& $oiio "$out\input.png" "$out\screen.png" "$out\readback.png" --mosaic 3x1 --resize 1920x360 -o "$out\compare.png"
