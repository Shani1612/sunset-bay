"""Export the Blender cars and people as a game-ready GLB for Sunset Bay (index.html).

    python export_game_assets.py            (with the bpy module)
    blender --background --python export_game_assets.py

Writes ../assets/sunset_bay.glb with:
  car_i20, car_sedan       car bodies (no wheels), merged into a few materials, reduced for real time
  wheel_i20, wheel_sedan   one left-hand wheel each, origin at the wheel centre
  P<n>_torso / _armL / _armR / _legL / _legR
                           six people in a neutral pose, split at the shoulders and hips so the game can
                           swing the limbs; each limb's origin is its joint
Node extras (glTF "extras" -> three.js userData) carry what the game needs: wheel positions, joint
positions and sizes, already in the game's axes (x = left side, y = up, z = forward, metres).
Material names are kept simple (Paint, Glass, Chrome, Black, ... Skin, Shirt, Pants, Shoe, Hair) so
the game can recolour each car and person.
"""
import os
import sys
import math
import runpy
import bpy
import bmesh
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
G = runpy.run_path(os.path.join(HERE, 'sunset_bay_city.py'), run_name='sunset_bay_city')
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUT = argv[0] if argv else os.path.join(HERE, '..', 'assets', 'sunset_bay.glb')
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
srgb = G['srgb']


def game_xyz(v):
    """Blender (x forward, y left, z up) -> game (x left, y up, z forward)."""
    return [round(float(v[1]), 4), round(float(v[2]), 4), round(float(v[0]), 4)]


# ---------------------------------------------------------------- simple materials
SIMPLE = {}


def simple(name, color, rough=0.5, metal=0.0, alpha=1.0, emit=None):
    if name in SIMPLE:
        return SIMPLE[name]
    old = bpy.data.materials.get(name)
    if old is not None:                   # a scene material already has this name: keep the export names exact
        old.name = name + '_scene'
    m = bpy.data.materials.new(name)
    m.name = name
    try:
        m.use_nodes = True
    except Exception:
        pass
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (*srgb(color), 1)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    if alpha < 1:
        b.inputs['Alpha'].default_value = alpha
        try:
            m.surface_render_method = 'BLENDED'
        except Exception:
            pass
    if emit:
        b.inputs['Emission Color'].default_value = (*srgb(emit), 1)
        b.inputs['Emission Strength'].default_value = 1.0
    SIMPLE[name] = m
    return m


CAR_RULES = [  # (substring in the original material name, simple material)
    ('CabinPaint', 'Paint'), ('_Paint', 'Paint'),
    ('RedGlass', 'RedLens'), ('TailDark', 'RedLens'), ('AmberGlass', 'Amber'),
    ('GlassFront', 'Glass'), ('GlassRear', 'Glass'), ('_Glass', 'Glass'), ('LampCover', 'Lens'),
    ('LightGuide', 'Lens'), ('Reverse', 'Lens'),
    ('Chrome', 'Chrome'), ('Reflector', 'Chrome'), ('RoofRail', 'Chrome'), ('SkidPlate', 'Chrome'),
    ('Barrel', 'Metal'), ('Disc', 'Metal'), ('Caliper', 'Metal'),
    ('Leather', 'Interior'), ('Interior', 'Interior'),
    ('Plate', 'Plate'),
    ('Tyre', 'Rubber'),
]


RIM_COLS = [('Chrome', '#c9ced4'), ('Barrel', '#8a8f96'), ('Disc', '#5d6066'), ('Caliper', '#3a3c40')]


def wheel_material(orig, base):
    """Tyre rubber, and everything else on the wheel in one metal material coloured per vertex."""
    if 'Tyre' in orig:
        return simple('Rubber', '#151516', 0.85), None
    for key, col in RIM_COLS:
        if key in orig:
            return simple('Rim', '#ffffff', 0.28, 0.85), col
    return simple('Rim', '#ffffff', 0.28, 0.85), '#121314'


def car_material(orig, base):
    for key, target in CAR_RULES:
        if key in orig:
            break
    else:
        target = 'Black'
    return {
        'Paint': lambda: simple('Paint', '#a8381c', 0.3, 0.5),
        'Glass': lambda: simple('Glass', '#10161c', 0.05, 0.0, alpha=0.55),
        'Lens': lambda: simple('Lens', '#dfe4ea', 0.1, 0.2, alpha=0.75),
        'RedLens': lambda: simple('RedLens', '#8e0c08', 0.15, 0.0, emit='#ff1a0a'),
        'Amber': lambda: simple('Amber', '#e0781a', 0.2),
        'Chrome': lambda: simple('Chrome', '#d6dbe1', 0.12, 1.0),
        'Metal': lambda: simple('Metal', '#55595f', 0.35, 0.9),
        'Interior': lambda: simple('Interior', '#9c8063', 0.6),
        'Plate': lambda: simple('Plate', '#e9e7df', 0.4),
        'Rubber': lambda: simple('Rubber', '#151516', 0.85),
        'Black': lambda: simple('Black', '#0f1012', 0.45),
    }[target](), None


PERSON_RULES = [('Scalp', 'Scalp'), ('_Skin', 'Skin'), ('_Top', 'Shirt'), ('_Jeans', 'Pants'), ('_Trousers', 'Pants'),
                ('_Shoe', 'Shoe'), ('_Sole', 'Sole'), ('_Hair', 'Hair'), ('EyeWhite', 'EyeWhite'), ('_Iris', 'Detail'),
                ('_Lips', 'Lips')]


def person_material(orig, base):
    """Skin, shirt, trousers and hair are recoloured per person in the game; the small face parts and the
    shoes keep their own colours as vertex colours on one shared material each."""
    for key, target in PERSON_RULES:
        if key in orig:
            break
    else:
        target = 'Detail'
    if target == 'Scalp':                 # skin and close-cropped hair: the game draws the hairline (see below)
        return simple('Scalp', '#ffffff', 0.55), None
    if target in ('EyeWhite', 'Detail', 'Lips'):
        return simple('Face', '#ffffff', 0.3), base or (0.012, 0.008, 0.006)
    if target in ('Shoe', 'Sole'):
        return simple('Shoe', '#ffffff', 0.55), base or (0.8, 0.8, 0.78)
    return {
        'Skin': lambda: simple('Skin', '#e3b08c', 0.5),
        'Shirt': lambda: simple('Shirt', '#2f4f7f', 0.85),
        'Pants': lambda: simple('Pants', '#2c4468', 0.85),
        'Hair': lambda: simple('Hair', '#2b1d15', 0.55),
    }[target](), None


# ---------------------------------------------------------------- mesh helpers
def baked(ob):
    """The object's evaluated mesh (all modifiers applied) in world coordinates."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=False, depsgraph=dg)
    me.transform(ob.matrix_world)
    return me


def base_color(m):
    """A material's flat base colour (linear RGB), or None when it is textured or has none."""
    try:
        b = m.node_tree.nodes['Principled BSDF']
        if b.inputs['Base Color'].is_linked:
            return None
        return tuple(b.inputs['Base Color'].default_value[:3])
    except Exception:
        return None


def keep_uv(me):
    """People carry only the scalp's 'HairU' map; other UV maps (none are textured in the export) are dropped."""
    for uv in list(me.uv_layers):
        if uv.name != 'HairU':
            me.uv_layers.remove(uv)


def set_active_colors(me):
    if 'Col' in me.color_attributes:
        me.color_attributes.active_color = me.color_attributes['Col']
        me.color_attributes.render_color_index = me.color_attributes.active_color_index


def children(root):
    return [o for o in bpy.data.objects if o.parent == root and o.type == 'MESH' and not o.hide_render]


def tri_count(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


def decimated(me, ratio):
    ob = bpy.data.objects.new('_reduce', me)
    bpy.context.scene.collection.objects.link(ob)
    md = ob.modifiers.new('Reduce', 'DECIMATE')
    md.ratio = ratio
    md.use_collapse_triangulate = True
    bpy.context.view_layer.update()
    me2 = baked(ob)
    bpy.data.objects.remove(ob)
    return me2


LIGHT_PIECE = 2500


def combine(name, pieces, matfn, origin=Vector((0, 0, 0)), target_tris=None, face_filter=None, per_piece=False):
    """Join (mesh, keep_face(center) or None) pieces into one object with simplified materials,
    reduced to about target_tris triangles, with its origin at `origin`.
    per_piece (people): only the heavy pieces (the head) are reduced, each on its own. Reducing small open pieces
    (the top of the trousers, a sleeve) stretches their end caps over the sides, and the smoothed normals then
    tilt to the sky in a pale band. Cars (closed shells made of many small parts) are reduced as a whole."""
    mats, index = [], {}
    bm = bmesh.new()
    styled = []
    for me in pieces:
        remap, cols = [], []
        for m in me.materials:
            sm, col = matfn(m.name if m else '', base_color(m))
            if sm.name not in index:
                index[sm.name] = len(mats)
                mats.append(sm)
            remap.append(index[sm.name])
            cols.append(col)
        styled.append((me, remap, cols))
    use_vc = any(c is not None for _, _, cols in styled for c in cols)
    for me, remap, cols in styled:
        if use_vc:
            ca = me.color_attributes.new('Col', 'FLOAT_COLOR', 'CORNER')
            for p in me.polygons:
                c = cols[p.material_index] if cols else None
                rgb = (1.0, 1.0, 1.0) if c is None else (c if isinstance(c, tuple) else tuple(srgb(c)))
                for li in p.loop_indices:
                    ca.data[li].color = (rgb[0], rgb[1], rgb[2], 1.0)
        for p in me.polygons:
            p.material_index = remap[p.material_index] if remap else 0
        if face_filter is not None:
            tmp = bmesh.new()
            tmp.from_mesh(me)
            kill = [f for f in tmp.faces if not face_filter(f.calc_center_median())]
            bmesh.ops.delete(tmp, geom=kill, context='FACES')
            tmp.to_mesh(me)
            tmp.free()
    meshes = [me for me, _, _ in styled]
    total = sum(tri_count(me) for me in meshes)
    if per_piece and target_tris and total > target_tris:
        light = sum(tri_count(me) for me in meshes if tri_count(me) < LIGHT_PIECE)
        heavy = total - light
        ratio = max(0.04, min(1.0, (target_tris - light) / max(1, heavy)))
        meshes = [decimated(me, ratio) if tri_count(me) >= LIGHT_PIECE and ratio < 1 else me for me in meshes]
    for me in meshes:
        bm.from_mesh(me)
    bmesh.ops.translate(bm, verts=bm.verts, vec=-origin)
    out = bpy.data.meshes.new(name)
    bm.to_mesh(out)
    bm.free()
    for m in mats:
        out.materials.append(m)
    set_active_colors(out)
    keep_uv(out)
    ob = bpy.data.objects.new(name, out)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = origin
    tris = tri_count(out)
    if not per_piece and target_tris and tris > target_tris:
        me2 = decimated(out.copy(), target_tris / tris)
        ob.data = me2
        set_active_colors(me2)
        keep_uv(me2)
        tris = tri_count(me2)
    for p in ob.data.polygons:
        p.use_smooth = True
    print('  %-14s %6d triangles, %d materials' % (name, tris, len(mats)))
    return ob


EXPORT = []

# ---------------------------------------------------------------- cars
for key, builder, wheel_x, track, R, target in (
        ('i20', G['car_i20'], G['I20_WHEELS'], 0.745, G['I20_R'], 16000),
        ('sedan', G['car'], (1.42, -1.4), 0.79, 0.335, 14000)):
    root = builder('Export_' + key, '#a8381c', (0, 0, 0), 0.0)
    bpy.context.view_layer.update()
    body, wheel = [], []
    for o in children(root):
        is_wheel = o.name.startswith('Tyre') or o.name.startswith('Rim')
        if is_wheel:
            # keep only the front-left wheel's parts (front: +x, left: +y)
            c = sum((o.matrix_world @ Vector(v.co) for v in o.data.vertices), Vector()) / max(1, len(o.data.vertices))
            if c.x > 0 and c.y > 0:
                wheel.append(baked(o))
        else:
            body.append(baked(o))
    cb = combine('car_' + key, body, car_material, target_tris=target)
    xs = [v.co.x for v in cb.data.vertices]
    cb['length'] = round(max(xs) - min(xs), 4)
    cb['wheels_front'] = round(wheel_x[0], 4)          # game z of the front axle
    cb['wheels_rear'] = round(wheel_x[1], 4)
    cb['track'] = track                                 # game |x| of the wheel centres
    cb['wheel_r'] = R
    wc = Vector((wheel_x[0], track, R))
    cw = combine('wheel_' + key, wheel, wheel_material, origin=wc, target_tris=2400)
    EXPORT += [cb, cw]
    for o in list(bpy.data.objects):                    # drop the build objects
        if o.parent == root:
            bpy.data.objects.remove(o)
    bpy.data.objects.remove(root)

# ---------------------------------------------------------------- people
VARIANTS = [  # (female, hair style, long sleeves, seed)
    (False, 'short', False, 11), (False, 'bald', True, 23), (False, 'short', True, 37),
    (True, 'long', False, 41), (True, 'bun', True, 53), (True, 'short', False, 67)]
for i, (female, style, sleeves, seed) in enumerate(VARIANTS):
    root = G['person']('Export_P%d' % i, (0, 0, 0), 0.0, seed, female=female, style=style, sleeves=sleeves, neutral=True)
    bpy.context.view_layer.update()
    J = {k: Vector(v) for k, v in root['J'].items()}
    # close-cropped hair: glTF has no shader nodes, so the scalp material's hairline (high at the forehead, down
    # to the nape, kept above the ears; in head coordinates) goes to the game as each vertex's height above the
    # hairline, in the UV map 'HairU'. That height is smooth, so it survives the mesh reduction; the game's scalp
    # shader turns it into a sharp hairline per pixel, blending the person's skin and hair colours.
    def smooth(x, lo, hi):
        t = max(0.0, min(1.0, (x - lo) / (hi - lo)))
        return t * t * (3 - 2 * t)

    for o in children(root):
        if o.name.endswith('Head') and o.data.materials and 'Scalp' in o.data.materials[0].name:
            for uv in list(o.data.uv_layers):
                o.data.uv_layers.remove(uv)
            hu = o.data.uv_layers.new(name='HairU')
            for li, loop in enumerate(o.data.loops):
                c = o.data.vertices[loop.vertex_index].co
                line = -0.035 + 0.105 * smooth(c.x, -0.06, 0.04)
                ear = smooth(abs(c.y), 0.035, 0.062) * smooth(c.x, -0.085, -0.02)
                line += ear * max(0.02 - line, 0.0)
                hu.data[li].uv = (c.z - line, 0.0)
    # Which part owns a face: the body, sleeves and trousers are Skin-modifier meshes grown from bones of known
    # thickness. Each face goes to the bone it is nearest to relative to that bone's thickness, so a thin arm
    # hanging beside the wide chest does not take the chest's sides. A limb also takes the faces it nearly ties
    # for (around its joint) so no gap opens when it swings.
    def seg_t(c, a, b):
        ab = b - a
        return max(0.0, min(1.0, (c - a).dot(ab) / max(ab.length_squared, 1e-9)))

    def chain_dist(c, segs):
        return min((c - (a + (b - a) * seg_t(c, a, b))).length for a, b in segs)

    ARM = {sd: [(J['sh' + sd], J['el' + sd]), (J['el' + sd], J['wr' + sd]), (J['wr' + sd], J['hand' + sd]),
                (J['hand' + sd], J['hand' + sd] + (J['hand' + sd] - J['wr' + sd]) * 0.4), (J['wr' + sd], J['thumb' + sd])]
           for sd in 'LR'}
    LEG = {sd: [(J['hip' + sd], J['knee' + sd]), (J['knee' + sd], J['ank' + sd])] for sd in 'LR'}

    def bone_part(p, q):
        """A bone is an arm's (or leg's) when both its ends lie along that limb and it reaches past the shoulder
        (hip); everything else (spine, neck, collarbones, pelvis) is the torso's."""
        for sd in 'LR':
            for part, chain, root_j in (('arm', ARM[sd], J['sh' + sd]), ('leg', LEG[sd], J['hip' + sd])):
                if chain_dist(p, chain) < 0.05 and chain_dist(q, chain) < 0.05 and \
                        max((p - root_j).length, (q - root_j).length) > 0.03:
                    return part + sd
        return 'torso'

    def skin_bones(o):
        me = o.data
        if not me.skin_vertices:
            return []
        sv, W = me.skin_vertices[0].data, o.matrix_world
        pts = [W @ v.co for v in me.vertices]
        rad = [max(1e-3, (sv[i].radius[0] + sv[i].radius[1]) / 2) for i in range(len(pts))]
        bones = []
        for e in me.edges:
            i, j = e.vertices
            bones.append((pts[i], pts[j], rad[i], rad[j], bone_part(pts[i], pts[j])))
        return bones

    def owner(bones, part, overlap=1.12):
        def keep(f):
            c, best = f.calc_center_median(), {}
            for a, b, ra, rb, pt in bones:
                t = seg_t(c, a, b)
                d = (c - (a + (b - a) * t)).length / (ra + (rb - ra) * t)
                if d < best.get(pt, 1e9):
                    best[pt] = d
            if part not in best:
                return False
            others = min([v for k, v in best.items() if k != part] or [1e9])
            return best[part] <= others * (1.0 if part == 'torso' else overlap)
        return keep

    # Inner cores (an ellipsoid of shirt in the chest, one of trousers in the hips) that the game puts inside the
    # body, so the small openings at the shoulders and hips while the limbs swing show cloth, not the inside.
    half = float(root['torso_half'])
    cores = {}

    def core(me, z0, z1, key, shrink=0.86):
        vs = [v.co for v in me.vertices if z0 <= v.co.z <= z1 and abs(v.co.y) < half]
        if len(vs) < 8:
            return
        lo = Vector((min(v.x for v in vs), min(v.y for v in vs), z0))
        hi = Vector((max(v.x for v in vs), max(v.y for v in vs), z1))
        c, r = (lo + hi) / 2, (hi - lo) / 2 * shrink
        cores[key] = game_xyz(c) + [round(r.y, 4), round(r.z, 4), round(r.x, 4)]

    parts = {'torso': [], 'armL': [], 'armR': [], 'legL': [], 'legR': []}
    for o in children(root):
        nm = o.name
        me = baked(o)
        bones = skin_bones(o)
        if 'Shirt' in nm:
            core(me, J['waist'].z, J['chest'].z + 0.04, 'core_top')
            # the shoulder balls must stay inside the shirt: 90% of the distance from the joint to its surface
            cores['shoulder_r'] = round(0.9 * min((v.co - J['shL']).length for v in me.vertices), 4)
        if 'Trousers' in nm:
            # kept low and slim: it must stay inside the trousers, below the shirt's hem, or it shows as a pale band
            hz = min(J['hipL'].z, J['hipR'].z)
            core(me, hz - 0.08, hz + 0.03, 'core_hips', shrink=0.72)
        if bones and ('Shirt' in nm or 'Body' in nm or 'Trousers' in nm):
            for part in parts:
                if any(b[4] == part for b in bones):
                    parts[part].append((me, owner(bones, part)))
        elif 'Shoe' in nm:
            parts['legL' if 'ShoeL' in nm else 'legR'].append((me, None))
        else:
            parts['torso'].append((me, None))                   # head, face, hair, collar
    origins = {'torso': Vector((0, 0, 0)), 'armL': J['shL'], 'armR': J['shR'], 'legL': J['hipL'], 'legR': J['hipR']}
    targets = {'torso': 9000, 'armL': 2600, 'armR': 2600, 'legL': 2400, 'legR': 2400}
    for part, items in parts.items():
        meshes = []
        for me, keep in items:
            m2 = me.copy()
            if keep is not None:
                tmp = bmesh.new()
                tmp.from_mesh(m2)
                kill = [f for f in tmp.faces if not keep(f)]
                bmesh.ops.delete(tmp, geom=kill, context='FACES')
                tmp.to_mesh(m2)
                tmp.free()
            meshes.append(m2)
        ob = combine('P%d_%s' % (i, part), meshes, person_material, origin=origins[part], target_tris=targets[part],
                     per_piece=True)
        ob['joint'] = game_xyz(origins[part])
        if part == 'torso':
            for k, v in cores.items():
                ob[k] = v
        if part.startswith('arm'):                               # where the hand is, for held things
            ob['hand'] = game_xyz(J['hand' + part[-1]] - J['sh' + part[-1]])
        ob['female'] = female
        EXPORT.append(ob)
    for o in list(bpy.data.objects):
        if o.parent == root:
            bpy.data.objects.remove(o)
    bpy.data.objects.remove(root)

# ---------------------------------------------------------------- write the GLB
bpy.ops.object.select_all(action='DESELECT')
for ob in EXPORT:
    ob.select_set(True)
bpy.ops.export_scene.gltf(filepath=os.path.abspath(OUT), export_format='GLB', use_selection=True,
                          export_extras=True, export_yup=True, export_apply=True,
                          export_vertex_color='ACTIVE')
print('wrote', os.path.abspath(OUT), os.path.getsize(OUT) // 1024, 'KB')
# the same models Draco-compressed for the new game (../city), which loads them with three.js's DRACOLoader
CITY = os.path.join(HERE, '..', 'city', 'assets', 'actors.glb')
if os.path.isdir(os.path.dirname(CITY)):
    bpy.ops.export_scene.gltf(filepath=os.path.abspath(CITY), export_format='GLB', use_selection=True,
                              export_extras=True, export_yup=True, export_apply=True, export_vertex_color='ACTIVE',
                              export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=7,
                              export_draco_position_quantization=14, export_draco_normal_quantization=10,
                              export_draco_texcoord_quantization=14, export_draco_color_quantization=10)
    print('wrote', os.path.abspath(CITY), os.path.getsize(CITY) // 1024, 'KB')
