# Skill effects, motions and summon bodies

## Play skill effects through the caster's effect manager
Skill .efx are built to run through a character's manager. A bare `EffectPlayer` at a point mostly shows nothing or a fragment,
and dropping a holder that an EffectPlayer used crashed the game. Use the caster's
`via.effect.script.ObjectEffectManager2`:
- `requestEffect(EffectID, via.vec3, via.Quaternion, via.GameObject, System.String, WwiseTriggerInfo)` at a point, or the
  `(EffectID, via.GameObject, Int32, WwiseTriggerInfo)` overload to attach it to a body (it follows the body).
- `EffectID`: `DataContainerIndex` (position in the manager's `ExternalDataContainers`), `ContainerID` (the section = a shell
  EffectParam ProviderID), `ElementID`. An unregistered element throws. `getStandardData(section, index, false).Elements` lists ids.
- It returns a `CreatedEffectContainer`. `add_ref` it and `killAll()` it yourself, or loops stay. `getAllPlayers()` ->
  `EffectPlayer:set_Color(via.Color)` (`rgba = 0xAABBGGRR`); `getExternParameter("TerminalPosX|Y|Z"):set_Value(f)` sets end points
  relative to the start.
- Never play section 14:11 (ShadowClone) on a body: its material stays on the character until the game restarts.
- `dd2_effect_trace` shows which effects are requested on whose manager while you act. It misses range-track effects.

## How a skill fits together (`dd2_skill_anatomy`)
Motion FSM node (`ch000000_upperbody.motfsm2` layer 1 / `ch000000.motfsm2` layer 0) -> its actions -> motion bank + motion number
(`humancommon.motbank`: 130 = magick skills) -> the motion's embedded clips: `app.PlayerTrack` (Move / Skill / Cancel windows),
`VFXRangeTrack` (effects), Wwise ref-table trigger tracks (RequestId -> TriggerId through `hm_requestreferencetablelistdata`),
camera shake, rotation, speed. Effects and sounds an ACTION requests itself sit in the action's fields, not in the motion. Some
actions also switch the camera (see camera.md). Map all of it before you change a look or a sound.

## Action requests
`Human.get_HumanActionSelector().Selectors[i]:requestActionImpl("<node>", layer)` requests a native node (selector 18 = Mage).
Read the current node with `ActionManager.Fsm:getCurrentNodeName(layer)`. A press during a cast node is ignored. A charge or wind-up
roots the character, so movement during it must be done by hand (and warps cut the camera, see camera.md).

## Speed and status
- Speed buff on any Human: `Human.ActionSpeedEnchanter:startEnchant(rate, sec, 0)` / `endEnchant(0)`.
- `DamageInfo.IsMagicAttack` marks magick hits; `<AttackOwnerObject>k__BackingField` is the attacker. `DamageInfo` fields never
  move anyone: a knock-back flight is a ragdoll velocity (`setLinearVelocityAll`) once the blown reaction has started.

## The Trickster decoy as a summon body
`Human.get_Job09ActionCtrl():get_DecoyHandler()`: `prepareDecoyAndAstralBody`, `create`, wait for the instance's
`get_CompletedClone()`, then `start(...)`. It is a real character that foes target on their own (it has its own hate entry). It
drains ~300 HP/s by itself and sets its HP in its own `lateUpdate`: hold HP from `LateUpdateBehavior`. Hiding it takes every
child piece's draw switched off each frame (about 23 pieces: meshes, gear, a ghost effect). `DrawEnable` on the main meshes
alone still shows a ghost figure. One decoy per character.

## Shells
- Live shells: `ShellManager.getShellList()`; per shell `get_Owner()`, `get_ShellParamId()`, `get_RequestId()`; end one with
  `requestDestroyShell(requestId)`.
- Shell params are shared: see shared-data.md.

## Glowing eyes on any human: the game's dragonsplague eye glow (2026-11-23)
`PawnManagerBehavior.PawnEyeGlowData._EyeGlowParamList` names it: the child GameObject `head`, materials `l_eye_mat` / `r_eye_mat`
(murmur3 UTF-16 hashes 22864041 / 352092899), variable `Emissive_Intensity` (177287546; also `app.EyeGlowController.EmmisiveIntensityHash`).
Default colour `Emissive_Color1/2` = red, intensity 0. Set colour + intensity on the head's `via.render.Mesh` (`setMaterialFloat4` /
`setMaterialFloat`, indices by name) and any colour glows; write it every frame while wanted and put the read values back after.
It shows through a mask's eye-holes. Judge it somewhere dim too: in bright daylight it read as pale "lenses" (we first thought it did
nothing). `app.EyeGlowController.tryInitialize` alone returns false (it needs EyeGlowData via `init`).
