#!/usr/bin/env python3
"""
STEP 8 (v3) — ЭКСПОРТЕР для 3D: реальный ЗАД + гассет с ПЕРЕМЕННОЙ шириной и ЦВЕТАМИ боковин + руки.

Физика пошива: перёд и зад имеют ОДИН контур (иначе не сшить по периметру) — различается только
ТЕКСТУРА. Поэтому берём контур переда как общий, на заднюю крышку кладём реальную заднюю текстуру.
Гассет-боковина идёт по периметру: в каждой точке своя ширина (= thickness сегмента) и свой ЦВЕТ
(= band из color_sides). Вся геометрия shapely считается ЗДЕСЬ (в Blender shapely нет).

Выход: output/<name>/
  panels.json   — body{contour_ring[N], bbox, front_tex, back_tex, gusset[N]{width,color}} + arms[] + sim
  front.png, back.png, arm0.png …

Запуск (venv inflatable-cutter):
  venv/bin/python export_panels.py <macet.pdf> --config <cfg.yaml> --name cosmo
"""
import os, sys, io, json, argparse, math

CUTTER = "/opt/inflatable-cutter"
sys.path.insert(0, CUTTER)
sys.path.insert(0, os.path.join(CUTTER, "src"))

import yaml, pymupdf, numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, Point
from shapely.geometry.polygon import orient
import shapely.affinity as aff

N_RING = 700                                                        # точек по периметру тела


def _texture_png(page, cfg, poly_mm, fmm, out_png, max_px=2048):
    from export_pdf_grouped import _bg_fill_colors, _pix_png_nobg
    ext = poly_mm.exterior
    bx0, by0, bx1, by1 = poly_mm.bounds
    sc = max_px / max(bx1 - bx0, by1 - by0)
    Z = fmm * sc
    clip = pymupdf.Rect(bx0 / fmm, by0 / fmm, bx1 / fmm, by1 / fmm)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(Z, Z), clip=clip, alpha=True)
    png = _pix_png_nobg(pix, _bg_fill_colors(page, cfg))
    tex = Image.open(io.BytesIO(png)).convert("RGBA")
    tex = Image.alpha_composite(Image.new("RGBA", tex.size, (255, 255, 255, 255)), tex)  # фигура НА БЕЛОМ (непокрытое -> белое, не чёрное)
    mask = Image.new("L", tex.size, 0)
    ImageDraw.Draw(mask).polygon([((x - bx0) * sc, (y - by0) * sc) for x, y in ext.coords], fill=255)
    tex.putalpha(mask)                                               # альфа = контур панели
    tex.save(out_png)
    return [float(bx0), float(by0), float(bx1), float(by1)], list(tex.size)


def _vector_texture(page, cfg, dds, contour_mm, fmm, out_png, px_per_mm=1.8):
    """ЧИСТАЯ векторная текстура панели через cairosvg (как печатный вектор): дизайн на белом,
    обрезан по контуру. Без растровых артефактов/тёмных полей."""
    import cairosvg
    from shapely import affinity
    from export_svg import _design_group, _tex_tf, _poly
    x0, y0, x1, y1 = contour_mm.bounds; W = x1 - x0; H = y1 - y0
    local = affinity.translate(contour_mm, -x0, -y0)
    tf = _tex_tf(contour_mm, local, fmm)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}mm" height="{H:.1f}mm" viewBox="0 0 {W:.1f} {H:.1f}">'
           '<rect width="100%" height="100%" fill="white"/>'
           f'<clipPath id="c">' + _poly(list(local.exterior.coords), fill="#000", stroke="none", sw=0) + '</clipPath>'
           f'<g clip-path="url(#c)">' + _design_group(dds, tf, fmm, "d") + '</g></svg>')
    cairosvg.svg2png(bytestring=svg.encode(), write_to=out_png,
                     output_width=int(W * px_per_mm), output_height=int(H * px_per_mm), background_color="white")
    return [float(x0), float(y0), float(x1), float(y1)], [int(W * px_per_mm), int(H * px_per_mm)]


def _arm_spine(poly_pt, fmm, fcx, fcy, f_pt, tip_pt, res=2.0):
    """Осевая линия (скелет) силуэта руки + радиус вдоль неё. От подмышки f к кончику tip.
    Возвращает [[x,y,r], ...] в ЦЕНТРИРОВАННЫХ мм (как contour_ring). Изгиб = из реальной формы."""
    from skimage.morphology import medial_axis
    from PIL import Image, ImageDraw
    poly = poly_pt if hasattr(poly_pt, "exterior") else Polygon(poly_pt)
    co = np.array(poly.exterior.coords[:-1])
    bx0, by0 = co[:, 0].min(), co[:, 1].min(); bx1, by1 = co[:, 0].max(), co[:, 1].max()
    sc = fmm / res                                                  # pt -> пиксель (res мм/px)
    W = int((bx1 - bx0) * sc) + 4; H = int((by1 - by0) * sc) + 4
    img = Image.new("L", (W, H), 0)
    ImageDraw.Draw(img).polygon([((x - bx0) * sc + 2, (y - by0) * sc + 2) for x, y in co], fill=255)
    mask = np.array(img) > 128
    skel, dist = medial_axis(mask, return_distance=True)
    ys, xs = np.where(skel)
    if len(xs) < 3:
        return None
    pts = np.c_[xs, ys]; rad = dist[ys, xs] * res                  # радиус в мм
    def topx(p_pt):
        return np.array([(p_pt[0] - bx0) * sc + 2, (p_pt[1] - by0) * sc + 2])
    start = topx(f_pt); end = topx(tip_pt)
    i0 = int(np.argmin(((pts - start) ** 2).sum(1)))               # ближайшая к подмышке
    # трассировка nearest-neighbour от подмышки, предпочитая движение к tip
    used = np.zeros(len(pts), bool); path = [i0]; used[i0] = True
    for _ in range(len(pts)):
        c = pts[path[-1]]; dd = ((pts - c) ** 2).sum(1); dd[used] = 1e18
        j = int(np.argmin(dd))
        if dd[j] > (6 / res) ** 2:                                 # разрыв -> стоп
            break
        path.append(j); used[j] = True
        if ((pts[j] - end) ** 2).sum() < (8 / res) ** 2:
            break
    out = []
    for k in path[::max(1, len(path) // 40)]:                      # ~40 точек
        px, py = pts[k]; mx = (px - 2) / sc + bx0; my = (py - 2) / sc + by0
        out.append([mx * fmm - fcx, my * fmm - fcy, float(rad[k])])
    return out


def _roll_piece(poly, nlev=22, nv=48):
    """РАСКАТКА реальной детали (annular-sector) в 3D-профиль осевой: [[s,d,r],...] в локальной рамке
    (s=вдоль оси, d=боковое отклонение=ИЗГИБ из формы детали, r=радиус кольца). Дуги->кольца, бок-рёбра=шов."""
    co = np.array(poly.exterior.coords[:-1]); N = len(co)
    if N < 8:                                                       # baza (прямоугольник) -> прямой цилиндр
        xs = co[:, 0]; ys = co[:, 1]
        r = (xs.max() - xs.min()) / (2 * math.pi); h = ys.max() - ys.min()
        return [[0.0, 0.0, r], [h, 0.0, r]]

    def turn(i):
        a = co[i] - co[(i - 1) % N]; b = co[(i + 1) % N] - co[i]
        na = math.hypot(*a); nb = math.hypot(*b)
        if na < 1e-6 or nb < 1e-6:
            return 0.0
        return abs(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]))
    corners = sorted(sorted(range(N), key=turn)[-4:])
    edges = []
    for k in range(4):
        i0, i1 = corners[k], corners[(k + 1) % 4]
        e = co[i0:i1 + 1] if i1 > i0 else np.vstack([co[i0:], co[:i1 + 1]])
        edges.append(e)
    lens = [np.hypot(np.diff(e[:, 0]), np.diff(e[:, 1])).sum() for e in edges]
    order = np.argsort(lens)
    A, B = edges[order[-1]], edges[order[-2]]                       # 2 длинных ребра = дуги

    def resample(e, m):
        d = np.r_[0, np.cumsum(np.hypot(np.diff(e[:, 0]), np.diff(e[:, 1])))]; L = d[-1] or 1
        t = np.linspace(0, L, m)
        return np.c_[np.interp(t, d, e[:, 0]), np.interp(t, d, e[:, 1])]
    A = resample(A, nv); B = resample(B, nv)
    if A[:, 1].mean() < B[:, 1].mean():
        A, B = B, A                                                 # A=низ(больше y), B=верх

    def arclen(e):
        return float(np.hypot(np.diff(e[:, 0]), np.diff(e[:, 1])).sum())

    def fitR(e):                                                    # радиус дуги в РАЗВЁРТКЕ (угл. раствор = arclen/R)
        x, y = e[:, 0], e[:, 1]; M = np.c_[2 * x, 2 * y, np.ones(len(e))]; b = x * x + y * y
        sol = np.linalg.lstsq(M, b, rcond=None)[0]
        return math.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 1e-6))
    al_b, al_t = arclen(A), arclen(B); Rb, Rt = fitR(A), fitR(B)
    r_bot, r_top = al_b / (2 * math.pi), al_t / (2 * math.pi)       # радиусы колец
    bend = (al_b / Rb) - (al_t / Rt)                               # ИЗГИБ = разница угловых растворов дуг (рад)
    h = float(np.hypot(*(A[len(A) // 2] - B[len(B) // 2])))         # длина оси детали
    out = []
    for i in range(nlev + 1):
        f = i / nlev; r = r_bot + (r_top - r_bot) * f
        if abs(bend) > 1e-3:
            rho = h / bend; a = bend * f
            s = rho * math.sin(a); d = rho * (1 - math.cos(a))     # ось = дуга окружности (изгиб из формы)
        else:
            s, d = h * f, 0.0
        out.append([float(s), float(d), float(r)])
    return out


def _roll_grid(poly, U=24, V=64):
    """РОБАСТНАЯ строгая раскатка детали в замкнутую трубу с ВОЛНИСТЫМИ торцами (скос).
    Рёбра по топологии (PCA + диагональные углы). Сечения = КРУГИ (r=arclen/2π), но торцы волнистые
    (кромка детали -> осевое смещение dev), труба замкнута. Изгиб из разницы растворов дуг."""
    co = np.array(poly.exterior.coords[:-1]); N = len(co)
    if N < 8:                                                       # baza -> прямой цилиндр
        xs = co[:, 0]; r = (xs.max() - xs.min()) / (2 * math.pi); h = co[:, 1].max() - co[:, 1].min()
        return [[[h * i / U, r * math.cos(2 * math.pi * j / V), r * math.sin(2 * math.pi * j / V)]
                 for j in range(V)] for i in range(U + 1)]

    c = co.mean(0); cc = co - c
    wv, Vc = np.linalg.eigh(cc.T @ cc)
    e1 = Vc[:, int(np.argmax(wv))]; e2 = Vc[:, int(np.argmin(wv))]   # e1=длинная(вдоль дуг), e2=короткая
    p1 = cc @ e1; p2 = cc @ e2
    cs = sorted(set([int(np.argmax(-p1 + p2)), int(np.argmax(p1 + p2)),
                     int(np.argmax(-p1 - p2)), int(np.argmax(p1 - p2))]))   # 4 угла = диагональные экстремумы
    if len(cs) < 4:
        return None

    def path(i, j):
        return list(range(i, j + 1)) if j >= i else list(range(i, N)) + list(range(0, j + 1))
    edges = [co[path(cs[k], cs[(k + 1) % 4])] for k in range(4)]
    elen = [float(np.hypot(np.diff(e[:, 0]), np.diff(e[:, 1])).sum()) for e in edges]
    o = np.argsort(elen)
    bot, top = edges[o[-1]], edges[o[-2]]                           # 2 длинных ребра = дуги; самое длинное = НИЗ

    def rs(e, m):
        d = np.r_[0, np.cumsum(np.hypot(np.diff(e[:, 0]), np.diff(e[:, 1])))]; L = d[-1] or 1
        t = np.linspace(0, L, m)
        return np.c_[np.interp(t, d, e[:, 0]), np.interp(t, d, e[:, 1])], L
    bot, Lb = rs(bot, V); top, Lt = rs(top, V)
    if ((bot[0] - c) @ e1) > ((bot[-1] - c) @ e1):
        bot = bot[::-1]
    if ((top[0] - c) @ e1) > ((top[-1] - c) @ e1):
        top = top[::-1]                                             # оба от одного шва (соответствие по θ)
    r_bot, r_top = Lb / (2 * math.pi), Lt / (2 * math.pi)

    def fit(e):                                                     # центр, радиус вписанной окружности
        x, y = e[:, 0], e[:, 1]; M = np.c_[2 * x, 2 * y, np.ones(len(e))]; b = x * x + y * y
        s = np.linalg.lstsq(M, b, rcond=None)[0]
        return np.array([s[0], s[1]]), math.sqrt(max(s[2] + s[0] ** 2 + s[1] ** 2, 1e-6))
    cb, Rb = fit(bot); ct, Rt = fit(top)
    res_b = np.hypot(bot[:, 0] - cb[0], bot[:, 1] - cb[1]) - Rb      # ВОЛНИСТОСТЬ = остаток от дуги (малый, скос)
    res_t = np.hypot(top[:, 0] - ct[0], top[:, 1] - ct[1]) - Rt
    bend = (Lb / Rb) - (Lt / Rt)                                   # изгиб оси (рад) из разницы растворов
    H = float(np.hypot(*(top.mean(0) - bot.mean(0))))              # осевая длина детали
    grid = []
    for i in range(U + 1):
        tw = i / U; rad = r_bot + (r_top - r_bot) * tw
        if abs(bend) > 1e-3:
            rho = H / bend; a = bend * tw
            axp = np.array([rho * math.sin(a), 0.0, rho * (1 - math.cos(a))]); axt = np.array([math.cos(a), 0.0, math.sin(a)])
        else:
            axp = np.array([H * tw, 0.0, 0.0]); axt = np.array([1.0, 0.0, 0.0])
        u = np.cross(axt, [0, 1, 0]); u = u / (np.linalg.norm(u) or 1); v = np.cross(axt, u)
        ring = []
        for j in range(V):
            th = 2 * math.pi * j / V
            dev = res_b[j] * (1 - tw) + res_t[j] * tw               # осевое смещение = волнистость кромки (скос)
            p = axp + rad * (math.cos(th) * u + math.sin(th) * v) + axt * dev
            ring.append([float(p[0]), float(p[1]), float(p[2])])
        grid.append(ring)
    return grid


def _triangulate(ring):
    """Ограниченная триангуляция кольца (shapely Delaunay + фильтр внутри) — СТРОГО по контуру,
    вогнутости НЕ заполняются. Возвращает список граней [[i,j,k],...] по индексам ring."""
    from shapely.ops import triangulate as _tri
    poly = Polygon(ring)
    idx = {(round(x, 3), round(y, 3)): i for i, (x, y) in enumerate(ring)}
    faces = []
    for t in _tri(poly):
        if poly.contains(t.representative_point()):
            vs = list(t.exterior.coords)[:-1]
            try:
                faces.append([idx[(round(x, 3), round(y, 3))] for x, y in vs])
            except KeyError:
                pass
    return faces


def _resample(poly, n):
    """n точек по периметру, старт = нижняя точка (макс y), ориентация CCW (стабильно для корреспонденции)."""
    poly = orient(poly, 1.0)
    ext = poly.exterior; L = ext.length
    coords = list(ext.coords)[:-1]
    bi = max(range(len(coords)), key=lambda i: coords[i][1])
    d0 = ext.project(Point(coords[bi]))
    return [tuple(ext.interpolate((d0 + L * k / n) % L).coords[0]) for k in range(n)]


def _gusset_at(p, g, strips, fan_w):
    """Для точки периметра p(мм, исходная рамка): (ширина_мм, цвет[r,g,b]). Низ -> fan_w + нейтраль."""
    line = g["gusset_line"]
    d = Point(p).distance(line)
    if d > 12.0:                                                    # точка на ДНИЩЕ (вне гассет-дуги)
        return fan_w, [240, 240, 240]
    arc = line.project(Point(p))
    s = next((x for x in strips if x.get("arc0", 1e9) <= arc <= x.get("arc1", -1e9)), None)
    if s is None:
        return fan_w, [240, 240, 240]
    w = float(s.get("thickness", s.get("w", fan_w)))
    bands = s.get("bands")
    if not bands:
        return w, [240, 240, 240]
    a0, a1 = s["arc0"], s["arc1"]
    flip = s.get("yn0", 0) < s.get("yn1", 0)
    a_top, a_bot = (a1, a0) if flip else (a0, a1)
    frac = (arc - a_top) / ((a_bot - a_top) or 1.0)
    frac = min(max(frac, 0.0), 1.0)
    c = next((cc for f0, f1, cc in bands if f0 <= frac <= f1), bands[-1][2])
    return w, [int(c[0]), int(c[1]), int(c[2])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("macet"); ap.add_argument("--config", required=True)
    ap.add_argument("--name", default="figure"); ap.add_argument("--out", default="/opt/inflatable-3d/output")
    args = ap.parse_args()
    args.macet = os.path.abspath(args.macet); args.config = os.path.abspath(args.config)
    args.out = os.path.abspath(args.out)
    os.chdir(CUTTER)

    from export_svg import build_layout
    from sides import get_front_figure, PT2MM
    from arm import cut_waving_arms

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    cfg["roll_print"] = False; cfg["print_arrange"] = False
    cfg["roll_pack"] = False; cfg["arrange_sides"] = False; cfg["repack"] = False
    # для 3D контур туго по фигуре (сглаживание лекала 70мм заливало вогнутости -> серые поля в 3D)
    cfg["front_smooth_mm"] = float(os.environ.get("SMOOTH", 0))
    page = pymupdf.open(args.macet)[0]; page._cfg = cfg

    # руки в ИСХОДНОЙ рамке — ловим ДО build_layout (он мутирует свой fig)
    fig0 = get_front_figure(page, cfg); fmm = fig0.s * PT2MM
    cut_arms = []
    if cfg.get("arm_cut") == "armpit":
        _, cut_arms = cut_waving_arms(page, fig0.contour_pt, cfg, strict=True)

    # полная раскладка: контур тела, гассет, цветные боковины, реальный зад
    lay, fig, contour_mm, g, arm_pts, seam = build_layout(page, cfg)
    strips = lay.get("strips", [])
    fan_w = float(cfg.get("base_width_mm", cfg.get("fan_diameter_mm", 460)))

    outdir = os.path.join(args.out, args.name); os.makedirs(outdir, exist_ok=True)

    # --- контур тела (общий перёд/зад), центрируем ---
    fb = contour_mm.bounds; fcx = (fb[0] + fb[2]) / 2; fcy = (fb[1] + fb[3]) / 2
    ring_orig = _resample(contour_mm, N_RING)
    ring = [[x - fcx, y - fcy] for x, y in ring_orig]
    bbox_c = [fb[0] - fcx, fb[1] - fcy, fb[2] - fcx, fb[3] - fcy]

    # --- гассет по точкам ---
    gus = []
    for p in ring_orig:
        w, c = _gusset_at(p, g, strips, fan_w)
        gus.append({"width_mm": round(w, 1), "color": c})

    # --- текстуры: ВЕКТОРНЫЕ (чистые, как печать) перёд + реальный зад ---
    from sides import front_drawings, back_drawings
    _vector_texture(page, cfg, front_drawings(page, cfg)[0], contour_mm, fmm, os.path.join(outdir, "front.png"))
    back = lay.get("back")
    if back and not back.get("from_front"):
        bsrc = back["src"]; bfmm = back["f_mm"]
        _vector_texture(page, cfg, back_drawings(page, cfg)[0], bsrc, bfmm, os.path.join(outdir, "back.png"))
        back_tex = "back.png"
    else:                                                           # симметр. фигура -> зад=перёд
        import shutil; shutil.copy(os.path.join(outdir, "front.png"), os.path.join(outdir, "back.png"))
        back_tex = "back.png"

    faces = _triangulate(ring)                                      # грани крышек СТРОГО по контуру (без заполнения вогнутостей)
    body = {"contour_ring": ring, "bbox_mm": bbox_c, "faces": faces,
            "front_tex": "front.png", "back_tex": back_tex, "gusset": gus}

    # --- РУКА: цепочка из РЕАЛЬНЫХ деталей пайплайна (радиусы = периметр ребра / 2π) ---
    import math as _m
    by_name = {p["name"]: p for p in lay.get("arm_pieces", [])}
    order = ["baza", "plecho", "predplechie"]                       # от тела к запястью
    chain = []
    for nm in order:
        p = by_name.get(nm)
        if not p:
            continue
        rolled = None; grid = None
        try:
            rolled = _roll_piece(p["poly"])                         # РАСКАТКА реальной детали (изгиб из формы)
            grid = _roll_grid(p["poly"])                            # СТРОГАЯ раскатка по контуру (волнистость/скос)
        except Exception as e:
            print(f"roll {nm} fail:", e)
        fc = list(p["poly"].exterior.coords)[:-1]                   # плоский полигон детали (локальные мм)
        fxs = [c[0] for c in fc]; fys = [c[1] for c in fc]; fox, foy = min(fxs), min(fys)
        flat_poly = [[x - fox, y - foy] for x, y in fc]
        chain.append({"name": nm, "r_bot": p["edge_bottom"] / (2 * _m.pi),
                      "r_top": p["edge_top"] / (2 * _m.pi), "h": p["h"], "rolled": rolled, "grid": grid,
                      "flat_poly": flat_poly,
                      "fill": [int(v) for v in (p.get("fill") or (255, 255, 255))]})
    hp = lay.get("hand_pieces", [])
    hp_by = {p["name"]: p for p in hp}
    hand = None
    fist = hp_by.get("левая сторона") or (hp[0] if hp else None)
    strip = hp_by.get("боковина")
    if fist is not None:
        fc = list(fist["poly"].exterior.coords)[:-1]
        hxs = [c[0] for c in fc]; hys = [c[1] for c in fc]
        hx0, hy0 = min(hxs), min(hys)
        hand_ring = [[x - hx0, y - hy0] for x, y in fc]             # локальные мм (origin=угол bbox)
        htex = None
        if fist.get("src") is not None:                            # текстура кисти с макета
            spoly = aff.scale(Polygon(np.asarray(fist["src"], float)), fmm, fmm, origin=(0, 0))
            _texture_png(page, cfg, spoly, fmm, os.path.join(outdir, "hand.png")); htex = "hand.png"
        hand = {"contour": hand_ring, "gusset_w": float(strip["w"]) if strip else 40.0,
                "w": max(hxs) - hx0, "h": max(hys) - hy0, "texture": htex}

    # привязка руки: подмышка f и кончик tip (исходная рамка -> центрир. мм); отверстие Ø из hole
    mounts = []
    for ca in cut_arms:
        f = ca.get("f"); tip = ca.get("tip")
        if not f:
            continue
        spine = None
        try:
            spine = _arm_spine(ca["poly_pt"], fmm, fcx, fcy, f, tip)   # осевая линия руки (изгиб из формы)
        except Exception as e:
            print("spine fail:", e)
        mounts.append({"armpit_mm": [f[0] * fmm - fcx, f[1] * fmm - fcy],
                       "tip_mm": [tip[0] * fmm - fcx, tip[1] * fmm - fcy] if tip else None,
                       "spine": spine})
    holes = lay.get("arm_holes", [])
    hole_r = holes[0]["r"] if holes else cfg.get("arm_hole_diameter_mm", 250) / 2.0

    # --- ДНИЩЕ + НОЖКА (реальные размеры пайплайна) ---
    bb = lay.get("base") or {}
    base = None
    if bb:
        lg = bb.get("leg")
        base = {"fan_d": bb["fan_d"], "leg_w": lg[2] if lg else 0, "leg_h": lg[3] if lg else 0}

    arm = {"chain": chain, "hand": hand, "mounts": mounts, "hole_r_mm": hole_r} if chain else None

    spec = {"scale": "mm", "blender_unit": "meter", "mm_per_unit": 1000.0, "y_down": True,
            "figure": args.name, "body": body, "arm": arm, "base": base,
            "sim": {"pressure": float(cfg.get("sim_pressure", 1.2)),
                    "stiffness": float(cfg.get("sim_stiffness", 15.0)),
                    "steps": int(cfg.get("sim_steps", 60))}}
    json.dump(spec, open(os.path.join(outdir, "panels.json"), "w"), indent=2)
    ws = [gg["width_mm"] for gg in gus]
    chain_s = " -> ".join(f"{c['name']}(r{c['r_bot']:.0f}->{c['r_top']:.0f})" for c in chain)
    print(f"OK: {outdir} | тело {bbox_c[2]-bbox_c[0]:.0f}x{bbox_c[3]-bbox_c[1]:.0f}мм | гассет {min(ws):.0f}..{max(ws):.0f}мм(8 bok) | "
          f"зад={'реальный' if back and not back.get('from_front') else 'копия'}\n  рука: {chain_s} | днище Ø{base['fan_d'] if base else '-'} ножка {base['leg_w']:.0f}x{base['leg_h']:.0f}мм" if base else "")


if __name__ == "__main__":
    main()
