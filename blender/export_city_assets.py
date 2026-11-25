"""Export the Blender city (buildings, street props, surface textures) for the new game in ../city/.

    python export_city_assets.py            (with the bpy module)
    blender --background --python export_city_assets.py

Writes
  ../city/assets/city.glb       building variants and street props
  ../city/assets/tex/*.jpg      the procedural surfaces (brick, stone, plaster, concrete, asphalt, paving, curb,
                                roof) baked to seamless tiles: <name>_albedo.jpg and <name>_normal.jpg

Building variants: B<i> (full detail: window frames, mullions, sills, lintels, keystones, string courses, AC units,
balconies, shopfront, sign with lettering, awning, cornice with dentils, parapet, roof plant) and B<i>L (the same
building with plain recessed windows, for distance). In glTF axes a building stands on x = 0..W, its facade at
z = 0 facing +z and its mass behind it to z = -depth. Node extras: W, depth, top (roof height), floors, style.
Wall and roof faces carry UVs in texture tiles (see TILES); the game assigns the baked textures by material name.
Props (Lamp, Hydrant, Bin, Bollard, Manhole, Signal) stand at the origin on z = 0.
"""
import os
import sys
import math
import random
import runpy
import bpy
import bmesh
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
G = runpy.run_path(os.path.join(HERE, 'sunset_bay_city.py'), run_name='sunset_bay_city')
OUT = os.path.join(HERE, '..', 'city', 'assets')
TEX = os.path.join(OUT, 'tex')
os.makedirs(TEX, exist_ok=True)
srgb, WALLS, TRIM, M, TEX_ARGS = G['srgb'], G['WALLS'], G['TRIM'], G['M'], G['TEX_ARGS']
scene = bpy.context.scene
QUICK = '--quick' in sys.argv

# texture tiles in metres (a whole number of brick / block / slab periods, so the patterns repeat seamlessly)
TILES = {
    'brick_red': ('BrickRed', 4.0, 3.6), 'brick_brown': ('BrickBrown', 4.0, 3.6), 'stone': ('Stone', 4.8, 4.8),
    'plaster_pink': ('PlasterPink', 4.0, 4.0), 'plaster_cream': ('PlasterCream', 4.0, 4.0),
    'plaster_teal': ('PlasterTeal', 4.0, 4.0), 'concrete': ('ConcreteWall', 4.8, 4.8),
    'roof': ('RoofTar', 4.0, 4.0), 'asphalt': ('Asphalt', 6.0, 6.0), 'sidewalk': ('Sidewalk', 6.0, 6.0),
    'curb': ('Curb', 2.0, 2.0),
}


# ---------------------------------------------------------------- textures
def bake_tiles():
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 4 if QUICK else 16
    size_a, size_n = (256, 128) if QUICK else (1024, 512)
    for key, (src, tw, th) in TILES.items():
        args = TEX_ARGS[src]
        mat = G['textured'](src + '_Tile', *args, tile=(tw, th))
        bm = bmesh.new()
        vs = [bm.verts.new(p) for p in ((0, 0, 0), (tw, 0, 0), (tw, th, 0), (0, th, 0))]
        f = bm.faces.new(vs)
        uv = bm.loops.layers.uv.new('UVMap')
        for loop, (u, v) in zip(f.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop[uv].uv = (u, v)
        me = bpy.data.meshes.new('Tile_' + key)
        bm.to_mesh(me)
        bm.free()
        me.materials.append(mat)
        ob = bpy.data.objects.new('Tile_' + key, me)
        scene.collection.objects.link(ob)
        bpy.ops.object.select_all(action='DESELECT')
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        nodes = mat.node_tree.nodes
        for kind, size in (('albedo', size_a), ('normal', size_n)):
            img = bpy.data.images.new('%s_%s' % (key, kind), size, size, alpha=False)
            if kind == 'normal':
                img.colorspace_settings.name = 'Non-Color'
            tn = nodes.new('ShaderNodeTexImage')
            tn.image = img
            for n in nodes:
                n.select = False
            tn.select = True
            nodes.active = tn
            if kind == 'albedo':
                bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, margin=0, use_clear=True)
            else:
                bpy.ops.object.bake(type='NORMAL', normal_space='TANGENT', margin=0, use_clear=True)
            img.filepath_raw = os.path.join(TEX, '%s_%s.jpg' % (key, kind))
            img.file_format = 'JPEG'
            scene.render.image_settings.quality = 88
            img.save()
            nodes.remove(tn)
        bpy.data.objects.remove(ob)
        print('  baked', key, '(%g x %g m)' % (tw, th))


# ---------------------------------------------------------------- simple materials
SIMPLE = {}


def simple(name, color, rough=0.6, metal=0.0, emit=None, emit_str=0.0):
    if name in SIMPLE:
        return SIMPLE[name]
    old = bpy.data.materials.get(name)
    if old is not None:
        old.name = name + '_scene'
    m = bpy.data.materials.new(name)
    m.name = name
    m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (*color, 1)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    if emit:
        b.inputs['Emission Color'].default_value = (*emit, 1)
        b.inputs['Emission Strength'].default_value = emit_str
    SIMPLE[name] = m
    return m


def base_color(m):
    try:
        return tuple(m.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value[:3])
    except Exception:
        return (0.5, 0.5, 0.5)


STYLE_OF = {m.name: k for k, m in WALLS.items()}
TRIM_OF = {m.name: k for k, m in TRIM.items()}


def plain_name(m):
    """A scene material's name without the suffixes Blender adds to repeated names ('.001', '_scene')."""
    return m.name.split('.')[0].replace('_scene', '')


def building_material(m):
    """(simple material, uv kind or None) for a material of the scene's buildings."""
    n = plain_name(m)
    if n in STYLE_OF:
        return simple('Wall_' + STYLE_OF[n], (1, 1, 1), 0.88), 'wall'
    if n in TRIM_OF:
        return simple('Trim_' + TRIM_OF[n], base_color(m), 0.7), None
    if n == 'RoofTar':
        return simple('Roof', (1, 1, 1), 0.92), 'roof'
    if n == 'FrameWhite':
        return simple('FrameW', base_color(m), 0.35), None
    if n == 'FrameDark':
        return simple('FrameD', base_color(m), 0.35, 0.6), None
    if n == 'WindowGlass':
        return simple('Glass', base_color(m), 0.04, 0.0), None
    if n == 'WindowLit':
        return simple('GlassLit', base_color(m), 0.06, 0.0, srgb('#ffc77a'), 1.0), None
    if n == 'ShopGlass':
        return simple('ShopGlass', base_color(m), 0.05, 0.0, srgb('#ffcf90'), 1.0), None
    if '_Sign' in n:
        return simple('Sign', (0.5, 0.5, 0.5), 0.4), None
    if '_Awning' in n:
        return simple('Awning', (0.4, 0.1, 0.1), 0.9), None
    if '_Letters' in n:
        return simple('Letters', srgb('#fff4dc'), 0.3, 0.0, srgb('#fff0d0'), 1.0), None
    if n == 'ACUnit':
        return simple('RoofAC', base_color(m), 0.45, 0.5), None
    if n == 'Wood':
        return simple('RoofWood', base_color(m), 0.7), None
    if n == 'Metal':
        return simple('Metal', base_color(m), 0.35, 1.0), None
    return simple('Misc_' + n, base_color(m), 0.6), None


# ---------------------------------------------------------------- mesh helpers
def evaluated(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    return bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=False, depsgraph=dg)


def bake_object(name, pieces, matfn, local, uv_tile=None, extras=None):
    """Join (object) pieces into one mesh in the frame `local` (a world->local matrix), with simple materials and
    texture-tile UVs on the faces whose material asks for them."""
    mats, index = [], {}
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new('UVMap')
    for ob in pieces:
        for md in list(ob.modifiers):
            if md.type == 'BEVEL':                      # the game has no use for the render bevels (9x triangles)
                ob.modifiers.remove(md)
        bpy.context.view_layer.update()
        me = evaluated(ob)
        me.transform(local @ ob.matrix_world)
        remap, kinds = [], []
        for m in me.materials:
            sm, kind = matfn(m)
            if sm.name not in index:
                index[sm.name] = len(mats)
                mats.append(sm)
            remap.append(index[sm.name])
            kinds.append(kind)
        tmp = bmesh.new()
        tmp.from_mesh(me)
        for f in tmp.faces:
            f.material_index = remap[f.material_index] if remap else 0
        tmp.to_mesh(me)
        tmp.free()
        n0 = len(bm.faces)
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        uvl = bm.loops.layers.uv.get('UVMap') or bm.loops.layers.uv.new('UVMap')
        for f in bm.faces[n0:]:
            # kinds by the face's original material (looked up through the remapped index)
            kind = None
            for k, ri in zip(kinds, remap):
                if ri == f.material_index and k:
                    kind = k
            if not kind or not uv_tile:
                continue
            tw, th = uv_tile(kind)
            n = f.normal
            for loop in f.loops:
                p = loop.vert.co
                if abs(n.z) > 0.7:                          # roof / ledge tops: plan view
                    loop[uvl].uv = (p.x / tw, p.y / th)
                elif abs(n.y) >= abs(n.x):                  # facade and back: along x
                    loop[uvl].uv = (p.x / tw, p.z / th)
                else:                                       # side walls: along y
                    loop[uvl].uv = (p.y / tw, p.z / th)
    out = bpy.data.meshes.new(name)
    bm.to_mesh(out)
    bm.free()
    for m in mats:
        out.materials.append(m)
    ob = bpy.data.objects.new(name, out)
    scene.collection.objects.link(ob)
    for k, v in (extras or {}).items():
        ob[k] = v
    tris = sum(len(p.vertices) - 2 for p in out.polygons)
    print('  %-8s %6d triangles, %2d materials' % (name, tris, len(mats)))
    return ob


EXPORT = []


def drop(root):
    for o in list(bpy.data.objects):
        if o.parent == root:
            bpy.data.objects.remove(o)
    bpy.data.objects.remove(root)


# ---------------------------------------------------------------- buildings
# (facade width, floors, style): low and tall, narrow and wide, every style; widths up to 16 m fit the short
# sides of the game's blocks
VARIANTS = [
    (12.0, 5, 'brick_red'), (14.5, 7, 'plaster_cream'), (10.5, 9, 'stone'), (15.5, 6, 'brick_brown'),
    (11.0, 4, 'plaster_pink'), (13.0, 11, 'concrete'), (16.0, 8, 'plaster_teal'), (9.5, 6, 'brick_brown'),
    (13.5, 13, 'stone'), (12.5, 7, 'plaster_pink'), (10.0, 5, 'plaster_teal'), (14.0, 10, 'brick_red'),
    (11.5, 8, 'concrete'), (15.0, 5, 'stone'), (12.0, 12, 'plaster_cream'), (13.0, 6, 'brick_red'),
    (10.5, 7, 'plaster_cream'), (14.5, 9, 'brick_brown'), (9.5, 4, 'stone'), (16.0, 14, 'concrete'),
]
if QUICK:
    VARIANTS = VARIANTS[:3]


def wall_tile(kind):
    return (4.0, 4.0) if kind == 'roof' else None


def export_buildings():
    for i, (W, floors, style) in enumerate(VARIANTS):
        for detailed in (True, False):
            random.seed(1000 + i)                       # the same lit windows and sign colours in both versions
            root = G['building']('Bx%d' % i, 0.0, 1, W, floors, style, detailed=detailed)
            bpy.context.view_layer.update()
            pieces = [o for o in bpy.data.objects if o.parent == root and o.type in ('MESH', 'FONT')]
            _, tw, th = TILES[style]
            tiles = {'wall': (tw, th), 'roof': (4.0, 4.0)}
            top = 4.4 + (floors - 1) * 3.3
            ob = bake_object('B%d' % i + ('' if detailed else 'L'), pieces, building_material, root.matrix_world.inverted(),
                             uv_tile=tiles.get, extras={'W': W, 'depth': 18.0, 'top': round(top, 3), 'floors': floors,
                                                        'style': style})
            EXPORT.append(ob)
            drop(root)


# ---------------------------------------------------------------- props
def prop_material(m):
    n = plain_name(m)
    table = {'LampPole': ('Pole', 0.4, 0.8), 'Hydrant': ('Hydrant', 0.4, 0.0), 'Metal': ('Metal', 0.35, 1.0),
             'DarkPlastic': ('SignalBox', 0.5, 0.0)}
    if n == 'LampGlow':
        return simple('LampGlow', base_color(m), 0.3, 0.0, srgb('#ffcf8a'), 1.0), None
    if n in table:
        nm, r, mt = table[n]
        return simple(nm, base_color(m), r, mt), None
    if n.startswith('Lens'):
        return SIMPLE[n], None
    return simple('Misc_' + n, base_color(m), 0.6), None


def prop(name, build, mats, extras=None):
    bm = bmesh.new()
    build(bm)
    ob = G['mesh_obj'](name + '_src', bm, mats)
    out = bake_object(name, [ob], prop_material, ob.matrix_world.inverted(), extras=extras)
    bpy.data.objects.remove(ob)
    EXPORT.append(out)


def export_props():
    # the street lamp of the Blender scene (pole, arm, head and its glowing lens)
    G['street_lamp'](0.0, 0.0, 1)
    root = [o for o in bpy.data.objects if o.name.startswith('Lamp') and o.type == 'EMPTY'][-1]
    mesh = [o for o in bpy.data.objects if o.parent == root and o.type == 'MESH']
    ob = bake_object('Lamp', mesh, prop_material, root.matrix_world.inverted(), extras={'arm': 2.05, 'height': 7.15})
    EXPORT.append(ob)
    drop(root)
    ob.name = 'Lamp'                                    # the name was taken by the lamp's own root
    prop('Hydrant', lambda bm: G['hydrant'](bm, 0, 0, 0.0), [M['hydrant']])
    prop('Bin', lambda bm: G['trash_can'](bm, 0, 0, 0.0), [M['lamp_pole']])
    prop('Bollard', lambda bm: G['bollard'](bm, 0, 0, 0.0), [M['lamp_pole']])
    prop('Manhole', lambda bm: G['bm_cyl'](bm, 0, 0, 0.0, 0.004, 0.35, 24, 0), [M['metal']])
    # a traffic signal (not in the render scene): pole, a head with three lenses under visors, facing -y
    # (towards the traffic it controls, in Blender axes) at 3.0 m
    for nm, col in (('LensRed', '#d0201a'), ('LensAmber', '#e8a21c'), ('LensGreen', '#21c46a')):
        simple(nm, srgb(col), 0.2, 0.0, srgb(col), 1.0)

    def signal(bm):
        bb, bc = G['bm_box'], G['bm_cyl']
        bc(bm, 0, 0, 0, 0.3, 0.14, 16, 0)                      # base
        bc(bm, 0, 0, 0, 4.0, 0.075, 16, 0)                     # pole
        bb(bm, -0.17, -0.2, 2.75, 0.17, 0.08, 3.85, 1)         # head
        bb(bm, -0.22, 0.08, 2.7, 0.22, 0.11, 3.9, 1)           # back plate
        for k, mi in ((0, 2), (1, 3), (2, 4)):
            z = 3.6 - k * 0.32
            ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=20, radius1=0.105, radius2=0.105, depth=0.02)
            bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=__import__('mathutils').Matrix.Rotation(math.pi / 2, 3, 'X'))
            bmesh.ops.translate(bm, verts=ret['verts'], vec=(0, -0.205, z))
            for fc in {fc for v in ret['verts'] for fc in v.link_faces}:
                fc.material_index = mi
            # visor: an open hood above the lens
            bb(bm, -0.13, -0.42, z + 0.115, 0.13, -0.2, z + 0.135, 1)
            bb(bm, -0.135, -0.42, z - 0.06, -0.115, -0.2, z + 0.135, 1)
            bb(bm, 0.115, -0.42, z - 0.06, 0.135, -0.2, z + 0.135, 1)
    prop('Signal', signal, [M['lamp_pole'], M['dark'], SIMPLE['LensRed'], SIMPLE['LensAmber'], SIMPLE['LensGreen']],
         extras={'lens_y': -0.215, 'lens_z': [3.6, 3.28, 2.96]})


# ---------------------------------------------------------------- go
if '--no-tex' not in sys.argv:
    bake_tiles()
export_buildings()
export_props()
bpy.ops.object.select_all(action='DESELECT')
for ob in EXPORT:
    ob.select_set(True)
path = os.path.abspath(os.path.join(OUT, 'city.glb'))
# Draco-compressed (about a tenth of the size); the game decodes it with three.js's DRACOLoader
bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_selection=True, export_extras=True,
                          export_yup=True, export_apply=True, export_vertex_color='NONE', export_materials='EXPORT',
                          export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=7,
                          export_draco_position_quantization=14, export_draco_normal_quantization=10,
                          export_draco_texcoord_quantization=12)
print('wrote', path, os.path.getsize(path) // 1024, 'KB')
