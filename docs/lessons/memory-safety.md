# Memory safety in REFramework Lua (DD2)

The game crashes "randomly" minutes after the bad write, in unrelated engine code (GC sweep calling a garbage vtable, release
of a freed object, null free-list pop). Every one of these came from the mod's own writes. All measured in game.

## set_field with a struct value overruns by 16 bytes
REFramework copies the value type's FULL size, object header included (`get_size()` = data + 0x10). A Guid (16 bytes) writes 32
and a `Nullable<vec3>` (0x20) writes 0x30. The extra bytes are not zeros: a ValueType read with `get_field` was read with the
same overrun, so the write plants the SOURCE object's next 16 bytes over your object's next fields.
- Safe with set_field: Lua `Vector3f` / `Quaternion`, a `via.vec3` from get_field, numbers, enums (get_field returns a Lua
  number), booleans, object references.
- Struct fields (Guid, Nullable, user structs): copy exactly `get_valuetype_size()` bytes with raw reads and writes
  (`read_qword`/`write_qword` at `get_address() + field offset`).
- `get_element` on an enum ARRAY returns a boxed object. Writing it into another array stores pointer bits.

## Struct returns and struct array elements
Reading fields off a value type that a method RETURNED (`EquipData:call("getCurrentArisenWeapon")` -> `ArisenWeaponData`,
then `:get_field("Main")`), after walking the same struct array with `get_elements()`, ended in "Heap allocation failed"
within seconds (2026-11-20). Treat struct returns and struct array elements like the set_field overrun: read raw
(`read_qword` at the field offset of the array's data) or use a method that returns a plain value or an object.

## set_field on a reference field releases the old value
It counts the new reference and releases the old one. If the old value got there by a raw copy (the overrun above, or your own
byte copy), the game never took a reference, and the release takes one from someone else. In one real case a shared engine string
("_root") lost one count per cast and wrapped to 4294967295 within 10 casts. Copy objects field by field only with code that
treats references as references.

## Objects you hand to the game
- **Never pass a freshly created object to a call the game may keep** (warps, queued requests, pending options). Lua frees it
  at its next collection and the game crashes later. Create ONE, `add_ref()`, keep it in a global, and reuse it (for example
  one `app.CharacterWarpOption` for every `Character.warp`).
- **Exception: `via.gui.Text.set_Message` takes a plain Lua string.** A kept, reused managed string crashed the game three times
  within minutes. The game seems to take over the string it is given. Fresh strings ran for ~50k writes without a crash.
- Find out whether a call takes, keeps or consumes a reference with `dd2_refcount` (count before, right after, after a wait).
  Measured: `ShellManager.requestCreateShell` HOLDS its own reference to the create info; `Character.warp` and
  `HitController.setDamageInfo` do not keep theirs. `obj:add_ref()` twice still counts as 1 (REFramework counts a wrapper once).
  A fresh `create_instance` reads 0xFFFFFFFF until its first add_ref.
- Objects that must live as long as the game: `add_ref_permanent()`.

## Calls
- A Lua INTEGER passed to a `System.Single` parameter is silently not applied (`setHpValue(400, false)`): pass `400 + 0.0`.
- Overloads: name the signature with ", " between types (`"warp(via.vec3, app.CharacterWarpOption)"`). An unresolved
  signature through `obj:call()` silently does nothing.
- Anything that changes game state (instantiate, give item, requests) only works from `UpdateBehavior`
  (`dd2_eval phase="update"`). The same call from the frame phase silently does nothing.

## Hooks
- Install each native hook ONCE per game run and swap the Lua body on reload. `sdk.hook` cannot be removed, so re-running the
  install on every script reload stacks hooks. Keep a registry keyed by (tag, method signature) and store the bodies in it.
- Pre-hook args: `args[2]` = this, `args[3..]` = parameters. Read ints with `sdk.to_int64(a) & 0xFFFFFFFF` (enums arrive as
  pointer-sized values), floats with `sdk.to_float`. Replace an object argument with `args[n] = sdk.to_ptr(obj)`.

## Forcing an intermittent crash
A crash that takes minutes of play can be forced in seconds: run
`re.on_application_entry("UpdateBehavior", function() collectgarbage("collect") end)` during a real input loop. Always do a
control run with the feature off. See testing.md "Crash hunts" for the numbers you need.

## Reference fields and clones DO take references (measured 2026-11-21, dd2_refcount with baseline)
- `obj:set_field("RefField", managed_obj)` on a reference-type field: NET +1 (the field owns a reference). Copying a native row's
  object (a via.Prefab `_Movie`) into your own rows is NOT a shared-reference bug.
- `MemberwiseClone()` of a row: its reference fields gain a reference each (net +1 per clone).
- Reading refcounts: your own Lua handle counts +1 until it is collected (~1 s). Measure against a no-handle baseline (dd2_refcount
  does it since 2026-11-21) or a first reading will look like "the call added a reference" when it did not, or the reverse.
