# dd2-mcp

MCP server for Dragon's Dogma 2 modding through REFramework. Started 2026-10-26 from the Bastion proof of concept.

| Tool | What it does |
| --- | --- |
| `dd2_status` | Game process, REFramework revision, bridge state, player position, and the crash record if the last run crashed |
| `dd2_eval` | Run Lua in the live game; `phase="update"` for anything that changes game state |
| `dd2_types` | Methods or fields of a managed type |
| `dd2_enum` | Every named value of an enum type (e.g. `app.Character.JobEnum`) |
| `dd2_rsz` | Offline search of the type dump (alphaZomega's RE_RSZ `rszdd2.json`): every serializable type and its fields, no game needed; `action="download"` fetches it |
| `dd2_weapons` | Weapon item table: WeaponID, vocation mask (`_Job` = 1 << JobEnum), stats; filter by job, WeaponID prefix or name |
| `dd2_job` | Vocation state of the player or a pawn (job, exp per job, job bits); `set_job` changes it (jobs above 10 need `force`: they crash the game unless a mod grows the per-job tables first) |
| `dd2_pak` | Read game paks without outside tools (DD2's 0x68 format): search the file list, find, extract (.tex also as PNG) |
| `dd2_keys` | Send a whole key sequence in one call (`'esc wait:1.5 s f e'`), optional screenshot at the end |
| `dd2_crash` | Analyze the last native crash (`reframework_crash.dmp`): exception, registers, disassembly at the fault, stack return addresses mapped to managed methods; no game needed |
| `dd2_methodmap` | Managed method -> native code RVA map per game build (`research/methodmap-<build>.tsv`): `export` from the running game, `find` by regex |
| `dd2_disasm` | Disassemble DD2.exe from disk at an RVA or a method name; rip-relative globals annotated (poke live with `D.poke32`) |
| `dd2_items` / `dd2_give_item` | Find item ids by name; give items (update phase) |
| `dd2_recent_items` | Item log: newest inventory changes across player, pawns and stash (owner, subcategory, key-item, NEW) |
| `dd2_gold` | Read gold and Rift Crystals; add or deduct gold |
| `dd2_deploy` | Copy a mod project's `src/reframework/**` into the game |
| `dd2_install_bridge` | Install the eval bridge (`dd2mcp/lua/dd2mcp_bridge.lua`) into `reframework/autorun` and hot-reload it in the running game |
| `dd2_log` | REFramework log for the current run, filtered |
| `dd2_quit` | Close the game when testing ends; optionally list saves written since a time |
| `dd2_launch` / `dd2_screenshot` | Start the game through Steam; capture the screen |
| `dd2_take_control` / `dd2_hand_back` | Bring the game to the front and unpause it for testing; return the screen to the player's terminal (the game auto-pauses) |
| `dd2_pause_state` / `dd2_key` | PauseManager state (5 = InGame pause menu); send a key press to the game |
| `dd2_saves` | Snapshot, list and restore the whole save folder (restore needs the game closed); `touched` lists saves written since a time and flags any outside the test units |
| `dd2_scenario` | Test scenarios: shelve/unshelve single slots of the test character, or `load` one end to end (restart, unshelve, launch, guard, title menu, play-time + position check; ~50 s) |
| `dd2_saveguard` | Arm/disarm the bridge save guard: refuses saves outside the test character and all autosaves while armed |
| `dd2_vortex` | Vortex profiles: `status`, `profiles`, `dev` / `release` (switch to a mod project's profile from its `dd2mod.json`, create it, enable REFramework, deploy), `switch`, `play` (return to the previous profile) |

Verified engine facts live in the Bastion plan doc. Key one: calls that change game state
(instantiate, getItem, addVersion) only work from `re.on_pre_application_entry("UpdateBehavior")`.
Call overloads by signature with `D.call(obj, "name(A, B)", ...)`: REFramework only resolves
signatures with ", " between types, and `obj:call()` with an unresolved one silently does nothing.

Environment: `DD2_GAME` (install path), `DD2_SAVE_BACKUPS`, `DD2_SCRATCH`.

## Save harness

DD2 keeps 15 save slots: 3 characters x 5 types (slot = unit*5 + type; 0 Auto, 1 Last Inn Rest,
2 unmoored-world restart, 3 Clear, 4 AddAuto). Scenarios are single slot files under
`SaveBackups/scenarios/<name>/` and only load from the slot they were saved in. Steam Cloud serves the
game, so swaps happen with the game closed (Steam rescans at launch); deleting a file outside the game does
not stick. `dd2_scenario load` points the character's "latest save" at the slot and confirms
"Load from Last Saved Data"; calling `app.MainFlowManager.requestStartType` directly throws (and once
crashed the game). The test character unit defaults to 0 (`DD2_TEST_UNITS`).

## Vortex profiles

Vortex runs both BG3 and DD2, so `dd2_vortex` always names the DD2 game when it switches. It talks to the
Vortex bridge extension from bg3-data-mcp (`bg3-data-mcp-bridge`, token in its `bridge.json`) and starts
Vortex if the bridge doesn't answer. Switches are refused while DD2 runs, because Vortex purges and
redeploys `dinput8.dll`. A mod project names its profiles in `dd2mod.json`:

```json
{"vortex": {"dev_profile": "Bastion-Dev", "release_profile": "Bastion-Release", "requires": ["REFramework"]}}
```

Defaults are `<ProjectDir>-Dev`, `<ProjectDir>-Release` and `["REFramework"]`. Files copied by `dd2_deploy` are
not Vortex-managed and survive profile switches.
