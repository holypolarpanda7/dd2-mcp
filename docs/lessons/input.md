# Controller input: the pad table, held modes, fake pad presses

Measured 2026-11-20 on the November 2026 build. `dd2_pad` prints the game's default controller table offline.

## The table
- `natives/stm/appsystem/ch/common/userdata/input/characterpadassign1.user` holds `app.CharacterPadAssign.Info` rows:
  `Action` (app.CharacterInput.Action, also the bit index in `UserInput.ButtonOnFlags`, a UInt64), `Button`
  (via.hid.GamePadButton bits), `PreInput` (an action that must be held first) and `IsIgnorePreInput`.
- Pad bits: 256 = LTrigTop = **LB**, 512 = LTrigBottom = **LT**, 1024 = RTrigTop = **RB**, 2048 = RTrigBottom = **RT**. Don't guess
  bumper/trigger from the action names. Check against the on-screen button guide, which shows the real glyphs.
- Default layout: LB = "Switch Weapon Skill" (Shift1 + Shift2), LT = Cancel + Sheathe/Draw, RB = Vocation Action (guard, aim,
  Quickspell), RT = Grab. Every button is used. Free action ids are 0 (None) and 63 (62 = SkillGuideFix).
- The live player table is `Character.<Input>k__BackingField.PadAssign`. Keyboard and mouse go through a different table
  (`keybindsettinguserdata.user`, `processKeyboardMouseAssign`).

## Held modes
- A row with `PreInput` = Shift1 (14) or Shift2 (15) suppresses the PreInput-0 rows on the same button while that shift is
  held. That's how LB + X gives Weapon Skill 1 and no light attack.
- A row whose PreInput is any other action still fires, but suppresses nothing. To make a new held mode, put **Shift2** on
  the mode button and add PreInput-15 rows on the buttons it should take over. A second row with your own action id
  (63) on the same button lets your code tell this mode apart from LB.
- A private table works. `sdk.create_instance("app.CharacterPadAssign"):add_ref_permanent()`, then `copyFrom(original)`
  (a deep copy: its own list and rows). Add `Info` rows (`add_ref_permanent` each) and call
  `CharacterInputBase.setPadAssign(app.IPadAssign)`. Never edit the shared original (see shared-data).
- To fire an action on release instead of press, move its rows onto a pad bit that no Xbox pad sets (RSide 536870912, LSide,
  CCenter) and raise that bit for one frame yourself. The game's own action then runs (tested with Sheathe/Draw).
- The HUD button guide follows the table: a button whose rows moved away shows no label.

## Fake pad presses for tests
There's no virtual pad. Write `UserInput.PadButton` / `PadButtonDown` / `PadButtonUp` in a pre-hook on `processPadAssign()`
(it exists on both UserInput and CharacterInput). Write only the player's input object (compare `get_address()`), then
log `ButtonOnFlags` per frame. `UserInput.processButton(UInt64, GamePadButton)` runs once per row (about 13 calls a frame),
so injecting there does nothing.

Sheathe/Draw and Cancel fire on press: the draw node starts about 6 update frames after the press.

## Press edges (measured 2026-11-21)
`UserInput.ButtonTriggerFlags` holds the press EDGES (worked out in `UserInput.update(Boolean, Boolean)` from `OldButtonOnFlags`). Actions
start on the edge, so a button still held after you changed something (e.g. swapped the weapon in the hand) does nothing until it
is pressed again. To replay a press: post-hook `UserInput.update` for the player's input only and OR the bit into
`ButtonTriggerFlags` and `ButtonOnFlags` for one or two frames, and only once the character can act (weapon drawn, back in
locomotion): a press on a sheathed weapon only draws it, and one during an ending attack is swallowed.
