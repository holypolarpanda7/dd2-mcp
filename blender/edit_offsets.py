"""Headless Blender: layer constant per-bone rotation offsets onto game motions (the motion's own sway is kept) and export the
results as FBX for `dd2_anim import`.  blender --background --python edit_offsets.py -- <in.fbx> <out.fbx> <spec.json> [group]
spec: {"<group>": [ {"src": "<action name substring>", "name": "<new name>", "frames": <keep N frames, optional>,
                     "offsets": {"<bone>": [["Y", -25], ["Z", 12]]},
                     "ramp": [f0, f1]      optional: offsets fade IN from frame f0 to f1 (draw: the hand rises)
                     "ramp_out": [f0, f1]  optional: offsets fade OUT (sheathe: the hand lowers) }, ...], ...}
Only entries of [group] (or all) whose src action exists in this FBX are processed.
Axes = the bone's LOCAL axes as imported (DD2 human, measured 2026-11-17 with axis_probe.py): X twists along the bone; Arm_Upper
Y- swings forward / Y+ back-down, Z+ raises outward (left; right arm: Y+ lowers too); Arm_Lower Y- bends the elbow, Y+ straightens.
Exported at 60 fps (motions are 60 fps; Blender defaults to 24). Frame 1 in Blender = frame 0 in the game."""
import bpy, sys, math, os, json
from mathutils import Quaternion
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_common import fcurves, set_action
argv = sys.argv[sys.argv.index("--") + 1:]
src, dst, specp = argv[:3]
group = argv[3] if len(argv) > 3 else None
spec = json.load(open(specp))
entries = [e for g, es in spec.items() if group in (None, g) for e in es]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src, automatic_bone_orientation=False, ignore_leaf_bones=False)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
AX = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}
originals = list(bpy.data.actions)

def ease(k):
    k = max(0.0, min(1.0, k)); return k * k * (3 - 2 * k)

made = []
for e in entries:
    base = next((a for a in originals if e["src"] in a.name), None)
    if not base:
        continue
    act = base.copy(); act.name = e["name"]
    set_action(arm, act)
    fcs = fcurves(act, arm)
    f_first = act.frame_range[0]
    if "frames" in e:                       # keep frames [first, first + N]
        last = f_first + e["frames"]
        for fc in fcs:
            for kp in [k for k in fc.keyframe_points if k.co[0] > last + 0.01][::-1]:
                fc.keyframe_points.remove(kp)
        act.use_frame_range = True; act.frame_start = f_first; act.frame_end = last
    for bone, rots in e.get("offsets", {}).items():
        off = Quaternion()
        for axis, deg in rots:
            off = off @ Quaternion(AX[axis], math.radians(deg))
        path = f'pose.bones["{bone}"].rotation_quaternion'
        cs = sorted([fc for fc in fcs if fc.data_path == path], key=lambda f: f.array_index)
        if len(cs) != 4:
            print("NO ROT KEYS", bone); continue
        # ramps need keys over the ramp: make sure every frame of a ramp has a key
        for r in ("ramp", "ramp_out"):
            if r in e:
                for f in range(int(e[r][0]), int(e[r][1]) + 1):
                    for c in cs:
                        c.keyframe_points.insert(f + f_first, c.evaluate(f + f_first), options={"FAST"})
        n = min(len(c.keyframe_points) for c in cs)
        for i in range(n):
            f = cs[0].keyframe_points[i].co[0] - f_first
            w = 1.0
            if "ramp" in e:
                a, b = e["ramp"]; w *= ease((f - a) / max(1e-6, b - a))
            if "ramp_out" in e:
                a, b = e["ramp_out"]; w *= 1 - ease((f - a) / max(1e-6, b - a))
            q = Quaternion([c.keyframe_points[i].co[1] for c in cs])
            q2 = q @ Quaternion().slerp(off, w)
            for j, c in enumerate(cs):
                kp = c.keyframe_points[i]
                kp.co[1] = q2[j]; kp.handle_left[1] = q2[j]; kp.handle_right[1] = q2[j]
        for c in cs:
            c.update()
    made.append(act)
    print("MADE", e["name"], "from", base.name, tuple(act.frame_range))
for a in originals:
    bpy.data.actions.remove(a)
if not made:
    print("NOTHING MADE"); sys.exit(0)
set_action(arm, made[0])
bpy.context.scene.render.fps = 60
bpy.ops.export_scene.fbx(filepath=dst, use_selection=False, bake_anim=True, bake_anim_use_all_actions=True,
                         bake_anim_use_nla_strips=False, add_leaf_bones=False, armature_nodetype="NULL")
print("EXPORTED", dst, [a.name for a in made])
