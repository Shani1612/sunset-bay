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


def wheel(root, x, side, mats):
    """Tyre, five-spoke rim with lug nuts, brake disc and caliper; axle along y, outer face toward `side`."""
    from mathutils import Matrix
    R, Wd = 0.335, 0.225
    y = side * 0.79
    # tyre: a rounded rubber profile spun around the axle (tread, shoulders, sidewalls down to the rim)
    prof = []
    ri, hw2, rc = 0.228, Wd / 2, 0.06
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
    rr, face = 0.235, y + side * 0.095

    def axle_cyl(radius, depth, yc, seg, mi, caps=True, r2=None):
        ret = bmesh.ops.create_cone(bm, cap_ends=caps, segments=seg, radius1=radius, radius2=r2 if r2 else radius, depth=depth)
        bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(-side * math.pi / 2, 3, 'X'))
        bmesh.ops.translate(bm, verts=ret['verts'], vec=(x, yc, R))
        for f in {f for v in ret['verts'] for f in v.link_faces}:
            f.material_index = mi

    axle_cyl(rr, 0.2, y + side * 0.01, 48, 2, caps=False)          # inner barrel (open)
    axle_cyl(rr + 0.012, 0.03, face, 48, 0, caps=False)             # polished lip
    axle_cyl(0.17, 0.025, face - side * 0.07, 40, 3)                # brake disc
    axle_cyl(0.075, 0.05, face - side * 0.02, 24, 0)                # hub
    axle_cyl(0.045, 0.02, face + side * 0.008, 20, 1)               # centre cap
    for k in range(5):                                              # lug nuts
        a = k * 2 * math.pi / 5 + math.pi / 5
        ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.011, radius2=0.011, depth=0.02)
        bmesh.ops.rotate(bm, verts=ret['verts'], cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'))
        bmesh.ops.translate(bm, verts=ret['verts'], vec=(x + 0.05 * math.sin(a), face + side * 0.006, R + 0.05 * math.cos(a)))
        for f in {f for v in ret['verts'] for f in v.link_faces}:
            f.material_index = 1
    # tapered, slightly dished spokes: narrow at the hub, wide at the rim
    for k in range(5):
        a = k * 2 * math.pi / 5
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for (rad, half, dy) in ((0.06, 0.022, 0.0), (rr - 0.005, 0.05, -0.03)):
            for (sgn, t) in ((-1, 0), (1, 0)):
                pass
        quad = []
        for dz in (-0.018, 0.018):                                   # spoke thickness along the axle
            ring = []
            for (rad, half, dish) in ((0.065, 0.022, 0.0), (rr - 0.004, 0.052, 0.028)):
                for sgn in (-1, 1):
                    u, v = sgn * half, rad                           # u across the spoke, v along it
                    px, pz = u * ca + v * sa, -u * sa + v * ca
                    ring.append((x + px, face - side * (0.012 + dish) + side * dz, R + pz))
            quad.append(ring)
        b0, t0 = quad[0], quad[1]
        # box order: bottom 0..3 (hub-, hub+, rim+, rim-), top 4..7
        bm_hex(bm, [b0[0], b0[1], b0[3], b0[2], t0[0], t0[1], t0[3], t0[2]], 0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm_box(bm, x + 0.08, face - side * 0.1, R + 0.06, x + 0.2, face - side * 0.04, R + 0.17, 4)   # caliper
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
GH = dict(xr0=-1.45, xr1=-0.72, xf1=0.12, xf0=0.92, zb=0.86, roof=1.43, wb=0.84, wt=0.66)
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
    h = max(0.012, (g['roof'] - g['zb']) * gh_top(x))
    w = lerp(g['wb'], g['wt'], pz)
    return Vector((x, py * w, g['zb'] + pz * h))


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
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    # recalc can pick the inside of an open patch: make the faces point away from the cabin centre
    centre = Vector((0, 0, GH['zb'] + 0.1))
    flips = [f for f in bm.faces if f.normal.dot(f.calc_center_median() - centre) < 0]
    if len(flips) > len(bm.faces) / 2:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    return mesh_obj(name, bm, [mat], parent=root, smooth=True)


def decal(name, body, mat, root, origin_fn, direction, nu, nv, offset=0.004, mask=None):
    """Project a grid of rays onto the evaluated body and build a thin skin that sits exactly on it.
    origin_fn(u, v) -> ray origin (body space) for u, v in [0, 1]."""
    dg = bpy.context.evaluated_depsgraph_get()
    be = body.evaluated_get(dg)
    d = Vector(direction).normalized()
    pts = {}
    for i in range(nu + 1):
        for j in range(nv + 1):
            u, v = i / nu, j / nv
            if mask and not mask(u, v):
                continue
            o = Vector(origin_fn(u, v))
            hit, loc, nor, _ = be.ray_cast(o, d, distance=10.0)
            if hit:
                if nor.dot(d) > 0:
                    nor = -nor
                pts[(i, j)] = loc + nor * offset
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
    if sum(f.normal.dot(d) for f in bm.faces) > 0:   # face the viewer, against the ray direction
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    return mesh_obj(name, bm, [mat], parent=root, smooth=True)


def body_hit(body, origin, direction):
    dg = bpy.context.evaluated_depsgraph_get()
    hit, loc, nor, _ = body.evaluated_get(dg).ray_cast(Vector(origin), Vector(direction).normalized(), distance=10.0)
    return (loc, nor) if hit else (None, None)


def car(name, color, loc, rot_z, lights_on=True):
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
    glass = principled(name + '_Glass', srgb('#0a0d11'), rough=0.015, ior=1.52, spec=1.0)
    head = principled(name + '_Head', srgb('#e9eef8'), rough=0.04, emit=srgb('#fff6e8'), emit_str=14.0 if lights_on else 0.0, spec=1.0)
    head_housing = principled(name + '_HeadHousing', srgb('#1a1c20'), rough=0.15, metal=0.6)
    tail = principled(name + '_Tail', srgb('#5a0606'), rough=0.05, emit=srgb('#ff1a10'), emit_str=5.0 if lights_on else 0.0, spec=1.0)
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
    body = mesh_obj(name + '_Body', loft(rings), [paint, mats['dark']], parent=root)
    subsurf(body, 3)
    for wx in (1.42, -1.4):
        cbm = bmesh.new()
        ret = bmesh.ops.create_cone(cbm, cap_ends=True, segments=64, radius1=0.4, radius2=0.4, depth=2.6)
        bmesh.ops.rotate(cbm, verts=ret['verts'], cent=(0, 0, 0), matrix=__import__('mathutils').Matrix.Rotation(math.pi / 2, 3, 'X'))
        bmesh.ops.translate(cbm, verts=ret['verts'], vec=(wx, 0, 0.345))
        cut = mesh_obj(name + '_ArchCut', cbm, [mats['dark']], parent=root)
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
    # keep the paint's smooth shading from bleeding into the cut arch surfaces (that showed as steps)
    es = body.modifiers.new('ArchEdges', 'EDGE_SPLIT')
    es.split_angle = math.radians(40)
    es.use_edge_sharp = False
    lbm = bmesh.new()
    for wx in (1.42, -1.4):
        for s in (1, -1):
            ret = bmesh.ops.create_cone(lbm, cap_ends=True, segments=48, radius1=0.385, radius2=0.385, depth=0.5)
            bmesh.ops.rotate(lbm, verts=ret['verts'], cent=(0, 0, 0), matrix=__import__('mathutils').Matrix.Rotation(math.pi / 2, 3, 'X'))
            bmesh.ops.translate(lbm, verts=ret['verts'], vec=(wx, s * 0.5, 0.345))
    # only the arch above the wheel centre: the lower half would stick out under the car
    bmesh.ops.delete(lbm, geom=[v for v in lbm.verts if v.co.z < 0.3], context='VERTS')
    mesh_obj(name + '_Liner', lbm, [mats['dark']], parent=root)
    bpy.context.view_layer.update()

    # ---- cabin: one painted shell (roof and pillars) with glass set into it
    g = GH
    gh_surface(name + '_Cabin', paint, root, g['xr0'], g['xf0'], 0.0, 1.0, 64, 40)
    off = 0.006
    gh_surface(name + '_Windscreen', glass, root, g['xf1'] + 0.03, g['xf0'] - 0.07, 0.17, 0.83, 24, 24, off)
    gh_surface(name + '_RearWindow', glass, root, g['xr0'] + 0.08, g['xr1'] - 0.03, 0.2, 0.8, 20, 24, off)
    for (v0, v1) in ((0.035, 0.205), (0.795, 0.965)):            # side windows, split by the B-pillar
        gh_surface(name + '_SideFront', glass, root, -0.22, g['xf0'] - 0.16, v0, v1, 28, 8, off)
        gh_surface(name + '_SideRear', glass, root, g['xr0'] + 0.22, -0.34, v0, v1, 24, 8, off)
    # wipers at the base of the windscreen
    wb = bmesh.new()
    for yy in (-0.05, 0.42):
        p = gh_point(g['xf0'] - 0.1, 0.5)
        bm_box(wb, p.x - 0.02, yy - 0.42, p.z + 0.005, p.x + 0.0, yy, p.z + 0.02, 0)
    mesh_obj(name + '_Wipers', wb, [mats['dark']], parent=root)

    # ---- parts projected onto the real body surface
    S = name + '_'
    for s in (1, -1):
        # headlights: dark housing, then the lens on top
        hy = s * 0.6
        # headlights: a slanted lens (higher at the outer corner) on a dark housing; shape comes from the
        # projection grid itself, so the edges are clean
        def lamp_origin(u, v, w, h, z0, rise):
            yy = s * (0.37 + u * w)                     # u = 0 inner end, u = 1 outer corner
            return (4.0, yy, z0 + rise * u + v * (h - 0.04 * u))
        decal(S + 'HeadHousing', body, head_housing, root, lambda u, v: lamp_origin(u, v, 0.48, 0.13, 0.6, 0.05), (-1, 0, 0), 40, 10, 0.004)
        decal(S + 'Headlight', body, head, root, lambda u, v: lamp_origin(0.04 + u * 0.92, v, 0.44, 0.08, 0.625, 0.05), (-1, 0, 0), 40, 8, 0.007)
        # tail lights wrapping the rear corners
        decal(S + 'Taillight', body, tail, root, lambda u, v, s=s: (-4.0, s * (0.36 + u * 0.4), 0.81 + v * 0.065), (1, 0, 0), 24, 6, 0.006)
        # side skirt, door handles
        decal(S + 'Skirt', body, mats['dark'], root, lambda u, v, s=s: (-1.0 + u * 2.0, s * 3.0, 0.3 + v * 0.07), (0, -s, 0), 40, 3, 0.004)
        for hx in (0.25, -0.62):
            decal(S + 'Handle', body, mats['chrome'], root, lambda u, v, s=s, hx=hx: (hx - 0.09 + u * 0.18, s * 3.0, 0.8 + v * 0.03), (0, -s, 0), 6, 2, 0.008)
        # door shut lines: front door, B-pillar, rear door, and the line under the window
        for xs in (0.9, -0.28, -1.12):
            decal(S + 'Seam', body, seam, root, lambda u, v, s=s, xs=xs: (xs - 0.004 + u * 0.008, s * 3.0, 0.33 + v * 0.62), (0, -s, 0), 1, 30, 0.002)
        # hood and trunk side lines
        decal(S + 'Seam', body, seam, root, lambda u, v, s=s: (0.98 + u * 1.25, s * (0.74 + v * 0.008), 3.0), (0, 0, -1), 30, 1, 0.002)
        decal(S + 'Seam', body, seam, root, lambda u, v, s=s: (-2.25 + u * 0.75, s * (0.74 + v * 0.008), 3.0), (0, 0, -1), 12, 1, 0.002)
    decal(S + 'Seam', body, seam, root, lambda u, v: (0.98 + v * 0.008, -0.74 + u * 1.48, 3.0), (0, 0, -1), 30, 1, 0.002)   # hood rear edge
    decal(S + 'Seam', body, seam, root, lambda u, v: (-1.5 + v * 0.008, -0.74 + u * 1.48, 3.0), (0, 0, -1), 30, 1, 0.002)  # trunk front edge
    # grille, lower intake, number plates, rear diffuser
    decal(S + 'GrilleFrame', body, mats['chrome'], root, lambda u, v: (4.0, -0.43 + u * 0.86, 0.5 + v * 0.17), (-1, 0, 0), 24, 6, 0.004)
    decal(S + 'Grille', body, grille, root, lambda u, v: (4.0, -0.4 + u * 0.8, 0.515 + v * 0.14), (-1, 0, 0), 24, 6, 0.007)
    decal(S + 'Intake', body, grille, root, lambda u, v: (4.0, -0.62 + u * 1.24, 0.33 + v * 0.1), (-1, 0, 0), 30, 4, 0.004)
    decal(S + 'PlateF', body, plate, root, lambda u, v: (4.0, -0.25 + u * 0.5, 0.37 + v * 0.11), (-1, 0, 0), 10, 3, 0.012)
    decal(S + 'PlateR', body, plate, root, lambda u, v: (-4.0, -0.25 + u * 0.5, 0.56 + v * 0.11), (1, 0, 0), 10, 3, 0.008)
    decal(S + 'Diffuser', body, mats['dark'], root, lambda u, v: (-4.0, -0.8 + u * 1.6, 0.33 + v * 0.13), (1, 0, 0), 30, 4, 0.004)
    decal(S + 'TailBar', body, tail, root, lambda u, v: (-4.0, -0.32 + u * 0.64, 0.85 + v * 0.02), (1, 0, 0), 20, 1, 0.006)
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
    car('HeroCar', '#9e1b1b', (6.0, -2.0, 0.0), 0.0)
    car('ParkedBlue', '#1f3f6e', (-4.0, 6.0, 0.0), math.pi)
    car('ParkedWhite', '#e9e8e4', (12.5, 6.0, 0.0), math.pi)
    car('ParkedBlack', '#121316', (19.5, -6.0, 0.0), 0.0)
    car('OncomingSilver', '#8c9096', (44.0, 2.0, 0.0), math.pi)
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
]


def main():
    setup_render(QUICK)
    world(sun_elev_deg=5.0, sun_az_deg=62.0, strength=1.0)
    if '--carstudio' in argv:
        bm = bmesh.new()
        bm_box(bm, -30, -30, -0.2, 30, 30, 0.0, 0)
        mesh_obj('Ground', bm, [M['asphalt']])
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


main()
