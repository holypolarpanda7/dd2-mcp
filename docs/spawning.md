# Spawning and despawning characters in DD2 (verified 2026-11-03, monsters)

Tool: `dd2_spawn` (templates / spawn / despawn / list), library `dd2mcp/lua/spawnlib.lua` (load with `loadfile("D:/DD2Modding/dd2-mcp/dd2mcp/lua/spawnlib.lua")()` in phase `update`).
Reconnect the MCP (`/mcp`) after updating dd2-mcp so the tool appears. The game must be focused and in a loaded save.

## How it works (monsters, category 3)
* The game's `GenerateManager` spawns from REQUESTS. `getEnemyRequests(category, true, false)` returns the saved request data of the loaded world
  (`GenerateRequestCharacterSaveData`: `_CharaIDHash`, `_Pos`/`_InitPos` in UNIVERSAL coordinates, `_ReqID`, category, layer, ...).
* Spawn = clone one request of the wanted `CharacterID`, move `_Pos`/`_InitPos` next to the player (player universal pos from `D.player_pos()` ux/uy/uz),
  give it a fresh `_ReqID` (`getPoolRequestID`), `convRequestSaveDataToContainer`, then `requestGenerate(container, flags, false, false, nil, nil)`.
  The instance appears 8-20 s later (async prefab load); the `GenerateController` shows `IsGenerated`.
* Flags (`GenerateOprionFlags`): 7 = DisableRegenerateOnUnload | RemoveContextOnUnload | NeverGenerateByDead makes it one-shot. Without them the game REGENERATES
  a destroyed instance near the player (repop logic). Passing the large bits (DisableSaveRequestToSaveData etc.) as a Lua number threw: only 7 is verified.
* Find instances: `GenerateManager._InstanceInfoManager:getInstanceInfoList(category, CharacterID)` -> `InstanceInfo` (Instance = GameObject, Chara, Container, Deleted).
* Despawn = `GenerateManager.execDestroyInstance(category, InstanceInfo, true, true, true)` (verified: the instance goes away and, with flags 7, stays away).

## Rules measured 2026-11-18 (11 game runs; spawnlib S.spawn rewritten to follow them)
* **Monster cap: `GenerateManager.getGenerateLimit(3)` = 30 loaded monster instances** (`_InstanceInfoManager:getInstanceInfoList(3)`).
  The camp-050 lab sits at exactly 30: that is why "dd2_spawn crashes the lab". Check `S.room()` first.
* A request sent at the cap (or for a character the area cannot generate) is REJECTED (`requestGenerate` -> nil) and the leftover
  crashed the game 1-60 s later in `InstanceInfoController.autoDestroyCharaInstance` (an instance whose
  `Character.GenerateInfo._Container._CommonInfo` is gone: the pooled container was recycled).
* Making room by despawning (`execDestroyInstance`) right before a spawn crashed in `GenerateReserveDistributor.ReserveQueue.update`.
* Works (forced GC every frame for 90 s, game alive): a cloned critter request in the lab (critters do not count to the cap); a cloned
  Saurian request at Gimmick_Camp_049 (21/30 monsters).
* Only characters NATIVE to the current area (the world has requests for them) generate. Not native = wolf at camp 049: from-scratch
  `getPoolGenerateInfoContainerForCharacter` -> rejected -> crash; Saurian clone turned into a wolf (id + the wolf's own
  `_InitSetTypeHash` 304624144) -> accepted, crashed during generation. Other routes for non-native characters (untested):
  the NPC wolf system (`NPCManager.registerWolfData`, `DummyDataManager.addDummyDataWolf`), domain-query tables
  (`GenerateManager._DomainQueryGenerateManager`, registry `DomainQueryGenerateTable._Elements`: 47 ids, Ch223001_Reinforce NOT
  registered; calling `requestDomainQueryGenerate` with nil callbacks threw).

## KNOWN HAZARDS in the OLD spawnlib (found 2026-11-18, FIXED in S.spawn the same day)
`S.spawn` edits the WORLD's own saved request in place and writes it with `set_field`:
* `_Pos` / `_InitPos` are `via.Position` (3 doubles, 24 bytes, at 0x30 / 0x50 of `GenerateRequestCharacterSaveData`):
  `set_field` with a struct overruns by 16 bytes (memory-safety lesson). Write them with `write_double(offset, v)`.
* `_ReqID` (0x48) is an OBJECT reference (`app.RequestID`): `set_field` releases the old one. On the world's own request that
  is a real (if unwanted) change; on a `MemberwiseClone` copy the old reference was never counted, so the release takes the
  SOURCE request's count and the game later uses a freed object. Two crashes on 2026-11-18 followed this pattern
  (`GenerateManager.isDisableGenerateRequest` read of -1; `InstanceInfoController.autoDestroyCharaInstance` garbage instance).
  Unproven cause; next test: add_ref every shared reference field of the clone before any set_field, with a forced-GC loop.
* `_CharaIDHash` must keep its matching `_InitSetTypeHash`: swapping only the id crashed in `isDisableGenerateRequest`.
* A request whose `_LoadArea` differs from the player's area (the camp-050 world's wolf request: 2 vs 1) is rejected
  (`requestGenerate` returns nil); setting `_LoadArea` alone did not help.
Details: DnD_Classes `docs/classes-plan.md` "Wild Shape test 2".

## Rules learned the hard way
* NEVER call `requestDestroy(container, ...)` twice on one container: containers are pooled; the second call crashed the game.
* Do not despawn and immediately respawn: one session crashed in `GenerateReserveDistributor.ReserveQueue.update` -> `InstanceInfoController.existsInstance` with a stale UniqueID.
  Leave a few seconds between a despawn and the next spawn (not yet proven sufficient).
* Only characters the loaded world has a saved request for can be cloned (`dd2_spawn templates`): in the camp050 scenario about 50 monster ids (goblins ch221, ch222, harpies
  ch225, golems, wolves ch220, ogre ch250 ...).
* A spawned monster is hostile and has AI: it wanders, fights the frozen/ghost camp and may be knocked down by other enemies before your test cast. Freeze the camp's existing
  enemies first (`set_UpdateSelf(false)`; `tests/spawn_lab_setup.py`) and use god mode (`dd2_god`). The harness freezes enemies, so statuses on frozen ones never update.

## Not solved
* PAWNS (category 1): the same clone route creates the main pawn instance (`ch100000_00`), but it appeared 57-68 m away (AI/saved position) and `requestPawnGenerateWarp`
  did not move it; `PawnManager.generateMainPawn(Position)` produced nothing. `dismissPawn(Character)` is the likely despawn.
* STRAY PAWNS (category 2) have 17 templates (ch110000...), untried.
* NPCs (category 4): no stored requests; `GenerateManager.getPoolGenerateInfoContainerForCharacter(GeneratorID, UniqueID, Position, Quaternion, vec3, category, layer,
  CharacterID, InitSetTypeEnum)` + `requestGenerate` could build one from scratch (untried).

## Status conditions (found while testing Shocking Grasp)
`StatusConditionCtrl.reqStatusConditionApply(enum, bool)` and accumulation lists do nothing. What works: an attack-data style DamageInfo with
`StatusConditionAtk.StatusConditionFlagOption = FORCE_APPLY (1)` and `StatusConditionFlagBit = StatusConditionDef.convertStatusConditionEnumToFlag(Electric)` (8192),
passed to `ctrl.reqStatusConditionApply(StatusConditionFlag, DamageInfo)`: Electric becomes active (~25 s) and the target drops into a hit reaction by itself.
`IsStatusConditionEnable(enum)` tells whether a monster can take it (the harpy-types cannot take Electric).
