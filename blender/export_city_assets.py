"""Export the Blender city (buildings, street props, trees, surface textures) for the game in ../city/.

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
TILES = {   # the white city: light plaster and stone, roof, light asphalt and paving, curb, beach sand
    'plaster_white': ('PlasterWhite', 4.0, 4.0), 'plaster_offwhite': ('PlasterOffwhite', 4.0, 4.0),
    'stone_light': ('StoneLight', 4.8, 4.8), 'roof': ('RoofTar', 4.0, 4.0),
    'asphalt_light': ('AsphaltLight', 6.0, 6.0), 'sidewalk_light': ('SidewalkLight', 6.0, 6.0),
    'curb': ('Curb', 2.0, 2.0), 'sand': ('Sand', 6.0, 6.0),
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
    return keep_named(m, n), None


def keep_named(m, n):
    """Any other material, simplified under its own name (the game looks materials up by name)."""
    try:
        b = m.node_tree.nodes['Principled BSDF']
        r, mt = b.inputs['Roughness'].default_value, b.inputs['Metallic'].default_value
    except Exception:
        r, mt = 0.6, 0.0
    return simple(n, base_color(m), r, mt)


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
# the white city: Bauhaus blocks (kind, style, shops or columns), white classic buildings, and glass towers
VARIANTS = [
    ('bauhaus', 14.0, 5, 'balcony', 'plaster_white', True), ('bauhaus', 16.0, 4, 'ribbon', 'plaster_offwhite', False),
    ('bauhaus', 15.0, 6, 'mixed', 'plaster_white', True), ('bauhaus', 12.0, 4, 'balcony', 'stone_light', True),
    ('bauhaus', 18.0, 5, 'ribbon', 'plaster_white', True), ('bauhaus', 13.0, 5, 'mixed', 'plaster_offwhite', False),
    ('bauhaus', 16.0, 6, 'balcony', 'plaster_white', True), ('bauhaus', 11.0, 4, 'ribbon', 'plaster_white', True),
    ('bauhaus', 20.0, 7, 'mixed', 'plaster_white', True), ('bauhaus', 14.0, 3, 'balcony', 'plaster_offwhite', True),
    ('bauhaus', 15.5, 5, 'ribbon', 'stone_light', True), ('bauhaus', 12.5, 6, 'balcony', 'plaster_white', False),
    ('classic', 14.0, 5, 'plaster_white'), ('classic', 12.0, 4, 'stone_light'), ('classic', 15.0, 6, 'plaster_offwhite'),
    ('classic', 11.0, 4, 'plaster_white'),
    ('tower', 40.0, 22, False), ('tower', 40.0, 30, True), ('tower', 40.0, 26, False), ('tower', 40.0, 36, True),
]
if QUICK:
    VARIANTS = VARIANTS[:2] + VARIANTS[12:13] + VARIANTS[16:17]


def export_buildings():
    for i, v in enumerate(VARIANTS):
        for detailed in (True, False):
            random.seed(1000 + i)                       # the same windows and sign colours in both versions
            name = 'Bx%d' % i
            if v[0] == 'bauhaus':
                _, W, floors, kind, style, shop = v
                root = G['bauhaus'](name, 0.0, 1, W, floors, kind, style=style, shop=shop, detailed=detailed, seed=i)
            elif v[0] == 'classic':
                _, W, floors, style = v
                root = G['building'](name, 0.0, 1, W, floors, style, detailed=detailed, letters=False)
            else:
                _, W, floors, rnd_ = v
                style = 'stone_light'
                root = G['tower'](name, 0.0, 1, W, W, floors, round_=rnd_, detailed=detailed, seed=i)
            bpy.context.view_layer.update()
            pieces = [o for o in bpy.data.objects if o.parent == root and o.type in ('MESH', 'FONT')]
            _, tw, th = TILES[style]
            tiles = {'wall': (tw, th), 'roof': (4.0, 4.0)}
            sign = list(root.get('sign', []))
            extras = {'W': W, 'depth': float(root['depth']), 'top': round(float(root['top']), 3), 'floors': floors,
                      'style': style, 'type': v[0]}
            if sign:   # the sign board's front, in glTF axes: x0, x1, z of its face, y0, y1
                extras['sign'] = [round(sign[0], 3), round(sign[1], 3), round(-sign[2], 3), round(sign[3], 3), round(sign[4], 3)]
            ob = bake_object('B%d' % i + ('' if detailed else 'L'), pieces, building_material, root.matrix_world.inverted(),
                             uv_tile=tiles.get, extras=extras)
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
    return keep_named(m, n), None


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
    # palms (three heights), street trees (two), a bench; each also as a light version for distance
    for k, (H, seed) in enumerate(((8.5, 3), (11.0, 7), (13.5, 11))):
        for d in (True, False):
            src = G['palm']('PalmSrc', H, seed, detailed=d)
            out = bake_object('Palm%d%s' % (k, '' if d else 'L'), [src], prop_material, src.matrix_world.inverted(), extras={'height': H})
            bpy.data.objects.remove(src)
            EXPORT.append(out)
    for k, (H, seed) in enumerate(((6.5, 2), (8.0, 5))):
        for d in (True, False):
            src = G['leafy_tree']('TreeSrc', H, seed, detailed=d)
            out = bake_object('Tree%d%s' % (k, '' if d else 'L'), [src], prop_material, src.matrix_world.inverted(), extras={'height': H})
            bpy.data.objects.remove(src)
            EXPORT.append(out)
    src = G['bench']('BenchSrc')
    EXPORT.append(bake_object('Bench', [src], prop_material, src.matrix_world.inverted()))
    bpy.data.objects.remove(src)


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
