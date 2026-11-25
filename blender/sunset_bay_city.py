"""Sunset Bay — a realistic city street built procedurally in Blender.

Builds a downtown street at sunset (buildings with real window openings, frames and glass, cornices,
shopfronts with awnings and signs; detailed cars with smooth subdivided bodies, cut wheel arches,
five-spoke rims, brakes and lights; asphalt, road markings, curbs, sidewalks, street lamps, props)
and renders it with Cycles from several cameras.

Run with Blender 4.2+ / 5.x:
    blender --background --python sunset_bay_city.py -- OUT_DIR [--quick] [--only NAME[,NAME]] [--blend FILE]
or with the bpy module:
    python sunset_bay_city.py -- OUT_DIR ...
"""
import bpy, bmesh, math, random, sys, os
from mathutils import Vector

# ---------------------------------------------------------------- arguments
argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUT = argv[0] if argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'renders')
QUICK = '--quick' in argv
ONLY = None
BLEND = None
if '--only' in argv:
    ONLY = set(argv[argv.index('--only') + 1].split(','))
if '--blend' in argv:
    BLEND = argv[argv.index('--blend') + 1]
os.makedirs(OUT, exist_ok=True)
random.seed(1124)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
COL = scene.collection


def srgb(h):
    """'#rrggbb' -> linear RGB tuple."""
    h = h.lstrip('#')
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


# ---------------------------------------------------------------- materials
def new_mat(name):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    return m, m.node_tree, m.node_tree.nodes['Principled BSDF']


def principled(name, color, rough=0.5, metal=0.0, coat=0.0, coat_rough=0.03, emit=None, emit_str=0.0, ior=1.45, spec=0.5):
    m, nt, b = new_mat(name)
    b.inputs['Base Color'].default_value = (*color, 1)
    b.inputs['Roughness'].default_value = rough
    b.inputs['Metallic'].default_value = metal
    b.inputs['IOR'].default_value = ior
    b.inputs['Specular IOR Level'].default_value = spec
    if coat:
        b.inputs['Coat Weight'].default_value = coat
        b.inputs['Coat Roughness'].default_value = coat_rough
    if emit:
        b.inputs['Emission Color'].default_value = (*emit, 1)
        b.inputs['Emission Strength'].default_value = emit_str
    return m


def textured(name, kind, c1, c2, mortar=None, scale=1.0, rough=0.85, bump=0.25, rough_var=0.1):
    """Procedural surfaces in object space (metres): brick / blocks / plaster / asphalt / concrete slabs."""
    m, nt, b = new_mat(name)
    N, L = nt.nodes, nt.links
    tc = N.new('ShaderNodeTexCoord')
    mp = N.new('ShaderNodeMapping')
    if kind in ('brick', 'blocks'):
        # wall texture plane: u = x + y runs along both the street front and the side walls, v = height
        sep = N.new('ShaderNodeSeparateXYZ')
        L.new(tc.outputs['Object'], sep.inputs[0])
        u = N.new('ShaderNodeMath')
        u.operation = 'ADD'
        L.new(sep.outputs[0], u.inputs[0])
        L.new(sep.outputs[1], u.inputs[1])
        comb = N.new('ShaderNodeCombineXYZ')
        L.new(u.outputs[0], comb.inputs[0])
        L.new(sep.outputs[2], comb.inputs[1])
        L.new(comb.outputs[0], mp.inputs['Vector'])
    else:
        L.new(tc.outputs['Object'], mp.inputs['Vector'])
    mp.inputs['Scale'].default_value = (scale, scale, scale)
    noise = N.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 3.0
    noise.inputs['Detail'].default_value = 8.0
    L.new(mp.outputs['Vector'], noise.inputs['Vector'])
    fine = N.new('ShaderNodeTexNoise')
    fine.inputs['Scale'].default_value = 60.0
    fine.inputs['Detail'].default_value = 6.0
    L.new(mp.outputs['Vector'], fine.inputs['Vector'])
    ramp = N.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (*c1, 1)
    ramp.color_ramp.elements[1].color = (*c2, 1)
    L.new(noise.outputs['Fac'], ramp.inputs['Fac'])
    height = fine.outputs['Fac']
    if kind in ('brick', 'blocks', 'slabs'):
        br = N.new('ShaderNodeTexBrick')
        L.new(mp.outputs['Vector'], br.inputs['Vector'])
        L.new(ramp.outputs['Color'], br.inputs['Color1'])
        # second brick colour: the same noise ramp, slightly darker
        dark = N.new('ShaderNodeMix')
        dark.data_type = 'RGBA'
        dark.blend_type = 'MULTIPLY'
        dark.inputs[0].default_value = 1.0
        L.new(ramp.outputs['Color'], dark.inputs[6])
        dark.inputs[7].default_value = (0.78, 0.74, 0.72, 1)
        L.new(dark.outputs[2], br.inputs['Color2'])
        br.inputs['Mortar'].default_value = (*(mortar or (0.55, 0.53, 0.5)), 1)
        if kind == 'brick':
            br.inputs['Scale'].default_value = 1.0
            br.inputs['Brick Width'].default_value = 0.25
            br.inputs['Row Height'].default_value = 0.075
            br.inputs['Mortar Size'].default_value = 0.012
        elif kind == 'blocks':
            br.inputs['Scale'].default_value = 1.0
            br.inputs['Brick Width'].default_value = 1.2
            br.inputs['Row Height'].default_value = 0.6
            br.inputs['Mortar Size'].default_value = 0.008
        else:  # pavement slabs
            br.offset = 0.0
            br.inputs['Scale'].default_value = 1.0
            br.inputs['Brick Width'].default_value = 1.5
            br.inputs['Row Height'].default_value = 1.5
            br.inputs['Mortar Size'].default_value = 0.012
        br.inputs['Bias'].default_value = 0.0
        L.new(br.outputs['Color'], b.inputs['Base Color'])
        # mortar joints are recessed
        mix = N.new('ShaderNodeMath')
        mix.operation = 'MULTIPLY_ADD'
        L.new(br.outputs['Fac'], mix.inputs[0])
        mix.inputs[1].default_value = -1.0
        mix.inputs[2].default_value = 1.0
        add = N.new('ShaderNodeMath')
        add.operation = 'MULTIPLY_ADD'
        L.new(fine.outputs['Fac'], add.inputs[0])
        add.inputs[1].default_value = 0.15
        L.new(mix.outputs[0], add.inputs[2])
        height = add.outputs[0]
    else:
        L.new(ramp.outputs['Color'], b.inputs['Base Color'])
    rr = N.new('ShaderNodeMapRange')
    rr.inputs['To Min'].default_value = rough - rough_var
    rr.inputs['To Max'].default_value = min(1.0, rough + rough_var)
    L.new(fine.outputs['Fac'], rr.inputs['Value'])
    L.new(rr.outputs['Result'], b.inputs['Roughness'])
    bp = N.new('ShaderNodeBump')
    bp.inputs['Strength'].default_value = bump
    bp.inputs['Distance'].default_value = 0.02
    L.new(height, bp.inputs['Height'])
    L.new(bp.outputs['Normal'], b.inputs['Normal'])
    return m


M = {}
M['asphalt'] = textured('Asphalt', 'plaster', srgb('#2b2a2c'), srgb('#3d3b3c'), scale=1.0, rough=0.82, bump=0.35)
M['concrete'] = textured('Sidewalk', 'slabs', srgb('#9a958e'), srgb('#b3ada4'), mortar=srgb('#6f6a64'), rough=0.8, bump=0.3)
M['curb'] = textured('Curb', 'plaster', srgb('#a7a39c'), srgb('#bdb8b0'), rough=0.75, bump=0.2)
M['paint_w'] = principled('LinePaint', srgb('#e9e6dc'), rough=0.55)
M['paint_y'] = principled('LineYellow', srgb('#e3b23c'), rough=0.55)
M['glass'] = principled('WindowGlass', srgb('#1a2230'), rough=0.03, ior=1.52, spec=1.0)
M['glass_lit'] = principled('WindowLit', srgb('#2a2418'), rough=0.05, emit=srgb('#ffc77a'), emit_str=1.6, spec=1.0)
M['glass_shop'] = principled('ShopGlass', srgb('#1d1915'), rough=0.04, emit=srgb('#ffcf90'), emit_str=0.9, spec=1.0)
M['frame_w'] = principled('FrameWhite', srgb('#e8e4dc'), rough=0.35)
M['frame_d'] = principled('FrameDark', srgb('#26282c'), rough=0.35, metal=0.6)
M['metal'] = principled('Metal', srgb('#8d939b'), rough=0.35, metal=1.0)
M['lamp_pole'] = principled('LampPole', srgb('#1d2421'), rough=0.4, metal=0.8)
M['lamp_glow'] = principled('LampGlow', srgb('#fff1d6'), rough=0.3, emit=srgb('#ffcf8a'), emit_str=25.0)
M['wood'] = principled('Wood', srgb('#6b4a32'), rough=0.7)
M['ac'] = principled('ACUnit', srgb('#b9bcbf'), rough=0.45, metal=0.5)
M['roof'] = textured('RoofTar', 'plaster', srgb('#3a3837'), srgb('#4a4744'), rough=0.9, bump=0.3)
M['hydrant'] = principled('Hydrant', srgb('#b3261e'), rough=0.4, coat=0.5)
M['dark'] = principled('DarkPlastic', srgb('#111214'), rough=0.5)

WALLS = {
    'brick_red': textured('BrickRed', 'brick', srgb('#7a3a2a'), srgb('#9a4c36'), mortar=srgb('#a9a197'), rough=0.85, bump=0.5),
    'brick_brown': textured('BrickBrown', 'brick', srgb('#5a3a2e'), srgb('#76503f'), mortar=srgb('#9d958a'), rough=0.85, bump=0.5),
    'stone': textured('Stone', 'blocks', srgb('#c9b89a'), srgb('#d9cbb0'), mortar=srgb('#a99a80'), rough=0.8, bump=0.3),
    'plaster_pink': textured('PlasterPink', 'plaster', srgb('#d59b8a'), srgb('#e3b3a1'), rough=0.9, bump=0.15),
    'plaster_cream': textured('PlasterCream', 'plaster', srgb('#e0cfae'), srgb('#ecdfc4'), rough=0.9, bump=0.15),
    'plaster_teal': textured('PlasterTeal', 'plaster', srgb('#5f8f8a'), srgb('#76a39d'), rough=0.9, bump=0.15),
    'concrete': textured('ConcreteWall', 'blocks', srgb('#8e8c88'), srgb('#a09d98'), mortar=srgb('#77746f'), rough=0.85, bump=0.2),
}
TRIM = {
    'brick_red': principled('TrimLimestone', srgb('#d6cdbd'), rough=0.75),
    'brick_brown': principled('TrimLimestone2', srgb('#cfc4b2'), rough=0.75),
    'stone': principled('TrimStone', srgb('#e4d8c2'), rough=0.7),
    'plaster_pink': principled('TrimWhite', srgb('#efe9df'), rough=0.6),
    'plaster_cream': principled('TrimBrownish', srgb('#a8896a'), rough=0.6),
    'plaster_teal': principled('TrimWhite2', srgb('#ece7dd'), rough=0.6),
    'concrete': principled('TrimConcrete', srgb('#6f6d69'), rough=0.6),
}


# ---------------------------------------------------------------- mesh helpers
def bm_hex(bm, p, mi=0):
    """8 corners in box order (bottom 0-3 counter-clockwise from x0y0, then top 4-7) -> closed solid."""
    vs = [bm.verts.new(v) for v in p]
    for f in ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)):
        bm.faces.new([vs[i] for i in f]).material_index = mi


def bm_box(bm, x0, y0, z0, x1, y1, z1, mi=0):
    if x1 < x0: x0, x1 = x1, x0
    if y1 < y0: y0, y1 = y1, y0
    if z1 < z0: z0, z1 = z1, z0
    bm_hex(bm, [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], mi)


def bm_cyl(bm, cx, cy, z0, z1, r, seg=16, mi=0):
    ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r, radius2=r, depth=z1 - z0)
    bmesh.ops.translate(bm, verts=ret['verts'], vec=(cx, cy, (z0 + z1) / 2))
    for f in {f for v in ret['verts'] for f in v.link_faces}:
        f.material_index = mi


def mesh_obj(name, bm, mats, parent=None, bevel=0.0, smooth=False):
    me = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    ob = bpy.data.objects.new(name, me)
    COL.objects.link(ob)
    if parent:
        ob.parent = parent
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    if bevel > 0:
        md = ob.modifiers.new('Bevel', 'BEVEL')
        md.width = bevel
        md.segments = 2
        md.limit_method = 'ANGLE'
        md.harden_normals = False
    return ob


def empty(name, loc=(0, 0, 0), rot_z=0.0):
    e = bpy.data.objects.new(name, None)
    COL.objects.link(e)
    e.location = loc
    e.rotation_euler = (0, 0, rot_z)
    return e


# ---------------------------------------------------------------- buildings
SHOP_NAMES = ['PIZZA', 'DINER', 'PHARMACY', 'SUSHI BAR', 'BOOKS', 'BARBER', 'DELI', 'COFFEE', 'LIQUOR', 'TATTOO', 'LAUNDRY', 'BAKERY']
SIGN_COLORS = ['#c0392b', '#1f5f99', '#2a7d4f', '#7d2a6e', '#b8860b', '#1c1c1c']
AWNING_COLORS = ['#8e1f1f', '#1f4e79', '#225c3a', '#5b2a66', '#7a5a1f', '#2a2a2a']


def building(name, x0, side, W, floors, style, detailed=True, depth=18.0):
    """A building whose street facade is at |y| = 12.5. side=+1: north side (faces -y), -1: south side."""
    if side > 0:
        root = empty(name, (x0, 12.5, 0), 0.0)
    else:
        root = empty(name, (x0 + W, -12.5, 0), math.pi)
    GH, FH = 4.4, 3.3
    top = GH + (floors - 1) * FH
    wall, trim = WALLS[style], TRIM[style]
    frame = M['frame_w'] if style in ('brick_red', 'brick_brown', 'plaster_teal', 'plaster_pink') else M['frame_d']
    mats = [wall, trim, frame, M['glass'], M['glass_lit'], M['glass_shop'], M['frame_d'], M['roof']]
    bm = bmesh.new()
    T = 0.32  # facade wall thickness (window reveal depth)
    # building mass behind the facade, and the roof slab
    bm_box(bm, 0, T, 0, W, depth, top, 0)
    bm_box(bm, 0.05, T, top - 0.01, W - 0.05, depth - 0.05, top + 0.02, 7)

    nb = max(2, int(W / 2.9))
    bay = W / nb
    ww = min(1.5, bay * 0.56)
    wh = 1.85 if style != 'concrete' else 1.6
    sill = 0.85
    # ---- upper floors: wall pieces around real window openings
    for f in range(1, floors):
        zf = GH + (f - 1) * FH
        z0, z1 = zf + sill, zf + sill + wh
        bm_box(bm, 0, 0, zf, W, T, z0, 0)              # spandrel under the windows
        bm_box(bm, 0, 0, z1, W, T, zf + FH, 0)         # wall above the windows
        xs = [0.0]
        for i in range(nb):
            cx = (i + 0.5) * bay
            xs += [cx - ww / 2, cx + ww / 2]
        xs.append(W)
        for k in range(0, len(xs), 2):                 # piers between openings
            if xs[k + 1] - xs[k] > 0.001:
                bm_box(bm, xs[k], 0, z0, xs[k + 1], T, z1, 0)
        if not detailed:
            for i in range(nb):
                cx = (i + 0.5) * bay
                bm_box(bm, cx - ww / 2, T - 0.12, z0, cx + ww / 2, T - 0.1, z1, 4 if random.random() < 0.3 else 3)
            continue
        for i in range(nb):
            cx = (i + 0.5) * bay
            a, b = cx - ww / 2, cx + ww / 2
            g0 = T - 0.14
            lit = random.random() < 0.28
            bm_box(bm, a, g0, z0, b, g0 + 0.02, z1, 4 if lit else 3)        # glass, recessed
            fw = 0.06
            for (p0, p1, q0, q1) in ((a, a + fw, z0, z1), (b - fw, b, z0, z1), (a, b, z0, z0 + fw), (a, b, z1 - fw, z1),
                                     (cx - 0.025, cx + 0.025, z0, z1), (a, b, z0 + wh * 0.68, z0 + wh * 0.68 + 0.05)):
                bm_box(bm, p0, g0 - 0.05, q0, p1, g0 + 0.04, q1, 2)        # frame, mullion, transom
            bm_box(bm, a - 0.12, -0.07, z0 - 0.09, b + 0.12, T - 0.1, z0, 1)  # stone sill
            if style in ('brick_red', 'brick_brown', 'stone'):
                bm_box(bm, a - 0.14, -0.04, z1, b + 0.14, T - 0.1, z1 + 0.22, 1)  # lintel
                bm_box(bm, cx - 0.12, -0.06, z1, cx + 0.12, T - 0.1, z1 + 0.26, 1)  # keystone
            if style in ('brick_red', 'brick_brown') and random.random() < 0.12:
                bm_box(bm, a + 0.25, -0.55, z0 + 0.02, b - 0.25, T - 0.12, z0 + 0.45, 6)  # window AC unit
            if style in ('plaster_pink', 'plaster_cream', 'plaster_teal') and f >= 2 and random.random() < 0.35:
                # small balcony with railing
                bm_box(bm, a - 0.35, -0.9, zf + 0.02, b + 0.35, 0, zf + 0.16, 1)
                for px in [a - 0.33 + k * 0.12 for k in range(int((b - a + 0.66) / 0.12) + 1)]:
                    bm_box(bm, px, -0.88, zf + 0.16, px + 0.025, -0.855, zf + 1.05, 6)
                bm_box(bm, a - 0.35, -0.9, zf + 1.02, b + 0.35, -0.84, zf + 1.07, 6)
                for sx in (a - 0.35, b + 0.33):
                    bm_box(bm, sx, -0.9, zf + 0.16, sx + 0.02, 0, zf + 1.07, 6)
        if style in ('stone', 'brick_red', 'brick_brown'):
            bm_box(bm, -0.02, -0.08, zf - 0.12, W + 0.02, T, zf + 0.06, 1)  # string course between floors
    # ---- ground floor: shopfront
    bm_box(bm, 0, 0, 0, W, T, 0.45, 1)                  # stone base
    bm_box(bm, 0, 0, 3.35, W, T, GH, 0)                 # wall over the shop
    bm_box(bm, 0, 0, 0.45, 0.45, T, 3.35, 0)            # end piers
    bm_box(bm, W - 0.45, 0, 0.45, W, T, 3.35, 0)
    gx0, gx1 = 0.45, W - 0.45
    bm_box(bm, gx0, T - 0.1, 0.45, gx1, T - 0.08, 3.35, 5)  # big lit shop window
    nmul = max(2, int((gx1 - gx0) / 2.2))
    for k in range(nmul + 1):
        x = gx0 + (gx1 - gx0) * k / nmul
        bm_box(bm, x - 0.04, T - 0.16, 0.45, x + 0.04, T - 0.06, 3.35, 6)
    bm_box(bm, gx0, T - 0.16, 2.6, gx1, T - 0.06, 2.67, 6)
    bm_box(bm, gx0, T - 0.16, 0.45, gx1, T - 0.06, 0.52, 6)
    # sign band
    sc = srgb(random.choice(SIGN_COLORS))
    signm = principled(name + '_Sign', sc, rough=0.4)
    mats.append(signm)
    bm_box(bm, 0.3, -0.18, 3.42, W - 0.3, 0.02, 4.15, len(mats) - 1)
    # awning (sloped fabric)
    awm = principled(name + '_Awning', srgb(random.choice(AWNING_COLORS)), rough=0.9)
    mats.append(awm)
    ai = len(mats) - 1
    ax0, ax1 = 0.6, W - 0.6
    bm_hex(bm, [(ax0, -1.7, 2.75), (ax1, -1.7, 2.75), (ax1, -0.05, 3.32), (ax0, -0.05, 3.32),
                (ax0, -1.7, 2.79), (ax1, -1.7, 2.79), (ax1, -0.05, 3.36), (ax0, -0.05, 3.36)], ai)
    bm_box(bm, ax0, -1.72, 2.45, ax1, -1.68, 2.79, ai)  # valance
    # ---- cornice and parapet
    bm_box(bm, -0.05, -0.55, top - 0.15, W + 0.05, T, top + 0.25, 1)
    bm_box(bm, -0.05, -0.38, top - 0.45, W + 0.05, T, top - 0.15, 1)
    for k in range(int(W / 0.6)):                       # dentils
        x = 0.25 + k * 0.6
        bm_box(bm, x, -0.3, top - 0.62, x + 0.22, T, top - 0.45, 1)
    bm_box(bm, 0, 0, top + 0.25, W, T, top + 1.15, 0)
    bm_box(bm, -0.04, -0.06, top + 1.15, W + 0.04, T + 0.04, top + 1.25, 1)
    ob = mesh_obj(name + '_Mesh', bm, mats, parent=root, bevel=0.012 if detailed else 0.0)
    # ---- shop sign lettering
    if detailed:
        cu = bpy.data.curves.new(name + '_Text', 'FONT')
        cu.body = random.choice(SHOP_NAMES)
        cu.align_x = 'CENTER'
        cu.align_y = 'CENTER'
        cu.size = 0.52
        cu.extrude = 0.025
        t = bpy.data.objects.new(name + '_Text', cu)
        COL.objects.link(t)
        t.parent = root
        t.location = (W / 2, -0.2, 3.79)
        t.rotation_euler = (math.radians(90), 0, 0)
        cu.materials.append(principled(name + '_Letters', srgb('#fff4dc'), rough=0.3, emit=srgb('#fff0d0'), emit_str=6.0))
    # ---- roof plant
    rbm = bmesh.new()
    for _ in range(random.randint(1, 3)):
        x = random.uniform(1.5, W - 3)
        y = random.uniform(4, depth - 4)
        bm_box(rbm, x, y, top, x + random.uniform(1.2, 2.2), y + random.uniform(1.0, 1.6), top + random.uniform(0.8, 1.2), 0)
    if random.random() < 0.45 and W > 8:
        x, y = random.uniform(3, W - 3), random.uniform(6, depth - 5)
        for dx, dy in ((-0.9, -0.9), (0.9, -0.9), (-0.9, 0.9), (0.9, 0.9)):
            bm_box(rbm, x + dx - 0.07, y + dy - 0.07, top, x + dx + 0.07, y + dy + 0.07, top + 1.6, 2)
        bm_cyl(rbm, x, y, top + 1.6, top + 4.2, 1.3, 24, 1)
        ret = bmesh.ops.create_cone(rbm, cap_ends=True, segments=24, radius1=1.45, radius2=0.05, depth=0.9)
        bmesh.ops.translate(rbm, verts=ret['verts'], vec=(x, y, top + 4.65))
        for fc in {fc for v in ret['verts'] for fc in v.link_faces}:
            fc.material_index = 1
    mesh_obj(name + '_Roof', rbm, [M['ac'], M['wood'], M['metal']], parent=root)
    return root


# ---------------------------------------------------------------- street
def street():
    bm = bmesh.new()
    bm_box(bm, -200, -7.5, -0.2, 420, 7.5, 0.0, 0)
    mesh_obj('Road', bm, [M['asphalt']])
    for s in (1, -1):
        bm = bmesh.new()
        bm_box(bm, -200, 7.75 * s, 0.0, 420, 12.5 * s, 0.15, 0)
        mesh_obj('Sidewalk' + str(s), bm, [M['concrete']])
        bm = bmesh.new()
        bm_box(bm, -200, 7.5 * s, -0.05, 420, 7.75 * s, 0.15, 0)
        mesh_obj('Curb' + str(s), bm, [M['curb']], bevel=0.03)
    # road markings
    bm = bmesh.new()
    for s in (1, -1):
        bm_box(bm, -200, 0.08 * s, 0.0, 420, 0.2 * s, 0.006, 1)          # double yellow
        bm_box(bm, -200, 7.1 * s, 0.0, 420, 7.22 * s, 0.006, 0)          # edge line
        x = -200.0
        while x < 420:                                                    # dashed lane divider
            bm_box(bm, x, 3.6 * s - 0.06, 0.0, x + 3.0, 3.6 * s + 0.06, 0.006, 0)
            x += 9.0
    for k in range(10):                                                   # crosswalk
        y = -6.6 + k * 1.4
        bm_box(bm, 28.0, y, 0.0, 32.0, y + 0.7, 0.006, 0)
    bm_box(bm, 26.8, -7.1, 0.0, 27.2, 7.1, 0.006, 0)                      # stop lines
    bm_box(bm, 32.8, -7.1, 0.0, 33.2, 7.1, 0.006, 0)
    mesh_obj('Markings', bm, [M['paint_w'], M['paint_y']])
    # manholes and patches
    bm = bmesh.new()
    for x in (-12, 18, 46, 75):
        bm_cyl(bm, x, random.uniform(-5, 5), 0.0, 0.004, 0.35, 24, 0)
    mesh_obj('Manholes', bm, [M['metal']])


def street_lamp(x, y, side):
    root = empty('Lamp', (x, y, 0.15), 0 if side > 0 else math.pi)
    bm = bmesh.new()
    bm_cyl(bm, 0, 0, 0, 0.35, 0.16, 16, 0)
    bm_cyl(bm, 0, 0, 0, 7.6, 0.075, 16, 0)
    bm_box(bm, -0.04, -1.9, 7.4, 0.04, 0.0, 7.5, 0)
    bm_box(bm, -0.22, -2.4, 7.25, 0.22, -1.7, 7.5, 0)
    bm_box(bm, -0.18, -2.35, 7.22, 0.18, -1.75, 7.26, 1)
    mesh_obj('LampMesh', bm, [M['lamp_pole'], M['lamp_glow']], parent=root, bevel=0.01)
    L = bpy.data.lights.new('LampLight', 'SPOT')
    L.energy = 900
    L.color = (1.0, 0.78, 0.5)
    L.spot_size = math.radians(115)
    L.spot_blend = 0.6
    L.shadow_soft_size = 0.25
    lo = bpy.data.objects.new('LampLight', L)
    COL.objects.link(lo)
    lo.parent = root
    lo.location = (0, -2.05, 7.15)


def props():
    bm = bmesh.new()
    for x, s in ((-20, 1), (22, -1), (52, 1)):                           # hydrants
        y = 8.3 * s
        bm_cyl(bm, x, y, 0.15, 0.75, 0.13, 16, 0)
        bm_cyl(bm, x, y, 0.75, 0.86, 0.15, 16, 0)
        bm_cyl(bm, x, y, 0.86, 0.95, 0.08, 16, 0)
        bm_box(bm, x - 0.22, y - 0.05, 0.5, x + 0.22, y + 0.05, 0.6, 0)
    mesh_obj('Hydrants', bm, [M['hydrant']], bevel=0.01)
    bm = bmesh.new()
    for x, s in ((-2, 1), (36, -1), (64, -1)):                           # trash cans
        bm_cyl(bm, x, 8.4 * s, 0.15, 1.05, 0.3, 20, 0)
    for x in [x for x in range(-40, 100, 6)]:                            # bollards near the crosswalk
        if 20 < x < 40:
            for s in (1, -1):
                bm_cyl(bm, x, 7.95 * s, 0.15, 1.0, 0.08, 12, 0)
    mesh_obj('StreetFurniture', bm, [M['lamp_pole']], bevel=0.01)


# ---------------------------------------------------------------- cars
def loft(rings, close=True):
    """rings: list of lists of (x, y, z) with the same vertex count -> quad mesh with n-gon end caps."""
    bm = bmesh.new()
    vr = [[bm.verts.new(p) for p in r] for r in rings]
    n = len(rings[0])
    for k in range(len(vr) - 1):
        for j in range(n):
            bm.faces.new((vr[k][j], vr[k][(j + 1) % n], vr[k + 1][(j + 1) % n], vr[k + 1][j]))
    if close:
        bm.faces.new(list(reversed(vr[0])))
        bm.faces.new(vr[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def superring(x, hw, z0, z1, n=10, e=4.0):
    zc, hh = (z0 + z1) / 2, (z1 - z0) / 2
    pts = []
    for j in range(n):
        t = j / n * 2 * math.pi
        c, s = math.cos(t), math.sin(t)
        pts.append((x, hw * math.copysign(abs(c) ** (2 / e), c), zc + hh * math.copysign(abs(s) ** (2 / e), s)))
    return pts


def subsurf(ob, lv=2):
    md = ob.modifiers.new('Subsurf', 'SUBSURF')
    md.levels = lv
    md.render_levels = lv
    for p in ob.data.polygons:
        p.use_smooth = True


def lerp(a, b, t):
    return a + (b - a) * t


def wheel(root, x, side, mats, R=0.335, Wd=0.225, track=0.79, spokes=5):
    """Tyre, spoked rim with lug nuts, brake disc and caliper; axle along y, outer face toward `side`.
    R: tyre radius, Wd: tyre width, track: half track (wheel centre |y|), spokes: number of spokes."""
    from mathutils import Matrix
    k_ = R / 0.335                                                   # the rim scales with the tyre
    y = side * track
    # tyre: a rounded rubber profile spun around the axle (tread, shoulders, sidewalls down to the rim)
    prof = []
    ri, hw2, rc = 0.228 * k_, Wd / 2, 0.06 * k_
    prof.append((ri, -hw2 + 0.02))
    for k in range(7):
        a = k / 6 * math.pi / 2
        prof.append((R - rc + rc * math.sin(a), -hw2 + rc - rc * math.cos(a)))
    for k in range(7):
        a = k / 6 * math.pi / 2
        prof.append((R - rc + rc * math.cos(a), hw2 - rc + rc * math.sin(a)))
    prof.append((ri, hw2 - 0.02))
    segs = 72
    bm = bmesh.new()
    rv = []
    for k in range(segs):
        th = k / segs * 2 * math.pi
        rv.append([bm.verts.new((x + r * math.sin(th), y + side * yy, R + r * math.cos(th))) for (r, yy) in prof])
    for k in range(segs):
        a, b = rv[k], rv[(k + 1) % segs]
        for j in range(len(prof) - 1):
            bm.faces.new((a[j], a[j + 1], b[j + 1], b[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh_obj('Tyre', bm, [mats['tyre']], parent=root, smooth=True)

    bm = bmesh.new()
    rr, face = 0.235 * k_, y + side * (Wd / 2 - 0.018)

    def axle_cyl(radius, depth, yc, seg, mi, caps=True, r2=None):
        ret = bmesh.ops.create_cone(bm, cap_ends=caps, segments=seg, radius1=radius, radius2=r2 if r2 else radius, depth=depth)
        bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(-side * math.pi / 2, 3, 'X'))
        bmesh.ops.translate(bm, verts=ret['verts'], vec=(x, yc, R))
        for f in {f for v in ret['verts'] for f in v.link_faces}:
            f.material_index = mi

    axle_cyl(rr, 0.2, y + side * 0.01, 48, 2, caps=False)          # inner barrel (open)
    axle_cyl(rr + 0.012, 0.03, face, 48, 0, caps=False)             # polished lip
    axle_cyl(0.17 * k_, 0.025, face - side * 0.07, 40, 3)           # brake disc
    axle_cyl(0.075 * k_, 0.05, face - side * 0.02, 24, 0)           # hub
    axle_cyl(0.045 * k_, 0.02, face + side * 0.008, 20, 1)          # centre cap
    for k in range(5):                                              # lug nuts
        a = k * 2 * math.pi / 5 + math.pi / 5
        ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.011, radius2=0.011, depth=0.02)
        bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'))
        bmesh.ops.translate(bm, verts=ret['verts'], vec=(x + 0.05 * k_ * math.sin(a), face + side * 0.006, R + 0.05 * k_ * math.cos(a)))
        for f in {f for v in ret['verts'] for f in v.link_faces}:
            f.material_index = 1
    # tapered, slightly dished spokes: narrow at the hub, wide at the rim (thinner when there are more)
    wsc = 5.0 / spokes
    for k in range(spokes):
        a = k * 2 * math.pi / spokes
        ca, sa = math.cos(a), math.sin(a)
        quad = []
        for dz in (-0.018, 0.018):                                   # spoke thickness along the axle
            ring = []
            for (rad, half, dish) in ((0.065 * k_, 0.022 * wsc, 0.0), (rr - 0.004, 0.052 * wsc, 0.028)):
                for sgn in (-1, 1):
                    u, v = sgn * half, rad                           # u across the spoke, v along it
                    px, pz = u * ca + v * sa, -u * sa + v * ca
                    ring.append((x + px, face - side * (0.012 + dish) + side * dz, R + pz))
            quad.append(ring)
        b0, t0 = quad[0], quad[1]
        # box order: bottom 0..3 (hub-, hub+, rim+, rim-), top 4..7
        bm_hex(bm, [b0[0], b0[1], b0[3], b0[2], t0[0], t0[1], t0[3], t0[2]], 0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm_box(bm, x + 0.08 * k_, face - side * 0.1, R + 0.06 * k_, x + 0.2 * k_, face - side * 0.04, R + 0.17 * k_, 4)   # caliper
    mesh_obj('Rim', bm, [mats['chrome'], mats['dark'], mats['barrel'], mats['disc'], mats['caliper']], parent=root, bevel=0.003)


def smooth_polyline(pts, iters=3):
    """Chaikin corner cutting, keeping the end points."""
    for _ in range(iters):
        out = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        out.append(pts[-1])
        pts = out
    return pts


def resample(pts, n):
    d = [0.0]
    for a, b in zip(pts[:-1], pts[1:]):
        d.append(d[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    out = []
    for k in range(n):
        s = d[-1] * k / (n - 1)
        i = max(0, min(len(pts) - 2, next((j for j in range(len(d) - 1) if d[j + 1] >= s), len(d) - 2)))
        t = 0 if d[i + 1] == d[i] else (s - d[i]) / (d[i + 1] - d[i])
        out.append((lerp(pts[i][0], pts[i + 1][0], t), lerp(pts[i][1], pts[i + 1][1], t)))
    return out


# greenhouse (cabin) shape: window line at the belt, roof on top, raked windscreen and rear window
SEDAN_GH = dict(xr0=-1.45, xr1=-0.72, xf1=0.12, xf0=0.92, zb=0.86, roof=1.43, wb=0.84, wt=0.66)
GH = dict(SEDAN_GH)


def set_gh(shape):
    """Select the cabin (greenhouse) shape of the car being built."""
    GH.clear()
    GH.update(shape)


def gh_zb(x):
    """Height of the window line: constant, or rising toward the rear when zb_rear is given."""
    g = GH
    if 'zb_rear' not in g:
        return g['zb']
    t = max(0.0, min(1.0, (g['xf0'] - x) / (g['xf0'] - g['xr0'])))
    return g['zb'] + (g['zb_rear'] - g['zb']) * t
GH_PROFILE = resample(smooth_polyline([(-1.0, 0.0), (-0.93, 0.6), (-0.8, 0.95), (-0.55, 1.0), (0.55, 1.0), (0.8, 0.95), (0.93, 0.6), (1.0, 0.0)]), 41)


def gh_top(x):
    g = GH
    if x <= g['xr0'] or x >= g['xf0']:
        return 0.0
    if x < g['xr1']:
        t = (x - g['xr0']) / (g['xr1'] - g['xr0'])
        return math.sin(t * math.pi / 2) ** 1.15
    if x > g['xf1']:
        t = (x - g['xf1']) / (g['xf0'] - g['xf1'])
        return math.cos(t * math.pi / 2) ** 0.95
    return 1.0


def gh_point(x, v):
    """Point on the greenhouse at length position x and around-profile position v (0 = left belt, 1 = right belt)."""
    g = GH
    k = v * (len(GH_PROFILE) - 1)
    i = min(int(k), len(GH_PROFILE) - 2)
    t = k - i
    py = lerp(GH_PROFILE[i][0], GH_PROFILE[i + 1][0], t)
    pz = lerp(GH_PROFILE[i][1], GH_PROFILE[i + 1][1], t)
    zb = gh_zb(x)
    roof = g['roof']
    if 'roof_rear' in g:                                  # the roof line falls toward the tailgate
        t = max(0.0, min(1.0, (g['xf1'] - x) / (g['xf1'] - g['xr1'])))
        roof = g['roof'] + (g['roof_rear'] - g['roof']) * t ** 1.4
    h = max(0.012, (roof - zb) * gh_top(x))
    w = lerp(g['wb'], g['wt'], pz) * (1.0 - g.get('end_taper', 0.0) * (1.0 - gh_top(x)))
    return Vector((x, py * w, zb + pz * h))


def faceted_chrome(name, color='#e6e9ee'):
    """Lamp reflector: chrome with small facets (Voronoi bump), so it sparkles like a real reflector."""
    m, nt, b = new_mat(name)
    b.inputs['Base Color'].default_value = (*srgb(color), 1)
    b.inputs['Metallic'].default_value = 1.0
    b.inputs['Roughness'].default_value = 0.07
    N, L = nt.nodes, nt.links
    tc = N.new('ShaderNodeTexCoord')
    vor = N.new('ShaderNodeTexVoronoi')
    vor.inputs['Scale'].default_value = 320.0
    L.new(tc.outputs['Object'], vor.inputs['Vector'])
    bp = N.new('ShaderNodeBump')
    bp.inputs['Strength'].default_value = 0.3
    bp.inputs['Distance'].default_value = 0.001
    L.new(vor.outputs['Distance'], bp.inputs['Height'])
    L.new(bp.outputs['Normal'], b.inputs['Normal'])
    return m


def two_sided_paint(name, color, inside):
    """Car paint outside, roof lining (headliner) on the inside of the cabin shell."""
    m, nt, b = new_mat(name)
    b.inputs['Base Color'].default_value = (*srgb(color), 1)
    b.inputs['Roughness'].default_value = 0.3
    b.inputs['Metallic'].default_value = 0.55
    b.inputs['Coat Weight'].default_value = 1.0
    b.inputs['Coat Roughness'].default_value = 0.02
    N, L = nt.nodes, nt.links
    inner = N.new('ShaderNodeBsdfPrincipled')
    inner.inputs['Base Color'].default_value = (*srgb(inside), 1)
    inner.inputs['Roughness'].default_value = 0.9
    geo = N.new('ShaderNodeNewGeometry')
    mix = N.new('ShaderNodeMixShader')
    L.new(geo.outputs['Backfacing'], mix.inputs[0])
    L.new(b.outputs[0], mix.inputs[1])
    L.new(inner.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], N['Material Output'].inputs['Surface'])
    return m


def thin(ob, t=0.004):
    """Give a single-surface glass skin a real thickness, so light goes in and out (no refraction offset)."""
    if ob is None:
        return None
    md = ob.modifiers.new('Thickness', 'SOLIDIFY')
    md.thickness = t
    md.offset = -1.0
    return ob


def soft(name, bm, mats, root, width=0.03, seg=4):
    """Upholstery and trim: rounded edges and smooth shading."""
    ob = mesh_obj(name, bm, mats, parent=root, smooth=True)
    md = ob.modifiers.new('Round', 'BEVEL')
    md.width = width
    md.segments = seg
    md.limit_method = 'ANGLE'
    md.harden_normals = True
    return ob


def gh_normal(x, v):
    """Outward surface normal of the glasshouse at (x, v), and the tangent along x."""
    e = 1e-3
    du = gh_point(min(x + e, GH['xf0'] - 1e-4), v) - gh_point(max(x - e, GH['xr0'] + 1e-4), v)
    dv = gh_point(x, min(v + e, 1)) - gh_point(x, max(v - e, 0))
    n = du.cross(dv)
    if n.length < 1e-9:
        return Vector((0, 0, 1)), du.normalized()
    n.normalize()
    p = gh_point(x, v)
    if n.dot(Vector((0, p.y, p.z - gh_zb(x) + 0.3))) < 0:
        n = -n
    return n, du.normalized()


def wipers(name, root, mat, blades):
    """Wiper blades parked on the glass: each one follows the glass surface along (x, v0..v1)."""
    bm = bmesh.new()
    for (x, v0, v1) in blades:
        rings = []
        for i in range(17):
            v = lerp(v0, v1, i / 16)
            p = gh_point(x, v)
            n, t = gh_normal(x, v)
            rings.append([tuple(p + n * a + t * b) for (a, b) in ((0.004, -0.01), (0.004, 0.01), (0.018, 0.008), (0.018, -0.008))])
        vr = [[bm.verts.new(q) for q in r] for r in rings]
        for k in range(len(vr) - 1):
            for j in range(4):
                bm.faces.new((vr[k][j], vr[k][(j + 1) % 4], vr[k + 1][(j + 1) % 4], vr[k + 1][j]))
        bm.faces.new(list(reversed(vr[0])))
        bm.faces.new(vr[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if mat is None:                                   # matte black rubber: no shiny line in low sun
        mat = principled(name + '_Rubber', srgb('#0a0a0b'), rough=0.85, spec=0.2)
    return mesh_obj(name, bm, [mat], parent=root)


def gh_surface(name, mat, root, x0, x1, v0, v1, nx, nv, offset=0.0):
    """A patch of the greenhouse surface (offset outward), used for the cabin shell and its glass."""
    bm = bmesh.new()
    grid = []
    for i in range(nx + 1):
        x = lerp(x0, x1, i / nx)
        row = []
        for j in range(nv + 1):
            v = lerp(v0, v1, j / nv)
            p = gh_point(x, v)
            if offset:
                e = 1e-3
                du = gh_point(min(x + e, GH['xf0'] - 1e-4), v) - gh_point(max(x - e, GH['xr0'] + 1e-4), v)
                dv = gh_point(x, min(v + e, 1)) - gh_point(x, max(v - e, 0))
                n = du.cross(dv)
                if n.length > 1e-9:
                    n.normalize()
                    if n.dot(Vector((0, p.y, p.z - GH['zb'] + 0.3))) < 0:
                        n = -n
                    p = p + n * offset
            row.append(bm.verts.new(p))
        grid.append(row)
    for i in range(nx):
        for j in range(nv):
            bm.faces.new((grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))
    bm.normal_update()
    inward = []
    for f in bm.faces:
        c = f.calc_center_median()
        if f.normal.dot(c - Vector((c.x, 0.0, GH['zb'] + 0.05))) < 0:
            inward.append(f)
    if inward:
        bmesh.ops.reverse_faces(bm, faces=inward)
    return mesh_obj(name, bm, [mat], parent=root, smooth=True)


def gh_shell(name, mats, root, holes, nx=160, nv=40, mx=0.03, mv=0.015, black_x=()):
    """The greenhouse surface with rectangular (x, v) openings. Grid lines fall exactly on the opening edges,
    and a band around each opening gets the second material (the black window seal)."""
    g = GH
    xs = [lerp(g['xr0'], g['xf0'], i / nx) for i in range(nx + 1)]
    vs = [j / nv for j in range(nv + 1)]
    for (a, b) in black_x:
        xs += [a, b]
    for h in holes:
        xs += [h[0], h[1], h[0] - mx, h[1] + mx]
        vs += [h[2], h[3], max(0.0, h[2] - mv), min(1.0, h[3] + mv)]
    xs = sorted(set(round(x, 6) for x in xs if g['xr0'] <= x <= g['xf0']))
    vs = sorted(set(round(v, 6) for v in vs))
    bm = bmesh.new()
    grid = [[bm.verts.new(gh_point(x, v)) for v in vs] for x in xs]
    for i in range(len(xs) - 1):
        for j in range(len(vs) - 1):
            xc, vc = (xs[i] + xs[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2
            if any(h[0] < xc < h[1] and h[2] < vc < h[3] for h in holes):
                continue
            f = bm.faces.new((grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))
            if any(h[0] - mx < xc < h[1] + mx and h[2] - mv < vc < h[3] + mv for h in holes):
                f.material_index = 1
            elif (vc < 0.205 or vc > 0.795) and any(a < xc < b for (a, b) in black_x):
                f.material_index = 1                                  # blacked-out pillar
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
    bm.normal_update()
    inward = []
    for f in bm.faces:
        c = f.calc_center_median()
        if f.normal.dot(c - Vector((c.x, 0.0, GH['zb'] + 0.05))) < 0:
            inward.append(f)
    if inward:
        bmesh.ops.reverse_faces(bm, faces=inward)
    return mesh_obj(name, bm, mats, parent=root, smooth=True)


def decal(name, body, mat, root, origin_fn, direction, nu, nv, offset=0.004, mask=None):
    """Project a grid of rays onto the evaluated body and build a thin skin that sits exactly on it.
    origin_fn(u, v) -> ray origin (body space) for u, v in [0, 1]; direction is a vector, or a function
    (u, v) -> vector so a part can wrap around a corner."""
    dg = bpy.context.evaluated_depsgraph_get()
    be = body.evaluated_get(dg)
    pts = {}
    dsum = Vector((0, 0, 0))
    for i in range(nu + 1):
        for j in range(nv + 1):
            u, v = i / nu, j / nv
            if mask and not mask(u, v):
                continue
            d = Vector(direction(u, v) if callable(direction) else direction).normalized()
            o = Vector(origin_fn(u, v))
            hit, loc, nor, _ = be.ray_cast(o, d, distance=10.0)
            if hit:
                pts[(i, j)] = loc - d * offset
                dsum += d
    bm = bmesh.new()
    vs = {k: bm.verts.new(p) for k, p in pts.items()}
    for i in range(nu):
        for j in range(nv):
            q = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            if all(k in vs for k in q):
                bm.faces.new([vs[k] for k in q])
    if not bm.faces:
        bm.free()
        return None
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if sum(f.normal.dot(dsum) for f in bm.faces) > 0:   # face the viewer, against the rays
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    return mesh_obj(name, bm, [mat], parent=root, smooth=True)


def side_seam(name, body, root, mat, s, pts, width=0.007, off=0.002):
    """A door shut line on the body side (side s) along a path of (x, z) points."""
    segs = list(zip(pts[:-1], pts[1:]))
    lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs]
    total = sum(lens)

    def at(u):
        d = u * total
        for i, ((a, b), L) in enumerate(zip(segs, lens)):
            if d <= L or i == len(segs) - 1:
                t = 0.0 if L == 0 else min(1.0, d / L)
                return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, (b[0] - a[0]) / L, (b[1] - a[1]) / L
            d -= L

    def origin(u, v):
        px, pz, tx, tz = at(u)
        w = (v - 0.5) * width
        return (px - tz * w, s * 3.0, pz + tx * w)
    return decal(name, body, mat, root, origin, (0, -s, 0), max(2, int(total / 0.015)), 1, off)


def arch_path(cx, cz, r, a0, a1, n=8):
    """Points on a circle around a wheel arch, from angle a0 to a1 (degrees)."""
    return [(cx + r * math.cos(math.radians(lerp(a0, a1, k / n))), cz + r * math.sin(math.radians(lerp(a0, a1, k / n)))) for k in range(n + 1)]


def door_handle(name, body, root, s, hx, hz, chrome, recess):
    """A pull handle standing off the door in a dark recess."""
    decal(name + 'Recess', body, recess, root,
          lambda u, v: (hx + 0.075 * v * math.cos(u * 2 * math.pi), s * 3.0, hz + 0.024 * v * math.sin(u * 2 * math.pi)),
          (0, -s, 0), 32, 3, 0.0015)
    p, n = body_hit(body, (hx, s * 3.0, hz), (0, -s, 0))
    if p is None:
        return
    rings = []
    for k in range(11):
        t = k / 10
        sc = max(0.25, math.sin(math.pi * t) ** 0.45)               # rounded ends
        x = hx - 0.085 + 0.17 * t
        rings.append(superring(x, 0.011 * sc, hz - 0.015 * sc, hz + 0.015 * sc, 12, 3.0))
    rings = [[(q[0], q[1] + p.y + s * 0.017, q[2]) for q in r] for r in rings]
    ho = mesh_obj(name, loft(rings), [chrome], parent=root)
    subsurf(ho, 1)


def body_hit(body, origin, direction):
    dg = bpy.context.evaluated_depsgraph_get()
    hit, loc, nor, _ = body.evaluated_get(dg).ray_cast(Vector(origin), Vector(direction).normalized(), distance=10.0)
    return (loc, nor) if hit else (None, None)


def car(name, color, loc, rot_z):
    set_gh(SEDAN_GH)
    root = empty(name, loc, rot_z)
    paint = principled(name + '_Paint', srgb(color), rough=0.3, metal=0.55, coat=1.0, coat_rough=0.02)
    mats = {
        'tyre': textured(name + '_Tyre', 'plaster', srgb('#161616'), srgb('#1f1f1f'), rough=0.85, bump=0.2),
        'chrome': principled(name + '_Chrome', srgb('#d9dde2'), rough=0.08, metal=1.0),
        'dark': principled(name + '_Trim', srgb('#0d0d0f'), rough=0.45),
        'barrel': principled(name + '_Barrel', srgb('#4a4d52'), rough=0.4, metal=0.9),
        'disc': principled(name + '_Disc', srgb('#5d5f63'), rough=0.3, metal=1.0),
        'caliper': principled(name + '_Caliper', srgb('#b0201c'), rough=0.35),
    }
    def clear_glass(nm, tint, rough=0.0):
        m = principled(nm, srgb(tint), rough=rough, ior=1.5, spec=1.0)
        m.node_tree.nodes['Principled BSDF'].inputs['Transmission Weight'].default_value = 1.0
        return m
    glass_front = clear_glass(name + '_GlassFront', '#b7c1c2')     # windscreen and front side windows: light tint
    glass_rear = clear_glass(name + '_GlassRear', '#7b8587')       # rear side windows and rear window: darker
    cover = clear_glass(name + '_LampCover', '#f4f6f8')            # clear polycarbonate lamp cover
    red_glass = clear_glass(name + '_RedGlass', '#c4170e', 0.015)  # tail-light lens
    amber_glass = clear_glass(name + '_AmberGlass', '#ff8a1c', 0.05)
    lamp_black = principled(name + '_LampBlack', srgb('#0c0d0f'), rough=0.08, spec=1.0)      # gloss-black housing
    reflector = faceted_chrome(name + '_Reflector')                                         # faceted chrome
    smooth_chrome = principled(name + '_LampChrome', srgb('#e6e9ee'), rough=0.05, metal=1.0)
    light_guide = principled(name + '_LightGuide', srgb('#dcdfe3'), rough=0.35, spec=0.6)   # unlit frosted strip
    tail_dark = principled(name + '_TailDark', srgb('#3a0604'), rough=0.2, spec=0.8)         # smoked lower chamber
    seal = principled(name + '_WindowSeal', srgb('#0b0b0c'), rough=0.3)
    shell_mat = two_sided_paint(name + '_CabinPaint', color, '#bdb8b0')                     # paint out, headliner in
    leather = textured(name + '_Leather', 'plaster', srgb('#a88a68'), srgb('#b99c79'), rough=0.5, bump=0.12)
    door_trim = principled(name + '_DoorTrim', srgb('#2d2b29'), rough=0.6)
    dash = principled(name + '_Dash', srgb('#1a1a1c'), rough=0.5)
    screen = principled(name + '_Screen', srgb('#030405'), rough=0.02, spec=1.0)
    liner_mat = textured(name + '_Liner', 'plaster', srgb('#1c1c1e'), srgb('#26262a'), rough=0.8, bump=0.6)
    grille = principled(name + '_Grille', srgb('#0b0b0c'), rough=0.25, metal=0.3)
    seam = principled(name + '_Seam', srgb('#050505'), rough=0.8)
    plate = principled(name + '_Plate', srgb('#e9e7df'), rough=0.4)
    L, HW = 4.72, 0.93
    # ---- body: lofted cross-sections, subdivided; wheel arches cut with booleans
    rings = []
    N = 21
    for k in range(N):
        t = k / (N - 1)                       # 0 = rear, 1 = front
        x = -L / 2 + t * L
        end = max(0.0, (abs(2 * t - 1) - 0.7) / 0.3)
        hw = HW * (1 - 0.12 * end ** 1.8)
        zb = 0.27 + 0.09 * end ** 1.5
        if t > 0.62:
            zt = lerp(0.97, 0.79, ((t - 0.62) / 0.38) ** 1.5)
        elif t < 0.2:
            zt = lerp(0.985, 0.9, ((0.2 - t) / 0.2) ** 1.8)
        else:
            zt = 0.985
        rings.append(superring(x, hw, zb, zt, 20, 5.5))
    body = mesh_obj(name + '_Body', loft(rings), [paint, liner_mat, door_trim], parent=root)
    subsurf(body, 3)
    for wx in (1.42, -1.4):
        cbm = bmesh.new()
        ret = bmesh.ops.create_cone(cbm, cap_ends=True, segments=64, radius1=0.37, radius2=0.37, depth=2.6)
        bmesh.ops.rotate(cbm, verts=ret['verts'], cent=(0, 0, 0), matrix=__import__('mathutils').Matrix.Rotation(math.pi / 2, 3, 'X'))
        bmesh.ops.translate(cbm, verts=ret['verts'], vec=(wx, 0, 0.345))
        cut = mesh_obj(name + '_ArchCut', cbm, [liner_mat], parent=root)
        cut.hide_render = True
        cut.hide_viewport = True
        bo = body.modifiers.new('Arch', 'BOOLEAN')
        bo.operation = 'DIFFERENCE'
        bo.object = cut
        try:
            bo.solver = os.environ.get('SB_BOOL', 'MANIFOLD')
            bo.material_mode = 'TRANSFER'
        except Exception:
            pass
    # the cabin: hollow the body under the greenhouse, which leaves a floor and door panels inside
    tbm = bmesh.new()
    bm_box(tbm, -1.22, -0.74, 0.73, 0.7, 0.74, 1.3, 0)
    tub = mesh_obj(name + '_CabinCut', tbm, [door_trim], parent=root)
    tub.hide_render = True
    tub.hide_viewport = True
    bo = body.modifiers.new('Cabin', 'BOOLEAN')
    bo.operation = 'DIFFERENCE'
    bo.object = tub
    try:
        bo.solver = os.environ.get('SB_BOOL', 'MANIFOLD')
        bo.material_mode = 'TRANSFER'
    except Exception:
        pass
    # keep the paint's smooth shading from bleeding into the cut arch surfaces (that showed as steps)
    es = body.modifiers.new('ArchEdges', 'EDGE_SPLIT')
    es.split_angle = math.radians(40)
    es.use_edge_sharp = False
    lbm = bmesh.new()
    for wx in (1.42, -1.4):
        for s in (1, -1):
            ret = bmesh.ops.create_cone(lbm, cap_ends=True, segments=48, radius1=0.36, radius2=0.36, depth=0.5)
            bmesh.ops.rotate(lbm, verts=ret['verts'], cent=(0, 0, 0), matrix=__import__('mathutils').Matrix.Rotation(math.pi / 2, 3, 'X'))
            bmesh.ops.translate(lbm, verts=ret['verts'], vec=(wx, s * 0.5, 0.345))
    # only the arch above the wheel centre: the lower half would stick out under the car
    bmesh.ops.delete(lbm, geom=[v for v in lbm.verts if v.co.z < 0.3], context='VERTS')
    mesh_obj(name + '_Liner', lbm, [liner_mat], parent=root)
    bpy.context.view_layer.update()

    # ---- cabin: a painted shell (roof and pillars, headliner inside) with real window openings,
    # black seals around them, thin clear glass and a full interior
    from mathutils import Matrix
    g = GH
    # windscreen and rear window sit between the roof rails; the side windows stop below the rails, with
    # A-, B- and C-pillars between the openings
    windows = [(g['xf1'] + 0.06, g['xf0'] - 0.07, 0.24, 0.76, glass_front), (g['xr0'] + 0.1, g['xr1'] - 0.06, 0.26, 0.74, glass_rear)]
    for (v0, v1) in ((0.035, 0.185), (0.815, 0.965)):
        windows += [(-0.2, g['xf0'] - 0.2, v0, v1, glass_front), (g['xr0'] + 0.3, -0.36, v0, v1, glass_rear)]
    gh_shell(name + '_Cabin', [shell_mat, seal], root, [w[:4] for w in windows])
    for k, (x0, x1, v0, v1, gm) in enumerate(windows):
        thin(gh_surface(name + '_Window%d' % k, gm, root, x0, x1, v0, v1, 160, 24), 0.005)
    # seats: cushion, shaped backrest with side bolsters, headrest on two posts (light leather)
    for sy in (0.38, -0.38):                                          # driver (left) and passenger
        sb = bmesh.new()
        bm_hex(sb, [(-0.32, sy - 0.25, 0.73), (0.14, sy - 0.25, 0.73), (0.14, sy + 0.25, 0.73), (-0.32, sy + 0.25, 0.73),
                    (-0.32, sy - 0.25, 0.82), (0.14, sy - 0.25, 0.85), (0.14, sy + 0.25, 0.85), (-0.32, sy + 0.25, 0.82)], 0)
        bm_hex(sb, [(-0.37, sy - 0.24, 0.8), (-0.24, sy - 0.24, 0.8), (-0.24, sy + 0.24, 0.8), (-0.37, sy + 0.24, 0.8),
                    (-0.5, sy - 0.22, 1.25), (-0.4, sy - 0.22, 1.25), (-0.4, sy + 0.22, 1.25), (-0.5, sy + 0.22, 1.25)], 0)
        for side in (-1, 1):                                          # bolsters
            yb = sy + side * 0.22
            bm_hex(sb, [(-0.36, yb - 0.035, 0.84), (-0.2, yb - 0.035, 0.84), (-0.2, yb + 0.035, 0.84), (-0.36, yb - 0.035 + 0.07, 0.84),
                        (-0.47, yb - 0.035, 1.17), (-0.36, yb - 0.035, 1.17), (-0.36, yb + 0.035, 1.17), (-0.47, yb + 0.035, 1.17)], 0)
        bm_box(sb, -0.5, sy - 0.13, 1.29, -0.41, sy + 0.13, 1.38, 0)     # headrest
        soft(name + '_Seat', sb, [leather], root, 0.03, 4)
        pb = bmesh.new()
        for py in (sy - 0.07, sy + 0.07):
            bm_cyl(pb, -0.455, py, 1.23, 1.3, 0.008, 10, 0)
        mesh_obj(name + '_HeadrestPosts', pb, [smooth_chrome], parent=root, smooth=True)
    rb = bmesh.new()                                                  # rear bench
    bm_hex(rb, [(-1.18, -0.64, 0.73), (-0.78, -0.64, 0.73), (-0.78, 0.64, 0.73), (-1.18, 0.64, 0.73),
                (-1.18, -0.64, 0.82), (-0.78, -0.64, 0.84), (-0.78, 0.64, 0.84), (-1.18, 0.64, 0.82)], 0)
    bm_hex(rb, [(-1.21, -0.64, 0.8), (-1.08, -0.64, 0.8), (-1.08, 0.64, 0.8), (-1.21, 0.64, 0.8),
                (-1.21, -0.6, 1.08), (-1.13, -0.6, 1.08), (-1.13, 0.6, 1.08), (-1.21, 0.6, 1.08)], 0)
    soft(name + '_RearBench', rb, [leather], root, 0.03, 4)
    # dashboard with an instrument hood, a centre screen and air vents; centre console and gear lever;
    # armrests on the door panels; rear-view mirror
    db = bmesh.new()
    bm_hex(db, [(0.36, -0.66, 0.8), (0.7, -0.66, 0.8), (0.7, 0.66, 0.8), (0.36, -0.66 + 1.32, 0.8),
                (0.36, -0.66, 1.05), (0.7, -0.66, 0.98), (0.7, 0.66, 0.98), (0.36, 0.66, 1.05)], 0)
    bm_box(db, 0.37, 0.2, 1.03, 0.49, 0.56, 1.11, 0)                    # instrument hood
    bm_box(db, -0.3, -0.11, 0.73, 0.38, 0.11, 0.88, 0)                 # centre console
    for ay in (0.7, -0.7):                                            # armrests
        bm_box(db, -0.5, ay - 0.05 if ay > 0 else ay, 0.86, 0.3, ay if ay > 0 else ay + 0.05, 0.9, 0)
    soft(name + '_Dashboard', db, [dash], root, 0.02, 3)
    sc = bmesh.new()
    bm_box(sc, 0.345, -0.14, 0.95, 0.36, 0.14, 1.07, 0)                # screen
    for vy in (-0.5, -0.24, 0.24, 0.5):                               # vents
        bm_box(sc, 0.352, vy - 0.06, 0.975, 0.362, vy + 0.06, 0.99, 1)
    bm_cyl(sc, 0.12, 0.0, 0.88, 0.97, 0.012, 12, 1)                     # gear lever
    ret = bmesh.ops.create_uvsphere(sc, u_segments=16, v_segments=8, radius=0.03)
    bmesh.ops.translate(sc, verts=ret['verts'], vec=(0.12, 0.0, 0.99))
    bm_box(sc, 0.2, -0.12, 1.31, 0.23, 0.12, 1.37, 2)                  # rear-view mirror
    bm_box(sc, 0.21, -0.015, 1.36, 0.22, 0.015, 1.42, 2)
    mesh_obj(name + '_Controls', sc, [screen, smooth_chrome, dash], parent=root, smooth=True, bevel=0.003)
    # steering wheel: leather rim, three spokes, airbag hub and the column into the dashboard
    swb = bmesh.new()
    rim = []
    for k in range(48):
        th = k / 48 * 2 * math.pi
        ring = []
        for j in range(12):
            ph = j / 12 * 2 * math.pi
            r = 0.18 + 0.02 * math.sin(ph)
            ring.append(swb.verts.new((0.02 * math.cos(ph), r * math.cos(th), r * math.sin(th))))
        rim.append(ring)
    for k in range(48):
        a, b = rim[k], rim[(k + 1) % 48]
        for j in range(12):
            swb.faces.new((a[j], a[(j + 1) % 12], b[(j + 1) % 12], b[j])).material_index = 0
    for ang in (0.0, math.pi, -math.pi / 2):                          # spokes: left, right, down
        sp = bmesh.ops.create_cube(swb, size=1.0)
        bmesh.ops.scale(swb, verts=sp['verts'], vec=(0.022, 0.045, 0.12))
        bmesh.ops.translate(swb, verts=sp['verts'], vec=(0, 0, 0.11))
        bmesh.ops.rotate(swb, verts=sp['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(ang + math.pi / 2, 3, 'X'))
        for f in {f for v in sp['verts'] for f in v.link_faces}:
            f.material_index = 1
    ret = bmesh.ops.create_cone(swb, cap_ends=True, segments=24, radius1=0.07, radius2=0.06, depth=0.06)
    bmesh.ops.rotate(swb, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
    bmesh.ops.translate(swb, verts=ret['verts'], vec=(0.02, 0, 0))
    ret2 = bmesh.ops.create_cone(swb, cap_ends=True, segments=16, radius1=0.035, radius2=0.035, depth=0.16)
    bmesh.ops.rotate(swb, verts=ret2['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
    bmesh.ops.translate(swb, verts=ret2['verts'], vec=(0.1, 0, 0))
    bmesh.ops.recalc_face_normals(swb, faces=swb.faces)
    bmesh.ops.rotate(swb, verts=swb.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(-22), 3, 'Y'))
    bmesh.ops.translate(swb, verts=swb.verts, vec=(0.24, 0.38, 1.0))
    mesh_obj(name + '_SteeringWheel', swb, [dash, smooth_chrome], parent=root, smooth=True)
    # wipers parked on the glass at the base of the windscreen
    wipers(name + '_Wipers', root, None, [(g['xf0'] - 0.12, 0.28, 0.56), (g['xf0'] - 0.12, 0.5, 0.74)])

    # ---- parts projected onto the real body surface
    S = name + '_'
    for s in (1, -1):
        # ---- headlight (unlit), modelled on a modern swept lamp: a silver frame, a gloss-black housing,
        # a faceted chrome reflector with a clear projector lens in a chrome bowl, an amber indicator at the
        # inner tip and a frosted light guide along the top edge, all under a clear glass cover. It wraps
        # the front corner and stays clear of the grille and the number plate.
        hd = (-1.0, -s * 0.35, 0.0)

        def head_pt(u, v, grow=0.0, s=s):
            u = -grow + u * (1 + 2 * grow)
            v = -grow * 2 + v * (1 + 4 * grow)
            uu = min(1.0, max(0.0, u))
            h = 0.03 + 0.09 * math.sin(math.pi * min(1.0, uu * 1.12)) ** 0.7
            x = 2.36 - 0.32 * uu ** 2
            y = s * (0.5 + 0.42 * u)
            z = 0.6 + 0.05 * uu ** 1.6 + v * h
            return (x + 3.0, y + s * 3.0 * 0.35, z)          # start 3 m back along the ray
        decal(S + 'HeadFrame', body, smooth_chrome, root, lambda u, v: head_pt(u, v, 0.03), hd, 64, 14, 0.003)
        decal(S + 'HeadHousing', body, lamp_black, root, head_pt, hd, 64, 14, 0.006)
        decal(S + 'HeadReflector', body, reflector, root, lambda u, v: head_pt(0.4 + u * 0.5, 0.1 + v * 0.66), hd, 36, 12, 0.009)
        decal(S + 'HeadGuide', body, light_guide, root, lambda u, v: head_pt(0.04 + u * 0.92, 0.8 + v * 0.14), hd, 64, 3, 0.011)
        thin(decal(S + 'Indicator', body, amber_glass, root, lambda u, v: head_pt(0.06 + u * 0.24, 0.18 + v * 0.5), hd, 16, 6, 0.013), 0.002)
        p, n = body_hit(body, head_pt(0.65, 0.42), hd)
        if p is not None:
            dn = Vector(hd).normalized()
            rot = (-dn).to_track_quat('Z', 'Y').to_matrix().to_4x4()
            pbm = bmesh.new()
            ret = bmesh.ops.create_cone(pbm, cap_ends=True, segments=40, radius1=0.05, radius2=0.034, depth=0.03)   # chrome bowl, closed
            bmesh.ops.transform(pbm, verts=ret['verts'], matrix=rot)
            bmesh.ops.translate(pbm, verts=ret['verts'], vec=p - dn * 0.024)
            mesh_obj(S + 'ProjBowl', pbm, [smooth_chrome], parent=root, smooth=True)
            lbm2 = bmesh.new()                                         # clear glass projector lens
            ret = bmesh.ops.create_uvsphere(lbm2, u_segments=32, v_segments=16, radius=0.032)
            bmesh.ops.scale(lbm2, verts=ret['verts'], vec=(1, 1, 0.55))
            bmesh.ops.transform(lbm2, verts=ret['verts'], matrix=rot)
            bmesh.ops.translate(lbm2, verts=ret['verts'], vec=p - dn * 0.022)
            mesh_obj(S + 'ProjLens', lbm2, [cover], parent=root, smooth=True)
        thin(decal(S + 'HeadCover', body, cover, root, lambda u, v: head_pt(u, v, 0.02), hd, 64, 14, 0.04), 0.003)
        # ---- tail light (unlit), wrapping around the rear corner onto the side: gloss-black frame, faceted
        # chrome reflector, a smoked lower chamber with a chrome divider, deep-red glass, and a clear-glass
        # reverse lamp at the inner end
        def tail_dir(u, v, s=s):
            w = max(0.0, min(1.0, (u - 0.62) / 0.38)) ** 1.2
            return (1.0 - w, -s * (0.25 + 0.75 * w), 0.0)

        def tail_pt(u, v, grow=0.0, s=s):
            # the aim point is inside the body (rear face at x = -2.36, side at |y| = 0.93), so every ray
            # meets the surface right at the lamp and none can skim past the corner along the side
            u = -grow + u * (1 + 2 * grow)
            v = -grow * 2 + v * (1 + 4 * grow)
            uu = min(1.0, max(0.0, u))
            w = max(0.0, (uu - 0.62) / 0.38)
            x = -2.25 + 0.32 * w ** 1.3
            y = s * (0.36 + 0.46 * min(1.0, uu / 0.62))
            z = 0.675 + v * (0.12 - 0.03 * w) + 0.01 * w
            d = Vector(tail_dir(u, v)).normalized()
            return tuple(Vector((x, y, z)) - d * 3.0)
        def tail_part(nm, mat, u0, u1, v0, v1, nu, nv, off, grow=0.0):
            # the aim point and the ray direction come from the same lamp coordinate (u, v)
            return decal(S + nm, body, mat, root,
                         lambda u, v: tail_pt(u0 + u * (u1 - u0), v0 + v * (v1 - v0), grow),
                         lambda u, v: tail_dir(u0 + u * (u1 - u0), v0 + v * (v1 - v0)), nu, nv, off)
        tail_part('TailFrame', lamp_black, 0.0, 1.0, 0.0, 1.0, 72, 12, 0.003, 0.025)
        tail_part('TailReflector', reflector, 0.0, 1.0, 0.0, 1.0, 72, 12, 0.006)
        tail_part('TailSmoked', tail_dark, 0.22, 1.0, 0.0, 0.42, 60, 5, 0.009)
        tail_part('TailDivider', smooth_chrome, 0.22, 1.0, 0.42, 0.49, 60, 1, 0.011)
        thin(tail_part('TailRed', red_glass, 0.21, 1.0, 0.0, 1.0, 60, 12, 0.03), 0.004)
        tail_part('ReverseInner', light_guide, 0.02, 0.18, 0.1, 0.9, 10, 8, 0.009)
        thin(tail_part('Reverse', cover, 0.0, 0.2, 0.0, 1.0, 12, 12, 0.03), 0.004)
        # side skirt, door handles
        decal(S + 'Skirt', body, mats['dark'], root, lambda u, v, s=s: (-1.0 + u * 2.0, s * 3.0, 0.3 + v * 0.07), (0, -s, 0), 40, 3, 0.004)
        for hx in (-0.02, -0.86):                                     # handles near each door's rear edge
            door_handle(S + 'Handle', body, root, s, hx, 0.815, mats['chrome'], seam)
        # door shut lines from the sill up under the window; the rear door's edge curves round the arch
        side_seam(S + 'DoorFront', body, root, seam, s, [(0.9, 0.955), (0.9, 0.37)])
        side_seam(S + 'DoorB', body, root, seam, s, [(-0.28, 0.955), (-0.28, 0.37)])
        a0 = math.degrees(math.atan2(0.6 - 0.345, -1.12 + 1.4))
        side_seam(S + 'DoorRear', body, root, seam, s, [(-1.12, 0.955), (-1.12, 0.6)] + arch_path(-1.4, 0.345, 0.385, a0, 4.0)[1:])
        # hood and trunk side lines
        decal(S + 'Seam', body, seam, root, lambda u, v, s=s: (0.98 + u * 1.25, s * (0.74 + v * 0.008), 3.0), (0, 0, -1), 30, 1, 0.002)
        decal(S + 'Seam', body, seam, root, lambda u, v, s=s: (-2.25 + u * 0.75, s * (0.74 + v * 0.008), 3.0), (0, 0, -1), 12, 1, 0.002)
    decal(S + 'Seam', body, seam, root, lambda u, v: (0.98 + v * 0.008, -0.74 + u * 1.48, 3.0), (0, 0, -1), 30, 1, 0.002)   # hood rear edge
    decal(S + 'Seam', body, seam, root, lambda u, v: (-1.5 + v * 0.008, -0.74 + u * 1.48, 3.0), (0, 0, -1), 30, 1, 0.002)  # trunk front edge
    # grille, lower intake, number plates, rear diffuser
    decal(S + 'GrilleFrame', body, mats['chrome'], root, lambda u, v: (4.0, -0.4 + u * 0.8, 0.51 + v * 0.15), (-1, 0, 0), 24, 6, 0.004)
    decal(S + 'Grille', body, grille, root, lambda u, v: (4.0, -0.375 + u * 0.75, 0.525 + v * 0.12), (-1, 0, 0), 24, 6, 0.007)
    decal(S + 'Intake', body, grille, root, lambda u, v: (4.0, -0.62 + u * 1.24, 0.31 + v * 0.07), (-1, 0, 0), 30, 3, 0.004)
    decal(S + 'PlateF', body, plate, root, lambda u, v: (4.0, -0.25 + u * 0.5, 0.395 + v * 0.1), (-1, 0, 0), 10, 3, 0.012)
    decal(S + 'PlateR', body, plate, root, lambda u, v: (-4.0, -0.25 + u * 0.5, 0.5 + v * 0.11), (1, 0, 0), 10, 3, 0.008)
    decal(S + 'Diffuser', body, mats['dark'], root, lambda u, v: (-4.0, -0.8 + u * 1.6, 0.33 + v * 0.13), (1, 0, 0), 30, 4, 0.004)
    # exhaust tips, placed at the rear surface
    ebm = bmesh.new()
    for s in (1, -1):
        p, n = body_hit(body, (-4.0, s * 0.55, 0.36), (1, 0, 0))
        if p is None:
            continue
        ret = bmesh.ops.create_cone(ebm, cap_ends=False, segments=24, radius1=0.042, radius2=0.042, depth=0.12)
        bmesh.ops.rotate(ebm, verts=ret['verts'], cent=(0, 0, 0), matrix=__import__('mathutils').Matrix.Rotation(math.pi / 2, 3, 'Y'))
        bmesh.ops.translate(ebm, verts=ret['verts'], vec=(p.x - 0.02, p.y, p.z))
    mesh_obj(S + 'Exhaust', ebm, [mats['chrome']], parent=root, smooth=True)
    # wing mirrors on short arms from the window base
    for s in (1, -1):
        base = gh_point(g['xf0'] - 0.22, 0.02 if s < 0 else 0.98)
        mr = []
        for k in range(6):
            t = k / 5
            mr.append(superring(lerp(-0.1, 0.12, t), 0.1 * (0.55 + 0.45 * math.sin(math.pi * t)), -0.065, 0.065, 12, 3.0))
        mbm = loft([[(p[0] + base.x, p[1] + base.y + s * 0.15, p[2] + base.z + 0.07) for p in r] for r in mr])
        mo = mesh_obj(S + 'Mirror', mbm, [paint], parent=root)
        subsurf(mo, 2)
        abm = bmesh.new()
        bm_box(abm, base.x - 0.04, base.y - s * 0.02, base.z + 0.02, base.x + 0.06, base.y + s * 0.08, base.z + 0.06, 0)
        mesh_obj(S + 'MirrorStalk', abm, [paint], parent=root, bevel=0.012)
    for wx in (1.42, -1.4):
        for s in (1, -1):
            wheel(root, wx, s, mats)
    return root


# ---------------------------------------------------------------- Hyundai i20 Active (2015-2020)
# Built to the published size: length 4.0 m, width 1.76 m, height 1.555 m, wheelbase 2.57 m, ground
# clearance 0.19 m, 195/55 R16 tyres. The front of the car is +x and its left side is +y.
I20_GH = dict(xr0=-1.95, xr1=-1.62, xf1=0.12, xf0=0.97, zb=0.88, zb_rear=0.98, roof=1.5, roof_rear=1.37, wb=0.78, wt=0.63, end_taper=0.1)
I20_TOP = [(-2.0, 1.02), (-1.93, 1.1), (-1.7, 1.13), (-1.2, 1.115), (-0.4, 1.07), (0.5, 1.03), (0.97, 1.0),
           (1.35, 0.95), (1.7, 0.91), (1.92, 0.88), (2.0, 0.83)]                # top of the body (side view)
I20_BOT = [(-2.0, 0.48), (-1.88, 0.4), (-1.7, 0.37), (1.55, 0.37), (1.82, 0.37), (2.0, 0.38)]
I20_HW = [(-2.0, 0.76), (-1.88, 0.85), (-1.6, 0.875), (1.3, 0.875), (1.7, 0.85), (1.9, 0.79), (2.0, 0.7)]
I20_WHEELS = (1.2, -1.37)
I20_R = 0.31


def mcurve(pts, x):
    """Monotone cubic through (x, y) control points: smooth, and it never overshoots."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(pts)
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    d = [(ys[k + 1] - ys[k]) / (xs[k + 1] - xs[k]) for k in range(n - 1)]
    m = [d[0]] + [0.0 if d[k - 1] * d[k] <= 0 else (d[k - 1] + d[k]) / 2 for k in range(1, n - 1)] + [d[-1]]
    for k in range(n - 1):
        if d[k] == 0:
            m[k] = m[k + 1] = 0.0
            continue
        a, b = m[k] / d[k], m[k + 1] / d[k]
        h = a * a + b * b
        if h > 9:
            t = 3 / math.sqrt(h)
            m[k], m[k + 1] = t * a * d[k], t * b * d[k]
    k = max(i for i in range(n - 1) if xs[i] <= x)
    hk = xs[k + 1] - xs[k]
    t = (x - xs[k]) / hk
    return ((2 * t ** 3 - 3 * t ** 2 + 1) * ys[k] + (t ** 3 - 2 * t ** 2 + t) * hk * m[k]
            + (-2 * t ** 3 + 3 * t ** 2) * ys[k + 1] + (t ** 3 - t ** 2) * hk * m[k + 1])


def lamp_part(S, body, root, nm, mat, pt, dr, u0, u1, v0, v1, nu, nv, off, grow=0.0):
    """One layer of a lamp: the aim point and the ray direction come from the same lamp coordinate."""
    return decal(S + nm, body, mat, root,
                 lambda u, v: pt(u0 + u * (u1 - u0), v0 + v * (v1 - v0), grow),
                 lambda u, v: dr(u0 + u * (u1 - u0), v0 + v * (v1 - v0)), nu, nv, off)


def round_part(S, body, root, nm, mat, cy, cz, r0, r1, direction, x_from, off, sy=1.0, sz=1.0):
    """A round (or oval) disc/ring projected onto the body; centre (cy, cz) in the plane facing the rays."""
    return decal(S + nm, body, mat, root,
                 lambda u, v: (x_from, cy + sy * (r0 + v * (r1 - r0)) * math.cos(u * 2 * math.pi),
                               cz + sz * (r0 + v * (r1 - r0)) * math.sin(u * 2 * math.pi)),
                 direction, 48, 3, off)


def car_i20(name, color, loc, rot_z):
    from mathutils import Matrix
    set_gh(I20_GH)
    root = empty(name, loc, rot_z)
    S = name + '_'
    paint = principled(S + 'Paint', srgb(color), rough=0.3, metal=0.55, coat=1.0, coat_rough=0.02)
    mats = {
        'tyre': textured(S + 'Tyre', 'plaster', srgb('#161616'), srgb('#1f1f1f'), rough=0.85, bump=0.2),
        'chrome': principled(S + 'Chrome', srgb('#d9dde2'), rough=0.08, metal=1.0),
        'dark': principled(S + 'Trim', srgb('#0d0d0f'), rough=0.45),
        'barrel': principled(S + 'Barrel', srgb('#4a4d52'), rough=0.4, metal=0.9),
        'disc': principled(S + 'Disc', srgb('#5d5f63'), rough=0.3, metal=1.0),
        'caliper': principled(S + 'Caliper', srgb('#3a3c40'), rough=0.4, metal=0.6),
    }

    def clear_glass(nm, tint, rough=0.0):
        m = principled(nm, srgb(tint), rough=rough, ior=1.5, spec=1.0)
        m.node_tree.nodes['Principled BSDF'].inputs['Transmission Weight'].default_value = 1.0
        return m
    glass_front = clear_glass(S + 'GlassFront', '#b7c1c2')
    glass_rear = clear_glass(S + 'GlassRear', '#6f797b')
    cover = clear_glass(S + 'LampCover', '#f4f6f8')
    red_glass = clear_glass(S + 'RedGlass', '#c4170e', 0.015)
    amber_glass = clear_glass(S + 'AmberGlass', '#ff8a1c', 0.05)
    lamp_black = principled(S + 'LampBlack', srgb('#0c0d0f'), rough=0.08, spec=1.0)
    reflector = faceted_chrome(S + 'Reflector')
    smooth_chrome = principled(S + 'LampChrome', srgb('#e6e9ee'), rough=0.05, metal=1.0)
    light_guide = principled(S + 'LightGuide', srgb('#dcdfe3'), rough=0.35, spec=0.6)
    tail_dark = principled(S + 'TailDark', srgb('#3a0604'), rough=0.2, spec=0.8)
    seal = principled(S + 'WindowSeal', srgb('#0b0b0c'), rough=0.25)
    shell_mat = two_sided_paint(S + 'CabinPaint', color, '#bdb8b0')
    cladding = textured(S + 'Cladding', 'plaster', srgb('#141416'), srgb('#19191b'), rough=0.62, bump=0.12, rough_var=0.05)
    skid = principled(S + 'SkidPlate', srgb('#b4b8bd'), rough=0.32, metal=0.9)
    rail = principled(S + 'RoofRail', srgb('#a2a7ad'), rough=0.3, metal=0.9)
    grille_mat = principled(S + 'Grille', srgb('#0b0b0c'), rough=0.2, metal=0.4)
    seam = principled(S + 'Seam', srgb('#050505'), rough=0.8)
    plate = principled(S + 'Plate', srgb('#e9e7df'), rough=0.4)
    leather = textured(S + 'Leather', 'plaster', srgb('#a88a68'), srgb('#b99c79'), rough=0.5, bump=0.12)
    door_trim = principled(S + 'DoorTrim', srgb('#2d2b29'), rough=0.6)
    dash = principled(S + 'Dash', srgb('#1a1a1c'), rough=0.5)
    screen = principled(S + 'Screen', srgb('#030405'), rough=0.02, spec=1.0)
    liner_mat = textured(S + 'Liner', 'plaster', srgb('#1c1c1e'), srgb('#26262a'), rough=0.8, bump=0.6)

    # ---- body: cross-sections lofted along the published side and plan profiles, subdivided
    rings = []
    N = 33
    for k in range(N):
        x = -2.0 + 4.0 * k / (N - 1)
        rings.append(superring(x, mcurve(I20_HW, x), mcurve(I20_BOT, x), mcurve(I20_TOP, x), 20, 4.5))
    body = mesh_obj(S + 'Body', loft(rings), [paint, liner_mat, door_trim], parent=root)
    subsurf(body, 3)
    cutters = []
    for wx in I20_WHEELS:                                             # wheel arches
        cbm = bmesh.new()
        ret = bmesh.ops.create_cone(cbm, cap_ends=True, segments=64, radius1=I20_R + 0.035, radius2=I20_R + 0.035, depth=2.6)
        bmesh.ops.rotate(cbm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'))
        bmesh.ops.translate(cbm, verts=ret['verts'], vec=(wx, 0, I20_R))
        cutters.append(mesh_obj(S + 'ArchCut', cbm, [liner_mat], parent=root))
    tbm = bmesh.new()                                                 # cabin and boot under the glasshouse
    bm_box(tbm, -1.85, -0.68, 0.7, 0.8, 0.68, 1.7, 0)
    cutters.append(mesh_obj(S + 'CabinCut', tbm, [door_trim], parent=root))
    for cut in cutters:
        cut.hide_render = True
        cut.hide_viewport = True
        bo = body.modifiers.new('Cut', 'BOOLEAN')
        bo.operation = 'DIFFERENCE'
        bo.object = cut
        try:
            bo.solver = os.environ.get('SB_BOOL', 'MANIFOLD')
            bo.material_mode = 'TRANSFER'
        except Exception:
            pass
    es = body.modifiers.new('CutEdges', 'EDGE_SPLIT')
    es.split_angle = math.radians(40)
    es.use_edge_sharp = False
    lbm = bmesh.new()                                                 # wheel-arch liners (upper half)
    for wx in I20_WHEELS:
        for s in (1, -1):
            ret = bmesh.ops.create_cone(lbm, cap_ends=True, segments=48, radius1=I20_R + 0.025, radius2=I20_R + 0.025, depth=0.5)
            bmesh.ops.rotate(lbm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'))
            bmesh.ops.translate(lbm, verts=ret['verts'], vec=(wx, s * 0.47, I20_R))
    bmesh.ops.delete(lbm, geom=[v for v in lbm.verts if v.co.z < 0.42], context='VERTS')   # nothing below the sills
    mesh_obj(S + 'Liner', lbm, [liner_mat], parent=root)
    bpy.context.view_layer.update()

    # ---- glasshouse: painted roof, black B- and C-pillars, window seals, clear glass, roof rails, spoiler
    g = GH
    windows = [(g['xf1'] + 0.06, g['xf0'] - 0.06, 0.24, 0.76, glass_front),      # windscreen
               (g['xr0'] + 0.06, g['xr1'] - 0.03, 0.27, 0.73, glass_rear)]       # tailgate glass
    for (v0, v1) in ((0.035, 0.19), (0.81, 0.965)):
        windows += [(-0.12, g['xf0'] - 0.2, v0, v1, glass_front),               # front door
                    (-1.02, -0.26, v0, v1, glass_rear),                         # rear door
                    (-1.42, -1.12, v0, v1, glass_rear)]                         # rear quarter (small)
    gh_shell(S + 'Cabin', [shell_mat, seal], root, [w[:4] for w in windows], black_x=[(-0.26, -0.12), (-1.12, -1.02), (g['xf0'] - 0.2, g['xf0'])])
    for k, (x0, x1, v0, v1, gm) in enumerate(windows):
        thin(gh_surface(S + 'Window%d' % k, gm, root, x0, x1, v0, v1, 140, 20), 0.005)
    fb = bmesh.new()
    for v in (0.29, 0.71):                                            # roof rails: one smooth tube each
        n_ = 30
        xs = [lerp(-1.5, 0.0, i / n_) for i in range(n_ + 1)]
        rings = []
        for i, x in enumerate(xs):
            p = gh_point(x, v)
            k = min(1.0, i / 3, (n_ - i) / 3)                            # ends taper down onto the roof
            hy, hz = 0.017 * (0.6 + 0.4 * k), 0.016 * (0.6 + 0.4 * k)
            cz = p.z + 0.012 + 0.034 * k
            ring = []
            for j in range(10):
                a = j / 10 * 2 * math.pi
                c_, s_ = math.cos(a), math.sin(a)
                ring.append((x, p.y + hy * math.copysign(abs(c_) ** 0.5, c_), cz + hz * math.copysign(abs(s_) ** 0.5, s_)))
            rings.append(ring)
        ro = mesh_obj(S + 'RoofRail', loft(rings), [rail], parent=root)
        subsurf(ro, 1)
        for i in (6, 15, 24):                                         # feet
            p = gh_point(xs[i], v)
            bm_box(fb, p.x - 0.035, p.y - 0.015, p.z - 0.01, p.x + 0.035, p.y + 0.015, p.z + 0.04, 0)
    mesh_obj(S + 'RailFeet', fb, [mats['dark']], parent=root, bevel=0.008)
    thin(gh_surface(S + 'Spoiler', paint, root, g['xr1'] - 0.02, g['xr1'] + 0.16, 0.2, 0.8, 24, 20, 0.022), 0.02)
    wipers(S + 'Wipers', root, None, [(g['xf0'] - 0.13, 0.28, 0.56), (g['xf0'] - 0.13, 0.5, 0.74),   # windscreen
                                                (g['xr0'] + 0.12, 0.42, 0.64)])                                 # tailgate glass

    # ---- interior: seats, rear bench, dashboard, console, steering wheel, parcel shelf
    for sy in (0.38, -0.38):
        sb = bmesh.new()
        bm_hex(sb, [(-0.2, sy - 0.25, 0.7), (0.26, sy - 0.25, 0.7), (0.26, sy + 0.25, 0.7), (-0.2, sy + 0.25, 0.7),
                    (-0.2, sy - 0.25, 0.81), (0.26, sy - 0.25, 0.84), (0.26, sy + 0.25, 0.84), (-0.2, sy + 0.25, 0.81)], 0)
        bm_hex(sb, [(-0.25, sy - 0.24, 0.79), (-0.12, sy - 0.24, 0.79), (-0.12, sy + 0.24, 0.79), (-0.25, sy + 0.24, 0.79),
                    (-0.38, sy - 0.22, 1.3), (-0.28, sy - 0.22, 1.3), (-0.28, sy + 0.22, 1.3), (-0.38, sy + 0.22, 1.3)], 0)
        for side in (-1, 1):
            yb = sy + side * 0.22
            bm_hex(sb, [(-0.24, yb - 0.035, 0.84), (-0.08, yb - 0.035, 0.84), (-0.08, yb + 0.035, 0.84), (-0.24, yb + 0.035, 0.84),
                        (-0.35, yb - 0.035, 1.2), (-0.24, yb - 0.035, 1.2), (-0.24, yb + 0.035, 1.2), (-0.35, yb + 0.035, 1.2)], 0)
        bm_box(sb, -0.38, sy - 0.13, 1.33, -0.29, sy + 0.13, 1.42, 0)
        soft(S + 'Seat', sb, [leather], root, 0.03, 4)
        pb = bmesh.new()
        for py in (sy - 0.07, sy + 0.07):
            bm_cyl(pb, -0.335, py, 1.27, 1.34, 0.008, 10, 0)
        mesh_obj(S + 'HeadrestPosts', pb, [smooth_chrome], parent=root, smooth=True)
    rbn = bmesh.new()
    bm_hex(rbn, [(-1.1, -0.62, 0.7), (-0.66, -0.62, 0.7), (-0.66, 0.62, 0.7), (-1.1, 0.62, 0.7),
                 (-1.1, -0.62, 0.8), (-0.66, -0.62, 0.82), (-0.66, 0.62, 0.82), (-1.1, 0.62, 0.8)], 0)
    bm_hex(rbn, [(-1.14, -0.62, 0.78), (-1.02, -0.62, 0.78), (-1.02, 0.62, 0.78), (-1.14, 0.62, 0.78),
                 (-1.25, -0.58, 1.2), (-1.15, -0.58, 1.2), (-1.15, 0.58, 1.2), (-1.25, 0.58, 1.2)], 0)
    soft(S + 'RearBench', rbn, [leather], root, 0.03, 4)
    db = bmesh.new()
    bm_hex(db, [(0.46, -0.6, 0.78), (0.8, -0.6, 0.78), (0.8, 0.6, 0.78), (0.46, 0.6, 0.78),
                (0.46, -0.6, 1.1), (0.8, -0.6, 1.02), (0.8, 0.6, 1.02), (0.46, 0.6, 1.1)], 0)
    bm_box(db, 0.47, 0.2, 1.08, 0.59, 0.56, 1.16, 0)                  # instrument hood
    bm_box(db, -0.2, -0.11, 0.7, 0.48, 0.11, 0.86, 0)                 # centre console
    for ay in (0.68, -0.68):                                          # armrests on the door panels
        bm_box(db, -0.4, ay - 0.05 if ay > 0 else ay, 0.86, 0.36, ay if ay > 0 else ay + 0.05, 0.9, 0)
    bm_box(db, -1.84, -0.66, 1.03, -1.3, 0.66, 1.05, 0)               # parcel shelf over the boot
    soft(S + 'Dashboard', db, [dash], root, 0.02, 3)
    sc = bmesh.new()
    bm_box(sc, 0.445, -0.14, 0.98, 0.46, 0.14, 1.1, 0)                 # touch screen
    for vy in (-0.5, -0.24, 0.24, 0.5):
        bm_box(sc, 0.452, vy - 0.06, 1.0, 0.462, vy + 0.06, 1.015, 1)  # air vents
    bm_cyl(sc, 0.2, 0.0, 0.86, 0.95, 0.012, 12, 1)                      # gear lever
    ret = bmesh.ops.create_uvsphere(sc, u_segments=16, v_segments=8, radius=0.03)
    bmesh.ops.translate(sc, verts=ret['verts'], vec=(0.2, 0.0, 0.97))
    bm_box(sc, 0.22, -0.12, 1.38, 0.25, 0.12, 1.44, 2)                 # rear-view mirror
    bm_box(sc, 0.23, -0.015, 1.43, 0.24, 0.015, 1.49, 2)
    mesh_obj(S + 'Controls', sc, [screen, smooth_chrome, dash], parent=root, smooth=True, bevel=0.003)
    swb = bmesh.new()                                                 # steering wheel
    rim = []
    for k in range(48):
        th = k / 48 * 2 * math.pi
        ring = []
        for j in range(12):
            ph = j / 12 * 2 * math.pi
            r = 0.18 + 0.02 * math.sin(ph)
            ring.append(swb.verts.new((0.02 * math.cos(ph), r * math.cos(th), r * math.sin(th))))
        rim.append(ring)
    for k in range(48):
        a, b = rim[k], rim[(k + 1) % 48]
        for j in range(12):
            swb.faces.new((a[j], a[(j + 1) % 12], b[(j + 1) % 12], b[j])).material_index = 0
    for ang in (0.0, math.pi, -math.pi / 2):
        sp = bmesh.ops.create_cube(swb, size=1.0)
        bmesh.ops.scale(swb, verts=sp['verts'], vec=(0.022, 0.045, 0.12))
        bmesh.ops.translate(swb, verts=sp['verts'], vec=(0, 0, 0.11))
        bmesh.ops.rotate(swb, verts=sp['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(ang + math.pi / 2, 3, 'X'))
        for f in {f for v in sp['verts'] for f in v.link_faces}:
            f.material_index = 1
    ret = bmesh.ops.create_cone(swb, cap_ends=True, segments=24, radius1=0.07, radius2=0.06, depth=0.06)
    bmesh.ops.rotate(swb, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
    bmesh.ops.translate(swb, verts=ret['verts'], vec=(0.02, 0, 0))
    ret2 = bmesh.ops.create_cone(swb, cap_ends=True, segments=16, radius1=0.035, radius2=0.035, depth=0.16)
    bmesh.ops.rotate(swb, verts=ret2['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
    bmesh.ops.translate(swb, verts=ret2['verts'], vec=(0.1, 0, 0))
    bmesh.ops.recalc_face_normals(swb, faces=swb.faces)
    bmesh.ops.rotate(swb, verts=swb.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(-22), 3, 'Y'))
    bmesh.ops.translate(swb, verts=swb.verts, vec=(0.36, 0.38, 1.04))
    mesh_obj(S + 'SteeringWheel', swb, [dash, smooth_chrome], parent=root, smooth=True)

    # ---- body-side details: cladding, sill strip, door lines, handles
    for s in (1, -1):
        for wx in I20_WHEELS:                                         # black plastic arch cladding
            decal(S + 'ArchCladding', body, cladding, root,
                  lambda u, v, s=s, wx=wx: (wx + (I20_R + 0.035 + 0.075 * v) * math.cos(math.pi * (-0.12 + 1.24 * u)), s * 3.0,
                                            I20_R + (I20_R + 0.035 + 0.075 * v) * math.sin(math.pi * (-0.12 + 1.24 * u))),
                  (0, -s, 0), 64, 3, 0.005)
        decal(S + 'SillCladding', body, cladding, root, lambda u, v, s=s: (-1.03 + u * 1.89, s * 3.0, 0.37 + v * 0.12), (0, -s, 0), 60, 3, 0.005)
        # door shut lines, from the sill up under the window: front door, B-pillar, and the rear door's
        # rear edge, which curves round the wheel arch like a real rear door
        def ztop(x):                                                 # where the shoulder meets the glasshouse
            hw, zb_, zt_ = mcurve(I20_HW, x), mcurve(I20_BOT, x), mcurve(I20_TOP, x)
            return (zb_ + zt_) / 2 + (zt_ - zb_) / 2 * max(0.0, 1 - (0.73 / hw) ** 4.5) ** (1 / 4.5)
        # front edge: from the A-pillar base, sweeping back past the mirror and down along the wheel arch
        side_seam(S + 'DoorFront', body, root, seam, s, [(0.95, ztop(0.95)), (0.93, 0.92), (0.88, 0.74), (0.845, 0.62), (0.82, 0.495)])
        side_seam(S + 'DoorB', body, root, seam, s, [(-0.19, ztop(-0.19)), (-0.19, 0.495)])
        rx = I20_WHEELS[1]
        a0 = math.degrees(math.atan2(0.6 - I20_R, -1.06 - rx))
        side_seam(S + 'DoorRear', body, root, seam, s, [(-1.06, ztop(-1.06)), (-1.06, 0.6)] + arch_path(rx, I20_R, 0.43, a0, 24.0)[1:])
        for hx in (0.05, -0.83):                                      # handles near each door's rear edge
            door_handle(S + 'Handle', body, root, s, hx, 0.945, mats['chrome'], seam)
        decal(S + 'Seam', body, seam, root, lambda u, v, s=s: (0.98 + u * 0.6, s * (0.66 + v * 0.008), 3.0), (0, 0, -1), 24, 1, 0.002)   # hood sides
        decal(S + 'Seam', body, seam, root, lambda u, v, s=s: (-4.0, s * (0.375 + u * 0.008), 0.62 + v * 0.42), (1, 0, 0), 1, 24, 0.002)  # tailgate sides
    decal(S + 'Seam', body, seam, root, lambda u, v: (0.98 + v * 0.008, -0.7 + u * 1.4, 3.0), (0, 0, -1), 30, 1, 0.002)        # hood rear edge
    decal(S + 'Seam', body, seam, root, lambda u, v: (-4.0, -0.375 + u * 0.75, 0.62 + v * 0.008), (1, 0, 0), 30, 1, 0.002)      # tailgate bottom

    # ---- front: hexagonal grille with chrome surround and logo, lower intake, plate, skid plate, fog lamps
    def grille_pt(u, v, grow=0.0):
        z = 0.56 - grow + v * (0.19 + 2 * grow)
        w = 0.27 + 0.07 * min(1.0, v / 0.7) - 0.03 * max(0.0, (v - 0.85) / 0.15) + grow
        return (4.0, (u - 0.5) * 2 * w, z)
    decal(S + 'GrilleFrame', body, smooth_chrome, root, lambda u, v: grille_pt(u, v, 0.015), (-1, 0, 0), 40, 12, 0.004)
    decal(S + 'Grille', body, grille_mat, root, grille_pt, (-1, 0, 0), 40, 12, 0.007)
    for vb in (0.3, 0.55, 0.8):
        decal(S + 'GrilleBar', body, smooth_chrome, root, lambda u, v, vb=vb: grille_pt(0.03 + u * 0.94, vb + v * 0.035), (-1, 0, 0), 40, 1, 0.009)
    decal(S + 'Logo', body, smooth_chrome, root, lambda u, v: (4.0, 0.085 * (0.78 + 0.22 * v) * math.cos(u * 2 * math.pi), 0.67 + 0.045 * (0.78 + 0.22 * v) * math.sin(u * 2 * math.pi)), (-1, 0, 0), 48, 2, 0.012)
    decal(S + 'FrontCladding', body, cladding, root, lambda u, v: (4.0, -0.72 + u * 1.44, 0.4 + v * 0.08), (-1, 0, 0), 60, 3, 0.004)
    decal(S + 'SkidF', body, skid, root, lambda u, v: (4.0, -0.42 + u * 0.84, 0.405 + v * 0.045), (-1, 0, 0), 30, 2, 0.008)
    decal(S + 'Intake', body, grille_mat, root, lambda u, v: (4.0, -0.38 + u * 0.76, 0.455 + v * 0.08), (-1, 0, 0), 30, 3, 0.006)
    decal(S + 'PlateF', body, plate, root, lambda u, v: (4.0, -0.25 + u * 0.5, 0.46 + v * 0.085), (-1, 0, 0), 12, 3, 0.016)
    for s in (1, -1):                                                 # fog lamps in black bezels
        round_part(S, body, root, 'FogBezel', cladding, s * 0.57, 0.5, 0.0, 0.065, (-1, 0, 0), 4.0, 0.008)
        round_part(S, body, root, 'FogReflector', reflector, s * 0.57, 0.5, 0.0, 0.04, (-1, 0, 0), 4.0, 0.011)
        p, n = body_hit(body, (4.0, s * 0.57, 0.5), (-1, 0, 0))
        if p is not None:
            fb = bmesh.new()
            ret = bmesh.ops.create_uvsphere(fb, u_segments=24, v_segments=12, radius=0.04)
            bmesh.ops.scale(fb, verts=ret['verts'], vec=(0.35, 1, 1))
            bmesh.ops.translate(fb, verts=ret['verts'], vec=(p.x + 0.004, p.y, p.z))
            mesh_obj(S + 'FogLens', fb, [cover], parent=root, smooth=True)

    # ---- headlights (unlit), after the i20's lamp: swept back and up onto the wing, chrome frame,
    # gloss-black housing, faceted reflector, clear projector lens in a chrome bowl, amber indicator,
    # frosted light guide along the top, a row of LED dots, clear glass cover
    for s in (1, -1):
        def head_dir(u, v, s=s):
            w = max(0.0, min(1.0, u)) ** 1.3
            return (-1.0 + 0.7 * w, -s * 0.75 * w, -0.1 - 0.5 * w)

        def head_pt(u, v, grow=0.0, s=s):
            u = -grow * 0.3 + u * (1 + 0.6 * grow)
            v = -grow * 2 + v * (1 + 4 * grow)
            uu = min(1.0, max(0.0, u))
            h = 0.04 + 0.09 * math.sin(math.pi * min(1.0, uu * 1.05)) ** 0.6
            x = 1.95 - 0.4 * uu ** 1.5
            y = s * (0.41 + 0.24 * u)
            z = 0.66 + 0.18 * uu ** 1.2 + v * h
            d = Vector(head_dir(u, v)).normalized()
            return tuple(Vector((x, y, z)) - d * 3.0)
        lamp_part(S, body, root, 'HeadFrame', smooth_chrome, head_pt, head_dir, 0, 1, 0, 1, 64, 14, 0.003, 0.03)
        lamp_part(S, body, root, 'HeadHousing', lamp_black, head_pt, head_dir, 0, 1, 0, 1, 64, 14, 0.006)
        lamp_part(S, body, root, 'HeadReflector', reflector, head_pt, head_dir, 0.38, 0.9, 0.12, 0.76, 36, 12, 0.009)
        lamp_part(S, body, root, 'HeadGuide', light_guide, head_pt, head_dir, 0.04, 0.96, 0.8, 0.94, 64, 3, 0.011)
        thin(lamp_part(S, body, root, 'Indicator', amber_glass, head_pt, head_dir, 0.05, 0.3, 0.15, 0.6, 16, 6, 0.013), 0.002)
        p, n = body_hit(body, head_pt(0.62, 0.42), head_dir(0.62, 0.42))
        if p is not None:
            dn = Vector(head_dir(0.62, 0.42)).normalized()
            rot = (-dn).to_track_quat('Z', 'Y').to_matrix().to_4x4()
            pbm = bmesh.new()
            ret = bmesh.ops.create_cone(pbm, cap_ends=True, segments=40, radius1=0.045, radius2=0.03, depth=0.028)
            bmesh.ops.transform(pbm, verts=ret['verts'], matrix=rot)
            bmesh.ops.translate(pbm, verts=ret['verts'], vec=p - dn * 0.022)
            mesh_obj(S + 'ProjBowl', pbm, [smooth_chrome], parent=root, smooth=True)
            lbm2 = bmesh.new()
            ret = bmesh.ops.create_uvsphere(lbm2, u_segments=32, v_segments=16, radius=0.029)
            bmesh.ops.scale(lbm2, verts=ret['verts'], vec=(1, 1, 0.55))
            bmesh.ops.transform(lbm2, verts=ret['verts'], matrix=rot)
            bmesh.ops.translate(lbm2, verts=ret['verts'], vec=p - dn * 0.02)
            mesh_obj(S + 'ProjLens', lbm2, [cover], parent=root, smooth=True)
        led = bmesh.new()
        for k in range(7):                                            # LED dots along the bottom edge
            uu = 0.34 + k * 0.08
            p, n = body_hit(body, head_pt(uu, 0.07), head_dir(uu, 0.07))
            if p is None:
                continue
            dn = Vector(head_dir(uu, 0.07)).normalized()
            ret = bmesh.ops.create_uvsphere(led, u_segments=12, v_segments=6, radius=0.007)
            bmesh.ops.translate(led, verts=ret['verts'], vec=p - dn * 0.012)
        mesh_obj(S + 'LEDs', led, [light_guide], parent=root, smooth=True)
        thin(lamp_part(S, body, root, 'HeadCover', cover, head_pt, head_dir, 0, 1, 0, 1, 64, 14, 0.04, 0.02), 0.003)

        # ---- tail lights (unlit), wrapping from the tailgate round the corner onto the side
        def tail_dir(u, v, s=s):
            w = max(0.0, min(1.0, (u - 0.55) / 0.45)) ** 1.2
            return (1.0 - w, -s * (0.2 + 0.8 * w), 0.0)

        def tail_pt(u, v, grow=0.0, s=s):
            u = -grow * 0.3 + u * (1 + 0.6 * grow)
            v = -grow * 2 + v * (1 + 4 * grow)
            uu = min(1.0, max(0.0, u))
            w = max(0.0, (uu - 0.55) / 0.45)
            x = -1.94 + 0.2 * w ** 1.3
            y = s * (0.4 + 0.26 * min(1.0, uu / 0.55))
            z = 0.84 + v * (0.15 - 0.04 * w) + 0.02 * w
            d = Vector(tail_dir(u, v)).normalized()
            return tuple(Vector((x, y, z)) - d * 3.0)
        lamp_part(S, body, root, 'TailFrame', lamp_black, tail_pt, tail_dir, 0, 1, 0, 1, 72, 12, 0.003, 0.025)
        lamp_part(S, body, root, 'TailReflector', reflector, tail_pt, tail_dir, 0, 1, 0, 1, 72, 12, 0.006)
        lamp_part(S, body, root, 'TailSmoked', tail_dark, tail_pt, tail_dir, 0.22, 1, 0, 0.42, 60, 5, 0.009)
        lamp_part(S, body, root, 'TailDivider', smooth_chrome, tail_pt, tail_dir, 0.22, 1, 0.42, 0.49, 60, 1, 0.011)
        thin(lamp_part(S, body, root, 'TailRed', red_glass, tail_pt, tail_dir, 0.21, 1, 0, 1, 60, 12, 0.03), 0.004)
        lamp_part(S, body, root, 'ReverseInner', light_guide, tail_pt, tail_dir, 0.02, 0.18, 0.1, 0.9, 10, 8, 0.009)
        thin(lamp_part(S, body, root, 'Reverse', cover, tail_pt, tail_dir, 0, 0.2, 0, 1, 12, 12, 0.03), 0.004)
        round_part(S, body, root, 'Reflector', red_glass, s * 0.7, 0.6, 0.0, 0.025, (1, 0, 0), -4.0, 0.006, sy=2.2)   # bumper reflectors

    # ---- rear: tailgate garnish, plate, black bumper with silver skid plate, exhaust
    decal(S + 'Garnish', body, smooth_chrome, root, lambda u, v: (-4.0, -0.38 + u * 0.76, 0.815 + v * 0.018), (1, 0, 0), 40, 1, 0.006)
    decal(S + 'LogoR', body, smooth_chrome, root, lambda u, v: (-4.0, 0.075 * (0.78 + 0.22 * v) * math.cos(u * 2 * math.pi), 0.9 + 0.04 * (0.78 + 0.22 * v) * math.sin(u * 2 * math.pi)), (1, 0, 0), 48, 2, 0.008)
    decal(S + 'PlateRecess', body, seam, root, lambda u, v: (-4.0, -0.28 + u * 0.56, 0.655 + v * 0.13), (1, 0, 0), 14, 4, 0.003)
    decal(S + 'PlateR', body, plate, root, lambda u, v: (-4.0, -0.26 + u * 0.52, 0.665 + v * 0.11), (1, 0, 0), 12, 3, 0.008)
    decal(S + 'RearCladding', body, cladding, root, lambda u, v: (-4.0, -0.78 + u * 1.56, 0.5 + v * 0.13), (1, 0, 0), 60, 3, 0.004)
    decal(S + 'SkidR', body, skid, root, lambda u, v: (-4.0, -0.5 + u * 1.0, 0.51 + v * 0.05), (1, 0, 0), 30, 2, 0.008)
    ebm = bmesh.new()
    p, n = body_hit(body, (-4.0, -0.48, 0.47), (1, 0, 0))
    if p is not None:
        ret = bmesh.ops.create_cone(ebm, cap_ends=False, segments=24, radius1=0.032, radius2=0.032, depth=0.1)
        bmesh.ops.rotate(ebm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
        bmesh.ops.translate(ebm, verts=ret['verts'], vec=(p.x - 0.02, p.y, p.z - 0.04))
        mesh_obj(S + 'Exhaust', ebm, [mats['chrome']], parent=root, smooth=True)

    # ---- wing mirrors on short stalks at the window base
    for s in (1, -1):
        base = gh_point(g['xf0'] - 0.12, 0.03 if s < 0 else 0.97)       # on the black sail
        mr = []
        for k in range(6):
            t = k / 5
            mr.append(superring(lerp(-0.1, 0.12, t), 0.1 * (0.55 + 0.45 * math.sin(math.pi * t)), -0.065, 0.065, 12, 3.0))
        mo = mesh_obj(S + 'Mirror', loft([[(q[0] + base.x, q[1] + base.y + s * 0.15, q[2] + base.z + 0.07) for q in r] for r in mr]), [paint], parent=root)
        subsurf(mo, 2)
        abm = bmesh.new()
        bm_box(abm, base.x - 0.04, base.y - s * 0.02, base.z + 0.02, base.x + 0.06, base.y + s * 0.08, base.z + 0.06, 0)
        mesh_obj(S + 'MirrorStalk', abm, [cladding], parent=root, bevel=0.012)

    # ---- wheels: 195/55 R16, ten-spoke alloys
    for wx in I20_WHEELS:
        for s in (1, -1):
            wheel(root, wx, s, mats, R=I20_R, Wd=0.2, track=0.745, spokes=10)
    return root


# ---------------------------------------------------------------- people
# Each person is built from a skeleton: the Skin modifier gives every bone a thickness and subdivision
# smooths it into a body. Clothes are separate, slightly larger skins over the body (shirt, trousers),
# with shoes, a modelled head (jaw, nose, eyes, brows, lips, ears) and hair on top.
SKIN_TONES = ['#f1c7a5', '#e3b08c', '#c98b66', '#a56c47', '#7c4d30', '#5b3723']
HAIR_COLS = ['#16110f', '#2b1d15', '#4a3020', '#7a5230', '#b08a55', '#d2bd92', '#8c8580']
TOP_COLS = ['#f2f2ee', '#1d1f24', '#2f4f7f', '#8a1f2b', '#3d6b4a', '#c9b48a', '#6b4a7a', '#d9822b', '#5f6b75', '#e8d7c0', '#b8c7d6']
PANTS = [('jeans', '#2c4468'), ('jeans', '#3d5a80'), ('chinos', '#c2b08a'), ('chinos', '#2a2a2e'),
         ('chinos', '#5a5f66'), ('jeans', '#1f2530'), ('chinos', '#4a3a2c')]
SHOE_COLS = ['#efefec', '#1a1a1c', '#5a3a26', '#3a3f4a', '#8a8f96']


def skin_obj(name, root, verts, edges, radii, mat, subd=2, roots=(0,)):
    """A smooth skinned shape from a skeleton: verts, edges and a (width, depth) radius per vertex."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], edges, [])
    ob = bpy.data.objects.new(name, me)
    COL.objects.link(ob)
    ob.parent = root
    md = ob.modifiers.new('Skin', 'SKIN')
    md.use_smooth_shade = True
    sv = me.skin_vertices[0].data
    for i, r in enumerate(radii):
        sv[i].radius = r if isinstance(r, tuple) else (r, r)
    for r_ in roots:
        sv[r_].use_root = True
    sm = ob.modifiers.new('Smooth', 'SUBSURF')
    sm.levels = subd
    sm.render_levels = subd
    me.materials.append(mat)
    return ob


def band(name, root, centre, axis, R, r, mat, n=40, m=10):
    """A torus around `axis` through `centre`: a collar or a sleeve hem where cloth meets skin."""
    a = Vector(axis).normalized()
    u = a.orthogonal().normalized()
    w = a.cross(u)
    bm = bmesh.new()
    rings = []
    for i in range(n):
        th = i / n * 2 * math.pi
        d = u * math.cos(th) + w * math.sin(th)
        rings.append([bm.verts.new(tuple(Vector(centre) + d * (R + r * math.cos(ph)) + a * (r * math.sin(ph))))
                      for ph in [j / m * 2 * math.pi for j in range(m)]])
    for i in range(n):
        A_, B_ = rings[i], rings[(i + 1) % n]
        for j in range(m):
            bm.faces.new((A_[j], A_[(j + 1) % m], B_[(j + 1) % m], B_[j]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_obj(name, bm, [mat], parent=root, smooth=True)


def add_hand(V, R, E, wi, el, wr, sgn, k, grip):
    """Append a hand skeleton to (V, R, E), starting at wrist vertex index wi.
    Palm faces the thigh (or up, holding a phone); the index finger and thumb are on the front side."""
    a = (wr - el).normalized()                                   # along the hand
    n0 = Vector((0, 0, 1)) if grip else Vector((0, -sgn, 0))     # palm normal
    n = (n0 - a * n0.dot(a)).normalized()
    b = a.cross(n).normalized()                                  # across the knuckles
    if grip:
        if b.y * (-sgn) < 0:
            b = -b                                               # thumb toward the body when holding a phone
    elif b.x < 0:
        b = -b                                                   # thumb forward when the arm hangs
    curl = (0.6, 0.7, 0.55) if grip else (0.2, 0.3, 0.22)        # bend at each finger joint (radians)
    P = wr + a * 0.045 * k
    pi_ = len(V)
    V.append(P)
    R.append(0.028 * k)
    E.append((wi, pi_))
    fingers = [(0.027, (0.04, 0.025, 0.02)), (0.009, (0.045, 0.028, 0.021)), (-0.009, (0.043, 0.027, 0.02)), (-0.026, (0.033, 0.021, 0.018))]
    for off, segs in fingers:
        q = wr + a * 0.088 * k + b * off * k
        prev = len(V)
        V.append(q)
        R.append(0.0095 * k)
        E.append((pi_, prev))
        ang = 0.0
        for j, L in enumerate(segs):
            ang += curl[j]
            q = q + (a * math.cos(ang) + n * math.sin(ang)) * L * k
            V.append(q)
            R.append((0.0085 - 0.0011 * j) * k)
            E.append((prev, len(V) - 1))
            prev = len(V) - 1
    t = wr + a * 0.03 * k + b * 0.02 * k + n * 0.01 * k          # thumb, along the index side of the palm
    prev = len(V)
    V.append(t)
    R.append(0.012 * k)
    E.append((wi, prev))
    td = (a * 0.8 + b * 0.35 + n * (0.5 if grip else 0.32)).normalized()
    for j, L in enumerate((0.03, 0.024, 0.02)):
        td = (td + n * (0.2 if grip else 0.08) + a * 0.1).normalized()
        t = t + td * L * k
        V.append(t)
        R.append((0.0105 - 0.0012 * j) * k)
        E.append((prev, len(V) - 1))
        prev = len(V) - 1


def human_pose(h, female, walk, phase, phone, rnd, neutral=False):
    """Joint positions for a standing or walking person facing +x (left side +y), feet on the ground."""
    k = h / 1.78
    hipw = (0.105 if female else 0.095) * k
    shw = (0.175 if female else 0.2) * k
    J = {'pelvis': Vector((0, 0, 0.95 * k)), 'waist': Vector((0.005 * k, 0, 1.08 * k)),
         'chest': Vector((0.012 * k, 0, 1.28 * k)), 'neck': Vector((0.008 * k, 0, 1.47 * k)),
         'head': Vector((0.02 * k, 0, 1.64 * k))}
    A = math.radians(24) if walk else 0.0
    for side, sgn in (('L', 1), ('R', -1)):
        th = A * math.sin(phase) * sgn                      # leg swing: + forward
        if not walk:
            th = 0.0 if neutral else math.radians(rnd.uniform(-3, 3))
        back = max(0.0, -th) / (A if A else 1.0)
        bend = math.radians(5 + 13 * back)                  # trailing knee bends a little; heel lifts ~8 cm
        hip = Vector((0, sgn * hipw, 0.92 * k))
        knee = hip + Vector((math.sin(th), sgn * 0.01, -math.cos(th))) * (0.43 * k)
        ank = knee + Vector((math.sin(th - bend), 0, -math.cos(th - bend))) * (0.42 * k)
        J['hip' + side], J['knee' + side], J['ank' + side] = hip, knee, ank
        al = -0.9 * th if walk else math.radians(3 if neutral else rnd.uniform(2, 6))   # arms swing against the legs
        el = math.radians(10 if neutral else 14 + (12 if al > 0 else 4))
        if phone and side == 'R':
            al, el = math.radians(12), math.radians(105)               # forearm up, looking at a phone
        sh = Vector((0, sgn * shw, 1.43 * k))
        elb = sh + Vector((math.sin(al), sgn * 0.05, -math.cos(al))) * (0.29 * k)
        wr = elb + Vector((math.sin(al + el), sgn * 0.02, -math.cos(al + el))) * (0.26 * k)
        hand = wr + (wr - elb).normalized() * (0.16 * k)
        thumb = wr + (wr - elb).normalized() * (0.06 * k) + Vector((0.025, -sgn * 0.01, 0)) * k
        J['clav' + side] = Vector((0.0, sgn * shw * 0.55, 1.425 * k))
        J['sh' + side], J['el' + side], J['wr' + side], J['hand' + side], J['thumb' + side] = sh, elb, wr, hand, thumb
    dz = 0.085 * k - min(J['ankL'].z, J['ankR'].z)                     # the lower foot stands on the ground
    for key in J:
        J[key] = J[key] + Vector((0, 0, dz))
    return J


def shoe(name, root, ank, mat, sole_mat, k):
    """A shoe from heel to toe, pitched so the toe stays on the ground when the heel lifts."""
    # the foot keeps its length: flat on the ground, or (heel lifted) pivoting on the toe
    L0 = 0.24 * k
    heel = Vector((ank.x - 0.055 * k, ank.y, max(0.012, ank.z - 0.073 * k)))
    lift = min(heel.z - 0.012, 0.6 * L0)
    toe = Vector((heel.x + math.sqrt(L0 * L0 - lift * lift), ank.y, 0.012))
    d = toe - heel
    L = d.length
    fwd = d.normalized()
    up = Vector((0, 0, 1))
    side = up.cross(fwd).normalized()
    up = fwd.cross(side).normalized()
    rings = []
    for i in range(9):
        t = i / 8
        w = 0.05 * k * (0.75 + 0.25 * math.sin(math.pi * min(1.0, t * 1.15)))
        hgt = 0.095 * k * (1 - 0.6 * t ** 1.5) if t > 0.25 else 0.095 * k
        c = heel + fwd * (L * (t * 1.08 - 0.04))
        ring = []
        for j in range(12):
            a = j / 12 * 2 * math.pi
            ca, sa = math.cos(a), math.sin(a)
            yy = w * math.copysign(abs(ca) ** 0.6, ca)
            zz = (hgt / 2) * math.copysign(abs(sa) ** 0.6, sa) + hgt / 2 - 0.012
            ring.append(tuple(c + side * yy + up * zz))
        rings.append(ring)
    so = mesh_obj(name, loft(rings), [mat], parent=root)
    subsurf(so, 1)
    srings = []                                                 # sole: one continuous rubber piece
    for i in range(9):
        t = i / 8
        c = heel + fwd * (L * (t * 1.08 - 0.04))
        w = 0.053 * k * (0.75 + 0.25 * math.sin(math.pi * min(1.0, t * 1.15)))
        ring = []
        for j in range(12):
            a_ = j / 12 * 2 * math.pi
            ca, sa = math.cos(a_), math.sin(a_)
            ring.append(tuple(c + side * (w * math.copysign(abs(ca) ** 0.35, ca)) + up * (0.009 * math.copysign(abs(sa) ** 0.35, sa) - 0.005)))
        srings.append(ring)
    sbm = loft(srings)
    smo = mesh_obj(name + 'Sole', sbm, [sole_mat], parent=root)
    subsurf(smo, 1)


def scalp_material(name, skin, hair):
    """Skin with close-cropped hair, the hairline drawn smoothly in head coordinates (no polygon steps):
    high at the forehead, sloping down to the nape, and kept above the ears."""
    m, nt, b = new_mat(name)
    N, L = nt.nodes, nt.links
    sb = skin.node_tree.nodes['Principled BSDF']
    for inp in ('Base Color', 'Roughness', 'Subsurface Weight', 'Subsurface Radius', 'Specular IOR Level'):
        try:
            b.inputs[inp].default_value = sb.inputs[inp].default_value
        except Exception:
            pass
    hb = N.new('ShaderNodeBsdfPrincipled')
    hb.inputs['Base Color'].default_value = hair.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value
    hb.inputs['Roughness'].default_value = 0.55
    tc = N.new('ShaderNodeTexCoord')
    sep = N.new('ShaderNodeSeparateXYZ')
    L.new(tc.outputs['Object'], sep.inputs[0])

    def math_(op, a, b_=None, v=None):
        n_ = N.new('ShaderNodeMath')
        n_.operation = op
        for i, x in enumerate((a, b_)):
            if x is None:
                continue
            if isinstance(x, (int, float)):
                n_.inputs[i].default_value = x
            else:
                L.new(x, n_.inputs[i])
        return n_.outputs[0]
    t = N.new('ShaderNodeMapRange')                                  # 0 at the back (x=-0.06) .. 1 at the front (x=0.04)
    t.interpolation_type = 'SMOOTHSTEP'
    L.new(sep.outputs[0], t.inputs['Value'])
    t.inputs['From Min'].default_value = -0.06
    t.inputs['From Max'].default_value = 0.04
    line = math_('MULTIPLY_ADD', t.outputs['Result'], 0.105)
    line.node.inputs[2].default_value = -0.035
    def ramp(val, lo, hi):                                           # smooth 0..1 between lo and hi
        r_ = N.new('ShaderNodeMapRange')
        r_.interpolation_type = 'SMOOTHSTEP'
        L.new(val, r_.inputs['Value'])
        r_.inputs['From Min'].default_value = lo
        r_.inputs['From Max'].default_value = hi
        return r_.outputs['Result']
    ear = math_('MULTIPLY', ramp(math_('ABSOLUTE', sep.outputs[1]), 0.035, 0.062), ramp(sep.outputs[0], -0.085, -0.02))
    line2 = math_('ADD', line, math_('MULTIPLY', ear, math_('MAXIMUM', math_('SUBTRACT', 0.02, line), 0.0)))
    edge = N.new('ShaderNodeMapRange')                               # a soft 3 mm hairline
    L.new(math_('SUBTRACT', sep.outputs[2], line2), edge.inputs['Value'])
    edge.inputs['From Min'].default_value = -0.0015
    edge.inputs['From Max'].default_value = 0.0015
    mix = N.new('ShaderNodeMixShader')
    L.new(edge.outputs['Result'], mix.inputs[0])
    L.new(b.outputs[0], mix.inputs[1])
    L.new(hb.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], N['Material Output'].inputs['Surface'])
    return m


def head(name, root, J, k, female, skin, hair_col, lip_col, eye_mat, white_mat, style, yaw, pitch):
    """Head with jaw and chin, nose, eyes, brows, lips and ears, and hair."""
    from mathutils import Matrix
    M = Matrix.Translation(J['head']) @ Matrix.Rotation(yaw, 4, 'Z') @ Matrix.Rotation(pitch, 4, 'Y') @ Matrix.Scale(k, 4)
    fw = 0.93 if female else 1.0                                 # a narrower jaw for women
    # (dz, front depth, back depth, half width): chin, jaw, mouth, nose, eyes, forehead, crown
    prof = [(-0.118, 0.012, 0.01, 0.012), (-0.112, 0.062, 0.035, 0.04 * fw), (-0.085, 0.082, 0.07, 0.062 * fw),
            (-0.05, 0.093, 0.092, 0.07), (-0.01, 0.1, 0.1, 0.076), (0.035, 0.1, 0.106, 0.079),
            (0.075, 0.091, 0.103, 0.077), (0.105, 0.071, 0.088, 0.066), (0.128, 0.042, 0.055, 0.042), (0.138, 0.01, 0.012, 0.01)]

    def ring(dz, fr, bk, w, n=16, grow=0.0):
        pts = []
        for j in range(n):
            a = j / n * 2 * math.pi
            ca, sa = math.cos(a), math.sin(a)
            x = (fr if ca > 0 else bk) * math.copysign(abs(ca) ** 0.85, ca) * (1 + grow)
            y = w * math.copysign(abs(sa) ** 0.85, sa) * (1 + grow)
            pts.append((x, y, dz * (1 + grow * 0.6)))
        return pts
    fine = []                                                    # four times as many rings, for a clean hairline
    for a_, b_ in zip(prof[:-1], prof[1:]):
        fine += [tuple(x + (y - x) * q / 4 for x, y in zip(a_, b_)) for q in range(4)]
    fine.append(prof[-1])
    hb = loft([ring(*p_, n=48) for p_ in fine])
    # the head mesh stays in head coordinates (the object carries the transform), so the scalp material
    # can draw the hairline from them
    ho = mesh_obj(name + 'Head', hb, [skin if style == 'bald' else scalp_material(name + 'Scalp', skin, hair_col)], parent=root)
    ho.matrix_basis = M
    subsurf(ho, 2)
    # features are placed on the real (subdivided) face surface, measured with rays, so none sink in
    bpy.context.view_layer.update()
    he = ho.evaluated_get(bpy.context.evaluated_depsgraph_get())

    def surf_x(y, z):                                            # rays in the head's own coordinates
        hit, loc, nor, _ = he.ray_cast(Vector((0.3, y, z)), Vector((-1, 0, 0)), distance=2.0)
        return loc.x if hit else 0.095
    fb = bmesh.new()
    # nose: a hull growing out of the face, with a rounded tip and nostril wings
    nx0 = surf_x(0.0, -0.012) - 0.093
    nose = [(0.086, 0.0, 0.042), (0.09, 0.007, 0.012), (0.09, -0.007, 0.012), (0.118, 0.0, -0.017), (0.112, 0.006, -0.026),
            (0.112, -0.006, -0.026), (0.102, 0.016, -0.03), (0.102, -0.016, -0.03), (0.095, 0.01, -0.036), (0.095, -0.01, -0.036),
            (0.108, 0.0, -0.035), (0.084, 0.014, -0.028), (0.084, -0.014, -0.028)]
    vs = [fb.verts.new((p_[0] + nx0, p_[1], p_[2])) for p_ in nose]
    bmesh.ops.convex_hull(fb, input=vs)
    bmesh.ops.transform(fb, verts=fb.verts, matrix=M)
    no = mesh_obj(name + 'Nose', fb, [skin], parent=root, smooth=True)
    subsurf(no, 2)
    eb = bmesh.new()

    def ell(bm, c, r, mi):
        ret = bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=1.0)
        bmesh.ops.scale(bm, verts=ret['verts'], vec=r)
        bmesh.ops.translate(bm, verts=ret['verts'], vec=c)
        for f in {f for v in ret['verts'] for f in v.link_faces}:
            f.material_index = mi
    for sgn in (1, -1):
        ex = surf_x(sgn * 0.032, 0.035)
        ell(eb, (ex - 0.005, sgn * 0.032, 0.035), (0.0115, 0.0135, 0.0092), 0)       # eyeball, just out of the socket
        ell(eb, (ex + 0.0062, sgn * 0.032, 0.0345), (0.0016, 0.0056, 0.0056), 1)     # iris and pupil
        ell(eb, (ex - 0.0035, sgn * 0.032, 0.0425), (0.0108, 0.0146, 0.0045), 3)     # upper eyelid
        bx = surf_x(sgn * 0.034, 0.055)
        ell(eb, (bx + 0.0005, sgn * 0.034, 0.0545), (0.003, 0.019, 0.0024), 2)       # eyebrow
        ell(eb, (0.0, sgn * 0.077, 0.008), (0.014, 0.0055, 0.026), 3)               # ear
    mx = surf_x(0.0, -0.057)
    ell(eb, (mx - 0.0005, 0.0, -0.0535), (0.0045, 0.019, 0.0036), 4)                # upper lip
    ell(eb, (mx - 0.0012, 0.0, -0.0608), (0.0048, 0.017, 0.0042), 4)                # lower lip
    bmesh.ops.transform(eb, verts=eb.verts, matrix=M)
    mesh_obj(name + 'Face', eb, [white_mat, eye_mat, hair_col, skin, lip_col], parent=root, smooth=True)
    if style in ('bald', 'short'):
        return
    # long hair and buns: a shell over the skull, open over the face
    long_ = style == 'long'
    hb2 = loft([ring(p_[0], p_[1], p_[2], p_[3], 24, 0.05 if not long_ else 0.075) for p_ in prof[3:]])
    kill = []
    for v in hb2.verts:
        x, y, z = v.co
        if x > 0.02 and z < (0.072 if not long_ else 0.068):
            if not (long_ and abs(y) > 0.06):                    # long hair comes down past the temples
                kill.append(v)
                continue
        if z < (-0.13 if long_ else -0.03):
            kill.append(v)
        elif not long_ and abs(y) > 0.05 and z < 0.012 and x > -0.035:
            kill.append(v)                                      # short hair stops above the ears
    bmesh.ops.delete(hb2, geom=kill, context='VERTS')
    bmesh.ops.transform(hb2, verts=hb2.verts, matrix=M)
    hro = mesh_obj(name + 'Hair', hb2, [hair_col], parent=root)
    subsurf(hro, 2)
    sol = hro.modifiers.new('Thick', 'SOLIDIFY')
    sol.thickness = 0.007 if not long_ else 0.012
    sol.offset = -1
    if long_:                                                   # hair falling to the shoulders behind the head
        rings = []
        for i in range(8):
            t = i / 7
            dz = lerp(0.07, -0.24, t)
            w = lerp(0.09, 0.1, t)
            dx = lerp(0.075, 0.035, t)
            cx = lerp(-0.035, -0.075, t)
            rings.append([(cx + dx * math.copysign(abs(math.cos(a_)) ** 0.8, math.cos(a_)),
                           w * math.copysign(abs(math.sin(a_)) ** 0.8, math.sin(a_)), dz)
                          for a_ in [j / 16 * 2 * math.pi for j in range(16)]])
        lb = loft(rings)
        bmesh.ops.transform(lb, verts=lb.verts, matrix=M)
        lo = mesh_obj(name + 'HairLong', lb, [hair_col], parent=root)
        subsurf(lo, 2)
    if style == 'bun':
        bb = bmesh.new()
        ret = bmesh.ops.create_uvsphere(bb, u_segments=20, v_segments=12, radius=0.042)
        bmesh.ops.translate(bb, verts=ret['verts'], vec=(-0.09, 0, 0.095))
        bmesh.ops.transform(bb, verts=bb.verts, matrix=M)
        mesh_obj(name + 'Bun', bb, [hair_col], parent=root, smooth=True)


def person(name, loc, yaw, seed, walk=None, phone=None, female=None, style=None, sleeves=None, neutral=False):
    """A pedestrian standing on the ground at loc, facing yaw (radians, 0 = +x)."""
    rnd = random.Random(seed)
    female = rnd.random() < 0.5 if female is None else female
    h = rnd.uniform(1.62, 1.74) if female else rnd.uniform(1.7, 1.88)
    k = h / 1.78
    g = rnd.uniform(0.92, 1.15)                                  # build
    walk = rnd.random() < 0.6 if walk is None else walk
    phone = (not walk and rnd.random() < 0.35) if phone is None else phone
    if neutral:
        walk, phone = False, False
    J = human_pose(h, female, walk, rnd.uniform(0, 2 * math.pi), phone, rnd, neutral)
    root = empty(name, loc, yaw)
    S = name + '_'
    skin = principled(S + 'Skin', srgb(rnd.choice(SKIN_TONES)), rough=0.5, spec=0.4)
    try:
        b = skin.node_tree.nodes['Principled BSDF']
        b.inputs['Subsurface Weight'].default_value = 0.12
        b.inputs['Subsurface Radius'].default_value = (0.01, 0.004, 0.002)
    except Exception:
        pass
    hair_col = principled(S + 'Hair', srgb(rnd.choice(HAIR_COLS)), rough=0.45, spec=0.5)
    tone = srgb(rnd.choice(SKIN_TONES))
    skin.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (*tone, 1)
    lip_col = principled(S + 'Lips', (tone[0] * 0.82, tone[1] * 0.55, tone[2] * 0.55) if not female else (tone[0] * 0.8, tone[1] * 0.42, tone[2] * 0.45), rough=0.35)
    eye_mat = principled(S + 'Iris', srgb(rnd.choice(['#2b1a10', '#3d2a17', '#2f4a5a', '#3c4a2a'])), rough=0.05, spec=1.0)
    white_mat = principled(S + 'EyeWhite', srgb('#ece8e2'), rough=0.1, spec=0.8)
    top = principled(S + 'Top', srgb(rnd.choice(TOP_COLS)), rough=0.85, spec=0.3)
    kind, pc = rnd.choice(PANTS)
    if kind == 'jeans':
        pants = textured(S + 'Jeans', 'plaster', srgb(pc), tuple(min(1, c * 1.25) for c in srgb(pc)), rough=0.85, bump=0.04, rough_var=0.04)
    else:
        pants = principled(S + 'Trousers', srgb(pc), rough=0.8, spec=0.3)
    shoe_mat = principled(S + 'Shoe', srgb(rnd.choice(SHOE_COLS)), rough=0.5, spec=0.4)
    sole_mat = principled(S + 'Sole', srgb('#e8e6e0' if rnd.random() < 0.5 else '#1b1b1c'), rough=0.7)
    long_sleeves = (rnd.random() < 0.45) if sleeves is None else sleeves
    style = rnd.choice(['short', 'short', 'bald'] if not female else ['long', 'long', 'bun', 'short']) if style is None else style

    def grow(r, f=1.12, a=0.008):
        return (r[0] * f + a, r[1] * f + a) if isinstance(r, tuple) else r * f + a
    torso = [((0.165 if female else 0.15) * g * k, (0.11 if female else 0.105) * g * k),     # hips
             ((0.12 if female else 0.13) * g * k, 0.09 * g * k),                             # waist
             ((0.15 if female else 0.168) * g * k, (0.122 if female else 0.108) * g * k)]   # chest
    # ---- bare skin where it shows: the neck, and the arms below the sleeves with hands and thumbs
    V = [J['neck'] + Vector((0, 0, -0.06 * k)), J['neck'], J['head'] + Vector((-0.012, 0, -0.07)) * k]
    R = [0.052 * k, 0.052 * k, 0.05 * k]
    E = [(0, 1), (1, 2)]
    roots = [0]
    for sd in ('L', 'R'):
        sh, el, wr = J['sh' + sd], J['el' + sd], J['wr' + sd]
        b0 = len(V)
        roots.append(b0)
        sgn = 1 if sd == 'L' else -1
        if long_sleeves:
            V += [wr - (wr - el) * 0.3, wr]
            R += [0.034 * k, 0.03 * k]
            E += [(b0, b0 + 1)]
            wi = b0 + 1
        else:
            V += [sh + (el - sh) * 0.35, el, el + (wr - el) * 0.35, wr]
            R += [0.05 * g * k, 0.043 * g * k, 0.041 * g * k, 0.03 * k]          # forearm muscle, then the wrist
            E += [(b0, b0 + 1), (b0 + 1, b0 + 2), (b0 + 2, b0 + 3)]
            wi = b0 + 3
        add_hand(V, R, E, wi, el, wr, sgn, k, phone and sd == 'R')
    skin_obj(S + 'Body', root, V, E, R, skin, roots=roots)

    # ---- shirt / T-shirt: it gives the torso its shape
    sV = [J['pelvis'] + Vector((0, 0, 0.005)), J['waist'], J['chest'], J['neck'] + Vector((0, 0, -0.045 * k))]
    sR = [grow(torso[0], 1.02, 0.004), grow(torso[1], 1.0, 0.006), grow(torso[2], 1.0, 0.006), 0.075 * k]
    sE = [(0, 1), (1, 2), (2, 3)]
    hems = []
    for sd in ('L', 'R'):
        b0 = len(sV)
        sh, el, wr = J['sh' + sd], J['el' + sd], J['wr' + sd]
        if long_sleeves:
            end = wr - (wr - el) * 0.15
            sV += [J['clav' + sd], sh, el, end]
            sR += [0.07 * g * k, (0.06 if female else 0.066) * g * k, 0.048 * g * k, 0.04 * k]
            sE += [(2, b0), (b0, b0 + 1), (b0 + 1, b0 + 2), (b0 + 2, b0 + 3)]
            hems.append((end, end - el, 0.033 * k))
        else:
            end = sh + (el - sh) * 0.5
            sV += [J['clav' + sd], sh, end]
            sR += [0.07 * g * k, (0.06 if female else 0.066) * g * k, 0.058 * g * k]
            sE += [(2, b0), (b0, b0 + 1), (b0 + 1, b0 + 2)]
            hems.append((end, end - sh, 0.05 * g * k))
    skin_obj(S + 'Shirt', root, sV, sE, sR, top, subd=3)
    collar_c = J['neck'] + Vector((0.004, 0, -0.036 * k))
    band(S + 'Collar', root, collar_c + Vector((0, 0, -0.006 * k)), (0.25, 0, 1), 0.054 * k, 0.0055 * k, top)

    # ---- trousers or jeans: from inside the shirt down to the shoes
    pV = [J['waist'] + Vector((0, 0, -0.03 * k)), J['pelvis']]
    pR = [grow(torso[1], 0.95, 0.0), grow(torso[0], 1.0, 0.0)]
    pE = [(0, 1)]
    for sd in ('L', 'R'):
        b0 = len(pV)
        kn, an = J['knee' + sd], J['ank' + sd]
        pV += [J['hip' + sd], kn, kn + (an - kn) * 0.35, an + Vector((0, 0, -0.018 * k))]
        pR += [0.1 * g * k, 0.064 * g * k, 0.066 * g * k, 0.054 * k]
        pE += [(1, b0), (b0, b0 + 1), (b0 + 1, b0 + 2), (b0 + 2, b0 + 3)]
    skin_obj(S + 'Trousers', root, pV, pE, pR, pants)

    # ---- shoes, head and hair, phone
    for sd in ('L', 'R'):
        shoe(S + 'Shoe' + sd, root, J['ank' + sd], shoe_mat, sole_mat, k)
    root['J'] = {key: list(v) for key, v in J.items()}                # joints, for the game export
    root['torso_half'] = ((0.15 if female else 0.168) * g * k + 0.006)
    root['k'] = k
    head(S, root, J, k, female, skin, hair_col, lip_col, eye_mat, white_mat, style,
         math.radians(rnd.uniform(-12, 12)), math.radians(16 if phone else rnd.uniform(-4, 4)))
    if phone:
        pb = bmesh.new()
        c = J['handR'] + Vector((-0.01, 0.0, 0.0))
        bm_box(pb, c.x - 0.006, c.y - 0.036, c.z - 0.075, c.x + 0.006, c.y + 0.036, c.z + 0.075, 0)
        from mathutils import Matrix
        bmesh.ops.rotate(pb, verts=pb.verts, cent=c, matrix=Matrix.Rotation(math.radians(-50), 3, 'Y'))   # screen tilted up
        mesh_obj(S + 'Phone', pb, [principled(S + 'PhoneMat', srgb('#0d0e10'), rough=0.15, spec=0.8)], parent=root, bevel=0.005)
    return root


# ---------------------------------------------------------------- world and lights
def world(sun_elev_deg, sun_az_deg, strength=1.0):
    w = bpy.data.worlds.new('Sky')
    try:
        w.use_nodes = True
    except Exception:
        pass
    scene.world = w
    nt = w.node_tree
    nt.nodes.clear()
    sky = nt.nodes.new('ShaderNodeTexSky')
    for t in ('MULTIPLE_SCATTERING', 'SINGLE_SCATTERING', 'NISHITA'):
        try:
            sky.sky_type = t
            break
        except Exception:
            continue
    sky.sun_disc = True
    sky.sun_elevation = math.radians(sun_elev_deg)
    sky.sun_rotation = math.radians(sun_az_deg)
    for attr, v in (('altitude', 50.0), ('air_density', 1.2), ('aerosol_density', 1.0), ('ozone_density', 1.0)):
        try:
            setattr(sky, attr, v)
        except Exception:
            pass
    try:
        sky.sun_intensity = 0.25
    except Exception:
        pass
    bg = nt.nodes.new('ShaderNodeBackground')
    bg.inputs['Strength'].default_value = strength
    out = nt.nodes.new('ShaderNodeOutputWorld')
    nt.links.new(sky.outputs['Color'], bg.inputs['Color'])
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    # a sun lamp along the same direction gives crisp, controllable shadows
    el, az = math.radians(sun_elev_deg), math.radians(sun_az_deg)
    # same convention as the sky texture (measured: rotation 95 deg puts the sun toward +x, slightly -y)
    sun_vec = Vector((math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)))
    print('sun direction', tuple(round(v, 3) for v in sun_vec))
    sd = bpy.data.lights.new('Sun', 'SUN')
    sd.energy = 4.5
    sd.color = (1.0, 0.55, 0.28)
    sd.angle = math.radians(0.8)
    so = bpy.data.objects.new('Sun', sd)
    COL.objects.link(so)
    so.rotation_euler = (-sun_vec).to_track_quat('-Z', 'Y').to_euler()
    return sun_vec


# ---------------------------------------------------------------- build the city
def build():
    street()
    styles = list(WALLS.keys())
    for side in (1, -1):
        x = -70.0
        i = 0
        while x < 260:
            W = random.uniform(9.5, 16.0)
            if 26.5 < x + W and x < 33.5:       # leave the cross street open at the crosswalk
                x = 34.0
                continue
            detailed = -40 < x < 110
            floors = random.randint(4, 9) if x < 120 else random.randint(6, 14)
            building('Bld_%d_%d' % (side, i), x, side, W, floors, random.choice(styles), detailed=detailed)
            x += W + (0.0 if random.random() < 0.8 else 0.4)
            i += 1
    for x in range(-40, 140, 22):
        street_lamp(x, 8.35, 1)
        street_lamp(x + 11, -8.35, -1)
    props()
    car_i20('HeroCar', '#a8381c', (6.0, -2.0, 0.0), 0.0)
    car_i20('ParkedBlue', '#1f3f6e', (-4.0, 6.0, 0.0), math.pi)
    car('ParkedWhite', '#e9e8e4', (12.5, 6.0, 0.0), math.pi)
    car('ParkedBlack', '#121316', (19.5, -6.0, 0.0), 0.0)
    car('OncomingSilver', '#8c9096', (44.0, 2.0, 0.0), math.pi)
    # pedestrians: walking along both sidewalks (clear of the lamp posts), standing at shop windows,
    # and a few crossing at the crosswalk
    pr = random.Random(2024)
    n = 0
    for side in (1, -1):
        x = -30.0
        while x < 100:
            x += pr.uniform(3.0, 7.0)
            if 26 < x < 34:
                continue
            if pr.random() < 0.3:                                      # standing by a shop window, facing the street
                person('Ped%d' % n, (x, side * pr.uniform(11.4, 11.9), 0.15), -side * math.pi / 2 + pr.uniform(-0.6, 0.6), 500 + n, walk=False)
            else:                                                      # walking along the sidewalk
                person('Ped%d' % n, (x, side * pr.uniform(9.2, 10.8), 0.15), (0.0 if pr.random() < 0.5 else math.pi) + pr.uniform(-0.12, 0.12), 500 + n, walk=True)
            n += 1
    for i, (x, y, yaw) in enumerate([(29.0, -3.5, math.pi / 2), (30.4, -2.2, math.pi / 2), (29.6, 2.8, -math.pi / 2), (31.2, 4.6, -math.pi / 2)]):
        person('Crossing%d' % i, (x, y, 0.0), yaw, 900 + i, walk=True)
    car('ParkedGreen', '#23493a', (-14.0, -6.0, 0.0), 0.0)


# ---------------------------------------------------------------- render
def setup_render(quick):
    r = scene.render
    r.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 24 if quick else 128
    try:
        scene.cycles.use_adaptive_sampling = True
        scene.cycles.adaptive_threshold = 0.02
    except Exception:
        pass
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = 'OPENIMAGEDENOISE'
    except Exception:
        pass
    scene.cycles.max_bounces = 6
    scene.cycles.glossy_bounces = 3
    scene.cycles.transmission_bounces = 4
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    r.resolution_x = 960 if quick else 1600
    r.resolution_y = 540 if quick else 900
    r.resolution_percentage = 100
    r.image_settings.file_format = 'JPEG'
    r.image_settings.quality = 92
    try:
        scene.view_settings.view_transform = 'AgX'
    except Exception:
        pass
    for look in ('AgX - Punchy', 'Punchy', 'AgX - Medium High Contrast', 'Medium High Contrast'):
        try:
            scene.view_settings.look = look
            break
        except Exception:
            continue
    scene.view_settings.exposure = 0.0
    print('colour:', scene.view_settings.view_transform, scene.view_settings.look)


def camera(name, loc, target, lens=35.0, dof=None):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.clip_end = 1500
    if dof:
        cd.dof.use_dof = True
        cd.dof.focus_distance = (Vector(target) - Vector(loc)).length
        cd.dof.aperture_fstop = dof
    co = bpy.data.objects.new(name, cd)
    COL.objects.link(co)
    co.location = loc
    co.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    return co


SHOTS = [
    ('01_street_sunset', (-24.0, -1.5, 1.7), (80.0, 2.0, 6.0), 28, None),
    ('02_hero_car_front', (11.5, -6.4, 0.95), (6.0, -2.0, 0.7), 40, 4.0),
    ('03_hero_car_rear', (0.0, -5.6, 1.2), (6.2, -2.0, 0.75), 35, 5.6),
    ('04_parked_cars', (16.0, 2.6, 1.3), (2.0, 6.5, 0.8), 32, None),
    ('05_wheel_detail', (8.6, -4.9, 0.6), (7.42, -2.8, 0.36), 50, 4.0),
    ('06_facades_up', (-5.0, -6.5, 1.6), (6.0, 13.0, 18.0), 20, None),
    ('07_shopfronts', (-12.0, 9.8, 1.65), (10.0, 12.0, 2.6), 28, None),
    ('08_aerial', (-45.0, -4.0, 38.0), (35.0, 2.0, 0.0), 30, None),
    ('09_crosswalk', (38.0, -10.0, 1.6), (22.0, 2.0, 1.5), 30, None),
    ('10_rooftops', (0.0, -2.0, 30.0), (30.0, 22.0, 20.0), 26, None),
]


CAR_STUDIO = [
    ('car_front34', (5.2, -4.6, 1.25), (0.3, 0, 0.62), 50, None),
    ('car_rear34', (-5.0, -4.4, 1.35), (-0.3, 0, 0.62), 50, None),
    ('car_side', (0.0, -7.5, 0.9), (0.0, 0, 0.65), 50, None),
    ('car_top', (3.2, -3.4, 4.2), (0.0, 0, 0.6), 40, None),
    ('car_wheel', (2.4, -2.3, 0.5), (1.42, -0.79, 0.34), 60, None),
    ('car_headlight', (3.4, -1.9, 0.85), (2.2, -0.7, 0.66), 70, None),
    ('car_taillight', (-3.5, -1.9, 0.95), (-2.25, -0.6, 0.74), 70, None),
    ('car_interior', (1.2, -2.6, 1.45), (-0.3, 0.0, 1.05), 45, None),
    ('car_cockpit', (2.7, -0.7, 2.5), (0.0, 0.2, 0.95), 32, None),
]


I20_STUDIO = [
    ('i20_front34', (5.0, -4.2, 1.4), (0.3, 0, 0.75), 50, None),
    ('i20_rear34', (-4.8, -4.2, 1.5), (-0.3, 0, 0.8), 50, None),
    ('i20_side', (0.0, -7.5, 1.0), (0.0, 0, 0.75), 50, None),
    ('i20_front', (6.5, 0.0, 1.0), (0.0, 0, 0.75), 50, None),
    ('i20_rear', (-6.5, 0.0, 1.1), (0.0, 0, 0.8), 50, None),
    ('i20_photo_match', (2.9, -1.35, 1.25), (1.75, -0.62, 0.82), 55, None),
    ('i20_taillight', (-3.2, -1.7, 1.15), (-1.95, -0.6, 0.92), 60, None),
    ('i20_cockpit', (2.6, -0.7, 2.4), (0.2, 0.2, 1.0), 32, None),
    ('i20_wheel', (2.2, -2.2, 0.5), (1.2, -0.745, 0.31), 55, None),
    ('i20_top', (3.0, -3.2, 4.3), (0.0, 0, 0.8), 40, None),
    ('i20_doors', (-0.2, -4.2, 1.05), (-0.2, -0.8, 0.8), 40, None),
]


PEOPLE_STUDIO = [
    ('people_group', (6.0, 0.0, 1.45), (0.0, 0.0, 0.95), 35, None),
    ('people_walk34', (3.6, -3.6, 1.6), (0.0, -0.9, 1.0), 45, None),
    ('people_close', (1.7, -1.35, 1.55), (0.0, -1.35, 1.35), 50, None),
    ('people_face', (0.8, -0.45, 1.65), (0.0, -0.45, 1.6), 70, None),
    ('people_hands', (0.75, -2.95, 0.95), (0.02, -2.45, 0.8), 50, None),
    ('people_feet', (1.1, -1.25, 0.32), (0.0, -0.9, 0.08), 35, None),
    ('people_back', (-5.5, 0.4, 1.45), (0.0, 0.0, 0.95), 35, None),
    ('people_side', (0.0, -6.5, 1.2), (0.0, 0.0, 0.95), 40, None),
    ('people_feet_back', (-1.6, 2.6, 0.35), (0.0, 2.25, 0.08), 35, None),
    ('people_head_side', (0.05, -2.9, 1.62), (0.02, -2.25, 1.6), 60, None),
    ('people_phone', (1.3, -0.15, 1.6), (0.05, -0.45, 1.35), 40, None),
]


def main():
    setup_render(QUICK)
    world(sun_elev_deg=5.0, sun_az_deg=62.0, strength=1.0)
    if '--peoplestudio' in argv:
        bm = bmesh.new()
        bm_box(bm, -30, -30, -0.2, 30, 30, 0.0, 0)
        mesh_obj('Ground', bm, [M['concrete']])
        for i, y in enumerate((-2.25, -1.35, -0.45, 0.45, 1.35, 2.25)):
            person('Person%d' % i, (0.0, y, 0.0), [0.15, -0.3, 0.05, 0.4, -0.1, 0.25][i], 101 + i * 7,
                   walk=[False, True, False, True, False, True][i], phone=[False, False, True, False, False, False][i])
        cams = [(s_[0], camera(*s_)) for s_ in PEOPLE_STUDIO]
    elif '--carstudio' in argv:
        bm = bmesh.new()
        bm_box(bm, -30, -30, -0.2, 30, 30, 0.0, 0)
        mesh_obj('Ground', bm, [M['asphalt']])
        if 'i20' in argv:
            car_i20('StudioCar', '#a8381c', (0, 0, 0), 0.0)
            cams = [(s[0], camera(*s)) for s in I20_STUDIO]
        else:
            car('StudioCar', '#9e1b1b', (0, 0, 0), 0.0)
            cams = [(s[0], camera(*s)) for s in CAR_STUDIO]
    else:
        build()
        cams = [(s[0], camera(*s)) for s in SHOTS]
    if BLEND:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(BLEND))
    for name, cam in cams:
        if ONLY and name not in ONLY:
            continue
        scene.camera = cam
        scene.render.filepath = os.path.join(OUT, name + '.jpg')
        print('rendering', name, flush=True)
        bpy.ops.render.render(write_still=True)


if __name__ == '__main__':
    main()
