"""Headless Blender: render an FBX's animations as pose sheets (front + side, N frames each) so motions can be judged without
the game. blender --background --factory-startup --python render_poses.py -- <in.fbx> <out_dir> [frames_per_action=6]
Writes <out_dir>/<action>.png (one row per view). Workbench, solid grey, fixed cameras on the body."""
import bpy, sys, os, math
from mathutils import Vector
argv = sys.argv[sys.argv.index("--") + 1:]
src, outdir = argv[0], argv[1]
nf = int(argv[2]) if len(argv) > 2 else 6
os.makedirs(outdir, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src, automatic_bone_orientation=False, ignore_leaf_bones=False)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "STUDIO"
sc.display.shading.color_type = "SINGLE"
sc.display.shading.single_color = (0.75, 0.72, 0.68)
sc.display.shading.show_cavity = True
sc.render.resolution_x, sc.render.resolution_y = 360, 480
sc.render.film_transparent = False
sc.world = bpy.data.worlds.new("w"); sc.world.color = (0.12, 0.13, 0.15)
cam_data = bpy.data.cameras.new("cam"); cam_data.type = "ORTHO"; cam_data.ortho_scale = 2.3
cam = bpy.data.objects.new("cam", cam_data); sc.collection.objects.link(cam); sc.camera = cam
# FBX from the game: Y-up converted to Z-up by the importer; the body stands ~1.7 m tall at the origin, facing -Y or +Y.
views = {"front": (0, -4, 1.0), "side": (4, 0, 1.0), "three_q": (2.8, -2.8, 1.2)}
def bounds():
    dg = bpy.context.evaluated_depsgraph_get()
    lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        ev = o.evaluated_get(dg); me = ev.to_mesh()
        for v in list(me.vertices)[::7]:
            w = ev.matrix_world @ v.co
            lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
        ev.to_mesh_clear()
    return lo, hi
CENTER, SIZE = Vector((0, 0, 0.95)), 2.0
def aim(loc):
    d0 = Vector(loc).normalized()
    cam.location = CENTER + d0 * SIZE * 3
    cam.rotation_euler = (CENTER - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.ortho_scale = SIZE * 1.15
    cam_data.clip_end = SIZE * 10
    cam_data.clip_start = SIZE * 0.01
for act in bpy.data.actions:
    global_center = None
    if not arm.animation_data:
        arm.animation_data_create()
    arm.animation_data.action = act
    f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
    frames = sorted(set(int(f0 + (f1 - f0) * i / max(1, nf - 1)) for i in range(nf)))
    sc.frame_set(f0)
    lo, hi = bounds()
    CENTER = (lo + hi) / 2; SIZE = max(hi - lo)
    print("BOUNDS", act.name, tuple(round(x, 3) for x in lo), tuple(round(x, 3) for x in hi))
    name = act.name.replace("|", "_").replace("Armature_", "").strip("_")
    for vname, loc in views.items():
        aim(loc)
        for i, f in enumerate(frames):
            sc.frame_set(f)
            sc.render.filepath = os.path.join(outdir, f"_{name}_{vname}_{i:02d}.png")
            bpy.ops.render.render(write_still=True)
    print("RENDERED", name, frames)
