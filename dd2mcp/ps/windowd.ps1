# Persistent window-control helper: one PowerShell process serves many requests over stdin, so the
# ~0.45 s PowerShell start + Add-Type compile is paid once. One request per line, one reply per line:
#   foreground | focus <hwnd> | focusgame | focusproc <name> | key <scan> [holdMs] | rect <name> | mouse <x> <y> | click <x> <y>
# (screen pixels). focusproc fronts a process's main window (e.g. Modmanager for Fluffy) and returns its handle.
# The Alt tap lets SetForegroundWindow through Windows' focus-stealing rules.
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
  public static void Click(int x, int y) {
    SetCursorPos(x, y); System.Threading.Thread.Sleep(40);
    mouse_event(0x2, 0, 0, 0, UIntPtr.Zero); System.Threading.Thread.Sleep(50); mouse_event(0x4, 0, 0, 0, UIntPtr.Zero);
  }
  public static bool Front(IntPtr h) {
    if (IsIconic(h)) ShowWindow(h, 9);
    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);
    SetForegroundWindow(h);
    for (int i = 0; i < 40; i++) { if (GetForegroundWindow() == h) return true; System.Threading.Thread.Sleep(10); }
    return false;
  }
  public static void Key(byte scan, int holdMs) {
    keybd_event(0, scan, 0x8, UIntPtr.Zero);
    System.Threading.Thread.Sleep(holdMs);
    keybd_event(0, scan, 0x8 | 0x2, UIntPtr.Zero);
  }
}
"@
$game = [IntPtr]::Zero
[Console]::Out.WriteLine("ready"); [Console]::Out.Flush()
while ($null -ne ($line = [Console]::In.ReadLine())) {
  $a = $line.Trim().Split(" ")
  try {
    switch ($a[0]) {
      "foreground" { $r = [W]::GetForegroundWindow().ToInt64() }
      "focus" { $h = [IntPtr][Int64]$a[1]; $r = if ([W]::Front($h)) { "foreground $($a[1])" } else { "not foreground $($a[1])" } }
      "focusgame" {
        if ($game -eq [IntPtr]::Zero -or -not (Get-Process -Id $gamePid -ErrorAction SilentlyContinue)) {
          $p = Get-Process DD2 -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
          if ($p) { $game = $p.MainWindowHandle; $gamePid = $p.Id } else { $game = [IntPtr]::Zero }
        }
        $r = if ($game -eq [IntPtr]::Zero) { "no game window" } elseif ([W]::Front($game)) { "foreground $($game.ToInt64())" } else { "not foreground $($game.ToInt64())" }
      }
      "focusproc" {
        $p = Get-Process $a[1] -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
        $r = if (-not $p) { "no window for $($a[1])" } elseif ([W]::Front($p.MainWindowHandle)) { "foreground $($p.MainWindowHandle.ToInt64())" } else { "not foreground $($p.MainWindowHandle.ToInt64())" }
      }
      "rect" {
        $p = Get-Process $a[1] -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
        if (-not $p) { $r = "no window for $($a[1])" } else { $rc = New-Object W+RECT; [void][W]::GetWindowRect($p.MainWindowHandle, [ref]$rc); $r = "$($rc.L) $($rc.T) $($rc.R) $($rc.B)" }
      }
      "mouse" { [void][W]::SetCursorPos([int]$a[1], [int]$a[2]); $r = "mouse $($a[1]) $($a[2])" }
      "click" { [W]::Click([int]$a[1], [int]$a[2]); $r = "clicked $($a[1]) $($a[2])" }
      "key" { $hold = if ($a.Length -gt 2) { [int]$a[2] } else { 60 }; [W]::Key([byte][int]$a[1], $hold); $r = "sent scan $($a[1])" }
      default { $r = "unknown $($a[0])" }
    }
  } catch { $r = "error $($_.Exception.Message)" }
  [Console]::Out.WriteLine($r); [Console]::Out.Flush()
}
