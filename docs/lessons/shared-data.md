# Shared game data: never write it, borrow a private copy

Many objects you reach from one character are the game's single shared instance for every character that uses them, for the
whole game run. They live in memory only, so a restart restores them and saves are not affected, which makes the bug look random.

## Known shared objects
- **`app.Shell.<ShellActionParameter>k__BackingField`** is the same object as `app.ShellParam._ShellActionParameterList` in the
  ShellParamData. Every shell of that param shares it: pawn and enemy mages too. Writing speed 0 for one spell froze every
  later Magick Bolt in the air, and an x2 on each cast compounded. Fix: build a private list (`sdk.create_instance` of the
  list type, `.ctor`, `Add`, keep it alive) and set only YOUR shell's field to it. A speed-0 shell never ends by itself: set
  `<RequestKill>k__BackingField`.
- **Camera settings handed over by actions** (the AimSetting a Mage spell node uses, its `_StartBlend` / `_EndBlend`
  CameraSwitchInterpParam): shared by the native skill. See camera.md for the clone-and-substitute pattern.
- **Effect holders and resources**: dropping a holder an EffectPlayer used crashed the game.
- Treat any param/setting/list read from a `*Param`, `*Setting`, `*Data` or `.user`-backed object as shared until shown otherwise.

## The pattern
1. Clone once: `obj:call("MemberwiseClone"):add_ref_permanent()` (shallow: sub-objects stay shared, so only read them), or
   build a fresh instance and copy the fields you need (mind memory-safety.md about struct fields).
2. Change the clone.
3. Substitute it only for your case: a pre-hook that swaps the argument (`args[n] = sdk.to_ptr(clone)`) while your own state
   says it is yours, or set your own instance's field to point at the clone.
4. The native behaviour stays untouched for the player's other vocations, pawns and enemies.

## Substitute where the game READS it (measured 2026-11-22)
Swap the clone in at the point the value is consumed, and measure the outcome, not the swap counter. A private scaled
`app.AttackUserData` swapped into `Shell.HitCtrl_CheckAttackCollisionHitHandler`'s argument changed nothing: the damage step reads
`HitController.DamageInfo.<AttackUserData>k__BackingField` (a per-hit object, safe to point at your clone). Set it in the
`Shell.callbackShellHit` pre-hook (args[3] = the DamageInfo). The broken version ran for six days because a counter said "swapped".

## Borrowing a native skill's pieces
A borrowed native skill (its wind-up motion, its node, its effects) brings everything the game wired to it: camera switches,
sounds, lock-on UI, enchant effects, flags. Map them all first (`dd2_skill_anatomy`, `dd2_effect_trace`, `dd2_sound_trace`,
`dd2_sound_loops` for cues that loop forever), then suppress or replace each one deliberately. A cue muted piecemeal can drop the
stop half of a start/stop pair and leave an endless loop.
