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

## A motlist MUST be sorted by motion number
The engine finds a motion number by binary search. An unsorted list (motions appended in any order) plays SOME numbers and
silently falls back to the game's motion for others: the same file worked for #0/#33 and not for #20/#30 (2026-11-17). reetool
`motlist-build`, `anim-import` and `mot-offset` sort before writing.

## When an override applies
- A motion resolves its list when it STARTS. Adding a bank changes nothing until the next motion change (draw, sheathe, idle
  switch, move start).
- The draw motion is resolved with the DRAWN bank type (it already plays the weapon's #30 while the character still counts as
  sheathed), so a drawn-type bank overrides the draw too.

## Event clips ride on motions
Motions carry clips that drive gameplay: cancel windows (`app.PlayerTrack`), the frame a weapon moves hip -> hand
(`app.SheatheDrawTrack`), look-at, sounds (Wwise ref-table tracks), effects (VFX range tracks), and for attacks shell requests.
Replacing a motion replaces its clips: keep the base motion's clips (graft) and re-time them if the length changes.

## Changing an existing motion without Blender: `mot-offset`
Constant per-bone rotation offsets (open a fist, raise an arm) applied straight to the motion data: key' = key * offset in the
bone's local axes. Every other bone, key count, frame and clip stays byte-identical, and a whole list (118 attack motions) takes
seconds. Same numbers as the Blender route (verified to 3 decimals). `--numbers` picks motions, `--suffix` renames them so a live
layer dump shows your list answered.

Bone local axes of the DD2 human (measured with blender/axis_probe.py; X always twists along the bone):
| bone | Y | Z |
|---|---|---|
| L/R_Arm_Upper | Y- swings the arm forward, Y+ back/down | left Z+ raises outward; right Z- brings it in to the body |
| L/R_Arm_Lower (elbow) | Y- bends, Y+ straightens | sideways |
| fingers (*_IndexF/MiddleF/RingF/PinkyF_1..3) | sideways spread | Z+ opens (uncurls), Z- curls |
| thumb (*_ThumbF_1/2) | Y- out sideways | Z+ lifts the thumb out |

## Judging motions without the game
`blender/render_poses.py` (headless Blender): an FBX's animations as pose sheets (3 views x N frames, auto-framed) +
`blender/sheet.py`; `blender/axis_probe.py` renders one bone's +/- offsets on each axis (close-up option for hands);
`blender/edit_offsets.py` layers offsets in Blender, with ramps (fade in/out) and trims, for motions built from another source.
Freeze-frame a pose in game: `GameObject.set_UpdateSelf(false)` holds it; the camera can't swing then (it reads input through the
player), so turn the frozen body to face the camera instead.

## Authoring pipeline
1. `dd2_anim export` -> FBX (Content Editor's exporter, game mesh as skeleton).
2. Blender: import FBX (automatic_bone_orientation off). The body imports at 1/100 scale (1.7 cm): mind camera clip planes. Bones
   point along local X: a rotation about X is a twist. Blender 5 actions are layered (F-curves via the slot's channelbag:
   blender/bl_common.py). Set the scene to 60 fps before exporting (motions are 60 fps; Blender defaults to 24). Export FBX with
   baked animation, no leaf bones.
3. `dd2_anim import` with a base motion (or reetool `anim-import --base-map <tsv>`: per animation name its own base motion and
   output number): graft the file's rotations (+ root/Hip translation) into a copy of a game motion. A plain
   import drops prop and IK joints and writes the export body's bone lengths into every bone (game motions are rotation-only except
   root, Hip, props and IK, so retargeting fits every body size).
4. `dd2_anim build` renumbers motions into your list; inject it with a dynamic bank.
Fidelity: edits come through exactly; untouched bones drift ~0.5 deg (some fingers up to ~6 deg) through Blender's FBX resampling.
A grafted motion keeps the base's foot-IK tracks (L/R_FootIK, L/R_ToeIK: motion tracks only, not scene joints): they PLACE THE FEET.
Measured 2026-11-17: legs held constant in the graft, yet the feet slid up to 20 cm because the base (an idle variation)
shifts its weight. Fix: `reetool mot-hold <in> <out> <n> "(Foot|Toe)IK$"` (every key = the first), or a base with still feet.
Stitched clips: compose.py `"lower": "hold"` keeps hips + legs still (Spine_0 compensates).

## Idle variations (measured 2026-11-17)
- Standing idle (bank 0 #0) is interrupted by idle variations, but the natural rotation only picks **#1-#3** (seen with
  weapons sheathed). A new motion added to a DynamicMotionBank as #5 never played on its own over minutes; as #1 it came up
  about 1 time in 3. To add a "fidget", replace one of #1-#3 instead of adding a new number.
- Props that should move with a fidget (e.g. a book taken into the hand) can follow the motion from Lua: read
  `getLayer(0):get_HighestWeightMotionNode():get_MotionName()` and `get_Frame()` every frame and key the prop by frame.

## A quick cast gesture with no wind-up: another vocation's upper-body node
`HumanActionSelector.Selectors[i]:requestActionImpl(node, 1)` runs any vocation's UPPER-BODY node (layer 1) on any character, with
any weapon held: the legs keep their locomotion (running stays running). Selector indices: 12 Job09 ... 14 Job07 ... 20 Job01, 21
NoJob. Mystic Spearhand `Job07_Blade` (bank 170 #1800, 95 frames) = a one-handed forward sweep, cancelable from frame 53, a good
"cast a spell" gesture for a bow or sword user (DnD_Classes 2026-11-23). Full-body skill nodes are no use for this: each carries its
skill's gameplay action. Watch the node's other actions: `HumanExSkillEffect` (SkillID 75 here) lights the HELD weapon with the
skill's effect (a blade along a bow); a pre-hook on `HumanExSkillEffect.start` returning SKIP_ORIGINAL removes it and the node still
plays (its `requestEffect` is not on that path). Swap the motion per character with a DynamicMotionBank on the same BankID/number.
Testing: a screenshot takes ~1 s, so read animation state BEFORE taking one (state read after it is late).

## Mirroring a motion left <-> right (`blender/mirror.py`)
Headless, no pose operators: per frame read every pose bone's armature-space matrix M, set bone b to `S @ M_twin(b) @ C_b` with S the
reflection across the side axis (X for DD2 humans as imported) and `C_b = (S @ Rest_twin)^-1 @ Rest_b`, then rebuild local keys parent
first (`basis = rest_rel^-1 @ M'_parent^-1 @ M'_b`). Twins: the side marker is a prefix (`L_Arm_Upper`) OR follows an underscore
(`SHAPEKEY_L_Pec_A`): missing the infix ones mirrors them onto themselves and tears the preview mesh. Graft the result onto the game
motion (`dd2_anim import` base map) so its clips stay; check with `render_poses.py` sheets side by side (DnD_Classes 2026-11-23: the
Spearhand's left-hand cast gesture as a right-hand one for bow users).
