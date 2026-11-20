# DD2 modding lessons (REFramework Lua)

Measured findings from building large REFramework mods for Dragon's Dogma 2. Each one cost a crash, a wasted test session or a
wrong conclusion before it was understood. Read the topic before you write that kind of code. `dd2_guide()` lists these files
and `dd2_guide("<topic>")` returns one. Game build: November 2026. Addresses (RVAs) change with game updates, so find them again
with `dd2_methodmap` / `dd2_disasm`.

| Topic | Read before you ... |
| --- | --- |
| [memory-safety](memory-safety.md) | write any field of a game object, or hand a Lua-made object to the game |
| [shared-data](shared-data.md) | change a value you read from a game object (params, settings, lists) |
| [camera](camera.md) | move the player by hand, play an action that switches the camera, or judge "camera feel" |
| [testing](testing.md) | drive the game from scripts, measure anything in game, or hunt an intermittent crash |
| [game-text](game-text.md) | add or change in-game text (.msg) or ship any base-game file in a pak |
| [id-tables](id-tables.md) | add ids past the game's own count (vocations, skills, augments, weapon kinds) |
| [effects-and-skills](effects-and-skills.md) | play a skill effect, borrow a skill's motion or sounds, or use a summon body |
| [animation](animation.md) | change a stance or idle, replace any motion for one character, or author motions in Blender |
| [input](input.md) | read or change controller input: the pad table, held modes (shifts), fake pad presses for tests |

Spawning monsters: [../spawning.md](../spawning.md). Custom Wwise banks: [../wwise-custom-banks.md](../wwise-custom-banks.md).

## The method that worked
1. **Map before changing.** Find the game's own path first: data files (`dd2_table`, `dd2_where`), the FSM node -> motion ->
   clip tracks chain (`dd2_skill_anatomy`), the method that really runs (`dd2_trace`, `dd2_disasm`). Then change one thing.
2. **Measure, don't guess.** Log per frame from inside the game (an `re.on_frame` sampler that you read back afterwards), and
   compare a working case against the broken one, for example standing against moving. If a report doesn't match what you
   measured, record what the user actually sees (`dd2_capture`) before you start another theory.
3. **Work with the game's design.** Use catalog entries, the action selector and message data, not emulated input or rewritten
   text. Native paths survive saves, pawns, menus and other mods. Emulation breaks somewhere you won't test.
4. **Never write shared game objects.** Clone or build a private copy and point only your own case at it (see shared-data).
