# Animation (DD2 humans)

Tool: `dd2_anim` (bank / list / clips / jmap / export / import / build). Joint table: `docs/data/human_joints.tsv`.

## How a motion is chosen
- FSM actions and code ask for a **BankID + motion number** (e.g. idle = bank 0 #0, draw = bank 0 #30, Mage cast = bank 130 #...).
- The character's **motion bank** (`humancommon.motbank`) has several motlists per BankID, each with `BankType` and a mask. The
  character's target bank type (`app.Human.calcTargetBankType`, `Motion.get_TargetBankType`) picks one: an entry plays when
  `(target & mask) == BankType`, the most specific wins, a BankID with no match uses its default entry. Motion numbers missing from
  the chosen list fall back to the default list.
- Target bank type: drawn = `Character.WeaponJob` in the low nibble (Mage 3 -> 0x1003 with a right weapon only), sheathed =
  WeaponJob << 8; states (carry, catch: 11/12/13) replace the nibble; flags 0x10/0x20 (battle mode)/0x40/0x80/0x1000/0x2000/0x4000.
  A new weapon that sits in an existing family plays that vocation's whole stance.

## Layers
Seven TreeLayers on the player: 0 full body (full-body FSM + locomotion code), 1 upper body (mask 10), 2/3 right/left hand (masks
20/21, `app.HumanHandMotionController`: None / Common / Job / KeepJob; common = bank 0 #90 hand_idle_loop), 4 additive tired, 5/6
additive extras. Masks live in `humanjointmap.jmap` (0 full, 10 upper, 20/21 hands, 30/31 arms, 1000 legs, 1001 root+hip). A motion
switches the common hand pose off with a `HumanCommonHandTrack` clip.

## Override motions for one character (no game file changed)
Add a `via.motion.DynamicMotionBank` to that character's `via.motion.Motion`: `set_MotionList(holder)` with your motlist, overwrite
BankID / BankType / mask, `set_Priority`, then `setDynamicMotionBankCount(n+1)` + `setDynamicMotionBank(n, bank)`. The game does the
same for chairs (`Human.addMotionBankOnSitChair`). Holder: `sdk.create_resource("via.motion.MotionListResource", path):add_ref()`,
`:create_holder("via.motion.MotionListResourceHolder")`; keep resource, holder and bank alive forever (memory-safety.md).
- Your motion numbers replace the game's for that BankID + bank type; everything else falls back.
- It applies from the next motion START (a playing motion keeps its list).
- One dynamic bank per (BankID, type, mask): a drawn stance, a sheathed stance and an attack bank are three banks.
- Ship your motlist as a NEW file in your pak (`natives/stm/animation/<mod>/x.motlist.1057`): new files are not checksum-checked.

## Event clips ride on motions
Motions carry clips that drive gameplay: cancel windows (`app.PlayerTrack`), the frame a weapon moves hip -> hand
(`app.SheatheDrawTrack`), look-at, sounds (Wwise ref-table tracks), effects (VFX range tracks), and for attacks shell requests.
Replacing a motion replaces its clips: keep the base motion's clips (graft) and re-time them if the length changes.

## Authoring pipeline
1. `dd2_anim export` -> FBX (Content Editor's exporter, game mesh as skeleton).
2. Blender: import FBX (automatic_bone_orientation off). Bones point along local X: a rotation about X is a twist. Set the scene to
   60 fps before exporting (motions are 60 fps; Blender defaults to 24). Export FBX with baked animation, no leaf bones.
3. `dd2_anim import` with a base motion: graft the file's rotations (+ root/Hip translation) into a copy of a game motion. A plain
   import drops prop and IK joints and writes the export body's bone lengths into every bone (game motions are rotation-only except
   root, Hip, props and IK, so retargeting fits every body size).
4. `dd2_anim build` renumbers motions into your list; inject it with a dynamic bank.
Fidelity: edits come through exactly; untouched bones drift ~0.5 deg (some fingers up to ~6 deg) through Blender's FBX resampling.
A grafted motion keeps the base's foot-IK tracks: pick a base with a similar leg pose.
