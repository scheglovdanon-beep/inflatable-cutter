"""
Боковые части (гассет): из контура переда строим кривую вдоль контура (минус днище)
и дробим её на полосы. Всё в финальных мм. Пока без припуска и раскраски.
"""
import math
import numpy as np
import cv2
from shapely.geometry import LineString, Point, MultiPolygon
from shapely.ops import unary_union
from shapely import affinity

from pieces import build_figure, _poly_of


def detect_protrusions(contour_mm, gusset_pts, cfg):
    """Детект тонких выпуклых выступов (антенна и т.п.) — универсально.
    opening убирает тонкие выпуклости; фильтр reach/R отсекает углы панелей.
    Возвращает список {arc_start, arc_center, arc_end} на кривой гассета (мм)."""
    R = int(cfg.get("protrusion_open_r_mm", 60))
    ratio_min = cfg.get("protrusion_reach_ratio", 0.6)
    min_area = cfg.get("protrusion_min_area_mm2", 150)
    x0, y0, x1, y1 = contour_mm.bounds
    W, H = int(x1 - x0) + 20, int(y1 - y0) + 20
    ring = np.array([[x - x0 + 10, y - y0 + 10] for x, y in contour_mm.exterior.coords], np.int32)
    mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [ring], 1)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * R + 1, 2 * R + 1))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ker)
    protr = ((mask > 0) & (opened == 0)).astype(np.uint8)
    dtb = cv2.distanceTransform((opened == 0).astype(np.uint8), cv2.DIST_L2, 5)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(protr, 8)

    gp = np.array(gusset_pts, float)
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(gp, axis=0).T))])
    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    out = []
    for i in range(1, n):
        if int(stats[i, cv2.CC_STAT_AREA]) < min_area:
            continue
        if dtb[lab == i].max() / R < ratio_min:              # угол панели -> отсев
            continue
        comp = cv2.dilate((lab == i).astype(np.uint8), grow)
        idxs = [k for k, (x, y) in enumerate(gp)
                if 0 <= int(y - y0 + 10) < H and 0 <= int(x - x0 + 10) < W
                and comp[int(y - y0 + 10), int(x - x0 + 10)]]
        if len(idxs) < 2:
            continue
        tip = max(idxs, key=lambda k: dtb[min(int(gp[k][1] - y0 + 10), H - 1),
                                           min(int(gp[k][0] - x0 + 10), W - 1)])
        out.append({"arc_start": cum[min(idxs)], "arc_center": cum[tip], "arc_end": cum[max(idxs)]})
    return out

PT2MM = 25.4 / 72.0


def front_drawings(page):
    """Перёд = левое из 2 крупнейших связных тел (руки/мусор отсекаются)."""
    R = page.rect
    drs = [x for x in page.get_drawings() if x.get("fill") is not None]
    drs = [x for x in drs if not (x["rect"].width > 0.95 * R.width and x["rect"].height > 0.95 * R.height)]
    polys = [(_poly_of(x), x) for x in drs]
    polys = [(p, x) for p, x in polys if p]
    u = unary_union([p for p, _ in polys])
    comps = sorted((u.geoms if isinstance(u, MultiPolygon) else [u]), key=lambda g: -g.area)
    bodies = sorted(comps[:2], key=lambda g: g.centroid.x)
    front, back = bodies[0], bodies[1]
    out = [x for p, x in polys
           if p.centroid.distance(front) <= p.centroid.distance(back) and front.buffer(2).contains(p.centroid)]
    return out, R.height


def get_front_figure(page, cfg):
    fr, page_H = front_drawings(page)
    return build_figure("front", fr, page_H, cfg["target_height_mm"], cfg["contour_gap_close_mm"])


def contour_to_mm(fig):
    """Контур переда в финальных мм (масштаб фигуры применён)."""
    f = fig.s * PT2MM
    return affinity.scale(fig.contour_pt, f, f, origin=(0, 0))


def _seglen(pts):
    p = np.asarray(pts, float)
    d = np.diff(p, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _arc(ring, i, j):
    """Точки кольца от i до j вперёд (с заворотом)."""
    n = len(ring); out = []; k = i
    while True:
        out.append(ring[k])
        if k == j:
            break
        k = (k + 1) % n
    return out


def build_gusset(contour_mm, cfg):
    max_len = cfg["side_max_len_mm"]
    ang_thr = cfg["corner_angle_deg"]
    simp = cfg["corner_simplify_mm"]
    bfrac = cfg["bottom_detect_frac"]

    ring = list(contour_mm.exterior.coords)[:-1]
    xs = np.array([p[0] for p in ring]); ys = np.array([p[1] for p in ring])
    ymax, h = ys.max(), ys.max() - ys.min()

    # --- днище: два нижних угла (y вниз => низ = большой y) ---
    bottom_idx = np.where(ys > ymax - bfrac * h)[0]
    iL = int(bottom_idx[np.argmin(xs[bottom_idx])])
    iR = int(bottom_idx[np.argmax(xs[bottom_idx])])
    arc1, arc2 = _arc(ring, iL, iR), _arc(ring, iR, iL)
    l1, l2 = _seglen(arc1), _seglen(arc2)
    if l1 < l2:                                   # днище = короткая дуга
        bottom_pts, bottom_len, gusset_pts, gusset_len = arc1, l1, arc2, l2
    else:
        bottom_pts, bottom_len, gusset_pts, gusset_len = arc2, l2, arc1, l1
    perimeter = _seglen(ring + [ring[0]])

    # --- точки разреза: углы + добивка по max_len ---
    line = LineString(gusset_pts)
    verts = list(line.simplify(simp).coords)
    corner_pos = []
    for k in range(1, len(verts) - 1):
        a = np.array(verts[k]) - np.array(verts[k - 1])
        b = np.array(verts[k + 1]) - np.array(verts[k])
        na, nb = np.hypot(*a), np.hypot(*b)
        if na < 1e-6 or nb < 1e-6:
            continue
        turn = math.degrees(math.acos(np.clip(np.dot(a, b) / (na * nb), -1, 1)))
        if turn >= ang_thr:
            corner_pos.append(line.project(Point(verts[k])))

    cuts = sorted(set([0.0, gusset_len] + [c for c in corner_pos if 0 < c < gusset_len]))
    # слить слишком короткие полосы (убираем внутренний разрез у самого короткого сегмента)
    min_len = cfg.get("side_min_len_mm", 0)
    while len(cuts) > 2:
        lens = [b - a for a, b in zip(cuts, cuts[1:])]
        i = int(np.argmin(lens))
        if lens[i] >= min_len:
            break
        if i + 1 < len(cuts) - 1:        # есть внутренний разрез справа сегмента
            del cuts[i + 1]
        elif 0 < i < len(cuts) - 1:      # иначе слева
            del cuts[i]
        else:
            break                        # сегмент между двумя концами — не трогаем
    # добивка по max_len
    final = [cuts[0]]
    for a, b in zip(cuts, cuts[1:]):
        gap = b - a
        if gap > max_len:
            k = math.ceil(gap / max_len)
            step = gap / k
            final += [a + m * step for m in range(1, k)]
        final.append(b)
    final = sorted(set(round(v, 3) for v in final))
    protected = set()

    # --- A. симметрия нижних деталей (лево = право): 1-я = max по углам, 2-я = trans_len ---
    trans_len = cfg.get("transition_piece_len_mm", 500)
    if len(final) >= 3:
        d_start = final[1]                                # длина первой детали от старта (дуга)
        d_end = gusset_len - final[-2]                    # от конца
        L1 = max(d_start, d_end)
        lo_zone, hi_zone = L1 + trans_len, gusset_len - L1 - trans_len
        if lo_zone < hi_zone:                             # нижние зоны не пересекаются
            new_bottom = [round(L1, 3), round(lo_zone, 3),
                          round(gusset_len - L1, 3), round(hi_zone, 3)]
            mid = [c for c in final if lo_zone <= c <= hi_zone]
            final = sorted(set([0.0, gusset_len] + mid + new_bottom))
            protected.update(new_bottom)

    # --- выступы (антенна и т.п.): ОДНА деталь-«песочные часы» (края main, талия tip) ---
    prot_specs = []
    if cfg.get("protrusion_narrow", True):
        min_piece = cfg.get("protrusion_min_piece_mm", 500)
        absorb = cfg.get("side_min_len_mm", 200)
        base_cuts = sorted(set(round(v, 3) for v in final))
        for p in detect_protrusions(contour_mm, gusset_pts, cfg):
            a_s, a_c, a_e = p["arc_start"], p["arc_center"], p["arc_end"]
            if not (0 < a_s < a_e < gusset_len):
                continue
            target = max(a_e - a_s, min_piece)
            ns, ne = a_c - target / 2, a_c + target / 2
            if ns < 0:
                ne -= ns; ns = 0
            if ne > gusset_len:
                ns = max(0.0, ns - (ne - gusset_len)); ne = gusset_len
            lower = [c for c in base_cuts if c <= ns]
            if lower and ns - lower[-1] < absorb:
                ns = lower[-1]
            upper = [c for c in base_cuts if c >= ne]
            if upper and upper[0] - ne < absorb:
                ne = upper[0]
            ns, ne = round(max(ns, 0.0), 3), round(min(ne, gusset_len), 3)
            final += [ns, ne]; protected.update([ns, ne])
            prot_specs.append((ns, ne, round(a_c, 3)))
        def inside(v):
            return any(a < v < b for a, b, _ in prot_specs)
        final = [v for v in sorted(set(round(x, 3) for x in final))
                 if v in protected or not inside(v)]

    # --- слить короткие полосы (защищаем нижние и выступ-разрезы) ---
    final = sorted(set(round(v, 3) for v in final))
    merge_thr = cfg.get("side_min_len_mm", 200)
    while len(final) > 2:
        d, i = min((final[j + 1] - final[j], j) for j in range(len(final) - 1))
        if d >= merge_thr:
            break
        if i + 1 <= len(final) - 2 and round(final[i + 1], 3) not in protected:
            del final[i + 1]
        elif i >= 1 and round(final[i], 3) not in protected:
            del final[i]
        else:
            break

    ymin = ys.min()
    def yn(arc):
        y = line.interpolate(arc).coords[0][1]
        return (ymax - y) / (ymax - ymin)                # 0 низ -> 1 верх (для порядка в раскладке)

    fan_w, main_w, tip_w = cfg["fan_diameter_mm"], cfg["main_width_mm"], cfg.get("tip_width_mm", 250)
    C = len(final)
    def t_cut(k):
        return fan_w if min(k, C - 1 - k) <= 1 else main_w
    def prot_of(a, b):
        for a_s, a_e, a_c in prot_specs:
            if abs(a - a_s) < 0.5 and abs(b - a_e) < 0.5:
                return a_c
        return None

    segments = []
    for i, (a, b) in enumerate(zip(final, final[1:])):
        yn0, yn1 = yn(a), yn(b)
        t0, t1 = t_cut(i), t_cut(i + 1)
        seg = {"i": i, "len": b - a, "arc0": a, "arc1": b, "y_norm": (yn0 + yn1) / 2,
               "thickness": max(t0, t1), "yn0": yn0, "yn1": yn1, "t0": t0, "t1": t1,
               "protrusion": False}
        a_c = prot_of(a, b)
        if a_c is not None:                               # деталь-выступ: талия = tip
            seg["protrusion"] = True
            seg["waist"] = tip_w
            seg["waist_frac"] = min(max((a_c - a) / (b - a), 0.05), 0.95)
        segments.append(seg)

    return {
        "perimeter": perimeter, "bottom_len": bottom_len, "gusset_len": gusset_len,
        "thickness_max": cfg["fan_diameter_mm"], "segments": segments, "cuts": final,
        "gusset_line": line, "gusset_pts": gusset_pts, "bottom_pts": bottom_pts,
        "corners_xy": [ring[iL], ring[iR]],
    }


# ---------- полосы-лекала ----------
def build_strips(g, gap_mm=60.0, label_h_mm=40.0):
    """Каждый сегмент -> прямоугольник thickness(своя) x len, в ряд с зазором."""
    strips, x = [], gap_mm
    for s in g["segments"]:
        w = s["thickness"]
        strips.append({"i": s["i"], "len": s["len"], "w": w, "x": x, "y": gap_mm + label_h_mm,
                       "side": s.get("side")})
        x += w + gap_mm
    sheet_w = x
    sheet_h = gap_mm + label_h_mm + max(s["len"] for s in g["segments"]) + gap_mm
    return strips, sheet_w, sheet_h


# ---------- раскладка «примерочная»: перёд в центре, боковины по своим сторонам ----------
def classify_sides(g, contour_mm):
    """Каждому сегменту приписать сторону фигуры (left/right) по x середины на кривой."""
    cx = (contour_mm.bounds[0] + contour_mm.bounds[2]) / 2
    line = g["gusset_line"]
    for s in g["segments"]:
        mx = line.interpolate((s["arc0"] + s["arc1"]) / 2).coords[0][0]
        s["side"] = "left" if mx < cx else "right"
    return g


def _pack_columns(strips, H, col_w, gap):
    cols, cur, cur_h = [], [], gap
    for s in strips:
        if cur and cur_h + s["len"] + gap > H:
            cols.append(cur); cur, cur_h = [], gap
        cur.append(s); cur_h += s["len"] + gap
    if cur:
        cols.append(cur)
    return cols


def _hourglass(xc, y, t_top, t_bot, waist, wf, L, N=40):
    """Деталь-выступ: ширина плавно t_top->waist->t_bot (вогнутые стороны, raised-cosine),
    талия на доле wf. Высота h подбирается так, чтобы ДЛИНА ИЗОГНУТОГО РЕБРА = L
    (ось чуть короче L). Возвращает (точки полигона, h)."""
    def w_at(s):
        if s <= wf:
            u = s / wf if wf > 0 else 1.0
            return t_top + (waist - t_top) * (1 - math.cos(math.pi * u)) / 2
        u = (s - wf) / (1 - wf) if wf < 1 else 1.0
        return waist + (t_bot - waist) * (1 - math.cos(math.pi * u)) / 2

    def edge_len(h):                                       # длина бокового (изогнутого) ребра
        p = [(w_at(k / N) / 2, (k / N) * h) for k in range(N + 1)]
        return sum(math.hypot(p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]) for i in range(N))

    lo, hi = L * 0.3, L                                    # ребро растёт с h; подбираем ребро = L
    for _ in range(40):
        mid = (lo + hi) / 2
        if edge_len(mid) > L:
            hi = mid
        else:
            lo = mid
    h = (lo + hi) / 2
    left = [(xc - w_at(k / N) / 2, y + (k / N) * h) for k in range(N + 1)]
    right = [(xc + w_at(k / N) / 2, y + (k / N) * h) for k in range(N, -1, -1)]
    return left + right, h


def build_fitting_layout(contour_mm, g, base=None, gap_mm=80.0):
    """Перёд по центру; левые боковины — один столбец слева, правые — один столбец справа,
    по порядку следования вдоль кривой (каждая деталь за предыдущей)."""
    col_w = g["thickness_max"] + gap_mm                   # шаг колонки по макс. толщине
    fx0, fy0, fx1, fy1 = contour_mm.bounds
    fw, fh = fx1 - fx0, fy1 - fy0
    # порядок в колонке — по высоте на фигуре: верх (макушка/тонкое) сверху, низ (корпус/толстое) снизу
    left = sorted((s for s in g["segments"] if s["side"] == "left"), key=lambda s: -s["y_norm"])
    right = sorted((s for s in g["segments"] if s["side"] == "right"), key=lambda s: -s["y_norm"])

    def column(strips, x):
        """Обычная полоса — трапеция (ребро = дуге L). Деталь-выступ — «песочные часы»:
        края = main, талия = tip, стороны плавно вогнуты (не дробим на мелкие)."""
        placed, y = [], gap_mm
        xc = x + g["thickness_max"] / 2
        for s in strips:
            flip = s["yn0"] < s["yn1"]                     # верх = конец ближе к макушке
            t_top, t_bot = (s["t1"], s["t0"]) if flip else (s["t0"], s["t1"])
            L = s["len"]
            if s.get("waist") is not None:
                wf = s["waist_frac"]; wf = 1 - wf if flip else wf
                poly, h = _hourglass(xc, y, t_top, t_bot, s["waist"], wf, L)
            else:
                h = math.sqrt(max(L * L - ((t_top - t_bot) / 2) ** 2, 1.0))
                poly = [(xc - t_top / 2, y), (xc + t_top / 2, y),
                        (xc + t_bot / 2, y + h), (xc - t_bot / 2, y + h)]
            lefts = poly[:len(poly) // 2] if s.get("waist") is not None else [poly[0], poly[3]]
            edge_len = sum(math.hypot(lefts[k + 1][0] - lefts[k][0], lefts[k + 1][1] - lefts[k][1])
                           for k in range(len(lefts) - 1))
            placed.append({**s, "poly": poly, "t_top": t_top, "t_bot": t_bot, "h": h,
                           "edge": L, "edge_len": edge_len, "x": x, "y": y})
            y += h + gap_mm
        return placed, y

    placed = []
    lp, lh = column(left, gap_mm)
    placed += lp
    front_x = gap_mm + col_w                              # перёд после левого столбца
    rp, rh = column(right, front_x + fw + gap_mm)
    placed += rp
    from shapely import affinity as _aff
    front = _aff.translate(contour_mm, front_x - fx0, gap_mm - fy0)
    sheet_w = front_x + fw + gap_mm + col_w
    sheet_h = max(fh + 2 * gap_mm, lh, rh)

    # днище + ножка — ПОД фигурой, ниже самой высокой колонки (без нахлёста)
    base_out = None
    if base is not None:
        dn = base["dnishe"]
        dw = dn.bounds[2] - dn.bounds[0]; dh = dn.bounds[3] - dn.bounds[1]
        cx = front_x + fw / 2                         # центр под передом
        base_y = max(fh + 2 * gap_mm, lh, rh) + gap_mm   # ниже всех колонок
        dn_x = cx - dw / 2
        dnishe = _aff.translate(dn, dn_x, base_y)
        fan_c = (dn_x + base["fan_c"][0], base_y + base["fan_c"][1])
        leg_w, leg_h = base["leg_w"], base["leg_h"]
        leg_y = base_y + dh + gap_mm
        leg_x = cx - leg_w / 2
        base_out = {"dnishe": dnishe, "fan_c": fan_c, "fan_d": base["fan_d"],
                    "leg": (leg_x, leg_y, leg_w, leg_h)}
        sheet_h = max(sheet_h, leg_y + leg_h + gap_mm)
        sheet_w = max(sheet_w, leg_x + leg_w + gap_mm, dn_x + dw + gap_mm)

    return {"front": front, "front_x": front_x, "strips": placed,
            "base": base_out, "sheet_w": sheet_w, "sheet_h": sheet_h}


def render_fitting(layout, out_png, target_h=1300, margin=40):
    from PIL import Image, ImageDraw
    sc = target_h / layout["sheet_h"]
    W = int(layout["sheet_w"] * sc) + 2 * margin
    H = int(layout["sheet_h"] * sc) + 2 * margin
    def T(x, y): return (margin + x * sc, margin + y * sc)
    im = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(im)
    # перёд — контур
    dr.line([T(x, y) for x, y in layout["front"].exterior.coords], fill=(120, 120, 120), width=2)
    # полосы — трапеции
    for s in layout["strips"]:
        col = (200, 0, 0) if s["side"] == "left" else (0, 0, 210)
        dr.polygon([T(x, y) for x, y in s["poly"]], outline=col)
        px, py = T(s["poly"][0][0], s["poly"][0][1])
        lbl = f'#{s["i"]} L{s["edge"]:.0f} w{s["t_top"]:.0f}/{s["t_bot"]:.0f}'
        if s.get("waist"):
            lbl += f' talia{s["waist"]:.0f}'
        dr.text((px, py - 12), lbl, fill=col)
    # днище + ножка под передом
    b = layout.get("base")
    if b:
        dr.polygon([T(x, y) for x, y in b["dnishe"].exterior.coords], outline=(0, 0, 0))
        fx, fy = b["fan_c"]; r = b["fan_d"] / 2
        dr.ellipse([T(fx - r, fy - r), T(fx + r, fy + r)], outline=(220, 0, 0), width=3)
        dnb = b["dnishe"].bounds
        dr.text(T(dnb[0], dnb[1] - 14), f'dnishe {dnb[2]-dnb[0]:.0f}x{dnb[3]-dnb[1]:.0f} / fanD{b["fan_d"]:.0f}', fill=(0, 0, 0))
        lx, ly, lw, lh = b["leg"]
        dr.rectangle([T(lx, ly), T(lx + lw, ly + lh)], outline=(0, 0, 0), width=2)
        dr.text(T(lx, ly - 14), f'nozhka {lw:.0f}x{lh:.0f}', fill=(0, 0, 0))
    im.save(out_png)
    return W, H


# ---------- debug-рендер (PIL, надёжно) ----------
def render_debug(contour_mm, g, out_png, target_h=1500, margin=60):
    from PIL import Image, ImageDraw
    ring = list(contour_mm.exterior.coords)
    xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
    x0, y0 = min(xs), min(ys)
    sc = target_h / (max(ys) - min(ys))
    W = int((max(xs) - min(xs)) * sc) + 2 * margin
    H = int((max(ys) - min(ys)) * sc) + 2 * margin
    def T(p): return (margin + (p[0] - x0) * sc, margin + (p[1] - y0) * sc)
    im = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    dr.line([T(p) for p in ring], fill=(220, 220, 220), width=1)     # контур
    # днище — толстая серая
    dr.line([T(p) for p in g["bottom_pts"]], fill=(150, 150, 150), width=6)
    # гассет — цвет = толщина (тонкий=синий, толстый=красный)
    line = g["gusset_line"]
    gp = g["gusset_pts"]
    tmin = min(s["thickness"] for s in g["segments"])
    tmax = max(s["thickness"] for s in g["segments"])
    def tcolor(t):
        f = (t - tmin) / (tmax - tmin) if tmax > tmin else 0.0
        return (int(30 + 190 * f), int(60 + 40 * (1 - f)), int(220 * (1 - f)))
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(np.array(gp, float), axis=0).T))])
    seg_of = lambda s: max(i for i, c in enumerate(g["cuts"]) if c <= s + 1e-6)
    for k in range(len(gp) - 1):
        seg = g["segments"][min(seg_of((cum[k] + cum[k + 1]) / 2), len(g["segments"]) - 1)]
        dr.line([T(gp[k]), T(gp[k + 1])], fill=tcolor(seg["thickness"]), width=5)
    for c in g["cuts"]:
        px, py = T(line.interpolate(c).coords[0])
        dr.ellipse([px - 7, py - 7, px + 7, py + 7], fill=(0, 0, 0))
    # подписи: №, длина, толщина
    for s in g["segments"]:
        mid = line.interpolate((s["arc0"] + s["arc1"]) / 2).coords[0]
        px, py = T(mid)
        dr.text((px + 6, py - 6), f'#{s["i"]} L{s["len"]:.0f} T{s["thickness"]:.0f}', fill=(0, 0, 0))
    im.save(out_png)
    return W, H
