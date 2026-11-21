# Testing DD2 mods from scripts

## Input and focus
- **DD2 pauses while its window is unfocused.** Update-phase evals still run, but actions and motion don't advance, so scripted
  requests look rejected. `dd2_take_control` first. Plain bridge evals don't focus the game.
- Without `dd2_take_control` input is ignored. `dd2_hand_back` returns the screen to the player's terminal.
- The window helper serves ONE command at a time: blocking holds (`holdl`, `holdr`, `key`) queue everything else, so "walk while
  holding" sent from another thread silently tests nothing. Use non-blocking `control.down(x)` / `control.up(x)` ('l', 'r' or a
  scan code) and sequence them yourself. A press shorter than a frame is not seen: hold 0.25 s or longer.
- Scan codes: W 17, S 31, X 45. A first click with a sheathed weapon only draws it: draw first.
- Camera aim: `control.pan(dx, dy)` (relative mouse, ~15 counts per degree).
- Parallel bridge evals alongside input hang: record inside the game (an `re.on_frame` / `re.on_application_entry` sampler that
  appends to a global) and read it afterwards.

## Stage before you measure
Take one screenshot before any test loop: daylight, target visible, camera on it, weapon drawn. Six blind runs once tested
nothing because the target was hidden in a forest. Range tests belong in open ground.

## World manipulation that crashes or misleads
- Teleporting ENEMIES (`Transform:set_Position`) crashed the game 4 times (AI, hit reaction and IK read stale data). Warp the
  player instead. A player warp lands 6-10 s late.
- `GameObject:set_UpdateSelf(false)` freezes a character but also stops its hit collision: shells reach it and never hit, and
  effects on it don't run. Unfreeze at the moment of release.
- Freezing every enemy and then spawning crashed in `GenerateReserveDistributor.ReserveQueue.update`.
- Pawns kill small enemies: use targets with high HP for numbers. Combat tests: god mode first.
- Slow motion for screenshots: `via.Application.set_GlobalSpeed(0.15)` through `sdk.call_native_func`. A screenshot takes ~1 s.
- Never press F on the "This tale has ended" screen: its first item loads the autosave.
- Tutorial popups pause the game a few seconds after a load.

## Saves
- Snapshot saves (`dd2_saves`) before anything that writes game state: autosaves overwrite the play-through.
- Test on a dedicated character unit and arm `dd2_saveguard`. Disarm it while a human plays a lab session, because the guard
  refuses the inn-rest autosave and the game hangs on a black loading screen.
- Dev builds out of the game after every session (`dd2_deploy undeploy=True`): they would run on the player's own saves.

## Recording what a player reports
`dd2_capture(seconds)` records a screen burst at ~30 fps while your script drives the input, and returns a contact sheet plus a
per-frame motion profile. Use it whenever a player describes a feel ("snaps", "stutters", "flickers") that your numbers don't
show. Delete the frames afterwards: they show the whole desktop.

## Crash hunts
- Intermittent crashes need numbers. With a ~30 % crash rate, 6 clean runs still happen 1 time in 8 by luck: use 8-10 runs per
  arm and compare against a known-good build the same way. Two wrong answers came from 4-6 run arms.
- `dd2_loadloop` repeats boot + load cycles in the background and keeps each crash's log and dump. `dd2_crash` reads a dump
  (Windows dumps: `%LOCALAPPDATA%\CrashDumps\DD2.exe.<pid>.dmp`).
- Mirror your mod's log lines into the REFramework log with a heartbeat, so a crash shows the last thing your mod did, to the ms.
- `dd2_launch(wait=N)` reports CRASHED when the crash dialog appears (it checks every DD2 window).

## Killing a test target (2026-11-21)
`HitController.setHpValue(0, false)` and `setHp(0, true, 0)` do not kill (IsDie stays false). A FROZEN character
(`set_UpdateSelf(false)`) keeps its HP whatever you set. For a death test: unfreeze it, `setHpValue(5, false)`, then land one real
hit. Unfrozen goblins wander off and climb rocks between steps: stage again right before shooting, or use an ogre (ch250).
