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
    w = lerp(g['wb'], g['wt'], pz)
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
            elif (vc < 0.26 or vc > 0.74) and any(a < xc < b for (a, b) in black_x):
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
    # wipers at the base of the windscreen
    wb = bmesh.new()
    for yy in (-0.05, 0.42):
        p = gh_point(g['xf0'] - 0.1, 0.5)
        bm_box(wb, p.x - 0.02, yy - 0.42, p.z + 0.005, p.x + 0.0, yy, p.z + 0.02, 0)
    mesh_obj(name + '_Wipers', wb, [mats['dark']], parent=root)

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
I20_GH = dict(xr0=-1.95, xr1=-1.62, xf1=0.12, xf0=0.97, zb=0.88, zb_rear=0.98, roof=1.5, roof_rear=1.37, wb=0.78, wt=0.63)
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
    cladding = textured(S + 'Cladding', 'plaster', srgb('#1a1a1b'), srgb('#232325'), rough=0.75, bump=0.5)
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
    gh_shell(S + 'Cabin', [shell_mat, seal], root, [w[:4] for w in windows], black_x=[(-0.26, -0.12), (-1.12, -1.02)])
    for k, (x0, x1, v0, v1, gm) in enumerate(windows):
        thin(gh_surface(S + 'Window%d' % k, gm, root, x0, x1, v0, v1, 140, 20), 0.005)
    rb = bmesh.new()
    for v in (0.29, 0.71):                                            # roof rails on three feet
        xs = [lerp(-1.48, -0.02, i / 24) for i in range(25)]
        pts = [gh_point(x, v) for x in xs]
        for a, b in zip(pts[:-1], pts[1:]):
            bm_hex(rb, [(a.x, a.y - 0.016, a.z + 0.03), (b.x, b.y - 0.016, b.z + 0.03), (b.x, b.y + 0.016, b.z + 0.03), (a.x, a.y + 0.016, a.z + 0.03),
                        (a.x, a.y - 0.016, a.z + 0.06), (b.x, b.y - 0.016, b.z + 0.06), (b.x, b.y + 0.016, b.z + 0.06), (a.x, a.y + 0.016, a.z + 0.06)], 0)
        for i in (0, 12, 24):
            p = pts[i]
            bm_box(rb, p.x - 0.04, p.y - 0.018, p.z - 0.01, p.x + 0.04, p.y + 0.018, p.z + 0.035, 0)
    mesh_obj(S + 'RoofRails', rb, [rail], parent=root, bevel=0.006)
    thin(gh_surface(S + 'Spoiler', paint, root, g['xr1'] - 0.02, g['xr1'] + 0.16, 0.2, 0.8, 24, 20, 0.022), 0.02)
    p = gh_point(g['xr0'] + 0.1, 0.5)
    wb_ = bmesh.new()
    bm_box(wb_, p.x - 0.01, -0.02, p.z + 0.006, p.x + 0.01, 0.24, p.z + 0.018, 0)   # rear wiper
    bm_box(wb_, g['xf0'] - 0.12, -0.5, gh_point(g['xf0'] - 0.1, 0.5).z + 0.006, g['xf0'] - 0.1, -0.02, gh_point(g['xf0'] - 0.1, 0.5).z + 0.02, 0)
    bm_box(wb_, g['xf0'] - 0.12, 0.0, gh_point(g['xf0'] - 0.1, 0.5).z + 0.006, g['xf0'] - 0.1, 0.46, gh_point(g['xf0'] - 0.1, 0.5).z + 0.02, 0)
    mesh_obj(S + 'Wipers', wb_, [mats['dark']], parent=root)

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
        for xs_ in (0.83, -0.19, -1.06):                              # door shut lines
            decal(S + 'Seam', body, seam, root, lambda u, v, s=s, xs_=xs_: (xs_ - 0.004 + u * 0.008, s * 3.0, 0.5 + v * 0.52), (0, -s, 0), 1, 30, 0.002)
        for hx in (0.32, -0.6):
            decal(S + 'Handle', body, mats['chrome'], root, lambda u, v, s=s, hx=hx: (hx - 0.09 + u * 0.18, s * 3.0, 0.93 + v * 0.03), (0, -s, 0), 6, 2, 0.008)
        decal(S + 'Seam', body, seam, root, lambda u, v, s=s: (0.98 + u * 0.95, s * (0.7 + v * 0.008), 3.0), (0, 0, -1), 30, 1, 0.002)   # hood sides
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
            u = -grow + u * (1 + 2 * grow)
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
            u = -grow + u * (1 + 2 * grow)
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
        base = gh_point(g['xf0'] - 0.24, 0.02 if s < 0 else 0.98)
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
]


def main():
    setup_render(QUICK)
    world(sun_elev_deg=5.0, sun_az_deg=62.0, strength=1.0)
    if '--carstudio' in argv:
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


main()
