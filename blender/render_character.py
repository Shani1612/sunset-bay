"""Studio renders of the dressed character (Cycles).

    blender -b -P blender/render_character.py -- characters/human_dressed.blend characters/renders [views] [height] [samples]

views: comma-separated names from VIEWS below; a name ending in '@walk' is
rendered in the walking test pose stored in the file.
"""
import bpy, sys, os, math, json
from mathutils import Vector, Matrix

a = sys.argv[sys.argv.index('--') + 1:]
blend, outdir = os.path.abspath(a[0]), os.path.abspath(a[1])
views = a[2].split(',') if len(a) > 2 else ['front', 'back', 'side', 'q34', 'torso', 'feet', 'q34@walk']
H = int(a[3]) if len(a) > 3 else 1000
SPP = int(a[4]) if len(a) > 4 else 64
os.makedirs(outdir, exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=blend)
sc = bpy.context.scene
arm = bpy.data.objects['m_ca01_skeleton']
z0 = arm.location.z

sc.render.engine = 'CYCLES'; sc.cycles.device = 'CPU'; sc.cycles.samples = SPP
sc.cycles.use_denoising = True; sc.render.film_transparent = False
sc.view_settings.view_transform = 'AgX' if 'AgX' in [i.identifier for i in sc.view_settings.bl_rna.properties['view_transform'].enum_items] else 'Filmic'
sc.view_settings.look = 'None'
sc.render.image_settings.file_format = 'JPEG'; sc.render.image_settings.quality = 92

# studio: soft key, fill, rim, a seamless floor
w = bpy.data.worlds.new('Studio'); sc.world = w; w.use_nodes = True
bg = w.node_tree.nodes['Background']; bg.inputs[0].default_value = (0.45, 0.47, 0.5, 1); bg.inputs[1].default_value = 0.25


def light(name, kind, energy, loc, size, color=(1, 1, 1)):
    L = bpy.data.lights.new(name, kind); L.energy = energy; L.color = color
    if kind == 'AREA':
        L.size = size
    o = bpy.data.objects.new(name, L); sc.collection.objects.link(o); o.location = loc
    o.rotation_euler = (Vector((0, 0, 1.0)) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    return o


light('Key', 'AREA', 600, (-2.2, -2.6, 3.0), 2.2, (1.0, 0.97, 0.92))
light('Fill', 'AREA', 170, (2.8, -2.0, 1.6), 3.0, (0.9, 0.95, 1.0))
light('Rim', 'AREA', 420, (0.6, 3.2, 2.6), 1.8)
light('Low', 'AREA', 50, (0.0, -2.5, 0.3), 2.0)
bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
floor = bpy.context.active_object
fm = bpy.data.materials.new('Floor'); fm.use_nodes = True
fb = fm.node_tree.nodes['Principled BSDF']; fb.inputs['Base Color'].default_value = (0.30, 0.31, 0.33, 1); fb.inputs['Roughness'].default_value = 0.7
floor.data.materials.append(fm)

cam = bpy.data.objects.new('Cam', bpy.data.cameras.new('Cam')); sc.collection.objects.link(cam); sc.camera = cam
VIEWS = {
    'front': ((0, -4.6, 1.05), (0, 0, 0.93), 50, 0.75),
    'back': ((0, 4.6, 1.05), (0, 0, 0.93), 50, 0.75),
    'side': ((4.6, 0, 1.05), (0, 0, 0.93), 50, 0.75),
    'q34': ((2.9, -3.6, 1.35), (0, 0, 0.93), 50, 0.75),
    'q34b': ((-2.9, 3.4, 1.35), (0, 0, 0.93), 50, 0.75),
    'torso': ((0.75, -1.55, 1.5), (0.03, 0, 1.25), 50, 0.8),
    'neck': ((0.2, -0.75, 1.62), (0, 0, 1.52), 50, 1.0),
    'hips': ((0.6, 1.3, 1.0), (0.0, 0.03, 0.88), 50, 1.0),
    'knees': ((0.7, -1.3, 0.6), (0.05, 0, 0.45), 50, 1.0),
    'feet': ((0.65, -0.95, 0.42), (0.08, -0.05, 0.1), 50, 1.33),
    'feetside': ((0.95, -0.25, 0.2), (0.19, -0.06, 0.08), 50, 1.33),
    'sleeve': ((0.95, -0.6, 1.45), (0.27, 0, 1.38), 50, 1.0),
    'backtorso': ((0.3, 1.6, 1.4), (0, 0, 1.2), 50, 0.8),
    'face': ((0.0, -0.75, 1.68), (0, 0, 1.64), 50, 1.0),
    'crotch': ((0.12, -0.75, 0.86), (0.0, -0.05, 0.88), 50, 1.0),
    'pit': ((0.45, -0.55, 1.22), (0.19, 0.02, 1.38), 50, 1.0),
    'neckside': ((0.45, -0.35, 1.65), (0.06, 0, 1.55), 50, 1.0),
    'shoeR': ((-0.55, -0.5, 0.22), (-0.19, -0.05, 0.06), 50, 1.33),
}


def apply_walk():
    for name, axis, deg in json.loads(arm['walk_test_pose']):
        pb = arm.pose.bones[name]; M = pb.matrix.copy(); h = M.to_translation()
        pb.matrix = Matrix.Translation(h) @ Matrix.Rotation(math.radians(deg), 4, axis) @ Matrix.Translation(-h) @ M
        bpy.context.view_layer.update()


def clear():
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()


for v in views:
    name, _, pose = v.partition('@')
    clear()
    if pose == 'walk':
        apply_walk()
    loc, tgt, lens, aspect = VIEWS[name]
    loc = Vector(loc) + Vector((0, 0, z0)); tgt = Vector(tgt) + Vector((0, 0, z0))
    sc.render.resolution_y = H; sc.render.resolution_x = int(H * aspect)
    cam.location = loc; cam.data.lens = lens
    cam.rotation_euler = (tgt - loc).to_track_quat('-Z', 'Y').to_euler()
    sc.render.filepath = os.path.join(outdir, v.replace('@', '_') + '.jpg')
    bpy.ops.render.render(write_still=True)
    print('rendered', sc.render.filepath, flush=True)
