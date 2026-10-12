#!/usr/bin/env python3
"""
STEP 9/10 (v3) — BLENDER WORKER: реальный зад + гассет переменной ширины с ЦВЕТАМИ боковин + руки.

Из panels.json (всё уже центрировано в мм, геометрия посчитана в экспортере):
  body.contour_ring[N] — общий контур перёд/зад;  body.gusset[N]{width,color} — ширина+цвет боковины в каждой точке.
Строим боксированную оболочку: перёд при Y=+w[i]/2 (текстура front), зад при Y=-w[i]/2 (текстура back),
стена-гассет между ними (вершинные цвета по сегментам). Remesh -> материалы по нормали -> Cloth+Pressure.
Руки — отдельными боксированными деталями.

Запуск: /opt/blender/blender -b --python generate.py -- <panels.json>
"""
import bpy, bmesh, sys, os, json, math, time, functools
import numpy as np
import mathutils
from mathutils import Vector
print = functools.partial(print, flush=True)                        # не терять логи при краше

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
SPEC = argv[0]; OUTDIR = os.path.dirname(os.path.abspath(SPEC))
spec = json.load(open(SPEC)); MM2M = 1.0 / spec["mm_per_unit"]; SIM = spec.get("sim", {})
t0 = time.time()
bpy.ops.wm.read_factory_settings(use_empty=True); scn = bpy.context.scene
WHITE = bool(os.environ.get("WHITE"))                               # тест: все детали белые, без текстур/цветов

# --- пресеты ткани: cloth-параметры ---
MATERIALS = {
    "current": dict(pressure=1.6, tension=15, comp=15, shear=15, bend=4.5, mass=0.30, air=1.0, quality=5),
    # Oxford 135D с пропиткой (ПВХ/ПУ): плотный полиэстер, почти не тянется, пропитка держит гладкую форму
    "oxford":    dict(pressure=1.3, tension=70,  comp=70,  shear=55, bend=30, mass=0.18, air=2.0, quality=8),
    # ПВХ тентовая/баннерная: очень жёсткая и тяжёлая, грани плоские, держит форму жёстко
    "pvc":       dict(pressure=1.3, tension=100, comp=100, shear=80, bend=55, mass=0.45, air=2.5, quality=8),
    # Рипстоп нейлон: средняя гибкость, лёгкая — умеренно пухлая
    "ripstop":   dict(pressure=1.3, tension=45,  comp=45,  shear=32, bend=12, mass=0.10, air=1.5, quality=9),
    # Полиэстер лёгкий без пропитки: мягкий, пухлее, мелкие складки
    "polyester": dict(pressure=1.3, tension=25,  comp=25,  shear=18, bend=5,  mass=0.12, air=1.2, quality=10),
    # Эластичный трикотаж/спандекс: сильно тянется -> раздувается шаром, очень округлый
    "spandex":   dict(pressure=1.3, tension=8,   comp=8,   shear=5,  bend=2,  mass=0.20, air=1.0, quality=12),
    # НЕРАСТЯЖИМЫЙ ПВХ/Oxford (как надувная горка): ткань НЕ тянется, форму держат панели+швы, принт не искажается
    # высокий bend -> перёд/зад остаются ПЛОСКИМИ (не заворачиваются на бок -> нет чёрной обводки на завороте)
    "nostretch": dict(pressure=1.3, tension=2000, comp=2000, shear=1500, bend=90, mass=0.20, air=2.5, quality=16),
}
MAT = os.environ.get("MATERIAL", "nostretch")
MATP = MATERIALS.get(MAT, MATERIALS["oxford"])


def img_mat(name, png, flip_u=False):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF"); b.inputs["Roughness"].default_value = 0.6
    p = os.path.join(OUTDIR, png)
    if not WHITE and os.path.isfile(p):
        tn = m.node_tree.nodes.new("ShaderNodeTexImage"); tn.image = bpy.data.images.load(p)
        m.node_tree.links.new(tn.outputs["Color"], b.inputs["Base Color"])
    else:
        b.inputs["Base Color"].default_value = (0.9, 0.9, 0.9, 1)
    m.blend_method = 'OPAQUE'; m["flip_u"] = flip_u
    return m


def color_mat(name, rgb):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1)
    b.inputs["Roughness"].default_value = 0.65
    return m


def apply_cloth(obj, name):
    """Cloth+Pressure на объекте. Для тела вызывается ПОСЛЕ выреза отверстия Ø250,
    иначе boolean modifier_apply сбрасывает/замораживает симуляцию (тело не надувалось)."""
    me = obj.data
    cl = obj.modifiers.new("cloth", "CLOTH"); cs = cl.settings
    cs.quality = int(MATP["quality"])
    cs.use_pressure = True
    cs.uniform_pressure_force = float(os.environ.get("PRESSURE", 5.0))
    _ten = float(os.environ.get("TENSION", MATP["tension"]))
    cs.tension_stiffness = _ten; cs.compression_stiffness = float(os.environ.get("COMP", _ten))
    cs.shear_stiffness = float(os.environ.get("SHEAR", MATP["shear"])); cs.bending_stiffness = float(os.environ.get("BEND", MATP["bend"])); cs.mass = MATP["mass"]
    cs.air_damping = MATP["air"]
    GRAV = float(os.environ.get("GRAVITY", 1.0))
    cl.settings.effector_weights.gravity = GRAV
    cl.collision_settings.use_self_collision = False
    if GRAV > 0 and name == "body":
        vg = obj.vertex_groups.new(name="pin")
        minz = min(v.co.z for v in me.vertices); thr = minz + 0.12
        vg.add([v.index for v in me.vertices if v.co.z < thr], 1.0, 'REPLACE')
        cs.vertex_group_mass = "pin"; cs.pin_stiffness = 1.0
    return obj


def make_inflatable(name, ring, widths, colors, bbox, m_front, m_back, voxel, faces=None, subdiv=2):
    """ring[N]=[x,y]мм(центр.); widths[N]; colors[N]; faces=триангуляция крышки СТРОГО по контуру.
    Без voxel-remesh (он заполнял вогнутости) — крышки из faces, гассет-квадры, подразбиение для ткани."""
    N = len(ring)
    rx = np.array([p[0] for p in ring]); ry = np.array([p[1] for p in ring])
    X = rx * MM2M; Z = -ry * MM2M; Wm = np.array(widths) * MM2M
    mesh = bpy.data.meshes.new(name); obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    fv = [bm.verts.new((X[i], +Wm[i] / 2, Z[i])) for i in range(N)]
    bv = [bm.verts.new((X[i], -Wm[i] / 2, Z[i])) for i in range(N)]
    if faces:                                                       # крышки из ОГРАНИЧЕННОЙ триангуляции (без заполнения)
        for a, b2, c in faces:
            try: bm.faces.new((fv[a], fv[b2], fv[c]))
            except Exception: pass
            try: bm.faces.new((bv[c], bv[b2], bv[a]))
            except Exception: pass
    else:
        bm.faces.new(fv); bm.faces.new(list(reversed(bv)))
    for i in range(N):                                              # стена-гассет
        try: bm.faces.new((fv[i], fv[(i + 1) % N], bv[(i + 1) % N], bv[i]))
        except Exception: pass
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if subdiv:                                                      # подразбить для ткани (форма/вогнутости сохраняются)
        for _ in range(subdiv):
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
    bm.to_mesh(mesh); bm.free()
    print(f"  [{name}] built(no-remesh): {len(mesh.vertices)}v", flush=True)
    bpy.context.view_layer.objects.active = obj

    me = obj.data
    # палитра гассета: квантуем цвета точек кольца -> уникальные материалы (без вершинных цветов)
    qcol = [(c[0] // 16 * 16, c[1] // 16 * 16, c[2] // 16 * 16) for c in colors]
    uniq = list(dict.fromkeys(qcol)); idx_of = {c: k for k, c in enumerate(uniq)}
    me.materials.append(m_front); me.materials.append(m_back)        # слот 0,1
    for k, uc in enumerate(uniq):                                   # слоты 2..
        me.materials.append(color_mat(f"{name}_g{k}", uc))
    ring_slot = np.array([2 + idx_of[qcol[i]] for i in range(len(qcol))])
    uvl = me.uv_layers.new(name="UVMap")
    bx0, by0, bx1, by1 = bbox; ww = (bx1 - bx0) or 1.0; hh = (by1 - by0) or 1.0
    for poly in me.polygons:
        ny = poly.normal.y
        ET = float(os.environ.get("EDGE_THR", 0.72))                 # порог: загиб вбок -> гассет (не чёрная обводка текстуры на скользящем крае)
        if ny > ET or ny < -ET:                                     # перёд(-Y)/зад(+Y) -> текстура
            slot = 0 if ny < -ET else 1                             # перёд на -Y (смотрим камерой с -Y, без flip)
            poly.material_index = slot
            flip = me.materials[slot].get("flip_u", False)
            INS = float(os.environ.get("UV_INSET", 1.0))             # 1.0 = точно по контуру (без белых полосок по краю)
            for li in poly.loop_indices:
                co = me.vertices[me.loops[li].vertex_index].co
                mx = co.x / MM2M; my = -co.z / MM2M
                u = (mx - bx0) / ww; v = 1.0 - (my - by0) / hh
                u = 0.5 + (u - 0.5) * INS; v = 0.5 + (v - 0.5) * INS
                uvl.data[li].uv = (1.0 - u if flip else u, v)
        else:                                                       # гассет -> материал ближайшей точки кольца
            cxx, czz = poly.center.x, poly.center.z
            j = int(np.argmin((cxx - X) ** 2 + (czz - Z) ** 2))
            poly.material_index = int(ring_slot[j])

    print(f"  [{name}] uv+colors done", flush=True)
    if os.environ.get("STATIC"):                                    # статика: боксированная оболочка СТРОГО по контуру, без надува
        bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True)
        bpy.context.view_layer.objects.active = obj; bpy.ops.object.shade_smooth()
        return obj
    if name != "body":                                              # тело: cloth добавим ПОСЛЕ выреза отверстия (ниже, в основном потоке)
        apply_cloth(obj, name)
    return obj


def build_tube(name, breakpts, origin, axis, rgb, seg=28, cap_top=True, cap_bottom=False, flat_cap=False):
    """Тюб из колец (t_мм, r_мм) вдоль оси axis от origin. Размеры — из деталей пайплайна.
    cap_bottom — заглушить первое кольцо (герметичный торец); flat_cap — плоская крышка (центр кольца)."""
    axis = axis.normalized()
    ref = Vector((0, 1, 0)) if abs(axis.y) < 0.9 else Vector((1, 0, 0))
    u = axis.cross(ref).normalized(); v = axis.cross(u).normalized()
    mesh = bpy.data.meshes.new(name); obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new(); rings = []
    for (t, r) in breakpts:
        c = origin + axis * (t * MM2M); R = r * MM2M
        rings.append([bm.verts.new(c + (math.cos(a) * R) * u + (math.sin(a) * R) * v)
                      for a in [2 * math.pi * k / seg for k in range(seg)]])
    for a, b in zip(rings, rings[1:]):
        for k in range(seg):
            bm.faces.new((a[k], a[(k + 1) % seg], b[(k + 1) % seg], b[k]))
    if cap_top:                                                     # купол/плоская крышка на конце
        t, r = breakpts[-1]
        tipc = origin + axis * (t * MM2M) if flat_cap else origin + axis * ((t + r) * MM2M)
        tv = bm.verts.new(tipc)
        for k in range(seg):
            bm.faces.new((rings[-1][k], rings[-1][(k + 1) % seg], tv))
    if cap_bottom:                                                  # герметичный нижний торец (плоская крышка в центре первого кольца)
        t, r = breakpts[0]; bc = origin + axis * (t * MM2M)
        bv = bm.verts.new(bc)
        for k in range(seg):
            bm.faces.new((rings[0][(k + 1) % seg], rings[0][k], bv))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh); bm.free()
    m = color_mat(name + "_m", rgb); mesh.materials.append(m)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.ops.object.shade_smooth()
    return obj


def build_tube_spine(name, spine3d, radii, rgb, seg=24):
    """Тюб вдоль осевой линии spine3d (Vector[]) с радиусами radii[] — ОРГАНИЧЕСКИЙ изгиб из формы руки."""
    n = len(spine3d)
    mesh = bpy.data.meshes.new(name); obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new(); rings = []
    angs = [2 * math.pi * k / seg for k in range(seg)]
    for k in range(n):
        c = spine3d[k]
        tang = (spine3d[1] - spine3d[0]) if k == 0 else (spine3d[-1] - spine3d[-2]) if k == n - 1 else (spine3d[k + 1] - spine3d[k - 1])
        if tang.length < 1e-9:
            tang = Vector((0, 0, 1))
        tang = tang.normalized()
        ref = Vector((0, 1, 0)) if abs(tang.y) < 0.9 else Vector((1, 0, 0))
        u = tang.cross(ref).normalized(); v = tang.cross(u).normalized()
        R = radii[k]
        rings.append([bm.verts.new(c + math.cos(a) * R * u + math.sin(a) * R * v) for a in angs])
    for a, b in zip(rings, rings[1:]):
        for k in range(seg):
            bm.faces.new((a[k], a[(k + 1) % seg], b[(k + 1) % seg], b[k]))
    tc = spine3d[-1] + (spine3d[-1] - spine3d[-2]).normalized() * radii[-1]   # купол на конце
    tv = bm.verts.new(tc)
    for k in range(seg):
        bm.faces.new((rings[-1][k], rings[-1][(k + 1) % seg], tv))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh); bm.free()
    mesh.materials.append(color_mat(name + "_m", rgb))
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.ops.object.shade_smooth()
    return obj


def build_hand(name, contour, gusset_w, tex, base, up, fn):
    """Кисть-подушка из РЕАЛЬНОГО контура кисти (перёд+зад фиста + полоска-гассет). Запястье(низ) у base,
    пальцы вдоль up; плоскость лицом fn. Текстура из hand.png."""
    up = up.normalized(); fn = fn.normalized()
    wide = up.cross(fn).normalized(); fn = wide.cross(up).normalized()
    xs = [c[0] for c in contour]; ys = [c[1] for c in contour]
    x0 = min(xs); w = (max(xs) - x0) or 1.0; y0 = min(ys); y1 = max(ys); h = (y1 - y0) or 1.0
    g = gusset_w * MM2M / 2

    def P3(u, v, side):
        return base + up * ((y1 - v) * MM2M) + wide * ((u - x0 - w / 2) * MM2M) + fn * (side * g)
    mesh = bpy.data.meshes.new(name); obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new(); N = len(contour)
    fv = [bm.verts.new(P3(x, y, +1)) for x, y in contour]
    bv = [bm.verts.new(P3(x, y, -1)) for x, y in contour]
    bm.faces.new(fv); bm.faces.new(list(reversed(bv)))
    for i in range(N):
        bm.faces.new((fv[i], fv[(i + 1) % N], bv[(i + 1) % N], bv[i]))
    bmesh.ops.triangulate(bm, faces=bm.faces[:]); bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh); bm.free()
    me = obj.data
    mf = img_mat(name + "_f", tex, flip_u=False) if tex else color_mat(name + "_f", [235, 235, 235])
    mb = img_mat(name + "_b", tex, flip_u=True) if tex else color_mat(name + "_b", [235, 235, 235])
    me.materials.append(mf); me.materials.append(mb); me.materials.append(color_mat(name + "_g", [235, 235, 235]))
    uvl = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        nd = poly.normal.dot(fn)
        slot = 0 if nd > 0.4 else (1 if nd < -0.4 else 2)
        poly.material_index = slot
        if slot == 2:
            continue
        flip = me.materials[slot].get("flip_u", False)
        for li in poly.loop_indices:
            rel = me.vertices[me.loops[li].vertex_index].co - base
            u = rel.dot(wide) / MM2M + x0 + w / 2; v = y1 - rel.dot(up) / MM2M
            tu = (u - x0) / w; tv = 1.0 - (v - y0) / h
            uvl.data[li].uv = (1.0 - tu if flip else tu, tv)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.ops.object.shade_smooth()
    return obj


objs = []
B = spec["body"]
gw = [gg["width_mm"] for gg in B["gusset"]]
if os.environ.get("FORCE_W"):
    gw = [float(os.environ["FORCE_W"])] * len(gw)
gc = [[170, 190, 210] if WHITE else gg["color"] for gg in B["gusset"]]
if not WHITE:                                                       # насытить цвета боковин до уровня текстуры переда/зада
    import colorsys as _cs
    def _boost(c):
        r,gg_,b=[x/255.0 for x in c]; h,l,sat=_cs.rgb_to_hls(r,gg_,b)
        sat=min(1.0, sat*1.5)                                      # ярче, не трогая белые (sat~0 остаётся белым)
        r,gg_,b=_cs.hls_to_rgb(h,l,sat); return [int(round(r*255)),int(round(gg_*255)),int(round(b*255))]
    gc=[_boost(c) for c in gc]
if os.environ.get("GUS_DEBUG"):
    gc = [[255, 0, 0]] * len(gc)                                    # диагностика: гассет красный
# сгладить ступеньки ширины гассета (стыки боковин 350<->460) -> нет резких изломов на боку
_kw = 11; _n = len(gw)
gw = [sum(gw[(i + j) % _n] for j in range(-_kw, _kw + 1)) / (2 * _kw + 1) for i in range(_n)]
# без flip текстуры гассет НЕ зеркалим (всё в реальной геометрии, согласовано)
if os.environ.get("GUS_MIRROR", "0") != "0":
    _rn = np.array(B["contour_ring"], float)
    _mi = [int(np.argmin((_rn[:, 0] + _rn[k, 0]) ** 2 + (_rn[:, 1] - _rn[k, 1]) ** 2)) for k in range(len(_rn))]
    gw = [gw[m] for m in _mi]; gc = [gc[m] for m in _mi]  # гассет виден как шов
body_obj = make_inflatable("body", B["contour_ring"], gw, gc, B["bbox_mm"],
                           (color_mat("front_dbg", [0, 200, 0]) if os.environ.get("DBG_FRONT") else img_mat("front", B["front_tex"], flip_u=False)),
                           (color_mat("back_dbg", [0, 120, 255]) if os.environ.get("DBG_BACK") else img_mat("back", os.environ.get("BACK_TEX", B["back_tex"]), flip_u=True)),
                           voxel=float(os.environ.get("VOXEL_BODY", SIM.get("voxel_body_m", 0.035))),
                           faces=B.get("faces"))
objs.append(body_obj)
if not os.environ.get("STATIC"):                                # cloth ДО выреза: тело замкнуто -> давление держится, надув работает
    apply_cloth(body_obj, "body")
hole_cuts = []                                                  # отверстия режем ПОСЛЕ надува (иначе разгерметизация)

# --- ТОЛЬКО BAZA: свёрнута в кольцо (края сшиты = замкнутый цилиндр Ø250), вшита в отверстие на гассете ---
arm = spec.get("arm")
if arm and arm.get("mounts") and not os.environ.get("NO_ARM"):
    ringA = np.array(B["contour_ring"], float); Nr = len(ringA)
    baza = next((c for c in arm.get("chain", []) if c["name"] == "baza"), None)
    br_mm = baza["r_bot"] if baza else (arm.get("hole_r_mm", 125))             # радиус кольца
    bh_mm = baza["h"] if baza else 38                                         # высота basы
    hole_r = br_mm * MM2M
    for mi, mnt in enumerate(arm["mounts"]):
        ax, ay = mnt["armpit_mm"]
        i = int(np.argmin((ringA[:, 0] - ax) ** 2 + (ringA[:, 1] - ay) ** 2))   # точка периметра = отверстие для руки
        tang = ringA[(i + 1) % Nr] - ringA[i - 1]
        nrm = np.array([tang[1], -tang[0]])
        if np.dot(nrm, ringA[i]) < 0:
            nrm = -nrm
        nrm = nrm / (np.linalg.norm(nrm) or 1.0)
        hole = Vector((ringA[i, 0] * MM2M, 0.0, -ringA[i, 1] * MM2M))           # центр отверстия на гассете (Y=0)
        d_out = Vector((nrm[0], 0.0, -nrm[1])).normalized()                    # наружу от боковины
        if not os.environ.get("NO_HOLE"):                                      # NO_HOLE=1 -> не резать отверстие (герметично)
            hole_cuts.append((hole.copy(), d_out.copy()))                      # ОТЛОЖИТЬ вырез до после надува
        rgb = (baza.get("fill", [235, 235, 235]) if baza else [235, 235, 235])
        # РУКА из РАСКАТАННЫХ реальных деталей (baza->plecho->predplechie); изгиб из формы деталей
        chain = [c for c in arm.get("chain", []) if c.get("rolled")]
        if chain and not os.environ.get("NO_SPINE"):
            FWD = math.radians(float(os.environ.get("ARM_FWD_DEG", 20)))        # небольшой наклон вперёд (видно)
            ex = d_out; ez = Vector((0, 0, 1)); ey = (ez - ez.dot(ex) * ex).normalized()  # плоскость изгиба (d_out, вверх)
            arm2 = [(0.0, 0.0)]; armr = [chain[0]["rolled"][0][2]]
            cpos = np.array([0.0, 0.0]); cang = 0.0
            for c in chain:
                roll = c["rolled"]
                loc = [np.array([s, d]) for s, d, r in roll]; lr = [r for s, d, r in roll]
                ca, sa = math.cos(cang), math.sin(cang); Rm = np.array([[ca, -sa], [sa, ca]])
                for k in range(1, len(loc)):
                    p = cpos + Rm @ loc[k]; arm2.append((p[0], p[1])); armr.append(lr[k])
                et = loc[-1] - loc[-2]
                cang += math.atan2(et[1], et[0]); cpos = np.array(arm2[-1])
            # 2D-план (s=вдоль, d=вверх) -> 3D в плоскости изгиба, + наклон вперёд по длине
            spine3d = []; radii = []; tot = len(arm2)
            for k, (s, d) in enumerate(arm2):
                p3 = hole + ex * (s * MM2M) - ey * (d * MM2M) + Vector((0, -1, 0)) * (FWD * (k / tot) * s * MM2M)  # -ey: изгиб вверх (машущая)
                spine3d.append(p3); radii.append(max(armr[k], 20.0) * MM2M)
            objs.append(build_tube_spine(f"arm{mi}", spine3d, radii, rgb))

# --- ДНИЩЕ + НОЖКА (реальные размеры: Ø fan_d, ножка свёрнута в тюб) ---
base = spec.get("base")
if base:
    zbot = min(-p[1] for p in B["contour_ring"]) * MM2M            # низ тела (центр. мм -> Z)
    xmid = (B["bbox_mm"][0] + B["bbox_mm"][2]) / 2 * MM2M
    rfan = base["fan_d"] / 2.0
    lw = base.get("leg_w", 0); lh = base.get("leg_h", 0)
    rleg = (lw / (2 * math.pi)) if lw else rfan                    # ножка-лента -> тюб
    orig = Vector((xmid, 0.0, zbot + 0.02))
    base_bps = [(0.0, rfan), (-max(lh, 5.0), rfan)]                # цилиндр-пьедестал БЕЗ ножки (без сужения к rleg)
    objs.append(build_tube("base_leg", base_bps, orig, Vector((0, 0, -1)), [210, 210, 210],
                           cap_top=True, cap_bottom=True, flat_cap=True))   # герметично: оба торца закрыты, «дверки» нет

steps = int(SIM.get("steps", 60))
scn.frame_start = 1; scn.frame_end = steps
print(f"симуляция {len(objs)} дет. × {steps} кадров…")
for f in range(1, steps + 1):
    scn.frame_set(f)
tsim = time.time()

dg = bpy.context.evaluated_depsgraph_get()
for obj in objs:
    baked = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    mats = list(obj.data.materials); obj.modifiers.clear(); obj.data = baked
    if not obj.data.materials:
        for m in mats:
            obj.data.materials.append(m)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT'); obj.select_set(True); bpy.ops.object.shade_smooth()

# --- ОТВЕРСТИЯ Ø250 в гассете: режем ПОСЛЕ надува (тело уже раздуто и запечено, вырез не мешает давлению) ---
for (hole, d_out) in hole_cuts:
    bpy.ops.mesh.primitive_cylinder_add(radius=hole_r, depth=0.6, location=(hole.x, hole.y, hole.z))
    cutter = bpy.context.active_object
    cutter.rotation_euler = Vector((0, 0, 1)).rotation_difference(d_out).to_euler()
    bmod = body_obj.modifiers.new("hole", "BOOLEAN"); bmod.object = cutter
    bmod.operation = 'DIFFERENCE'; bmod.solver = 'EXACT'
    bpy.context.view_layer.objects.active = body_obj
    bpy.ops.object.select_all(action='DESELECT'); body_obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier="hole")
    bpy.data.objects.remove(cutter, do_unlink=True)

# --- группировка: каждая деталь = отдельный объект (меш+свои материалы), все под общим узлом фигуры ---
root = bpy.data.objects.new(f"figure_{spec['figure']}", None)       # Empty-группа
scn.collection.objects.link(root)
for obj in objs:
    obj.parent = root
print(f"группа '{root.name}': " + ", ".join(f"{o.name}({len(o.data.materials)} мат.)" for o in objs))

import mathutils
allv = [obj.matrix_world @ v.co for obj in objs for v in obj.data.vertices]
mn = mathutils.Vector((min(p.x for p in allv), min(p.y for p in allv), min(p.z for p in allv)))
mx = mathutils.Vector((max(p.x for p in allv), max(p.y for p in allv), max(p.z for p in allv)))
dim = mx - mn
print(f"итоговые габариты: {dim.x:.2f}(ш) x {dim.y:.2f}(гл) x {dim.z:.2f}(в) м")

glb = os.path.join(OUTDIR, "model.glb")
bpy.ops.object.select_all(action='SELECT')
bpy.ops.export_scene.gltf(filepath=glb, export_format='GLB', export_apply=True, export_materials='EXPORT')
blend = os.path.join(OUTDIR, "model.blend"); bpy.ops.wm.save_as_mainfile(filepath=blend)

prev_ok = False
try:
    if os.environ.get("NO_RENDER"):
        raise RuntimeError("skip render")
    scn.render.engine = 'CYCLES'; scn.cycles.device = 'CPU'; scn.cycles.samples = 16
    scn.render.resolution_x = 800; scn.render.resolution_y = 1200; scn.render.film_transparent = True
    ctr = (mn + mx) / 2
    # ПОЛ: плоскость под днищем (фигура стоит на нём), ловит тень
    bpy.ops.mesh.primitive_plane_add(size=max(dim.x, dim.y) * 6, location=(ctr.x, ctr.y, mn.z))
    floor = bpy.context.active_object; fm = color_mat("floor", [225, 225, 228]); floor.data.materials.append(fm)
    cam_data = bpy.data.cameras.new("cam"); cam_data.type = 'PERSP'; cam_data.lens = 55
    cam = bpy.data.objects.new("cam", cam_data); bpy.context.collection.objects.link(cam); scn.camera = cam
    R = max(dim.x, dim.z)
    cam.location = (ctr.x + R * 0.35, ctr.y - R * 1.9, ctr.z + R * 0.18)   # 3/4 чуть сверху -> виден пол
    aim = mathutils.Vector((ctr.x, ctr.y, ctr.z - dim.z * 0.08))
    cam.rotation_euler = (aim - cam.location).to_track_quat('-Z', 'Y').to_euler()
    sun = bpy.data.lights.new("sun", 'SUN'); sun.energy = 3.0
    so = bpy.data.objects.new("sun", sun); bpy.context.collection.objects.link(so)
    so.rotation_euler = (math.radians(55), math.radians(20), 0)       # лучи к +Y -> освещают перёд(-Y)
    fill = bpy.data.lights.new("fill", 'SUN'); fill.energy = 2.2       # заливка правого/дальнего бока
    fo = bpy.data.objects.new("fill", fill); bpy.context.collection.objects.link(fo)
    fo.rotation_euler = (math.radians(70), math.radians(-55), 0)
    fill2 = bpy.data.lights.new("fill2", 'SUN'); fill2.energy = 1.6    # ещё заливка с другого бока
    fo2 = bpy.data.objects.new("fill2", fill2); bpy.context.collection.objects.link(fo2)
    fo2.rotation_euler = (math.radians(60), math.radians(55), 0)
    world = bpy.data.worlds.new("w"); scn.world = world; world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
    scn.render.filepath = os.path.join(OUTDIR, "preview.png")
    bpy.ops.render.render(write_still=True); prev_ok = os.path.isfile(scn.render.filepath)
except Exception as e:
    print("preview fail:", e)

print("==== DONE ====")
print(f"детали: {len(objs)} | гассет {min(gw):.0f}..{max(gw):.0f}мм (цвета по сегментам)")
print(f"BLEND:{'OK' if os.path.isfile(blend) else 'FAIL'} GLB:{'OK' if os.path.isfile(glb) else 'FAIL'} "
      f"({os.path.getsize(glb)//1024 if os.path.isfile(glb) else 0}KB) PREVIEW:{'OK' if prev_ok else 'FAIL'}")
print(f"sim:{tsim-t0:.1f}s total:{time.time()-t0:.1f}s")
