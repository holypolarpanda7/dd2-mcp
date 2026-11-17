# Screen burst at ~30 fps: frames kept in memory, saved at the end (saving while capturing halves the rate).
# powershell.exe -File burst_capture.ps1 -secs 7 -out <windows dir> [-width 640]
param([double]$secs = 7, [string]$out, [int]$width = 640)
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$full = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($full)
$w = $width; $h = [int]($b.Height * $width / $b.Width)
$frames = New-Object System.Collections.ArrayList; $ts = New-Object System.Collections.ArrayList
"ready"
[Console]::Out.Flush()
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt $secs) {
  $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
  $s = New-Object System.Drawing.Bitmap $w, $h; $sg = [System.Drawing.Graphics]::FromImage($s); $sg.DrawImage($full, 0, 0, $w, $h); $sg.Dispose()
  [void]$frames.Add($s); [void]$ts.Add($sw.Elapsed.TotalMilliseconds)
}
for ($i = 0; $i -lt $frames.Count; $i++) { $frames[$i].Save((Join-Path $out ("f{0:D4}_{1:D6}.png" -f $i, [int]$ts[$i]))) }
"frames $($frames.Count)"
