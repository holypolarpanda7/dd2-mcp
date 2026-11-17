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
