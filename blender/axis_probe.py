"""Headless Blender: which local axis bends a bone which way. Renders frame 1 of an action with offset variants on ONE bone.
blender --background --python axis_probe.py -- <in.fbx> <action substring> <bone> <out.png> [deg=50]"""
import bpy, sys, math, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_common import fcurves, set_action
from mathutils import Vector, Quaternion
argv = sys.argv[sys.argv.index("--") + 1:]
src, actname, bone, outp = argv[:4]
deg = float(argv[4]) if len(argv) > 4 else 50
view = Vector(tuple(float(x) for x in argv[5].split(","))) if len(argv) > 5 else Vector((0.45, -1, 0.15))
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src, automatic_bone_orientation=False, ignore_leaf_bones=False)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
act = next(a for a in bpy.data.actions if actname in a.name)
set_action(arm, act)
sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"; sc.display.shading.light = "STUDIO"; sc.display.shading.show_cavity = True
sc.render.resolution_x, sc.render.resolution_y = 300, 400
sc.world = bpy.data.worlds.new("w"); sc.world.color = (0.12, 0.13, 0.15)
cd = bpy.data.cameras.new("c"); cd.type = "ORTHO"; cam = bpy.data.objects.new("c", cd); sc.collection.objects.link(cam); sc.camera = cam
sc.frame_set(1)
dg = bpy.context.evaluated_depsgraph_get()
lo, hi = Vector((1e9,)*3), Vector((-1e9,)*3)
for o in meshes:
    ev = o.evaluated_get(dg); me = ev.to_mesh()
    for v in list(me.vertices)[::9]:
        w = ev.matrix_world @ v.co; lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
    ev.to_mesh_clear()
C, S = (lo + hi) / 2, max(hi - lo)
focus = argv[6] if len(argv) > 6 else None
if focus:                                  # close-up on one joint (fingers)
    C = arm.matrix_world @ arm.pose.bones[focus].head
    S = S * 0.16
cd.ortho_scale = S * 1.15; cd.clip_start = S * 0.01; cd.clip_end = S * 10
cam.location = C + view.normalized() * S * 3
cam.rotation_euler = (C - cam.location).to_track_quat("-Z", "Y").to_euler()
pb = arm.pose.bones[bone]
# mute the bone's rotation curves so our value is what renders
for fc in fcurves(act, arm):
    if fc.data_path == f'pose.bones["{bone}"].rotation_quaternion': fc.mute = True
base = pb.rotation_quaternion.copy()
sc.frame_set(1)
variants = [("none", None)] + [(f"{a}{'+' if s > 0 else '-'}", (a, s)) for a in "XYZ" for s in (1, -1)]
files = []
for name, v in variants:
    q = base.copy()
    if v:
        axis = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[v[0]]
        q = base @ Quaternion(axis, math.radians(deg * v[1]))
    pb.rotation_quaternion = q
    bpy.context.view_layer.update()
    f = outp + f".{name}.png"; sc.render.filepath = f; bpy.ops.render.render(write_still=True); files.append((name, f))
print("FILES", files)
