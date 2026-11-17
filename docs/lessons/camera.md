# Camera (DD2)

## Structure
- `app.CameraManager._MainCameraControllers[0]` = `app.MainCameraController`. Fields: `_CurrentCameraController` /
  `_PrevCameraController` (`app.UsuallyCameraController`, `app.AimCameraController`, ...), `_SwitchInterpParam`,
  `_SwitchInterpElapsedTime` (frames, ~60 per second), `get_SwitchInterpRate()` 0..1, `get_IsSwitch()`, `_PlayStatus`
  (2 while switching).
- `app.CameraDefine.ControlType`: Player 0, Usually 1, Combat 2, Narrow 3, AirShip 4, Notice 5, Climbing 6, **Aim 7**, Ballista 8,
  Follow 9, CinematicAction 10, Watch 11, InsertView 12, Talk 13, Requirement 14, Shot 15, CutScene 16, ...
- A camera Transform's `AxisZ` points BACK at the viewer (the camera looks down -Z). A character's AxisZ is its forward.
  Aim along `-cam:get_AxisZ()`.

## Switches and blends
- `MainCameraController.switchCamera(ControlType, CameraSwitchInterpParam, PostEffectSetting, ToPlayerCameraOption)` only queues
  the request (next type, next blend). The update applies it.
- `CameraSwitchInterpParam`: `_SwitchInterpMode`, `_SwitchInterpFrame` (frames at ~60/s), `_SwitchInterpCurve` (`_PresetCurveType`
  = `app.Ease`: Linear 0, EaseIn 1, EaseOut 2, Smooth 3, InSine 4 ... InOutBounce 33).
- Mage spell wind-up nodes (Job03_Prepare*) switch to Aim with an AimSetting that is NOT in
  `AimCameraController._AimCameraSetting._AimSettings`: read `_AimSetting` on the live controller. Its StartBlend is 40 frames and
  its EndBlend 90 frames, both Smooth. Players feel the 40-frame dolly-in (1.5 m in ~0.45 s) as a "snap".
- **Slower blend for your own case only:** pre-hook `switchCamera`. When the type is 7 and the switch is yours, replace `args[4]`
  with a kept clone of the game's param (`MemberwiseClone():add_ref_permanent()`) whose `_SwitchInterpFrame` you raise (72
  worked well). The game's setting and the native skill stay untouched.
- `CameraSwitchInterpPresetSettings` presets (HelloStart, TalkStart, ...) are for conversations only.

## Warps make the camera CUT
`Character.warp(vec3, CharacterWarpOption)` calls `MainCameraController.onWarpTarget()` synchronously. That sets IsWarpFirstUpdate
(+0x262), and the next camera update cuts with no blend: the blend timer stays at 0 and the rate jumps to 0. If you move a
rooted character by hand with a warp every frame (actions such as the Mage charge and spell wind-ups root the character), every
camera switch during the move becomes an instant jump, while standing still blends fine.
Fix: set a flag around your own small warp steps and skip `onWarpTarget` in a pre-hook while it is set
(`return sdk.PreHookResult.SKIP_ORIGINAL`). Real teleports keep cutting.

## Measuring camera feel
- Per frame from inside the game: camera position, player position, `_SwitchInterpElapsedTime`, controller type and the action
  node. A cut shows as one big step of (camera - player). A blend shows as many small steps. Compare standing against moving.
- What the player sees: `dd2_capture` (screen burst at ~30 fps). Xbox Game Bar records the FOCUSED window: twice it recorded
  the terminal instead of the game. Plot the frame-to-frame difference of a background strip (top rows + one side, not the
  character): a camera move is a bell over many frames, a snap is a single spike.
