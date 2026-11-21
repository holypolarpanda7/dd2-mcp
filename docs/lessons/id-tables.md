# Ids past the game's own count (vocations, skills, augments, weapon kinds)

The game sizes many tables to its own id count. A new id needs every table that is indexed by it grown first, or it crashes or
silently falls back. `dd2_table_scan` lists live arrays and lists of given lengths (walked from every singleton and the player),
which finds the tables still sized to the original count.

## Counts that live in globals
- A managed static that the compiled code reads is the same memory as a rip-relative global. Find the global AT RUN TIME from
  the method's own code (the first `8B /r` rip-relative load in, e.g., `JobContext.get_JobIndex`), never from a fixed offset.
  A game update moved get_JobIndex and its global. The fixed-offset patch then read 0, its guard refused, and job 11 silently
  shared the Warfarer's slots (exp, rank) for a week.
- `HumanSkillContext.MaxNormalSkillCount` (39) sizes the learned core-skill list (`EnableNormalSkills`). It is read in the ctor
  and `prepareEnableNormalSkillList`. Raise it at boot, then refill contexts that were built earlier. Measured: a new id is
  learned, saved and still learned after a reload.
- Grow companion tables too, for example GuiManager's per-id job table for custom skills. Without it the boot crashed with "Heap
  allocation failed".

## Engine limits found the hard way
- **At most 7 core-skill rows (`GuiManager.addNormalSkillDefine`) per vocation.** 8+ rows crashed ~1 load in 8 within 1-12 ms of
  the add, in random engine code (heap damage). 7 rows: 0/24 loads. The overflowing engine code was never found. Natives have 3-5
  rows. Register rows once a save is loaded, not at the title (title-time 8 rows: 1 boot in 3 crashed).
- `GuiManager.getNormalSkillDefine(id, job)` turns the id into its enum NAME and returns null for names under 5 characters, so
  ids without an enum name show blank rows. Hook it to return your row for your ids.

## Weapon kinds
Spells and stats scale with a weapon only through the game's weapon-kind table (the WeaponIDUtil family tree,
`dd2_weapon_kind`). A new weapon line needs its own family node with a base family. 113 code sites check families.

## Ids and saves
Ids you ship are fixed forever: saves store them. Give your mod a block (e.g. 16 per custom vocation) and keep the rest free.

## More than one custom vocation (measured 2026-11-21, jobs 11 + 12)
- Jobs must be consecutive from 11; the job count global covers the highest. Clone every per-job row from a HOST vocation
  (stats, lock-on, stamina, guild JobInformationParam, per-character EquipedSkills): the new vocation then works on the host's
  machinery with no further code (job 12 from the Archer: guild row, change vocation, skills from the slots).
- The HELD weapon's vocation picks basic attacks and skill nodes by itself (`Character.getWeaponJob`): give your vocation's bit
  to bows/daggers/swords and it fights with the Archer/Thief/Fighter sets. A skill of another vocation's weapon simply does
  nothing while the wrong weapon is held.
- Equip masks: `ItemWeaponParam._Job` is a UInt16 bit mask (1 << job), so only jobs 12-15 fit; `_EquipCategory` 1 = shields.
- Warfarer features are closed by INLINE `CurrentJob == 10` compares in compiled code (weapon-list menus,
  `Job10WeaponManager.isJob10`): hooking one method does not hand them to another job.
- A save made as a custom vocation, loaded WITHOUT the mod: it loaded (job 12 test); some per-job values read wrong
  ("Overencumbered"); the guild's Change Vocation still works, so players can recover. Tell them to change first anyway.
