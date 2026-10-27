# dd2-mcp

MCP server for Dragon's Dogma 2 modding through REFramework. Started 2026-10-26 from the Bastion proof of concept.

| Tool | What it does |
| --- | --- |
| `dd2_status` | Game process, REFramework revision, bridge state, player position |
| `dd2_eval` | Run Lua in the live game; `phase="update"` for anything that changes game state |
| `dd2_types` | Methods or fields of a managed type |
| `dd2_items` / `dd2_give_item` | Find item ids by name; give items (update phase) |
| `dd2_recent_items` | Item log: newest inventory changes across player, pawns and stash (owner, subcategory, key-item, NEW) |
| `dd2_gold` | Read gold and Rift Crystals; add or deduct gold |
| `dd2_deploy` | Copy a mod project's `src/reframework/**` into the game |
| `dd2_install_bridge` | Install the eval bridge (`dd2mcp/lua/dd2mcp_bridge.lua`) into `reframework/autorun` |
| `dd2_log` | REFramework log for the current run, filtered |
| `dd2_launch` / `dd2_screenshot` | Start the game through Steam; capture the screen |
| `dd2_take_control` / `dd2_hand_back` | Bring the game to the front and unpause it for testing; return the screen to the player's terminal (the game auto-pauses) |
| `dd2_pause_state` / `dd2_key` | PauseManager state (5 = InGame pause menu); send a key press to the game |
| `dd2_saves` | Snapshot, list and restore the whole save folder (restore needs the game closed) |

Verified engine facts live in the Bastion plan doc. Key one: calls that change game state
(instantiate, getItem, addVersion) only work from `re.on_pre_application_entry("UpdateBehavior")`.
Call overloads by signature with `D.call(obj, "name(A, B)", ...)`: REFramework only resolves
signatures with ", " between types, and `obj:call()` with an unresolved one silently does nothing.

Environment: `DD2_GAME` (install path), `DD2_SAVE_BACKUPS`, `DD2_SCRATCH`.
