# Window control for handing the screen between the player's terminal and DD2.
#   -Action foreground          print the current foreground window handle
#   -Action focus -Hwnd <h>     bring a window handle to the front
#   -Action focusgame           bring DD2's main window to the front, print its handle
#   -Action key -Scan <code>    send one key (hardware scan code) to whatever is in front
# The Alt tap lets SetForegroundWindow through Windows' focus-stealing rules.
param([string]$Action = "foreground", [Int64]$Hwnd = 0, [int]$Scan = 1, [int]$HoldMs = 80)
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  public static bool Front(IntPtr h) {
    if (IsIconic(h)) ShowWindow(h, 9);
    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);
    SetForegroundWindow(h);
    System.Threading.Thread.Sleep(300);
    return GetForegroundWindow() == h;
  }
  public static void Key(byte scan, int holdMs) {
    keybd_event(0, scan, 0x8, UIntPtr.Zero);
    System.Threading.Thread.Sleep(holdMs);
    keybd_event(0, scan, 0x8 | 0x2, UIntPtr.Zero);
  }
}
"@
switch ($Action) {
  "foreground" { [W]::GetForegroundWindow().ToInt64() }
  "focus" { if ([W]::Front([IntPtr]$Hwnd)) { "foreground $Hwnd" } else { "not foreground $Hwnd" } }
  "focusgame" {
    $p = Get-Process DD2 -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    if (-not $p) { "no game window"; exit 1 }
    $h = $p.MainWindowHandle
    if ([W]::Front($h)) { "foreground $($h.ToInt64())" } else { "not foreground $($h.ToInt64())" }
  }
  "key" { [W]::Key([byte]$Scan, $HoldMs); "sent scan $Scan" }
}
