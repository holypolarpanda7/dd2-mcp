# Headless Blender scripts for DD2 motions

Run with `blender --background --factory-startup --python <script> -- <args>` (Blender 5.2 tested). Inputs are FBX files from
`dd2_anim export`. Lessons: `docs/lessons/animation.md`.

| script | does |
|---|---|
| `render_poses.py <in.fbx> <out_dir> [frames]` | pose stills of every animation (front / side / three-quarter, auto-framed); then `python sheet.py <out_dir>` |
| `axis_probe.py <in.fbx> <action> <bone> <out> [deg] [view x,y,z] [focus bone]` | one bone's +/- rotation on each local axis, rendered (find which axis bends it which way); then `strip.py` |
| `edit_offsets.py <in.fbx> <out.fbx> <spec.json> [group]` | layer constant per-bone offsets onto motions (sway kept), copies, ramps, trims; exports FBX at 60 fps |
| `bl_common.py` | Blender 5 layered-action helpers |
For constant offsets on existing motions prefer reetool `mot-offset` (exact, no Blender).
