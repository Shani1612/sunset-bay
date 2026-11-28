"""Street furniture for the city: things on the sidewalks that cars knock over and people kick.

    blender -b -P blender/make_props.py -- city/assets/props.glb [--renders DIR]

  Cone       a traffic cone (orange, two reflective bands)        0.50 m, 3 kg
  CafeTable  a round bistro table (cast-iron foot, marble top)    0.74 m, 14 kg
  CafeChair  a bistro chair (steel frame, rattan seat and back)   0.86 m, 4 kg
  Planter    a concrete planter with a shrub                      0.95 m, 160 kg
  Scooter    a parked electric scooter on its stand               1.15 m, 16 kg
  NewsBox    a newspaper box (blue, a window)                     1.05 m, 30 kg
  MailBox    a red post box on its pole                           1.35 m, 22 kg
  Dumpster   a green waste container on wheels                    1.30 m, 240 kg
  Barrier    a red and white plastic road barrier                 1.00 m, 9 kg
  SignPost   a street name sign on its pole (blue, white)         2.80 m, 12 kg

Each model sits on the ground at its origin (x right, y up, z forward in the game's axes after export) and
uses one material: a colour atlas (8 x 4 cells of 8 x 8 texels, each face's texture coordinates at its cell's
centre), so each kind is one draw call however many are placed. Its node's extras tell the game how it moves
when hit: its collision radius and height, its mass, and how it goes (fly: it tumbles free; tip: it falls over
about its foot, as a pole does).
"""
import bpy, bmesh, math, os, sys, json
import numpy as np
from mathutils import Vector, Matrix

A = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUT = os.path.abspath(A[0] if A else 'city/assets/props.glb')
RENDERS = os.path.abspath(A[A.index('--renders') + 1]) if '--renders' in A else None

# the atlas: name -> (colour, roughness, metalness)
CELLS = [('Orange', '#f05a14', 0.55, 0.0), ('Reflect', '#e9ecef', 0.25, 0.3), ('Black', '#18191b', 0.7, 0.0),
         ('Iron', '#2b2d30', 0.45, 0.8), ('Marble', '#e8e4dc', 0.25, 0.0), ('Steel', '#a9aeb4', 0.3, 0.9),
         ('Rattan', '#b88a52', 0.8, 0.0), ('Concrete', '#b9b4aa', 0.9, 0.0), ('Leaf', '#3f6e2f', 0.8, 0.0),
         ('LeafLight', '#5a8a3a', 0.8, 0.0), ('Soil', '#4a3a2c', 0.95, 0.0), ('Deck', '#26282b', 0.6, 0.2),
         ('Teal', '#1f9aa5', 0.4, 0.2), ('Rubber', '#141415', 0.9, 0.0), ('Blue', '#1f4fa8', 0.4, 0.3),
         ('Glass', '#5a6a72', 0.05, 0.3), ('Red', '#c3241d', 0.4, 0.2), ('White', '#f1f1ee', 0.5, 0.0),
         ('Green', '#2e6b3a', 0.55, 0.3), ('SignBlue', '#1d4b8f', 0.4, 0.1), ('Yellow', '#e6b822', 0.5, 0.1),
         ('Paper', '#dcd8cc', 0.9, 0.0)]
CELL = {n: i for i, (n, *_r) in enumerate(CELLS)}


def srgb(h):
    h = h.lstrip('#')
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def cell_uv(name):
    i = CELL[name]
    return (((i % 8) + 0.5) / 8, ((i // 8) + 0.5) / 4)


class Build:
    """A mesh being made: parts added with a material (an atlas cell) each."""
    def __init__(self):
        self.bm = bmesh.new()
        self.uv = self.bm.loops.layers.uv.new('UVMap')

    def _paint(self, faces, cell):
        uv = cell_uv(cell)
        for f in faces:
            f.smooth = True
            for l in f.loops:
                l[self.uv].uv = uv

    def cyl(self, r1, r2, h, cell, at=(0, 0, 0), segs=20, rot=None, caps=True):
        """A (truncated) cone along z from at, of height h, radii r1 (bottom), r2 (top)."""
        ret = bmesh.ops.create_cone(self.bm, cap_ends=caps, cap_tris=False, segments=segs, radius1=r1, radius2=r2, depth=h)
        vs = ret['verts']
        bmesh.ops.translate(self.bm, verts=vs, vec=(0, 0, h / 2))
        if rot is not None:
            bmesh.ops.rotate(self.bm, verts=vs, cent=(0, 0, 0), matrix=rot)
        bmesh.ops.translate(self.bm, verts=vs, vec=at)
        self._paint({f for v in vs for f in v.link_faces}, cell)
        return vs

    def box(self, size, cell, at=(0, 0, 0), bevel=0.0, rot=None):
        """A box of size (x, y, z) with its bottom centre at `at` (edges rounded by bevel)."""
        before = set(self.bm.faces)
        ret = bmesh.ops.create_cube(self.bm, size=1.0)
        vs = ret['verts']
        bmesh.ops.scale(self.bm, verts=vs, vec=size)
        bmesh.ops.translate(self.bm, verts=vs, vec=(0, 0, size[2] / 2))
        faces = {f for v in vs for f in v.link_faces}
        if bevel:
            edges = list({e for f in faces for e in f.edges})
            bmesh.ops.bevel(self.bm, geom=edges + vs, offset=bevel, segments=2, affect='EDGES', profile=0.5)
            faces = {f for f in self.bm.faces if f not in before}       # (the bevel remakes the box's own faces too)
            vs = list({v for f in faces for v in f.verts})
        if rot is not None:
            bmesh.ops.rotate(self.bm, verts=vs, cent=(0, 0, 0), matrix=rot)
        bmesh.ops.translate(self.bm, verts=vs, vec=at)
        self._paint({f for f in faces if f.is_valid}, cell)
        return vs

    def ball(self, r, cell, at, sub=2, squash=1.0):
        ret = bmesh.ops.create_icosphere(self.bm, subdivisions=sub, radius=r)
        vs = ret['verts']
        bmesh.ops.scale(self.bm, verts=vs, vec=(1, 1, squash))
        bmesh.ops.translate(self.bm, verts=vs, vec=at)
        self._paint({f for v in vs for f in v.link_faces}, cell)
        return vs

    def ring(self, R, r, cell, at, axis='x', segs=24, tsegs=8):
        """A torus (a wheel's tyre): radius R, tube r, its axis along x or y."""
        ret = bmesh.ops.create_circle(self.bm, cap_ends=False, segments=segs, radius=R)
        rows = []
        for i in range(segs):
            a = 2 * math.pi * i / segs
            rows.append([self.bm.verts.new((0, (R + r * math.cos(2 * math.pi * j / tsegs)) * math.cos(a),
                                            (R + r * math.cos(2 * math.pi * j / tsegs)) * math.sin(a) + 0)) for j in range(tsegs)])
        bmesh.ops.delete(self.bm, geom=ret['verts'], context='VERTS')
        faces = []
        for i in range(segs):
            a_, b_ = rows[i], rows[(i + 1) % segs]
            for j in range(tsegs):
                faces.append(self.bm.faces.new((a_[j], b_[j], b_[(j + 1) % tsegs], a_[(j + 1) % tsegs])))
        vs = [v for row in rows for v in row]
        # the tube's offset along the axle
        for i in range(segs):
            for j in range(tsegs):
                rows[i][j].co.x += r * math.sin(2 * math.pi * j / tsegs)
        if axis == 'y':
            bmesh.ops.rotate(self.bm, verts=vs, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Z'))
        bmesh.ops.translate(self.bm, verts=vs, vec=at)
        bmesh.ops.recalc_face_normals(self.bm, faces=faces)
        self._paint(faces, cell)

    def done(self, name, mat):
        bmesh.ops.recalc_face_normals(self.bm, faces=self.bm.faces)
        me = bpy.data.meshes.new(name); self.bm.to_mesh(me); self.bm.free()
        me.materials.append(mat)
        ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
        return ob


def RX(d):
    return Matrix.Rotation(math.radians(d), 3, 'X')


def RY(d):
    return Matrix.Rotation(math.radians(d), 3, 'Y')


def RZ(d):
    return Matrix.Rotation(math.radians(d), 3, 'Z')


# ---------------------------------------------------------------- the props (Blender: x right, y back-to-front is -y, z up)
def cone():
    b = Build()
    b.box((0.38, 0.38, 0.035), 'Black', bevel=0.01)
    b.cyl(0.14, 0.11, 0.12, 'Orange', at=(0, 0, 0.035), caps=False)
    b.cyl(0.11, 0.094, 0.06, 'Reflect', at=(0, 0, 0.155), caps=False)
    b.cyl(0.094, 0.075, 0.09, 'Orange', at=(0, 0, 0.215), caps=False)
    b.cyl(0.075, 0.062, 0.05, 'Reflect', at=(0, 0, 0.305), caps=False)
    b.cyl(0.062, 0.03, 0.145, 'Orange', at=(0, 0, 0.355))
    return b, dict(r=0.19, h=0.5, mass=3, how='fly')


def cafe_table():
    b = Build()
    b.cyl(0.24, 0.2, 0.03, 'Iron', at=(0, 0, 0), segs=24)
    b.cyl(0.05, 0.035, 0.12, 'Iron', at=(0, 0, 0.03))
    b.cyl(0.028, 0.028, 0.52, 'Iron', at=(0, 0, 0.15), segs=12)
    b.cyl(0.11, 0.06, 0.04, 'Iron', at=(0, 0, 0.66))
    b.cyl(0.32, 0.32, 0.035, 'Marble', at=(0, 0, 0.705), segs=32)
    return b, dict(r=0.33, h=0.74, mass=14, how='fly')


def cafe_chair():
    b = Build()
    for x in (-0.19, 0.19):
        for y in (-0.19, 0.19):
            b.cyl(0.012, 0.012, 0.45, 'Steel', at=(x, y, 0), segs=8, rot=RY(math.copysign(4, x)) @ RX(math.copysign(-4, y)))
    b.cyl(0.215, 0.215, 0.035, 'Rattan', at=(0, 0, 0.45), segs=24)
    b.cyl(0.222, 0.222, 0.015, 'Steel', at=(0, 0, 0.445), segs=24, caps=False)
    for x in (-0.17, 0.17):                                     # the back's posts and its rattan
        b.cyl(0.011, 0.011, 0.42, 'Steel', at=(x, 0.19, 0.46), segs=8, rot=RX(-8))
    b.box((0.36, 0.025, 0.2), 'Rattan', at=(0, 0.215, 0.64), bevel=0.008, rot=RX(-8))
    return b, dict(r=0.25, h=0.86, mass=4, how='fly')


def planter():
    b = Build()
    b.box((1.0, 0.5, 0.5), 'Concrete', bevel=0.03)
    b.box((0.9, 0.4, 0.04), 'Soil', at=(0, 0, 0.47))
    for i, (x, y, r) in enumerate(((-0.28, 0.0, 0.26), (0.0, 0.03, 0.3), (0.3, -0.02, 0.25), (-0.1, -0.08, 0.2), (0.15, 0.1, 0.2))):
        b.ball(r, 'Leaf' if i % 2 else 'LeafLight', at=(x, y, 0.62 + r * 0.45), sub=2, squash=0.85)
    return b, dict(r=0.55, h=0.95, mass=160, how='fly')


def scooter():
    b = Build()
    b.box((0.16, 0.86, 0.05), 'Deck', at=(0, 0.02, 0.09), bevel=0.015)
    for y in (-0.42, 0.44):                                     # the wheels (front at -y, the stem over it)
        b.ring(0.095, 0.022, 'Rubber', at=(0, y, 0.118), axis='y', segs=20, tsegs=6)
        b.cyl(0.06, 0.06, 0.03, 'Steel', at=(-0.015, y, 0.118), rot=RY(90), segs=12)
    b.cyl(0.02, 0.02, 1.0, 'Teal', at=(0, -0.42, 0.118), rot=RX(-10), segs=10)
    b.cyl(0.014, 0.014, 0.5, 'Black', at=(-0.25, -0.59, 1.08), rot=RY(90), segs=8)
    b.box((0.05, 0.14, 0.04), 'Teal', at=(0, 0.47, 0.15), bevel=0.01)           # the rear mudguard
    b.cyl(0.008, 0.008, 0.14, 'Steel', at=(0.06, 0.1, 0.012), rot=RY(-25), segs=6)  # the stand
    return b, dict(r=0.42, h=1.15, mass=16, how='fly', lean=-8)


def news_box():
    b = Build()
    b.box((0.5, 0.42, 0.06), 'Iron', bevel=0.01)
    for x in (-0.18, 0.18):
        b.cyl(0.02, 0.02, 0.32, 'Iron', at=(x, 0, 0.06), segs=8)
    b.box((0.52, 0.44, 0.62), 'Blue', at=(0, 0, 0.38), bevel=0.03)
    b.box((0.38, 0.02, 0.24), 'Glass', at=(0, -0.222, 0.66), bevel=0.005)
    b.box((0.34, 0.01, 0.18), 'Paper', at=(0, -0.21, 0.69))
    b.box((0.08, 0.04, 0.03), 'Steel', at=(0, -0.235, 0.6), bevel=0.008)
    return b, dict(r=0.3, h=1.0, mass=30, how='fly')


def mail_box():
    b = Build()
    b.cyl(0.04, 0.04, 0.85, 'Iron', segs=12)
    b.box((0.44, 0.32, 0.5), 'Red', at=(0, 0, 0.82), bevel=0.05)
    b.box((0.26, 0.02, 0.035), 'Black', at=(0, -0.165, 1.16), bevel=0.008)
    b.box((0.2, 0.012, 0.12), 'White', at=(0, -0.166, 0.98), bevel=0.004)
    return b, dict(r=0.25, h=1.32, mass=22, how='tip')


def dumpster():
    b = Build()
    b.box((1.7, 1.0, 1.0), 'Green', at=(0, 0, 0.14), bevel=0.03)
    b.box((1.78, 1.08, 0.09), 'Green', at=(0, 0, 1.12), bevel=0.02, rot=None)     # the lid
    b.box((1.8, 0.06, 0.08), 'Iron', at=(0, -0.53, 0.95), bevel=0.01)
    for x in (-0.7, 0.7):
        for y in (-0.38, 0.38):
            b.cyl(0.07, 0.07, 0.05, 'Rubber', at=(x - 0.025, y, 0.07), rot=RY(90), segs=12)
            b.box((0.06, 0.06, 0.08), 'Iron', at=(x, y, 0.1))
    b.box((0.5, 0.01, 0.16), 'White', at=(0, -0.505, 0.55))
    return b, dict(r=0.95, h=1.3, mass=240, how='fly', box=(0.9, 0.55))


def barrier():
    b = Build()
    for x in (-0.55, 0.55):
        b.box((0.08, 0.5, 0.04), 'Black', at=(x, 0, 0), bevel=0.01)
        b.box((0.06, 0.06, 0.95), 'White', at=(x, 0, 0.04), bevel=0.01)
    for z, c in ((0.82, 'Red'), (0.56, 'White')):
        b.box((1.3, 0.04, 0.16), c, at=(0, 0, z), bevel=0.01)
    for x in (-0.33, 0.0, 0.33):
        b.box((0.16, 0.042, 0.16), 'White' if x else 'Red', at=(x, 0, 0.82))
    return b, dict(r=0.6, h=1.0, mass=9, how='fly', box=(0.68, 0.25))


def sign_post():
    b = Build()
    b.cyl(0.035, 0.035, 2.8, 'Steel', segs=12)
    for z, rotz in ((2.45, 0), (2.2, 90)):
        b.box((0.7, 0.025, 0.2), 'SignBlue', at=(0.38 if not rotz else 0, 0 if not rotz else 0.38, z), bevel=0.01,
              rot=RZ(rotz))
        b.box((0.62, 0.028, 0.035), 'White', at=(0.38 if not rotz else 0, 0 if not rotz else 0.38, z + 0.08), rot=RZ(rotz))
    return b, dict(r=0.15, h=2.8, mass=12, how='tip')


PROPS = [('Cone', cone), ('CafeTable', cafe_table), ('CafeChair', cafe_chair), ('Planter', planter), ('Scooter', scooter),
         ('NewsBox', news_box), ('MailBox', mail_box), ('Dumpster', dumpster), ('Barrier', barrier), ('SignPost', sign_post)]


def atlas_material():
    W, H = 64, 32
    col = np.ones((H, W, 4), np.float32); mr = np.ones((H, W, 4), np.float32)
    for i, (n, hexc, rough, metal) in enumerate(CELLS):
        r, c = i // 8, i % 8
        col[r * 8:(r + 1) * 8, c * 8:(c + 1) * 8, :3] = srgb(hexc)
        mr[r * 8:(r + 1) * 8, c * 8:(c + 1) * 8, :] = [1.0, rough, metal, 1.0]
    imgs = []
    for name, px, cs in (('props_color', col, 'sRGB'), ('props_mr', mr, 'Non-Color')):
        im = bpy.data.images.new(name, W, H, alpha=True)
        im.colorspace_settings.name = cs                    # (first: changing it later regenerates the image, black)
        im.pixels = px.ravel().tolist(); im.update(); im.pack()
        imgs.append(im)
    m = bpy.data.materials.new('Props'); m.use_nodes = True
    nt = m.node_tree; b = nt.nodes['Principled BSDF']
    t = nt.nodes.new('ShaderNodeTexImage'); t.image = imgs[0]; t.interpolation = 'Closest'
    nt.links.new(t.outputs['Color'], b.inputs['Base Color'])
    t2 = nt.nodes.new('ShaderNodeTexImage'); t2.image = imgs[1]; t2.interpolation = 'Closest'
    sep = nt.nodes.new('ShaderNodeSeparateColor'); nt.links.new(t2.outputs['Color'], sep.inputs['Color'])
    nt.links.new(sep.outputs['Green'], b.inputs['Roughness']); nt.links.new(sep.outputs['Blue'], b.inputs['Metallic'])
    return m


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mat = atlas_material()
    report = {}
    obs = []
    for i, (name, fn) in enumerate(PROPS):
        b, info = fn()
        ob = b.done(name, mat)
        me = ob.data; me.calc_loop_triangles()
        co = np.array([v.co[:] for v in me.vertices])
        lo, hi = co.min(0), co.max(0)
        assert lo[2] > -0.005, (name, 'below the ground', lo[2])
        for k, v in info.items():
            ob[k] = v
        ob['h'] = round(float(hi[2]), 3)
        report[name] = dict(triangles=len(me.loop_triangles), size=[round(float(v), 3) for v in hi - lo], **info)
        obs.append(ob)
        print('[props]', name, report[name], flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=OUT, export_format='GLB', export_extras=True, export_yup=True, export_apply=True,
                              export_materials='EXPORT', export_animations=False, export_draco_mesh_compression_enable=False)
    json.dump(report, open(OUT.replace('.glb', '_report.json'), 'w'), indent=1)
    print('[props] wrote', OUT, os.path.getsize(OUT), 'bytes', flush=True)
    if RENDERS:                                                 # a line-up in the studio
        os.makedirs(RENDERS, exist_ok=True)
        sc = bpy.context.scene; sc.render.engine = 'CYCLES'; sc.cycles.samples = 48; sc.cycles.use_denoising = True
        sc.render.resolution_x, sc.render.resolution_y = 1400, 520
        for k, ob in enumerate(obs):
            ob.location.x = (k - (len(obs) - 1) / 2) * 1.35
        w = bpy.data.worlds.new('W'); sc.world = w; w.use_nodes = True
        w.node_tree.nodes['Background'].inputs[0].default_value = (0.75, 0.78, 0.82, 1); w.node_tree.nodes['Background'].inputs[1].default_value = 0.8
        sun = bpy.data.objects.new('Sun', bpy.data.lights.new('Sun', 'SUN')); sun.data.energy = 3.5
        sun.rotation_euler = (math.radians(50), 0, math.radians(30)); sc.collection.objects.link(sun)
        bmg = bmesh.new(); bmesh.ops.create_grid(bmg, x_segments=1, y_segments=1, size=20)
        gm = bpy.data.meshes.new('g'); bmg.to_mesh(gm); g = bpy.data.objects.new('g', gm); sc.collection.objects.link(g)
        gmat = bpy.data.materials.new('G'); gmat.use_nodes = True
        gmat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.5, 0.5, 0.5, 1); g.data.materials.append(gmat)
        cam = bpy.data.objects.new('C', bpy.data.cameras.new('C')); sc.collection.objects.link(cam); sc.camera = cam
        cam.location = (0, -13.0, 3.0); cam.data.lens = 34
        cam.rotation_euler = (Vector((0, 0, 0.6)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
        sc.render.filepath = os.path.join(RENDERS, 'props.jpg'); sc.render.image_settings.file_format = 'JPEG'
        bpy.ops.render.render(write_still=True)


main()
