"""
Машущая рука — шаблонная: 3 детали (база-крепление, плечо, предплечье), без кисти.
Берём эталонный шаблон (PDF), масштабируем под Ø отверстия (s = D_hole/D_template).
База — крепится на отверстие Ø D_hole (ширина = длине окружности π·D_hole).
"""
import pymupdf
import numpy as np
import cv2
import math
from shapely.geometry import Polygon, box
from shapely import affinity

WRIST_MIN_FRAC = 0.10   # мин. доля площади кисти (отсекает палец/мусор как ложный излом)

PT2MM = 25.4 / 72.0


def _poly_pts(d, n=18):
    pts = []
    for it in d["items"]:
        k = it[0]
        if k == "l":
            pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
        elif k == "c":
            p0, p1, p2, p3 = it[1], it[2], it[3], it[4]
            for t in np.linspace(0, 1, n):
                pts.append(((1 - t) ** 3 * p0.x + 3 * (1 - t) ** 2 * t * p1.x + 3 * (1 - t) * t * t * p2.x + t ** 3 * p3.x,
                            (1 - t) ** 3 * p0.y + 3 * (1 - t) ** 2 * t * p1.y + 3 * (1 - t) * t * t * p2.y + t ** 3 * p3.y))
        elif k == "re":
            r = it[1]; pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        elif k == "qu":
            q = it[1]; pts += [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
    return pts


def measure_edges(poly, ang_thr=30, simp_mm=4.0):
    """Длины двух дуг-кромок детали (нижняя/верхняя) = швы с соседними деталями.
    Разбивает контур на рёбра по углам; две длинные дуги = (bottom, top) по среднему y."""
    v = np.array(poly.exterior.simplify(simp_mm).coords[:-1]); n = len(v)
    corners = []
    for i in range(n):
        a = v[i] - v[(i - 1) % n]; b = v[(i + 1) % n] - v[i]
        na, nb = np.hypot(*a), np.hypot(*b)
        if na < 1e-6 or nb < 1e-6:
            continue
        import math
        if math.degrees(math.acos(np.clip(np.dot(a, b) / (na * nb), -1, 1))) >= ang_thr:
            corners.append(i)
    if len(corners) < 2:
        return None, None
    edges = []
    for k in range(len(corners)):
        i, j = corners[k], corners[(k + 1) % len(corners)]
        idx = []; t = i
        while True:
            idx.append(t)
            if t == j:
                break
            t = (t + 1) % n
        p = v[idx]
        edges.append((float(np.hypot(*np.diff(p, axis=0).T).sum()), float(p[:, 1].mean())))
    edges.sort(reverse=True)                                  # две самые длинные = дуги-кромки
    (l1, y1), (l2, y2) = edges[0], edges[1]
    return (l1, l2) if y1 > y2 else (l2, l1)                  # (bottom, top) по среднему y


def build_arm(cfg, hole_d=None):
    """3 детали руки из шаблона, в мм, масштаб s=D_hole/D_template. Классификация по форме.
    hole_d — диаметр отверстия (мм); None -> arm_hole_diameter_mm. При arm_hole_from_cut
    приходит ДЛИНА РЕЗА -> база/плечо подгоняются под π·hole_d (окружность отверстия)."""
    tpl = cfg.get("arm_template_pdf", "examples/arm_template.pdf")
    hd = float(hole_d) if hole_d else cfg.get("arm_hole_diameter_mm", 250)
    s = hd / cfg.get("arm_template_hole_mm", 250)
    page = pymupdf.open(tpl)[0]
    pieces = []
    for d in page.get_drawings():
        pts = _poly_pts(d)
        if len(pts) < 3:
            continue
        try:
            poly = Polygon(pts).buffer(0)
        except Exception:
            continue
        if poly.is_empty or poly.area <= 0:
            continue
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        poly = affinity.scale(poly, PT2MM * s, PT2MM * s, origin=(0, 0))
        mnx, mny, mxx, mxy = poly.bounds
        poly = affinity.translate(poly, -mnx, -mny)
        bottom, top = measure_edges(poly)
        pieces.append({"poly": poly, "w": mxx - mnx, "h": mxy - mny, "area": poly.area,
                       "edge_bottom": bottom, "edge_top": top})
    if len(pieces) < 3:
        return pieces
    base = max(pieces, key=lambda p: p["w"] / p["h"]); base["name"] = "baza"      # самая вытянутая
    rest = [p for p in pieces if p is not base]
    sh = max(rest, key=lambda p: p["area"]); sh["name"] = "plecho"                # больше по площади
    for p in rest:
        if p is not sh:
            p["name"] = "predplechie"
    return pieces


# ======================================================================================
#  РЕЗ РУКИ «ОТ ПОДМЫШКИ, ПЕРПЕНДИКУЛЯРНО ВЫЛЕТУ РУКИ» (arm_cut: "armpit")
#  Работает по контуру силуэта ТЕЛА в PT (рука слита с телом единой заливкой).
#  Аддитивно: detect_wrist/cut_arm/build_arm/place_arm НЕ трогаются.
# ======================================================================================

def _rasterize_body(body, close_px=9, pad=20):
    """Растеризовать полигон тела в маску для cv2. Возвращает (mask, px2pt=1.0, (ox,oy)):
    координата в pt = координата в px + (ox,oy). (масштаб px:pt = 1:1 — тело уже в pt)."""
    x0, y0, x1, y1 = body.bounds
    W = int(x1 - x0) + 2 * pad
    H = int(y1 - y0) + 2 * pad
    mask = np.zeros((H, W), np.uint8)
    ring = np.array([[x - x0 + pad, y - y0 + pad] for x, y in body.exterior.coords], np.int32)
    cv2.fillPoly(mask, [ring], 255)
    if close_px:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    return mask, 1.0, (x0 - pad, y0 - pad)


def _largest_contour(mask):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    return max(cnts, key=cv2.contourArea)


def _poly_width(poly):
    return poly.bounds[2] - poly.bounds[0]


def _poly_height(poly):
    return poly.bounds[3] - poly.bounds[1]


def _arm_tip_candidates(cc, s, e, f, ox, oy):
    """Кандидаты-кончики для подмышки (вогнутый дефект f, ограниченный hull-мостом s..e).
    Вылет руки = одна из сторон моста (дуга контура s->f или f->e); её дальняя от f вершина.
    Возвращаем обе стороны (одна = кисть, другая = корпус/голова) — проверка резом выберет руку."""
    g = lambda i: (float(cc[i][0] + ox), float(cc[i][1] + oy))
    fx, fy = g(f)
    n = len(cc)
    cands = []
    for a, b in ((s, f), (f, e)):                                   # две дуги моста, прилегающие к f
        path = []; i = a; guard = 0
        while guard <= n:
            path.append(i)
            if i == b:
                break
            i = (i + 1) % n; guard += 1
        if len(path) < 2:
            continue
        pts = np.array([g(j) for j in path], float)
        d = np.hypot(pts[:, 0] - fx, pts[:, 1] - fy)
        cands.append(tuple(pts[int(np.argmax(d))]))
    for i in (s, e):                                               # плюс сами hull-концы (короткая дуга)
        cands.append(g(i))
    uniq = []
    for p in cands:
        if all(math.hypot(p[0] - q[0], p[1] - q[1]) > 1.0 for q in uniq):
            uniq.append((float(p[0]), float(p[1])))
    return uniq


def _line_poly_span(body, p, direction, max_reach):
    """Длина пересечения тела с лучами из p в ±direction (до выхода из полигона).
    Возвращает (span_total, reach_pos, reach_neg)."""
    from shapely.geometry import LineString
    p = np.asarray(p, float); d = np.asarray(direction, float)
    d = d / (np.hypot(*d) or 1.0)
    probe = LineString([tuple(p - d * max_reach), tuple(p + d * max_reach)])
    inter = body.intersection(probe)
    if inter.is_empty:
        return 0.0, 0.0, 0.0
    # длина части, содержащей p (ближайшая к p)
    from shapely.geometry import MultiLineString
    segs = list(inter.geoms) if isinstance(inter, MultiLineString) else [inter]
    best = min(segs, key=lambda s: s.distance(__import__("shapely").geometry.Point(*p)))
    return best.length, 0.0, 0.0


def _perp_chord(poly, p, u):
    """Концы (B,T) хорды полигона через точку p перпендикулярно оси u (отрезок, содержащий p)."""
    from shapely.geometry import LineString, MultiLineString, Point
    p = np.asarray(p, float); u = np.asarray(u, float); u = u / (np.hypot(*u) or 1.0)
    n = np.array([-u[1], u[0]])
    reach = max(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1])
    probe = LineString([tuple(p - n * reach), tuple(p + n * reach)])
    inter = poly.intersection(probe)
    if inter.is_empty:
        return None
    segs = list(inter.geoms) if isinstance(inter, MultiLineString) else [inter]
    seg = min(segs, key=lambda s: s.distance(Point(*p)))
    c = list(seg.coords)
    return np.asarray(c[0], float), np.asarray(c[-1], float)


def _wrist_by_width(arm_poly, tip, armpit_f, cfg):
    """Запястье по ПРОФИЛЮ ШИРИНЫ вдоль оси руки tip→подмышка. От кончика: пальцы (узко) →
    ЛАДОНЬ (пик ширины) → ЗАПЯСТЬЕ (локальный минимум) → предплечье (ровно). Режем в
    локальном минимуме ПОСЛЕ ладонного пика = реальное запястье (а не костяшки пальцев).
    armpit_f=None -> ось берётся из главной оси полигона (дальний от tip конец = тело).
    Возвращает (B,T) концы хорды запястья или None."""
    tip = np.asarray(tip, float)
    if armpit_f is not None:
        fv = np.asarray(armpit_f, float)
    else:                                                          # ось из полигона: дальний конец = тело
        pts = np.asarray(arm_poly.exterior.coords[:-1], float)
        m = pts.mean(0); pax = np.linalg.svd(pts - m)[2][0]
        pr = (pts - m) @ pax
        e_lo, e_hi = pts[int(np.argmin(pr))], pts[int(np.argmax(pr))]
        fv = e_lo if np.hypot(*(e_lo - tip)) > np.hypot(*(e_hi - tip)) else e_hi
    u = fv - tip; L = float(np.hypot(*u))
    if L < 1e-6:
        return None
    u = u / L
    N = 80
    frac_max = cfg.get("wrist_width_search_frac", 0.55)             # кисть — в первой доле руки
    S = np.linspace(0.04 * L, frac_max * L, N)
    width = np.full(N, np.nan)
    pos = []
    for k, s in enumerate(S):
        p = tip + u * s
        ch = _perp_chord(arm_poly, p, u)
        pos.append(p)
        if ch is not None:
            width[k] = float(np.hypot(*(ch[1] - ch[0])))
    # заполнить дыры и сгладить
    valid = ~np.isnan(width)
    if valid.sum() < 5:
        return None
    width = np.interp(np.arange(N), np.where(valid)[0], width[valid])
    w = np.convolve(width, np.ones(5) / 5, mode="same")
    palm = int(np.argmax(w[:int(0.6 * N)]))                        # ладонный пик (в первой половине)
    # локальный минимум ПОСЛЕ ладони = запястье
    tail = w[palm:]
    if len(tail) < 3:
        return None
    wr_rel = palm + int(np.argmin(tail))
    # не дальше, чем явно пошёл ровный/растущий предплечный участок: первый лок.мин после пика
    for k in range(palm + 1, N - 1):
        if w[k] <= w[k - 1] and w[k] <= w[k + 1]:
            wr_rel = k
            break
    p = pos[wr_rel]
    ch = _perp_chord(arm_poly, p, u)
    if ch is None:
        return None
    return _snap_wrist_to_notches(arm_poly, ch[0], ch[1], tip, cfg)   # привязка к точкам перехода


def _contour_notches(pts):
    """Вогнутые «уголки» контура (convexity defects) = точки перехода форм. [(point, depth_pt)].
    Устойчиво к самопересечениям/дублям: убираем подряд идущие дубли (из-за округления в int,
    они ломают монотонность hull) и ловим cv2.error (кривой контур -> нет уголков)."""
    P = np.asarray(pts, float)
    if len(P) < 4:
        return []
    Q = (P - P.min(0)).astype(np.int32)
    keep = np.ones(len(Q), bool)
    keep[1:] = np.any(Q[1:] != Q[:-1], axis=1)          # дедуп подряд идущих одинаковых
    Q = Q[keep]; Pk = P[keep]
    if len(Q) < 4:
        return []
    c = Q.reshape(-1, 1, 2)
    try:
        hull = cv2.convexHull(c, returnPoints=False)
        if hull is None or len(hull) < 3:
            return []
        D = cv2.convexityDefects(c, hull)
    except cv2.error:
        return []
    if D is None:
        return []
    return [(Pk[int(f)], depth / 256.0) for s, e, f, depth in D.reshape(-1, 4)]


def _snap_wrist_to_notches(arm_poly, B, T, tip, cfg):
    """Привязать концы хорды запястья к РЕАЛЬНЫМ точкам перехода предплечье→кисть:
    вогнутым «уголкам» запястья (у большого пальца и со стороны мизинца). Для каждой
    стороны от середины хорды берём самую глубокую вогнутость контура в полосе ±|хорда|
    вдоль оси. Если с какой-то стороны вогнутости нет — оставляем конец от ширины."""
    B = np.asarray(B, float); T = np.asarray(T, float); tip = np.asarray(tip, float)
    chord = T - B; cl = float(np.hypot(*chord))
    if cl < 1e-6:
        return B, T
    cdir = chord / cl
    mid = (B + T) / 2.0
    u = mid - tip; u = u / (np.hypot(*u) or 1.0)                    # ось tip→запястье
    band = cfg.get("wrist_notch_band_frac", 0.7) * cl
    min_depth = cfg.get("wrist_notch_min_depth_frac", 0.06) * cl
    newB, newT = B, T; bB, bT = min_depth, min_depth
    for p, depth in _contour_notches(arm_poly.exterior.coords[:-1]):
        if abs(float((p - mid) @ u)) > band:                       # вне полосы запястья — не уголок запястья
            continue
        side = float((p - mid) @ cdir)
        if side < 0 and depth > bB:
            bB = depth; newB = p
        elif side > 0 and depth > bT:
            bT = depth; newT = p
    return newB, newT


def local_arm_thickness(body, f, n, Wb):
    """Толщина руки у подмышки = длина пересечения тела с короткой линией через f вдоль n."""
    return _line_poly_span(body, f, n, Wb * 1.2)[0] or (0.05 * Wb)


def local_body_halfwidth(body, f, n, Wb):
    """Расстояние от f до противоположного бока тела вдоль n (половина пролёта)."""
    span = _line_poly_span(body, f, n, Wb * 1.6)[0]
    return max(span * 0.5, 0.05 * Wb)


def piece_containing(parts, pt):
    from shapely.geometry import Point
    P = Point(*pt)
    inside = [p for p in parts if p.contains(P)]
    if inside:
        return max(inside, key=lambda p: p.area)
    return min(parts, key=lambda p: p.distance(P))   # ближайший, если точка на кромке


def blade_perp(body, f, tip, cfg):
    """Геометрия лезвия ПЕРПЕНДИКУЛЯРНО оси руки (f->tip): пара концов (p0, p1)."""
    f = np.asarray(f, float); tip = np.asarray(tip, float)
    u = tip - f; nu = np.hypot(*u) or 1.0; u = u / nu
    n = np.array([-u[1], u[0]])                                  # перпендикуляр поперёк руки
    Wb = _poly_width(body)
    at = local_arm_thickness(body, f, n, Wb)
    bw = local_body_halfwidth(body, f, n, Wb)
    half = max(2.5 * at, 1.2 * bw, cfg.get("blade_min_frac", 0.08) * Wb)
    return tuple(f + n * half), tuple(f - n * half)


def blade_pair(fa, fb, ext_frac=0.12):
    """Лезвие = отрезок между двумя подмышечными вогнутостями (верх/низ основания руки),
    продлённый на ext_frac с каждого конца. Для ПОДНЯТЫХ рук (B,D): «шейка» основания."""
    A = np.asarray(fa, float); B = np.asarray(fb, float)
    d = B - A; L = np.hypot(*d) or 1.0; d = d / L; ext = ext_frac * L
    return tuple(A - d * ext), tuple(B + d * ext)


def cut_across_arm(body, f, tip, cfg, blade=None):
    """Рез тела лезвием через подмышку f. По умолчанию — ПЕРПЕНДИКУЛЯРНО оси руки (f->tip);
    если передан явный `blade` (пара концов), режем им (стратегия «пара подмышек»).
    Возвращает (arm_poly, rest_poly) или (None, None) если рез не состоялся/срезал корпус."""
    from shapely.geometry import LineString, Point
    from shapely.ops import split, unary_union
    f = np.asarray(f, float); tip = np.asarray(tip, float)
    if blade is None:
        p0, p1 = blade_perp(body, f, tip, cfg)
    else:
        p0, p1 = blade
    blade = LineString([tuple(p0), tuple(p1)])
    sp = split(body, blade)
    parts = list(getattr(sp, "geoms", [sp]))
    if len(parts) < 2:                                            # концы «пары подмышек» часто лежат ЧУТЬ
        ext = body.exterior                                       # внутри контура -> split не режет. Привяжем
        def _pierce(p, other):                                    # концы к контуру и чуть выведем наружу.
            P = Point(*p)
            if not body.contains(P):
                return tuple(p)
            b = np.asarray(ext.interpolate(ext.project(P)).coords[0], float)
            d = b - np.asarray(other, float); n = np.hypot(*d) or 1.0
            return tuple(b + d / n * 2.0)                         # 2pt за контур (от другого конца)
        blade = LineString([_pierce(p0, p1), _pierce(p1, p0)])
        sp = split(body, blade)
        parts = list(getattr(sp, "geoms", [sp]))
    if len(parts) < 2:
        return None, None
    arm = piece_containing(parts, tuple(tip))
    if arm.area > cfg.get("arm_max_frac", 0.35) * body.area:
        return None, None
    rest = unary_union([p for p in parts if p is not arm])
    if rest.geom_type == "MultiPolygon":
        rest = max(rest.geoms, key=lambda p: p.area)
    return arm, rest


def _refine_armpit(body, base, all_f, cfg, Wb, Hb, axx, min_arm, max_arm,
                   min_aspect, tip_free_frac):
    """Опустить точку f в НАСТОЯЩУЮ подмышку (нижняя вогнутость основания руки).

    У поднятых рук (B,D) и вытянутых (E) базовый дефект часто берётся у ПЛЕЧА/шеи
    (верхняя вогнутость) — рез отрезает не всю руку / захватывает лишнее. Здесь среди
    ВСЕХ вогнутостей contура ищем ту, что лежит на основании ТОЙ ЖЕ руки (рядом с базой,
    порог armpit_pair_local_frac·len) и даёт самую НИЖНЮЮ (max y) чистую подмышку:
    рука целиком уходит, лишнего нет. Рез — перпендикуляр к оси ИЛИ отрезок между
    базовой и нижней вогнутостью («пара подмышек», для поднятых рук). Возвращает dict
    с обновлёнными f/poly/len/blade или исходный base, если лучше не нашлось."""
    hand = base["tip"]; hx, hy = hand
    bf = np.asarray(base["f"], float)
    side = base["side"]
    local_frac = cfg.get("armpit_pair_local_frac", 0.9)
    center_frac = cfg.get("armpit_center_frac", 0.15)
    min_depth = cfg.get("armpit_min_depth_frac", 0.06) * Hb
    asp_relax = cfg.get("armpit_refine_aspect_relax", 0.7)
    best = {**base, "blade": None, "_yf": base["f"][1], "_A": base["poly"].area}
    for (xf, yf, d_pt) in all_f:
        if d_pt < 0.4 * min_depth:                                # совсем мелкие — мусор
            continue
        if math.hypot(xf - bf[0], yf - bf[1]) > local_frac * base["len"]:
            continue                                              # не у основания этой руки
        if (xf < axx) != (side == "L") and abs(xf - axx) > center_frac * Wb:
            continue                                              # другая сторона тела
        f = (xf, yf)
        arm_len = math.hypot(hx - xf, hy - yf)
        if arm_len < tip_free_frac * max(Wb, Hb):
            continue
        for bl in (None, blade_pair(base["f"], f)):               # перпендикуляр или пара подмышек
            arm, rest = cut_across_arm(body, f, hand, cfg, blade=bl)
            if arm is None:
                continue
            A = arm.area
            if A < min_arm or A > max_arm:
                continue
            asp = arm_len / max(A / max(arm_len, 1e-6), 1e-6)
            if asp < min_aspect * asp_relax:
                continue
            if (yf, -A) > (best["_yf"], -best["_A"]):             # самая нижняя подмышка, при равенстве — меньше площадь
                best = {"f": f, "tip": hand, "len": arm_len, "poly": arm, "asp": asp,
                        "side": side, "depth": base.get("depth", 0.0), "blade": bl,
                        "_yf": yf, "_A": A}
    best.pop("_yf", None); best.pop("_A", None)
    return best


def _contour_concavities(body, cfg):
    """Все вогнутости контура тела (x,y,depth) глубже порога — кандидаты подмышки/верха плеча."""
    mask, _, (ox, oy) = _rasterize_body(body, close_px=int(cfg.get("armpit_close_px", 9)))
    c = _largest_contour(mask)
    if c is None:
        return []
    hull = cv2.convexHull(c, returnPoints=False)
    try:
        D = cv2.convexityDefects(c, hull)
    except cv2.error:
        return []
    if D is None:
        return []
    Hb = _poly_height(body)
    thr = cfg.get("armpit_min_depth_frac", 0.03) * Hb
    out = []
    for s, e, ff, depth in D.reshape(-1, 4):
        d = depth / 256.0
        if d >= thr:
            out.append((float(c[int(ff)][0][0] + ox), float(c[int(ff)][0][1] + oy), d))
    return out


def _arm_has_hand(page, arm_poly, cfg):
    """ВАЛИДАЦИЯ: есть ли в срезанном куске кисть. Нет кисти -> это не рука (ложный срез).
    ТОЛЬКО геометрия (профиль ширины) — без растрового детекта по цвету (тот жрал память -> OOM)."""
    from shapely.geometry import Polygon as _P
    arr = np.asarray(arm_poly.exterior.coords[:-1], float)
    if len(arr) < 4:
        return False, None, None
    cen = np.asarray(arm_poly.centroid.coords[0], float)
    tip = tuple(arr[int(np.argmax(np.hypot(arr[:, 0] - cen[0], arr[:, 1] - cen[1])))])  # дальняя точка = кисть
    try:
        ww = _wrist_by_width(_P(arr), tip, None, cfg)                # геометрия, без растра
        if ww is None:
            return False, None, None
        sc = _wrist_score(arr, [tuple(ww[0]), tuple(ww[1])], tip, 1.0)
    except Exception:
        return False, None, None
    ok = sc is not None and sc[0]
    return bool(ok), tip, [tuple(ww[0]), tuple(ww[1])]


def _skeleton_arm_candidates(body, cfg):
    """Кандидаты руки по СКЕЛЕТУ силуэта: кисть = конец длинной ветки, подмышка = развилка
    у тела. Ловит руки, примыкающие к телу гладко (без вогнутости в контуре). [(armpit_xy, tip_xy, len)]."""
    from skimage.morphology import skeletonize
    from scipy.ndimage import convolve
    mask, _, (ox, oy) = _rasterize_body(body, close_px=int(cfg.get("armpit_close_px", 9)))
    sk = skeletonize(mask > 0)
    if sk.sum() < 10:
        return []
    H, W = sk.shape
    kern = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]])
    deg = convolve(sk.astype(np.uint8), kern, mode="constant") * sk
    skset = set(map(tuple, np.argwhere(sk)))
    ends = [tuple(p) for p in np.argwhere(deg == 1)]
    def nbrs(p):
        y, x = p
        return [(y + dy, x + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dy or dx) and (y + dy, x + dx) in skset]
    minlen = cfg.get("arm_skel_min_frac", 0.10) * max(W, H)
    out = []
    for e in ends:
        path, prev, cur = [e], None, e
        for _ in range(W + H):
            if deg[cur] >= 3 and len(path) > 1:
                break
            nx = [n for n in nbrs(cur) if n != prev]
            if not nx:
                break
            prev, cur = cur, nx[0]; path.append(cur)
        if len(path) < minlen:
            continue
        out.append(((cur[1] + ox, cur[0] + oy), (e[1] + ox, e[0] + oy), len(path)))  # (armpit, tip, len)
    return out


def detect_arms_v2(page, body, cfg):
    """Детект руки v2 (TZ_arm_detect_v2) по ТОЧНОМУ контуру: подмышка = вогнутость корня руки;
    рез — ВЕРТИКАЛЬ (90° к холсту) чуть внутрь руки от подмышки (проходит по вырезу под рукой
    и отделяет руку, не задевая тело); отрезанное ВАЛИДИРУЕТСЯ наличием КИСТИ (нет кисти -> не
    рука). Возвращает (список {poly_pt, f, tip}, остаточное тело)."""
    from shapely.geometry import LineString, Point
    from shapely.ops import split, unary_union
    max_arm = cfg.get("arm_detect_max_frac", 0.30) * body.area      # строгий лимит детекта (не из manual arm_max_frac)
    min_arm = cfg.get("armpit_min_arm_frac", 0.015) * body.area
    offs = cfg.get("arm_vcut_offsets_pt", [6, 10, 16, 22, 30])      # сдвиг вертикали внутрь руки от подмышки
    exp = cfg.get("expected_arms") or 1                             # по умолчанию 1 рука; 2 — только если явно задано
    arms, cur = [], body
    for _ in range(exp):
        axx = cur.centroid.x; b = cur.bounds
        cons = sorted(_contour_concavities(cur, cfg), key=lambda t: -t[2])  # глубочайшие вогнутости — первыми
        found = None
        for (fx, fy, fd) in cons:
            side = 1 if fx >= axx else -1                            # рука отходит от центра тела
            for off in offs:
                x = fx + side * off
                parts = list(getattr(split(cur, LineString([(x, b[1] - 5), (x, b[3] + 5)])), "geoms", [cur]))
                if len(parts) < 2:
                    continue
                armside = [p for p in parts if (p.centroid.x - x) * side > 0]  # куски со стороны руки
                for arm in sorted(armside, key=lambda p: p.area):    # самый компактный -> рука
                    Aa = arm.area
                    if Aa < min_arm or Aa > max_arm:
                        continue
                    ok, tip, _w = _arm_has_hand(page, arm, cfg)       # ВАЛИДАЦИЯ кистью
                    if ok:
                        found = (arm, parts, (fx, fy), tip); break
                if found:
                    break
            if not found and cfg.get("arm_vcut_perp_fallback", True):   # ПОДНЯТЫЕ вверх руки: перпендикуляр к оси
                reach = cfg.get("arm_perp_min_reach_frac", 0.15) * max(b[2] - b[0], b[3] - b[1])
                hull = np.asarray(cur.convex_hull.exterior.coords[:-1], float)
                best = None                                             # берём НАИМЕНЬШУЮ валидную руку (не кусок тела)
                for tc in hull:
                    if np.hypot(tc[0] - fx, tc[1] - fy) < reach:
                        continue
                    armp, restp = cut_across_arm(cur, (fx, fy), tuple(tc), cfg)  # перпендикуляр к (подмышка->tc)
                    if armp is None or not (min_arm <= armp.area <= max_arm):
                        continue
                    ok, tip, _w = _arm_has_hand(page, armp, cfg)
                    if ok and (best is None or armp.area < best[0].area):
                        best = (armp, restp, tip)
                if best is not None:
                    found = (best[0], [best[0], best[1]], (fx, fy), best[2])
                    root_r = cfg.get("arm_root_pair_frac", 0.38) * max(b[2] - b[0], b[3] - b[1])
                    lower = None; lbest = 1e18                       # перенос подмышки на НИЖНИЙ угол корня (торс->рука)
                    for (gx, gy, gd) in cons:                        # парный угол = БЛИЖАЙШАЯ вогнутость ниже верхнего
                        if gy <= fy + 2:
                            continue
                        dd2 = np.hypot(gx - fx, gy - fy)
                        if dd2 > root_r:
                            continue
                        if dd2 < lbest:
                            lbest = dd2; lower = (gx, gy)
                    if lower is not None:                            # рез ХОРДОЙ верхний_угол<->нижняя_подмышка
                        exc = cur.exterior
                        a = np.asarray(exc.interpolate(exc.project(Point(fx, fy))).coords[0], float)
                        c = np.asarray(exc.interpolate(exc.project(Point(*lower))).coords[0], float)
                        dv = c - a; nn = np.hypot(*dv)
                        if nn > 1:
                            dv = dv / nn
                            pr = list(getattr(split(cur, LineString([tuple(a - dv * 40), tuple(c + dv * 40)])), "geoms", [cur]))
                            thand = Point(*best[2])                   # кисть уже найдена перп-резом -> не перепроверяем
                            for cand in sorted(pr, key=lambda p: p.area):
                                if not (min_arm <= cand.area <= max_arm):
                                    continue
                                if cand.distance(thand) < 5:          # кусок хорды, содержащий кисть = рука
                                    found = (cand, pr, (float(lower[0]), float(lower[1])), best[2]); break
            if found:
                break
        if not found:
            break
        arm, parts, f, tip = found
        rest = unary_union([p for p in parts if p is not arm])
        if rest.geom_type == "MultiPolygon":
            rest = max(rest.geoms, key=lambda p: p.area)
        arms.append({"poly_pt": arm, "f": f, "tip": tip})
        cur = rest
        if exp is not None and len(arms) >= exp:
            break
    return arms, cur


def cut_waving_arms(page, contour_pt, cfg, strict=True):
    """Вырезать машущие руки ОТ ПОДМЫШКИ из контура (PT). Возвращает (body_pt, cut_arms).
    При кандидатов>expected_arms оставляем самые «рукоподобные» по цвету (кожа/рукав R>>B,
    не шланг/вода). strict=True (перёд): расхождение с expected_arms -> ошибка; strict=False
    (зад): режем best-effort без ошибок. Общий путь для переда и зада."""
    if cfg.get("arm_cut") != "armpit":
        return contour_pt, []
    body_pt = contour_pt
    man = cfg.get("manual_arms")                                      # [{f, tip, blade:[[x,y],[x,y]]}] — ручной рез
    if man:                                                           # blade = пара углов корня; режем ХОРДОЙ между ними
        from shapely.geometry import Point as _Pt
        exc = body_pt.exterior
        def _snap(p):
            return np.asarray(exc.interpolate(exc.project(_Pt(*p))).coords[0], float)
        cut = []
        from shapely.geometry import LineString as _LS
        darts = []                                                    # пунктир-вытачки плеча (PT) для отрисовки
        for m in man:
            f = tuple(m["f"]); tip = tuple(m["tip"]); bl = m.get("blade")
            shA = shB = None                                          # концы хорды реза (для закругления плеча)
            if bl:                                                    # 2 ТОЧКИ: хорда — привязать к контуру + продлить
                a = _snap(bl[0]); c = _snap(bl[1]); shA, shB = a, c; dv = c - a; nn = np.hypot(*dv)
                if nn > 1:
                    dv = dv / nn; bl = (tuple(a - dv * 40), tuple(c + dv * 40))
            else:                                                    # 1 ТОЧКА: ВЕРТИКАЛЬНЫЙ рез (90° к холсту) от f
                fs = _snap(f); fx = float(fs[0])                     # вверх до следующей точки контура
                yb = body_pt.bounds
                inter = exc.intersection(_LS([(fx, yb[1] - 5), (fx, yb[3] + 5)]))
                pts = []
                if not inter.is_empty:
                    for g in getattr(inter, "geoms", [inter]):
                        pts += [tuple(c) for c in np.atleast_2d(np.asarray(g.coords))]
                ups = [p for p in pts if p[1] < fs[1] - 1]           # выше f (y вниз -> меньше y = выше)
                if ups:
                    up = min(ups, key=lambda p: fs[1] - p[1])        # ближайшая точка контура вверх
                    shA = np.array([fx, float(fs[1])]); shB = np.array([fx, float(up[1])])
                    bl = ((fx, float(fs[1]) + 40), (fx, float(up[1]) - 40))  # вертикальная хорда, продлённая
            arm_poly, body2 = cut_across_arm(body_pt, f, tip, cfg, blade=bl)
            if arm_poly is None:
                if strict:
                    raise RuntimeError(f"ручной рез руки не удался (f={f}, tip={tip})")
                continue
            body_pt = body2
            exc = body_pt.exterior                                    # контур изменился после реза
            blade_pt = [tuple(shA), tuple(shB)] if shA is not None else None  # концы хорды реза (стык руки) в PT
            cut.append({"poly_pt": arm_poly, "f": f, "tip": tip,
                        "wrist": m.get("wrist"), "elbow": m.get("elbow"),  # ручные срезы запястья/локтя (опц.)
                        "blade_pt": blade_pt,                              # линия реза плеча (для разметки на переде)
                        "hole_dy": m.get("hole_dy", 0.0)})                 # ручной сдвиг отверстия руки (мм)
        if darts:
            cfg["_shoulder_darts"] = cfg.get("_shoulder_darts", []) + darts   # PT -> перевести в мм в build_layout
        return body_pt, cut
    if cfg.get("arm_detect_v2"):                                      # v2: вертикальный рез у корня + валидация кистью
        arms2, body2 = detect_arms_v2(page, body_pt, cfg)
        if strict and cfg.get("expected_arms") is not None and len(arms2) != cfg["expected_arms"]:
            raise RuntimeError(f"v2: ожидалось {cfg['expected_arms']} рук, найдено {len(arms2)}")
        return body2, arms2
    arms = detect_armpits(body_pt, cfg)
    exp = cfg.get("expected_arms")
    if exp is not None and len(arms) > exp:
        from side_color import arm_fill_color
        def _warmth(a):
            c = arm_fill_color(page, np.asarray(a["poly"].exterior.coords, float))
            return int(c[0]) - int(c[2])
        arms = sorted(arms, key=_warmth, reverse=True)[:exp]
    if strict and exp is not None and len(arms) != exp:
        raise RuntimeError(f"ожидалось {exp} рук, найдено {len(arms)}")
    cut = []
    for a in arms:
        arm_poly, body2 = cut_across_arm(body_pt, a["f"], a["tip"], cfg, blade=a.get("blade"))
        if arm_poly is None:
            if strict:
                raise RuntimeError("рез от подмышки не разделил тело — проверь пороги")
            continue
        body_pt = body2
        bl = a.get("blade")
        cut.append({"poly_pt": arm_poly, "f": a["f"], "tip": a["tip"],
                    "blade_pt": [tuple(bl[0]), tuple(bl[1])] if bl else None})
    return body_pt, cut


def detect_armpits(body, cfg):
    """Найти машущие руки по вогнутым изломам (подмышкам) контура тела (PT).
    Возвращает список {f:(x,y), tip:(x,y), poly:arm, len, side, blade}. 1 для A/B/D/E, 2 для C.
    Точка f опускается в НИЖНЮЮ подмышку (_refine_armpit), чтобы рука уходила целиком."""
    mask, _, (ox, oy) = _rasterize_body(body, close_px=int(cfg.get("armpit_close_px", 9)))
    c = _largest_contour(mask)
    if c is None:
        return []
    hull_i = cv2.convexHull(c, returnPoints=False)
    D = cv2.convexityDefects(c, hull_i)
    if D is None:
        return []
    cc = c.reshape(-1, 2)
    Wb, Hb = _poly_width(body), _poly_height(body)
    axx, axy = body.centroid.x, body.centroid.y
    min_depth = cfg.get("armpit_min_depth_frac", 0.06) * Hb
    center_frac = cfg.get("armpit_center_frac", 0.15)
    min_arm = cfg.get("armpit_min_arm_frac", 0.03) * body.area
    max_arm = cfg.get("arm_max_frac", 0.35) * body.area
    min_aspect = cfg.get("arm_min_aspect", 2.5)
    tip_free_frac = cfg.get("armpit_tip_free_frac", 0.20)
    up_slack = cfg.get("armpit_up_slack_frac", 0.0) * Hb           # допускаем чуть ниже центра
    all_f = [(float(c[int(ff)][0][0] + ox), float(c[int(ff)][0][1] + oy), depth / 256.0)
             for s, e, ff, depth in D.reshape(-1, 4)]              # все вогнутости (для refine)
    cand = []
    for s, e, ff, depth in D.reshape(-1, 4):
        d_pt = depth / 256.0
        xf = float(c[ff][0][0] + ox); yf = float(c[ff][0][1] + oy)
        if d_pt < min_depth:
            continue
        if abs(xf - axx) < center_frac * Wb:                      # шея/пах/между пальцами/между персон
            continue
        if yf > axy + up_slack:                                   # ниже центра = ноги/низ
            continue
        f = (xf, yf)
        best_here = None
        for tip in _arm_tip_candidates(cc, s, e, ff, ox, oy):     # перебор сторон моста -> рука
            arm_len = math.hypot(tip[0] - xf, tip[1] - yf)
            if arm_len < tip_free_frac * max(Wb, Hb):
                continue
            if cfg.get("arm_tip_outward"):                            # рука уходит НАРУЖУ от оси (боковые руки),
                if (tip[0] - xf) * (xf - axx) <= 0:                   # не вверх к ложке/декору: tip в сторону подмышки
                    continue
                if abs(tip[0] - axx) <= abs(xf - axx):                # и дальше от оси, чем подмышка
                    continue
            arm, rest = cut_across_arm(body, f, tip, cfg)
            if arm is None:
                continue
            A = arm.area
            thick = A / max(arm_len, 1e-6)
            asp = arm_len / max(thick, 1e-6)
            if A < min_arm or A > max_arm or asp < min_aspect:
                continue
            if best_here is None or asp > best_here["asp"]:       # самый «рукоподобный» рез дефекта
                best_here = {"f": f, "tip": tip, "len": arm_len, "poly": arm, "asp": asp,
                             "side": "L" if xf < axx else "R", "depth": d_pt}
        if best_here is not None:
            cand.append(best_here)
    # лучший (по длине вылета) с каждой стороны
    best = {}
    for a in cand:
        if a["side"] not in best or a["len"] > best[a["side"]]["len"]:
            best[a["side"]] = a
    # опустить f в НИЖНЮЮ подмышку (вся рука целиком, без лишнего)
    refined = [_refine_armpit(body, a, all_f, cfg, Wb, Hb, axx, min_arm, max_arm,
                              min_aspect, tip_free_frac) for a in best.values()]
    res = sorted(refined, key=lambda a: -a["len"])
    return res[:cfg.get("max_arms", 2)]


def place_arm_piece(layout, arm_poly_pt, fig, cfg, page, gap=120.0):
    """Положить отрезанную руку (PT-контур) РЯДОМ с передней панелью + текстура макета.
    Возвращает arm_pts (контур руки в PT) для наполнения arm_dds (§3.7)."""
    f_mm = fig.s * PT2MM
    arm_mm = affinity.scale(arm_poly_pt, f_mm, f_mm, origin=(0, 0))
    p = affinity.translate(arm_mm, -arm_mm.bounds[0], -arm_mm.bounds[1])    # к (0,0)
    w = p.bounds[2]; h = p.bounds[3]
    occupied = {pc.get("arm_side") for pc in layout.get("hand_pieces", [])}
    if "left" not in occupied:
        _shift_layout(layout, w + gap); x = gap; side = "left"             # колонка слева ОТ ПЕРЕДА
    else:
        x = layout["front"].bounds[2] + gap; side = "right"                # сразу справа от переда
    placed = affinity.translate(p, x, gap)
    # src из ТОЙ ЖЕ exterior.coords[:-1] (пересчёт в PT макета) -> равная длина с dst
    dst_coords = list(placed.exterior.coords)[:-1]
    src_pts = [((cx - x) / f_mm + arm_mm.bounds[0] / f_mm,
                (cy - gap) / f_mm + arm_mm.bounds[1] / f_mm) for (cx, cy) in dst_coords]
    layout.setdefault("hand_pieces", []).append(
        {"name": "ruka", "poly": placed, "src": src_pts, "arm_side": side, "w": w, "h": h})
    layout["sheet_w"] = max(layout["sheet_w"], x + w + gap)
    layout["sheet_h"] = max(layout["sheet_h"], placed.bounds[3] + gap)
    return np.array([[px, py] for px, py in arm_poly_pt.exterior.coords])


def detect_wrist(contour_pts, body_x):
    """Запястье (начало кисти) на контуре машущей руки.
    Вход: точки замкнутого контура руки (в мм), body_x — X центра тела (рука
    крепится к телу ближним концом; кисть — на дальнем конце оси).
    Возвращает dict: {tip:(x,y) кончик кисти, wrist:[B,T] линия-рез запястья,
    frac:доля площади кисти}. Логика: PCA-ось руки -> два крайних конца; кисть =
    конец дальше от тела. Каждый вогнутый излом (convexity defect) — кандидат-якорь B;
    его парная точка T = узкое место (шейка) на ПРОТИВОПОЛОЖНОЙ кромке (ближайшая к B,
    не у самого кончика), рез B->T отсекает лобик со стороны кисти. Из якорей с
    долей кисти >= WRIST_MIN_FRAC (отсекает палец/мусор) берём с НАИМЕНЬШЕЙ долей —
    кисть самый компактный реальный лобик у кончика (предплечье/локоть отсекают больше)."""
    pts = np.asarray(contour_pts, dtype=float)
    x0, y0 = pts[:, 0].min(), pts[:, 1].min()
    P = pts - [x0, y0]
    W, H = int(P[:, 0].max()) + 20, int(P[:, 1].max()) + 20
    mask = np.zeros((H, W), np.uint8)
    cv2.fillPoly(mask, [(P + 10).astype(np.int32)], 1)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(cnts, key=cv2.contourArea)
    cc = c.reshape(-1, 2).astype(float)
    n = len(cc)
    hull = cv2.convexHull(c, returnPoints=False)
    D = cv2.convexityDefects(c, hull)
    if D is None:
        return None
    sig = [int(r[2]) for r in D.reshape(-1, 4) if r[3] / 256 > 5]       # выраженные вогнутые изломы
    if not sig:
        return None
    m = cc.mean(0)
    axis = np.linalg.svd(cc - m)[2][0]                                  # главная ось руки
    proj = (cc - m) @ axis
    i_a, i_b = int(np.argmin(proj)), int(np.argmax(proj))
    gx = lambda i: cc[i, 0] - 10 + x0                                    # X в исходных координатах
    hand = i_a if abs(gx(i_a) - body_x) > abs(gx(i_b) - body_x) else i_b  # дальше от тела = кисть
    arc = lambda i, j: min(abs(i - j), n - abs(i - j))
    side = lambda f: ((f - hand) % n) < n / 2
    total = abs(Polygon(cc).area)

    def partner_frac(b):
        """Для якоря b: парная точка T (шейка напротив) и доля площади кисти."""
        cand = [i for i in range(n) if side(i) != side(b) and arc(i, hand) > arc(b, hand) * 0.4]
        if not cand:
            return None
        t = min(cand, key=lambda i: np.hypot(cc[i, 0] - cc[b, 0], cc[i, 1] - cc[b, 1]))
        path, i = [], b                                                 # дуга b->t через кисть
        while True:
            path.append(i)
            if i == t:
                break
            i = (i + 1) % n
        if hand not in path:
            path, i = [], t
            while True:
                path.append(i)
                if i == b:
                    break
                i = (i + 1) % n
        return t, abs(Polygon(cc[path]).area) / total

    scored = []
    for b in sig:
        pf = partner_frac(b)
        if pf:
            scored.append((b, pf[0], pf[1]))
    good = [s for s in scored if s[2] >= WRIST_MIN_FRAC]
    if not good:
        return None
    b, t, frac = min(good, key=lambda s: s[2])                          # самый компактный реальный лобик
    to_xy = lambda i: (float(cc[i, 0] - 10 + x0), float(cc[i, 1] - 10 + y0))
    return {"tip": to_xy(hand), "wrist": [to_xy(b), to_xy(t)], "frac": frac}


def _plen(poly):
    """Длина разомкнутой ломаной."""
    p = np.asarray(poly, float)
    return float(np.hypot(*np.diff(p, axis=0).T).sum())


def cut_arm(contour_pts, wrist, tip):
    """Разрезать руку по линии запястья B–T на два замкнутых контура.
    Возвращает (hand_pts, forearm_pts): кисть = дуга B..кончик..T (замкнута прямой),
    предплечье = дуга T..локоть..B (замкнута той же прямой)."""
    A = np.asarray(contour_pts, float)
    n = len(A)
    idx = lambda p: int(np.argmin((A[:, 0] - p[0]) ** 2 + (A[:, 1] - p[1]) ** 2))
    iB, iT, iH = idx(wrist[0]), idx(wrist[1]), idx(tip)

    def seg(a, b):
        path, i = [], a
        while True:
            path.append(i)
            if i == b:
                break
            i = (i + 1) % n
        return path

    arcH = seg(iB, iT)
    if iH not in arcH:
        arcH = seg(iT, iB)
    arcF = seg(iT, iB) if arcH == seg(iB, iT) else seg(iB, iT)
    return A[arcH], A[arcF]


def _top_edge(forearm_pts, wrist, ang_thr=22.0, simp_mm=4.0):
    """Длины (верхнее, нижнее) рёбер предплечья. forearm_pts упорядочены T..основание..B
    (срез запястья B–T замыкает контур, в силуэт не входит). Разбиваем цепочку по углам;
    ребро ОСНОВАНИЯ = ребро с самой дальней от середины запястья серединой; всё до него =
    верхняя кромка (от T), всё после = нижняя (до B)."""
    from shapely.geometry import LineString
    B, T = np.asarray(wrist[0], float), np.asarray(wrist[1], float)
    wmid = (B + T) / 2.0
    v = np.array(LineString(forearm_pts).simplify(simp_mm).coords)       # упоряд. цепочка T..B
    n = len(v)
    corners = [0]
    for i in range(1, n - 1):
        a = v[i] - v[i - 1]; b = v[i + 1] - v[i]
        na, nb = np.hypot(*a), np.hypot(*b)
        if na < 1e-6 or nb < 1e-6:
            continue
        if math.degrees(math.acos(np.clip(a @ b / (na * nb), -1, 1))) >= ang_thr:
            corners.append(i)
    corners.append(n - 1)
    edges = []                                                           # (len, i_start, i_end, dist-до-wmid)
    for k in range(len(corners) - 1):
        i, j = corners[k], corners[k + 1]
        mid = (v[i] + v[j]) / 2.0
        edges.append((_plen(v[i:j + 1]), i, j, float(np.hypot(*(mid - wmid)))))
    if not edges:
        L = _plen(v) / 2.0
        return L, L
    base = max(range(len(edges)), key=lambda k: edges[k][3])             # ребро основания = самое дальнее
    i_lo, i_hi = edges[base][1], edges[base][2]
    ch1, ch2 = v[0:i_lo + 1], v[i_hi:]                                   # две кромки по сторонам основания
    l1, l2 = _plen(ch1) if i_lo > 0 else 0.0, _plen(ch2) if i_hi < n - 1 else 0.0
    y1 = ch1[:, 1].mean() if len(ch1) > 1 else 1e9
    y2 = ch2[:, 1].mean() if len(ch2) > 1 else 1e9
    return (l1, l2) if y1 <= y2 else (l2, l1)                            # (верх=меньший y, низ)


def build_hand(hand_pts, forearm_pts, wrist, scale, gap_mm=40.0,
               fore_top_mm=None, fore_bot_mm=None):
    """Деталь кисти «печенье»: перёд + зеркальный зад + боковина-прямоугольник.
    Все замеры в ОДНОМ масштабе (вырезанные детали руки), затем × scale → мм.
    Ширина боковины W = (верхнее_ребро_предплечья − 2·ровное_ребро_кисти)/2;
    высота H = длина кривой кромки кисти (периметр без линии среза).
    Открытый край кисти 2·straight + 2·W == верхнее ребро предплечья (инвариант).

    fore_top_mm/fore_bot_mm — ЯВНЫЕ рёбра предплечья в мм (для стыковки с ЭТАЛОННЫМ
    предплечьем, у которого верхнее ребро фиксировано). Если заданы — _top_edge не
    вызывается. Если кисть шире этого ребра (2·straight ≥ 0.9·fore_top) — кисть
    ПРОПОРЦИОНАЛЬНО ужимается (hand_scale), чтобы боковина W была положительной и шов сошёлся."""
    B, T = np.asarray(wrist[0], float), np.asarray(wrist[1], float)
    straight = float(np.hypot(*(B - T))) * scale                        # ровное ребро кисти
    curved = (_plen(hand_pts) - float(np.hypot(*(hand_pts[0] - hand_pts[-1])))) * scale
    if fore_top_mm is not None:                                         # стыковка с ЭТАЛОННЫМ предплечьем
        fore_top = float(fore_top_mm)
        fore_bot = float(fore_bot_mm) if fore_bot_mm is not None else fore_top
    else:
        fore_top, fore_bot = _top_edge(forearm_pts, wrist)
        fore_top *= scale
        fore_bot *= scale
    hand_scale = 1.0                                                    # ужать кисть под ребро эталона
    if 2.0 * straight >= 0.9 * fore_top:
        hand_scale = (0.7 * fore_top) / (2.0 * straight)
        straight *= hand_scale
        curved *= hand_scale
    W = (fore_top - 2.0 * straight) / 2.0                               # ширина боковины
    H = curved                                                          # высота боковины
    if W <= 0:
        raise ValueError(
            f"ширина боковины кисти <= 0 ({W:.1f} мм): верхнее ребро предплечья "
            f"{fore_top:.1f} < 2·ровное ребро кисти {2*straight:.1f}. Проверь, что кисть "
            f"и предплечье в одном масштабе.")
    # перёд — кисть в мм, bbox в (0,0) (с учётом ужатия hand_scale)
    front = affinity.scale(Polygon(hand_pts), scale * hand_scale, scale * hand_scale, origin=(0, 0))
    mnx, mny, mxx, mxy = front.bounds
    front = affinity.translate(front, -mnx, -mny)
    fw, fh = mxx - mnx, mxy - mny
    back = affinity.scale(front, xfact=-1.0, origin=(fw / 2.0, fh / 2.0))  # зеркало Л↔П
    gusset = box(0, 0, W, H)
    # раскладка без наложений: перёд | зад | боковина
    back = affinity.translate(back, fw + gap_mm, 0)
    gusset = affinity.translate(gusset, 2 * fw + 2 * gap_mm, 0)
    opening = 2.0 * straight + 2.0 * W
    return {
        "front": front, "back": back, "gusset": gusset,
        "straight": straight, "curved": curved, "fore_top": fore_top, "fore_bot": fore_bot,
        "W": W, "H": H, "opening": opening, "src_pts": np.asarray(hand_pts, float),
        "sheet_w": 2 * fw + 2 * gap_mm + W, "sheet_h": max(fh, H),
    }


def _shift_layout(layout, dx):
    """Сдвинуть всю раскладку вправо на dx (чтобы освободить колонку слева под руку)."""
    layout["front"] = affinity.translate(layout["front"], dx, 0)
    layout["front_x"] += dx
    for s in layout["strips"]:
        s["poly"] = [(x + dx, y) for x, y in s["poly"]]
        s["x"] += dx
    b = layout.get("base")
    if b:
        b["dnishe"] = affinity.translate(b["dnishe"], dx, 0)
        b["fan_c"] = (b["fan_c"][0] + dx, b["fan_c"][1])
        lx, ly, lw, lh = b["leg"]; b["leg"] = (lx + dx, ly, lw, lh)
    ah = layout.get("arm_hole")
    if ah:
        ah["cx"] += dx
    layout["sheet_w"] += dx


def place_arm(layout, pieces, arm_side, gap=120.0):
    """Колонка из 3 деталей руки НА СТОРОНЕ руки, снизу вверх: база -> плечо -> предплечье.
    Детали не касаются (зазор gap). Слева — со сдвигом раскладки, справа — за правым краем."""
    if len(pieces) < 3:
        return
    col_w = max(p["w"] for p in pieces)
    if arm_side == "left":
        _shift_layout(layout, col_w + gap)
        base_x = gap
    else:
        base_x = layout["sheet_w"] + gap
    # сверху вниз в колонке: предплечье, плечо, база (т.е. снизу вверх: база, плечо, предплечье)
    placed, y = [], gap
    for name in ("predplechie", "plecho", "baza"):
        p = next(x for x in pieces if x.get("name") == name)
        px = base_x + (col_w - p["w"]) / 2.0                      # по центру колонки
        placed.append({**p, "poly": affinity.translate(p["poly"], px, y)})
        y += p["h"] + gap
    layout.setdefault("arm_pieces", []).extend(placed)            # НАКОПЛЕНИЕ (несколько отдельных рук)
    layout["sheet_w"] = max(layout["sheet_w"], base_x + col_w + gap)
    layout["sheet_h"] = max(layout["sheet_h"], y + gap)
    return placed                                                 # колонка этой руки (для place_hand над ней)


def _shift_all_y(layout, dy):
    """Сдвинуть всю раскладку вниз на dy (чтобы ничего не ушло выше поля)."""
    layout["front"] = affinity.translate(layout["front"], 0, dy)
    for s in layout["strips"]:
        s["poly"] = [(x, y + dy) for x, y in s["poly"]]
        s["y"] += dy
    b = layout.get("base")
    if b:
        b["dnishe"] = affinity.translate(b["dnishe"], 0, dy)
        b["fan_c"] = (b["fan_c"][0], b["fan_c"][1] + dy)
        lx, ly, lw, lh = b["leg"]; b["leg"] = (lx, ly + dy, lw, lh)
    ah = layout.get("arm_hole")
    if ah:
        ah["cy"] += dy
    for ap in layout.get("arm_pieces", []):
        ap["poly"] = affinity.translate(ap["poly"], 0, dy)
    for hp in layout.get("hand_pieces", []):
        hp["poly"] = affinity.translate(hp["poly"], 0, dy)
    layout["sheet_h"] += dy


def place_hand(layout, h, arm_side, gap=120.0, arm_col=None):
    """Детали кисти РЯДОМ, НАД колонкой руки, по порядку шва: левая сторона (перёд) ->
    боковина -> правая сторона (зад). Ряд выровнен по низу над верхом колонки руки;
    если ряд уходит выше поля — сдвигаем всю раскладку вниз.
    arm_col — колонка ИМЕННО этой руки (для нескольких отдельных рук); иначе все arm_pieces."""
    norm = lambda p: affinity.translate(p, -p.bounds[0], -p.bounds[1])   # к началу координат
    row = [("левая сторона", norm(h["front"])), ("боковина", norm(h["gusset"])),
           ("правая сторона", norm(h["back"]))]
    aps = arm_col if arm_col is not None else layout.get("arm_pieces", [])
    if aps:
        xs0 = min(ap["poly"].bounds[0] for ap in aps); xs1 = max(ap["poly"].bounds[2] for ap in aps)
        col_cx = (xs0 + xs1) / 2.0
        top = min(ap["poly"].bounds[1] for ap in aps)
    else:                                                                # запас: над правым краем листа
        col_cx = layout["sheet_w"] - 300; top = gap
    row_w = sum(p.bounds[2] for _, p in row) + gap * (len(row) - 1)
    x = col_cx - row_w / 2.0
    bottom = top - gap                                                   # низ ряда — над рукой
    placed = []
    for name, p in row:
        pw, ph = p.bounds[2], p.bounds[3]
        # src_pts (контур кисти в pt) привязан к перёду/заду -> для текстуры: аффин src->poly
        src = h.get("src_pts") if "сторона" in name else None
        placed.append({"name": name, "poly": affinity.translate(p, x, bottom - ph),
                       "w": pw, "h": ph, "src": src})
        x += pw + gap
    layout.setdefault("hand_pieces", []).extend(placed)                  # НАКОПЛЕНИЕ (несколько отдельных кистей)
    miny = min(pl["poly"].bounds[1] for pl in placed)
    if miny < gap:                                                       # не вылезать за верх поля
        _shift_all_y(layout, gap - miny)
    layout["sheet_w"] = max(layout["sheet_w"], col_cx + row_w / 2.0 + gap)


def _develop_frustum(c1, c2, L, n=60, tilt1=0.0, tilt2=0.0):
    """Развёртка усечённого конуса (окружности c1 внешняя, c2 внутренняя, наклонная высота L) —
    кольцевой сектор. tilt1/tilt2 — наклон среза на концах c1/c2 (рад), даёт ВОЛНИСТУЮ кромку
    (косой срез на изгибе руки): радиальный сдвиг = r·tan(tilt)·cos(φ), r=c/2π, φ — положение
    по окружности. Амплитуда волны кодирует угол изгиба. c1==c2 -> прямоугольник (цилиндр)."""
    from shapely.geometry import Polygon, box
    swap = c1 < c2
    if swap:
        c1, c2, tilt1, tilt2 = c2, c1, tilt2, tilt1
    if abs(c1 - c2) < 1e-3:
        if abs(tilt1) < 1e-6 and abs(tilt2) < 1e-6:
            return box(0, 0, c1, L)
        c2 = c1 - 1e-3
    R1 = L * c1 / (c1 - c2); R2 = L * c2 / (c1 - c2); th = c1 / R1
    a = np.linspace(-th / 2, th / 2, n)
    phi = (a + th / 2) / th * 2 * np.pi                              # положение по окружности 0..2π
    A1 = (c1 / (2 * np.pi)) * math.tan(tilt1)                        # амплитуда волны на c1
    A2 = (c2 / (2 * np.pi)) * math.tan(tilt2)
    r1 = R1 + A1 * np.cos(phi); r2 = R2 + A2 * np.cos(phi)
    outer = [(r1[i] * np.sin(a[i]), -r1[i] * np.cos(a[i])) for i in range(n)]
    inner = [(r2[i] * np.sin(a[i]), -r2[i] * np.cos(a[i])) for i in range(n - 1, -1, -1)]
    p = Polygon(outer + inner)
    return affinity.translate(p, -p.bounds[0], -p.bounds[1])


def detect_wrist_color(page, arm_pt, cfg):
    """Запястье по ЦВЕТУ: граница светлой перчатки (кисть) и цветного рукава.
    Берём ГЛАВНЫЙ светлый компонент (перчатку) — так ложная светлая кромка рукава
    отсекается; два конца его границы с рукавом на силуэте = рез запястья.
    Возвращает {tip, wrist:[B,T]} в pt. tip = кончик (дальше от тела = min x)."""
    from side_color import _flat
    A = np.asarray(arm_pt, float); nA = len(A)
    bx0, by0, bx1, by1 = A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max()
    inbb = lambda r: r.x0 >= bx0 - 2 and r.x1 <= bx1 + 2 and r.y0 >= by0 - 2 and r.y1 <= by1 + 2
    Z, mg = (cfg or {}).get("arm_raster_z", 5.0), 10               # меньше растр руки = меньше памяти
    Wp, Hp = int((bx1 - bx0 + 2 * mg) * Z), int((by1 - by0 + 2 * mg) * Z)
    from PIL import Image, ImageDraw
    tex = Image.new("RGB", (Wp, Hp), (255, 255, 255)); dtx = ImageDraw.Draw(tex)
    col = lambda c: tuple(int(255 * v) for v in c) if c else None
    Tr = lambda x, y: ((x - bx0 + mg) * Z, (y - by0 + mg) * Z)
    for dd in page.get_drawings():
        if (dd.get("fill") is None and dd.get("color") is None) or not inbb(dd["rect"]):
            continue
        f = col(dd.get("fill")); s = col(dd.get("color"))
        for sub in _flat(dd):
            if len(sub) < 2:
                continue
            pts = [Tr(x, y) for x, y in sub]
            if f is not None and len(pts) >= 3:
                dtx.polygon(pts, fill=f)
            if s is not None:
                dtx.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=s, width=max(1, int(dd.get("width", .5) * Z)))
    arr = np.asarray(tex).astype(int); vmean = arr.mean(2)
    sil = np.zeros((Hp, Wp), np.uint8); cv2.fillPoly(sil, [((A - [bx0, by0] + mg) * Z).astype(np.int32)], 1)
    light_thr = cfg.get("glove_light_thr", 205)
    glove = ((vmean > light_thr) & (sil > 0)).astype(np.uint8)
    sleeve = ((sil > 0) & (vmean < 150) & ~((vmean > light_thr))).astype(np.uint8)
    num, lab, stats, _ = cv2.connectedComponentsWithStats(glove, 8)
    if num < 2:
        return None
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))             # главный светлый = перчатка
    gmain = (lab == main).astype(np.uint8)
    band = (cv2.dilate(gmain, np.ones((7, 7), np.uint8)) &
            cv2.dilate(sleeve, np.ones((7, 7), np.uint8)) & sil).astype(np.uint8)
    ys, xs = np.where(band > 0)
    if len(xs) < 4:
        return None
    pts = np.c_[xs, ys].astype(float)
    m = pts.mean(0); ax = np.linalg.svd(pts - m)[2][0]; pr = (pts - m) @ ax
    to_arm = lambda px, py: (px / Z - mg + bx0, py / Z - mg + by0)
    e1 = to_arm(*pts[np.argmin(pr)]); e2 = to_arm(*pts[np.argmax(pr)])
    i1 = int(np.argmin((A[:, 0] - e1[0]) ** 2 + (A[:, 1] - e1[1]) ** 2))
    i2 = int(np.argmin((A[:, 0] - e2[0]) ** 2 + (A[:, 1] - e2[1]) ** 2))
    tip = tuple(A[int(np.argmin(A[:, 0]))])                            # кончик кисти = дальше от тела
    return {"tip": tip, "wrist": [tuple(A[i1]), tuple(A[i2])]}


def arm_shoulder_xy(page, fig, cfg):
    """Точка плеча на теле (куда ставить отверстие под руку) — ближайшая к кромке примыкания
    рукава. Для фигур без шеи (hole ставится по плечу, а не по шее)."""
    from sides import get_arm_contour, contour_to_mm, smooth_front, PT2MM as _P
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    scale = fig.s * _P
    arm_pt, body_x = get_arm_contour(page)
    wr = detect_wrist(arm_pt.tolist(), body_x=body_x)
    _, fore_pt = cut_arm(arm_pt, wr["wrist"], wr["tip"])
    S = fore_pt * scale
    body = smooth_front(contour_to_mm(fig), cfg)
    d = np.array([body.exterior.distance(Point(p)) for p in S])
    att = S[d < max(d.min() * 2.2, np.percentile(d, 22))]
    return tuple(nearest_points(body, Point(att.mean(0)))[0].coords[0])


def _wrist_score(arm_arr, wrist, tip, scale):
    """Оценка кандидата-запястья: рез cut_arm и проверка инварианта кисти.
    Возвращает (ok, straight_mm, fore_top_mm, W_mm, hand_pts, fore_pts) или None.
    Кисть ДОЛЖНА лежать у кончика (центр дуги кисти ближе к tip, чем к телу),
    и ширина боковины W=(fore_top-2·straight)/2 > 0."""
    try:
        hand_pts, fore_pts = cut_arm(arm_arr, wrist, tip)
    except Exception:
        return None
    if len(hand_pts) < 3 or len(fore_pts) < 3:
        return None
    B, T = np.asarray(wrist[0], float), np.asarray(wrist[1], float)
    straight = float(np.hypot(*(B - T))) * scale
    fore_top, _ = _top_edge(fore_pts, wrist)
    fore_top *= scale
    W = (fore_top - 2.0 * straight) / 2.0
    # кисть компактна у кончика: её площадь < предплечья, и центроид ближе к tip
    tip = np.asarray(tip, float)
    hc = hand_pts.mean(0); fc = fore_pts.mean(0)
    hand_at_tip = np.hypot(*(hc - tip)) < np.hypot(*(fc - tip))
    ok = (W > 0) and hand_at_tip and (abs(Polygon(hand_pts).area) < abs(Polygon(fore_pts).area))
    return ok, straight, fore_top, W, hand_pts, fore_pts


def _wrist_on_poly(page, arm_pt, tip, cfg, armpit=None):
    """Запястье на НАШЕМ полигоне руки. Берём tip из detect_armpits (авторитетный кончик
    кисти). Пробуем ОБА метода — по ЦВЕТУ (светлая перчатка↔рукав) и по выпuклости
    (convexity) — и выбираем валидный (W>0, кисть у кончика). Возвращает (wrist, tip, method).
    armpit (a["f"]) задаёт ОСЬ руки для профиля ширины (hand→подмышка): инвариант к форме
    среза плеча, иначе ось берётся из PCA полигона и «едет» при смене реза (клин у запястья)."""
    A = np.asarray(arm_pt, float)
    tip = tuple(np.asarray(tip, float))
    scale = 1.0  # масштаб не важен для относительной валидации (W>0 инвариантен к scale)
    cands = []
    # 0) по ПРОФИЛЮ ШИРИНЫ (режем на запястье = минимум после ладони) — ОСНОВНОЙ метод
    try:
        ww = _wrist_by_width(Polygon(A), tip, armpit if armpit is not None else cfg.get("_armpit_f"), cfg)
    except Exception:
        ww = None
    if ww is not None:
        cands.append(("width", [tuple(ww[0]), tuple(ww[1])]))
    # 1) по цвету — НО tip берём свой (detect_wrist_color путает кончик для ладони справа)
    try:
        wc = detect_wrist_color(page, A, cfg)
    except Exception:
        wc = None
    if wc is not None:
        cands.append(("color", wc["wrist"]))
    # 2) по выпуклости; body_x = сторона, противоположная кончику кисти вдоль оси
    m = A.mean(0); ax = np.linalg.svd(A - m)[2][0]; pr = (A - m) @ ax
    e_lo = A[int(np.argmin(pr))]; e_hi = A[int(np.argmax(pr))]
    tv = np.asarray(tip, float)
    body_end = e_lo if np.hypot(*(e_hi - tv)) < np.hypot(*(e_lo - tv)) else e_hi
    try:
        wr = detect_wrist(A.tolist(), body_x=float(body_end[0]))
    except Exception:
        wr = None
    if wr is not None:
        cands.append(("convexity", wr["wrist"]))
    # выбрать валидный; ПРИОРИТЕТ метода width > color > convexity (width режет на запястье),
    # при равенстве — больший запас W
    prio = {"width": 3, "color": 2, "convexity": 1}
    best = None
    for name, wrist in cands:
        sc = _wrist_score(A, wrist, tip, scale)
        if sc is None:
            continue
        ok, straight, fore_top, Wv, _, _ = sc
        key = (1 if ok else 0, prio.get(name, 0), Wv)
        if best is None or key > best[0]:
            best = (key, name, wrist)
    if best is None:
        raise ValueError("запястье не найдено (ни по цвету, ни по convexity)")
    return best[2], tip, best[1]


def build_arm_from_poly(page, arm_pt, tip, scale, cfg, armpit=None, wrist=None, elbow=None, hole_d=None):
    """РУКА ПО ЭТАЛОННОМУ ШАБЛОНУ (examples/arm_template.pdf, фикс. форма для фигуры 2700,
    Ø250) + КИСТЬ, задетектированная из НАШЕГО вырезанного полигона руки (detect_armpits).

    База/плечо/предплечье берутся ИЗ ЭТАЛОНА (build_arm) — одинаковы для всех фигур 2700,
    чистые конические сектора без волн. Из фигуры берётся ТОЛЬКО кисть: детект запястья ->
    cut_arm -> build_hand, состыкованная с ВЕРХНИМ ребром эталонного предплечья
    (opening кисти == predplechie.edge_top). Если кисть шире — пропорционально ужимается.

    arm_pt   — shapely Polygon (контур руки в PT, y вниз);
    tip      — (x,y) кончик кисти в PT (из detect_armpits);
    scale    — fig.s*PT2MM (PT исходника -> мм финала);
    Возвращает {"pieces":[baza,plecho,predplechie], "hand":<build_hand>, "diag":{...}}."""
    arm_poly = arm_pt if hasattr(arm_pt, "exterior") else Polygon(arm_pt)
    arm_arr = np.asarray(arm_poly.exterior.coords[:-1], float)       # PT, замкнут -> без дубля

    if armpit is not None:                                           # tip = самый ДАЛЬНИЙ угол руки от подмышки:
        _fa = np.asarray(armpit, float)                             # для СОГНУТОЙ руки (кулак вверх) ось кисть->подмышка
        _d = np.hypot(arm_arr[:, 0] - _fa[0], arm_arr[:, 1] - _fa[1])  # верна; иначе прямая tip->подмышка идёт через
        tip = tuple(arm_arr[int(np.argmax(_d))])                    # изгиб и запястье уезжает к манжету (кисть=вся рука)

    # 1) запястье + рез на (кисть, предплечье) — ТОЛЬКО для получения формы кисти
    if wrist is not None:                                             # РУЧНОЙ срез запястья: концы привязать к контуру
        from shapely.geometry import Point as _Pt
        exc = arm_poly.exterior
        B = np.asarray(exc.interpolate(exc.project(_Pt(*wrist[0]))).coords[0], float)
        T = np.asarray(exc.interpolate(exc.project(_Pt(*wrist[1]))).coords[0], float)
        wrist = (tuple(B), tuple(T)); tip_used = tuple(tip); wmethod = "manual"
    else:
        wrist, tip_used, wmethod = _wrist_on_poly(page, arm_arr, tip, cfg, armpit=armpit)
    hand_pts, fore_pts = cut_arm(arm_arr, wrist, tip_used)

    # 2) ЭТАЛОННАЯ рука: база/плечо/предплечье. hole_d (= длина реза при arm_hole_from_cut) ->
    #    эталон масштабируется так, что база/нижнее ребро плеча = π·hole_d (окружность отверстия).
    pieces = build_arm(cfg, hole_d=hole_d)
    pred = next(p for p in pieces if p.get("name") == "predplechie")
    plec = next(p for p in pieces if p.get("name") == "plecho")
    baza = next(p for p in pieces if p.get("name") == "baza")
    fore_top = pred["edge_top"]                                      # ребро стыковки с кистью (= opening)
    fore_bot = pred["edge_bottom"]                                  # ребро стыковки с плечом (локоть)

    # 3) КИСТЬ, состыкованная с эталонным предплечьем: opening == predplechie.edge_top
    hand = build_hand(hand_pts, fore_pts, wrist, scale,
                      fore_top_mm=fore_top, fore_bot_mm=fore_bot)

    # 4) ЦВЕТ ЗОН: детект локтя -> цвет предплечья (кожа) и плеча (рукав) с РЕАЛЬНОЙ руки.
    #    predplechie = цвет предплечья; plecho и baza(полоска крепления) = цвет плеча.
    elbow_cut, fore_color, sh_color = _arm_zone_colors(page, arm_poly, arm_arr, wrist, tip_used, cfg, armpit=armpit, elbow=elbow)
    wsnap = cfg.get("arm_white_snap")                                # near-белый диапазон -> чистый белый (255,255,255)
    if wsnap is not None:
        def _snapw(c):
            return (255, 255, 255) if (c is not None and min(c) >= wsnap) else c
        fore_color = _snapw(fore_color); sh_color = _snapw(sh_color)
    if fore_color is None:                                           # зона пустая (короткое предплечье/
        fore_color = sh_color                                       # однородный рукав) -> цвет другой зоны
    if sh_color is None:
        sh_color = fore_color
    if fore_color is not None:
        pred["fill"] = fore_color
    if sh_color is not None:
        plec["fill"] = sh_color
        baza["fill"] = sh_color

    if cfg.get("arm_texture_bands"):                                 # полосы текстуры вдоль руки (как боковины)
        fore_bands, sh_bands = _arm_zone_bands(page, arm_poly, arm_arr, wrist, tip_used, cfg, armpit=armpit)
        if fore_bands:
            pred["bands"] = fore_bands
        if sh_bands:
            plec["bands"] = sh_bands

    hole_d = float(hole_d) if hole_d else cfg.get("arm_hole_diameter_mm", 250)
    hole_c = math.pi * hole_d
    diag = {
        "wrist_method": wmethod, "hole_d": hole_d,
        "base_bottom": baza["edge_bottom"], "base_top": baza["edge_top"],   # база: низ↔Ø250, верх↔плечо
        "sh_bottom": plec["edge_bottom"], "sh_top": plec["edge_top"],       # плечо: низ↔база, верх↔локоть
        "fore_bottom": pred["edge_bottom"], "fore_top": pred["edge_top"],   # предпл: низ↔плечо, верх↔кисть
        "elbow": elbow_cut, "fore_color": fore_color, "sh_color": sh_color,
    }
    return {"pieces": pieces, "hand": hand, "diag": diag}


def _elbow_color_s(page, arm_poly, wmid, u, L, cfg):
    """Локоть по ЦВЕТОВОМУ ПЕРЕХОДУ: в перпендикулярных срезах берём ДОМИНИРУЮЩИЙ цвет (по
    всей ширине руки); где доминанта резко флипает (рукав<->кожа = манжет), там локоть.
    Надёжнее осевой линии на согнутой руке. Возвращает axial s или None (однотонная)."""
    from side_color import front_raster
    from sides import front_drawings
    from collections import Counter
    img, bx0, by0, Z, mg = front_raster(page, front_drawings)
    H, W = img.shape[:2]
    wmid = np.asarray(wmid, float); u = np.asarray(u, float)

    def colat(p):
        px = int((p[0] - bx0 + mg) * Z); py = int((p[1] - by0 + mg) * Z)
        return tuple(int(v) for v in img[min(H - 1, max(0, py)), min(W - 1, max(0, px))])
    N = max(16, int(L / cfg.get("arm_band_step_pt", 6)))
    prof = []
    for k in range(N):
        s = L * (k + 0.5) / N; p = wmid + u * s
        ch = _perp_chord(arm_poly, p, u)
        if ch is None:
            prof.append(None); continue
        a = np.asarray(ch[0], float); b = np.asarray(ch[1], float)
        cols = [colat(a + (b - a) * t) for t in np.linspace(0.15, 0.85, 9)]  # по ширине среза
        cols = [c for c in cols if sum(c) >= 60]                     # без контура/тени
        prof.append(Counter(cols).most_common(1)[0][0] if cols else None)
    lo = cfg.get("elbow_search_lo_frac", 0.20) * L
    hi = cfg.get("elbow_search_hi_frac", 0.85) * L
    best = None; bestd = cfg.get("elbow_color_min_diff", 90)
    for k in range(1, N):
        if prof[k] is None or prof[k - 1] is None:
            continue
        s = L * k / N
        if not (lo <= s <= hi):
            continue
        diff = sum(abs(prof[k][j] - prof[k - 1][j]) for j in range(3))
        if diff > bestd:
            bestd = diff; best = s
    return best


def _elbow_edge_colors(page, arm_poly, wmid, u, L, elbow_s, cfg):
    """Цвета по обе стороны от локтя (перехода рукав<->кожа): доминирующий цвет растровых
    срезов по ширине руки в узких полосах НИЖЕ локтя (кожа/предплечье) и ВЫШЕ (рукав/плечо).
    Семплим РЯДОМ с локтем, поэтому не залезаем в корпус (у мужика с табличкой зона плеча
    у тела = красный логотип). Возвращает (fore_color|None, sh_color|None)."""
    from side_color import front_raster
    from sides import front_drawings
    from collections import Counter
    img, bx0, by0, Z, mg = front_raster(page, front_drawings)
    H, W = img.shape[:2]
    wmid = np.asarray(wmid, float); u = np.asarray(u, float)

    def colat(p):
        px = int((p[0] - bx0 + mg) * Z); py = int((p[1] - by0 + mg) * Z)
        return tuple(int(v) for v in img[min(H - 1, max(0, py)), min(W - 1, max(0, px))])

    def band_dom(s_lo, s_hi):
        cols = []
        n = max(3, int((s_hi - s_lo) / cfg.get("arm_band_step_pt", 6)))
        for k in range(n):
            s = s_lo + (s_hi - s_lo) * (k + 0.5) / n
            ch = _perp_chord(arm_poly, wmid + u * s, u)
            if ch is None:
                continue
            a = np.asarray(ch[0], float); b = np.asarray(ch[1], float)
            for t in np.linspace(0.25, 0.75, 7):                     # центр среза (без краёв/контура)
                c = colat(a + (b - a) * t)
                if sum(c) >= 60 and not (min(c) > 200):               # без контура и без белого фона
                    cols.append(c)
        return Counter(cols).most_common(1)[0][0] if cols else None

    reach = cfg.get("elbow_edge_reach_frac", 0.18) * L               # ширина полосы по каждую сторону
    fore = band_dom(max(0.0, elbow_s - reach), elbow_s)              # предплечье (кожа)
    sh = band_dom(elbow_s, min(L, elbow_s + reach))                  # плечо (рукав)
    return fore, sh


def _elbow_axial(arm_poly, arm_arr, wmid, u, L, cfg):
    """Локоть вдоль оси (s от запястья): сперва глубочайшая ВОГНУТОСТЬ контура в полосе; если
    её нет (плавный сгиб, напр. скафандр) — по ИЗГИБУ осевой линии (скелет). Возвращает
    (elbow_s, elbow_point|None). Если ничего — (0.5*L, None)."""
    lo = cfg.get("elbow_search_lo_frac", 0.20) * L
    hi = cfg.get("elbow_search_hi_frac", 0.85) * L
    min_d = cfg.get("elbow_min_depth_frac", 0.08) * L
    best = min_d; es = None; ep = None
    for p, d in _contour_notches(arm_arr):
        s = float((np.asarray(p, float) - wmid) @ u)
        if lo <= s <= hi and d > best:
            best = d; es = s; ep = np.asarray(p, float)
    if es is not None:
        return es, ep
    # фолбэк: изгиб осевой линии (скелет)
    try:
        from skimage.morphology import skeletonize
        mnx, mny = arm_arr[:, 0].min(), arm_arr[:, 1].min()
        W = int(arm_arr[:, 0].max() - mnx) + 10; H = int(arm_arr[:, 1].max() - mny) + 10
        mask = np.zeros((H, W), np.uint8)
        cv2.fillPoly(mask, [(arm_arr - [mnx, mny] + 5).astype(np.int32)], 1)
        sk = skeletonize(mask > 0); ys, xs = np.where(sk)
        P = np.c_[xs, ys].astype(float) + [mnx - 5, mny - 5]
        if len(P) >= 6:
            far_pt = np.asarray(wmid, float) + u * L                 # конец у плеча/подмышки
            tips = np.array([p for p in P if np.sum(np.hypot(P[:, 0] - p[0], P[:, 1] - p[1]) < 2.5) <= 2])
            cand = tips if len(tips) else P
            start = cand[int(np.argmin(np.hypot(cand[:, 0] - far_pt[0], cand[:, 1] - far_pt[1])))]  # старт с КОНЦА
            order, used, cur = [start], {tuple(start)}, start
            for _ in range(len(P)):
                rem = np.array([p for p in P if tuple(p) not in used])
                if not len(rem):
                    break
                d = np.hypot(rem[:, 0] - cur[0], rem[:, 1] - cur[1])
                if d.min() > 8:
                    break
                cur = rem[int(np.argmin(d))]; order.append(cur); used.add(tuple(cur))
            order = np.array(order)
            if len(order) >= 6:
                w = max(3, len(order) // 8); best_a = 0.0; bi = None
                for i in range(w, len(order) - w):
                    v1 = order[i] - order[i - w]; v2 = order[i + w] - order[i]
                    ang = math.acos(np.clip(v1 @ v2 / (np.hypot(*v1) * np.hypot(*v2) + 1e-9), -1, 1))
                    s = float((order[i] - wmid) @ u)
                    if ang > best_a and lo <= s <= hi:
                        best_a = ang; bi = i
                if bi is not None and best_a >= math.radians(cfg.get("elbow_bend_min_deg", 20)):
                    ep = order[bi]; return float((ep - wmid) @ u), ep
    except Exception:
        pass
    return 0.5 * L, None


def _arm_zone_bands(page, arm_poly, arm_arr, wrist, tip, cfg, armpit=None):
    """Полосы текстуры вдоль руки (как у боковин): вдоль оси запястье->подмышка берём
    доминирующий цвет в тонких срезах, сливаем похожие (hue) и схлопываем мелочь. Делим
    по локтю: предплечье [запястье..локоть] и плечо [локоть..подмышка]. Возвращает
    (fore_bands, sh_bands) — списки (f0,f1,(r,g,b)), f=0 у запястья/локтя соответственно."""
    from side_color import arm_zone_color, _simplify_bands, _merge_small_bands
    wmid = (np.asarray(wrist[0], float) + np.asarray(wrist[1], float)) / 2.0
    tip = np.asarray(tip, float)
    if armpit is not None:
        far = np.asarray(armpit, float)
    else:
        far = arm_arr[int(np.argmax(np.hypot(arm_arr[:, 0] - tip[0], arm_arr[:, 1] - tip[1])))]
    u = far - wmid; L = float(np.hypot(*u))
    if L < 1e-6:
        return None, None
    u = u / L
    esc = _elbow_color_s(page, arm_poly, wmid, u, L, cfg)            # локоть по цветовому переходу, иначе геометрия
    elbow_s = esc if esc is not None else _elbow_axial(arm_poly, arm_arr, wmid, u, L, cfg)[0]
    white = (255, 255, 255)
    base = arm_zone_color(page, arm_poly, wmid, u, 0.0, L) or white   # базовый цвет руки (скафандр/кожа)
    base = tuple(int(v) for v in base)
    N = max(16, int(L / cfg.get("arm_band_step_pt", 5)))              # МЕЛКИЕ срезы — ловим тонкую повязку
    raw = []
    for k in range(N):
        c = arm_zone_color(page, arm_poly, wmid, u, L * k / N, L * (k + 1) / N) or base
        raw.append((k / N, (k + 1) / N, tuple(int(v) for v in c)))
    simp = _simplify_bands(raw, base, cfg.get("hue_merge_deg", 25.0))
    # ЛОГИКА ПОВЯЗКИ: базовый фон сливаем, НЕ-базовые полосы (повязки) сохраняем даже тонкими
    tol = cfg.get("arm_band_base_tol", 48)
    is_base = lambda c: sum(abs(int(a) - int(b)) for a, b in zip(c, base)) <= tol
    bands = []
    for f0, f1, c in simp:
        cc = base if is_base(c) else tuple(c)
        if bands and tuple(bands[-1][2]) == cc:
            bands[-1] = (bands[-1][0], f1, cc)
        else:
            bands.append((f0, f1, cc))
    ef = max(0.05, min(0.95, elbow_s / L))

    def rescale(a, b):
        out = []
        for f0, f1, c in bands:
            x0, x1 = max(f0, a), min(f1, b)
            if x1 > x0:
                out.append(((x0 - a) / (b - a), (x1 - a) / (b - a), c))
        return out or None
    return rescale(0.0, ef), rescale(ef, 1.0)


def _elbow_top_crease(arm_poly, arm_arr, wmid, u, ep, other, L, cfg):
    """Верхний угол локтя = ВЫПУКЛЫЙ угол верхнего ребра, БЛИЖАЙШИЙ к центру сгиба `ep`
    (точке скелета), на стороне, ПРОТИВОПОЛОЖНОЙ нижней точке `other`. Если не нашли — None."""
    try:
        ccw = arm_poly.exterior.is_ccw
    except Exception:
        ccw = True
    N = len(arm_arr); w = max(2, N // 50)
    es = (ep[0] - wmid[0]) * u[0] + (ep[1] - wmid[1]) * u[1]
    R = cfg.get("elbow_corner_r_frac", 0.22) * L
    min_ang = math.radians(cfg.get("elbow_corner_min_deg", 40))
    side_o = math.copysign(1.0, u[0] * (other[1] - wmid[1]) - u[1] * (other[0] - wmid[0]))
    best = None; best_dist = 1e18
    for i in range(N):
        p = arm_arr[i]; s = (p[0] - wmid[0]) * u[0] + (p[1] - wmid[1]) * u[1]
        if abs(s - es) > R:
            continue
        side_p = math.copysign(1.0, u[0] * (p[1] - wmid[1]) - u[1] * (p[0] - wmid[0]))
        if side_p == side_o:
            continue
        a0 = arm_arr[(i - w) % N]; b0 = arm_arr[(i + w) % N]
        v1 = p - a0; v2 = b0 - p
        n1 = math.hypot(*v1); n2 = math.hypot(*v2)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        cr = v1[0] * v2[1] - v1[1] * v2[0]
        convex = (cr > 0) if ccw else (cr < 0)
        ang = math.acos(max(-1.0, min(1.0, (v1 @ v2) / (n1 * n2))))
        d = math.hypot(p[0] - ep[0], p[1] - ep[1])                  # ближайший к центру сгиба
        if convex and ang > min_ang and d < best_dist:
            best_dist = d; best = np.asarray(p, float)
    return best


def _elbow_by_color_edges(page, arm_poly, arm_arr, wrist, tip, cfg):
    """Локоть по ЦВЕТОВОЙ ГРАНИЦЕ рукав<->кожа на ДВУХ рёбрах руки: идём по верхнему и нижнему
    ребру от запястья к подмышке, пробуем цвет чуть внутрь; концы реза = точки, где цвет
    переключается кожа->рукав. Возвращает (pt_top, pt_bot) или None (однотонная рука)."""
    from side_color import front_raster
    from sides import front_drawings
    img, bx0, by0, Z, mg = front_raster(page, front_drawings)
    H, W = img.shape[:2]
    cen = np.array([arm_poly.centroid.x, arm_poly.centroid.y])

    def colat(p):
        px = int((p[0] - bx0 + mg) * Z); py = int((p[1] - by0 + mg) * Z)
        return img[min(H - 1, max(0, py)), min(W - 1, max(0, px))].astype(int)

    def inward_col(p):
        n = cen - p; nn = np.hypot(*n)
        return colat(p + n / nn * 8.0) if nn > 1e-6 else colat(p)

    wmid = (np.asarray(wrist[0], float) + np.asarray(wrist[1], float)) / 2.0
    _, rest = cut_arm(arm_arr, wrist, tip)                           # цепочка T..подмышка..B (без кисти)
    rest = np.asarray(rest, float)
    if len(rest) < 8:
        return None
    far_i = int(np.argmax(np.hypot(rest[:, 0] - wmid[0], rest[:, 1] - wmid[1])))

    def edge_transition(chain):                                     # chain: от запястья к подмышке
        if len(chain) < 4:
            return None
        cols = [inward_col(p) for p in chain]
        fore = np.median(cols[:max(2, len(cols) // 6)], axis=0)     # у запястья (кожа)
        sh = np.median(cols[-max(2, len(cols) // 6):], axis=0)      # у подмышки (рукав)
        if sum(abs(fore - sh)) < cfg.get("elbow_color_min_diff", 90):
            return None                                            # однотонное ребро
        for i in range(len(cols)):
            if sum(abs(cols[i] - sh)) < sum(abs(cols[i] - fore)):  # переключился на рукав
                return np.asarray(chain[i], float)
        return None

    top = edge_transition(rest[:far_i + 1])                         # ребро T->подмышка
    bot = edge_transition(rest[far_i:][::-1])                       # ребро B->подмышка
    if top is not None and bot is not None:
        return (tuple(top), tuple(bot))
    return None


def _arm_zone_colors(page, arm_poly, arm_arr, wrist, tip, cfg, armpit=None, elbow=None):
    """Локоть = самая глубокая вогнутость контура руки в осевой полосе между запястьем и
    подмышкой. Делит руку на предплечье (запястье..локоть) и плечо (локоть..подмышка);
    возвращает (elbow_chord, fore_color, sh_color). Цвета берутся из 2D по зонам.
    armpit — истинная подмышка из реза (a["f"]): задаёт КОНЕЦ оси руки, чтобы зона плеча не
    уходила в корпус (у «мужика с табличкой» полигон руки прихватывает туловище с логотипом)."""
    from side_color import arm_zone_color, arm_zone_color_raster
    _zcol = ((lambda *A: arm_zone_color_raster(*A, cfg)) if cfg.get("arm_zone_color_raster")  # по пикселям vs bbox
             else arm_zone_color)
    wmid = (np.asarray(wrist[0], float) + np.asarray(wrist[1], float)) / 2.0
    tip = np.asarray(tip, float)
    if armpit is not None:                                           # конец оси = настоящая подмышка
        far = np.asarray(armpit, float)
    else:
        far = arm_arr[int(np.argmax(np.hypot(arm_arr[:, 0] - tip[0], arm_arr[:, 1] - tip[1])))]
    u = far - wmid; L = float(np.hypot(*u))
    if L < 1e-6:
        return None, None, None
    u = u / L
    if elbow is not None:                                            # РУЧНОЙ срез локтя: концы привязать к контуру
        exc = arm_poly.exterior
        from shapely.geometry import Point as _Pt
        ea = np.asarray(exc.interpolate(exc.project(_Pt(*elbow[0]))).coords[0], float)
        eb = np.asarray(exc.interpolate(exc.project(_Pt(*elbow[1]))).coords[0], float)
        emid = (ea + eb) / 2.0
        elbow_s = float((emid - wmid) @ u); elbow_p = emid
        fore = _zcol(page, arm_poly, wmid, u, 0.0, elbow_s)
        sh = _zcol(page, arm_poly, wmid, u, elbow_s, L)
        return (tuple(ea), tuple(eb)), fore, sh
    esc = _elbow_color_s(page, arm_poly, wmid, u, L, cfg)            # 1) цветовой переход рукав<->кожа (манжет)
    emax = cfg.get("elbow_max_frac")                                 # цветовой переход у ПЛЕЧА (пад/корпус), не локоть:
    if esc is not None and emax is not None and esc > emax * L:      # слишком близко к подмышке -> это не локоть,
        esc = None                                                  # берём локоть по ИЗГИБУ (согнутая рука)
    if esc is not None:
        elbow_s, elbow_p = esc, (wmid + u * esc)
    else:
        elbow_s, elbow_p = _elbow_axial(arm_poly, arm_arr, wmid, u, L, cfg)  # 2) вогнутость/изгиб скелета
    fore = _zcol(page, arm_poly, wmid, u, 0.0, elbow_s)
    sh = _zcol(page, arm_poly, wmid, u, elbow_s, L)                 # до подмышки, не в корпус
    if esc is not None:                                             # цвет рукава берём ПРЯМО с перехода (манжет),
        cfore, csh = _elbow_edge_colors(page, arm_poly, wmid, u, L, esc, cfg)  # не из широкой зоны у корпуса
        if csh is not None:
            sh = csh
        if cfore is not None and fore is None:
            fore = cfore
    elbow_chord = None
    if elbow_p is not None:
        ch = _perp_chord(arm_poly, elbow_p, u)
        if ch is not None:
            A = np.asarray(ch[0], float); B = np.asarray(ch[1], float)
            top = _elbow_top_crease(arm_poly, arm_arr, wmid, u, elbow_p, B, L, cfg)
            if top is not None:
                elbow_chord = (tuple(top), tuple(B))
            else:
                top = _elbow_top_crease(arm_poly, arm_arr, wmid, u, elbow_p, A, L, cfg)
                elbow_chord = (tuple(top), tuple(A)) if top is not None else (tuple(A), tuple(B))
    return elbow_chord, fore, sh


def build_arm_measured(page, fig, cfg):
    """Рука-конусы ИЗ РЕАЛЬНОЙ формы рукава (как у космонавта: база+плечо+предплечье, без кисти).
    Отверстие Ø arm_hole_diameter_mm (эталон 2700) -> окружность плеча=базе. Локоть/запястье —
    по замеру рукава (скелет+distance transform). Возвращает (pieces, shoulder_xy_mm)."""
    from skimage.morphology import skeletonize
    from sides import get_arm_contour, contour_to_mm, smooth_front, PT2MM as _P
    from shapely.geometry import Point, box
    scale = fig.s * _P
    arm_pt, body_x = get_arm_contour(page)
    wr = detect_wrist(arm_pt.tolist(), body_x=body_x)
    _, fore_pt = cut_arm(arm_pt, wr["wrist"], wr["tip"])
    S = fore_pt * scale                                              # рукав (без кисти), мм
    body = smooth_front(contour_to_mm(fig), cfg)
    mnx, mny = S[:, 0].min(), S[:, 1].min()
    W = int(S[:, 0].max() - mnx) + 20; H = int(S[:, 1].max() - mny) + 20
    mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [(S - [mnx, mny] + 10).astype(np.int32)], 1)
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    sk = skeletonize(mask > 0); ys, xs = np.where(sk); P = np.c_[xs, ys].astype(float)
    tips = np.array([p for p in P if np.sum(np.hypot(P[:, 0] - p[0], P[:, 1] - p[1]) < 2.5) <= 2])
    start = tips[np.argmax(tips[:, 0])] if len(tips) else P[np.argmax(P[:, 0])]   # плечо (тело справа)
    order, used, cur = [start], {tuple(start)}, start
    for _ in range(len(P)):
        rem = np.array([p for p in P if tuple(p) not in used])
        if not len(rem):
            break
        d = np.hypot(rem[:, 0] - cur[0], rem[:, 1] - cur[1])
        if d.min() > 6:
            break
        cur = rem[np.argmin(d)]; order.append(cur); used.add(tuple(cur))
    order = np.array(order); cum = np.r_[0, np.cumsum(np.hypot(*np.diff(order, axis=0).T))]
    Ltot = cum[-1] if len(cum) > 1 else 100.0
    mid = order[len(order) // 2]
    el_d = 2 * dt[int(mid[1]), int(mid[0])]
    wr_d = float(np.hypot(*(np.array(wr["wrist"][0]) - np.array(wr["wrist"][1])))) * scale
    v1 = mid - order[0]; v2 = order[-1] - mid                       # угол изгиба в локте
    bend = math.acos(np.clip(v1 @ v2 / (np.hypot(*v1) * np.hypot(*v2) + 1e-9), -1, 1))
    T = bend / 2.0                                                  # наклон косого среза = пол-угла
    hole_d = cfg.get("arm_hole_diameter_mm", 250)                    # эталон 2700
    sh_c, el_c, wr_c, Lseg = np.pi * hole_d, np.pi * el_d, np.pi * wr_d, Ltot / 2
    specs = [("baza", box(0, 0, sh_c, cfg.get("arm_base_height_mm", 40))),
             ("plecho", _develop_frustum(sh_c, el_c, Lseg, tilt2=T)),       # верх(локоть) волнистый
             ("predplechie", _develop_frustum(el_c, wr_c, Lseg, tilt1=T))]  # низ(локоть) волнистый — совпадает
    pieces = []
    for nm, poly in specs:
        b = poly.bounds
        pieces.append({"name": nm, "poly": poly, "w": b[2] - b[0], "h": b[3] - b[1],
                       "edge_bottom": b[2] - b[0], "edge_top": b[2] - b[0]})
    # точка плеча на теле (для отверстия): ближайшая к кромке примыкания рукава
    d = np.array([body.exterior.distance(Point(p)) for p in S])
    att = S[d < max(d.min() * 2.2, np.percentile(d, 22))]
    from shapely.ops import nearest_points
    hp = nearest_points(body, Point(att.mean(0)))[0]
    return pieces, (hp.x, hp.y)


def add_spare_arm(layout, gap=150.0):
    """Запасная рука: копия всех деталей руки (кисть лево/боковина/право + база/плечо/
    предплечье), размещённая НИЖЕ оригинальной колонки. Пишется в layout['spare_arm']
    (список деталей с kind='hand'/'arm'); текстура кисти перестроится по новому poly (src тот же)."""
    hands = layout.get("hand_pieces", [])
    arms = layout.get("arm_pieces", [])
    base = hands + arms
    if not base:
        return
    miny = min(p["poly"].bounds[1] for p in base)
    gmax = layout["front"].bounds[3]                                 # глобальный низ всей раскладки
    for s in layout.get("strips", []):
        gmax = max(gmax, max(y for _, y in s["poly"]))
    for p in base:
        gmax = max(gmax, p["poly"].bounds[3])
    b = layout.get("base")
    if b:
        gmax = max(gmax, b["dnishe"].bounds[3], b["leg"][1] + b["leg"][3])
    if layout.get("back"):
        gmax = max(gmax, layout["back"]["poly"].bounds[3])
    dy = (gmax + gap) - miny                                         # запаска — в чистую полосу НИЖЕ всего
    spare = []
    for p in hands:
        spare.append({**p, "poly": affinity.translate(p["poly"], 0, dy),
                      "name": p["name"] + " (запас)", "kind": "hand"})
    for p in arms:
        spare.append({**p, "poly": affinity.translate(p["poly"], 0, dy),
                      "name": p.get("name", "") + " (запас)", "kind": "arm"})
    layout["spare_arm"] = spare
    set_maxy = max(p["poly"].bounds[3] for p in base)
    layout["sheet_h"] = max(layout["sheet_h"], set_maxy + dy + gap)


def render_hand(h, out_png, target_h=900, margin=40):
    """Превью детали кисти: перёд, зеркальный зад, боковина-прямоугольник + подписи."""
    from PIL import Image, ImageDraw
    polys = [("перёд", h["front"], (0, 150, 0)), ("зад (зеркало)", h["back"], (30, 80, 220)),
             ("боковина", h["gusset"], (200, 60, 0))]
    mnx = min(p.bounds[0] for _, p, _ in polys); mny = min(p.bounds[1] for _, p, _ in polys)
    mxx = max(p.bounds[2] for _, p, _ in polys); mxy = max(p.bounds[3] for _, p, _ in polys)
    sc = (target_h - 2 * margin) / (mxy - mny)
    W = int((mxx - mnx) * sc) + 2 * margin
    im = Image.new("RGB", (W, target_h), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    T = lambda x, y: (margin + (x - mnx) * sc, margin + (y - mny) * sc)
    for name, p, col in polys:
        dr.polygon([T(x, y) for x, y in p.exterior.coords], outline=col, width=3)
        cx, cy = p.centroid.x, p.centroid.y
        dr.text(T(cx, cy), name, fill=col)
    dr.text((margin, 8),
            f"KIST: rovn.rebro={h['straight']:.0f} krivaya H={h['H']:.0f} "
            f"verh.rebro predpl={h['fore_top']:.0f} shirina W={h['W']:.0f} | "
            f"otkrytyi krai 2*s+2*W={h['opening']:.0f}=={h['fore_top']:.0f}", fill=(0, 0, 0))
    im.save(out_png)


# ============ ВШИВНЫЕ ДЕТАЛИ (ложка и пр.): рез + лекало + отверстие ============

def cut_details(page, contour_pt, cfg, strict=True):
    """Вырезать вшивные детали из контура (PT) ручным резом (как рука). Читает
    manual_details=[{f, blade?:[[x,y],[x,y]], tip?, hole_dy?}]. Возвращает (body_pt, details).
    details[i]: {poly_pt, f, tip, attach_pt, attach_len_pt (длина линии реза, PT), hole_dy}."""
    man = cfg.get("manual_details")
    if not man:
        return contour_pt, []
    from shapely.geometry import Point as _Pt, LineString as _LS
    body_pt = contour_pt; cen0 = contour_pt.centroid; out = []
    for m in man:
        exc = body_pt.exterior
        snap = lambda p: np.asarray(exc.interpolate(exc.project(_Pt(*p))).coords[0], float)
        f = tuple(m["f"]); bl = m.get("blade"); a_pt = c_pt = None; blade = None
        if bl:
            a = snap(bl[0]); c = snap(bl[1]); a_pt, c_pt = a, c
            dv = c - a; nn = np.hypot(*dv)
            if nn > 1:
                dv = dv / nn; blade = (tuple(a - dv * 40), tuple(c + dv * 40))
        else:
            fs = snap(f); fx = float(fs[0]); yb = body_pt.bounds
            inter = exc.intersection(_LS([(fx, yb[1] - 5), (fx, yb[3] + 5)])); pts = []
            for gg in getattr(inter, "geoms", [inter]):
                if not gg.is_empty:
                    pts += [tuple(cc) for cc in np.atleast_2d(np.asarray(gg.coords))]
            ups = [p for p in pts if p[1] < fs[1] - 1]
            if ups:
                up = min(ups, key=lambda p: fs[1] - p[1])
                a_pt = np.array([fx, fs[1]]); c_pt = np.array([fx, up[1]])
                blade = ((fx, float(fs[1]) + 40), (fx, float(up[1]) - 40))
        if blade is None:
            if strict:
                raise RuntimeError(f"рез детали не удался (f={f})")
            continue
        tip = m.get("tip")
        if not tip:
            u = np.array([f[0] - cen0.x, f[1] - cen0.y]); u = u / (np.linalg.norm(u) or 1)
            ex = np.asarray(exc.coords, float); tip = tuple(ex[int(((ex - np.array(f)) @ u).argmax())])
        det, body2 = cut_across_arm(body_pt, f, tip, cfg, blade=blade)
        if det is None:
            if strict:
                raise RuntimeError(f"рез детали не удался (f={f})")
            continue
        body_pt = body2
        attach_len = float(np.hypot(*(c_pt - a_pt))) if (a_pt is not None and c_pt is not None) else 0.0
        wseg = m.get("width")                                         # ширина = длина ОТРЕЗКА из 2 точек (PT)
        width_pt = float(np.hypot(wseg[1][0] - wseg[0][0], wseg[1][1] - wseg[0][1])) if (wseg and len(wseg) == 2) else None
        out.append({"poly_pt": det, "f": list(f), "tip": list(tip),
                    "attach_pt": a_pt.tolist() if a_pt is not None else list(f),
                    "attach_len_pt": attach_len, "width_pt": width_pt, "hole_dy": m.get("hole_dy", 0.0)})
    return body_pt, out


def build_detail(det_poly_mm, attach_len_mm, gap_mm=40.0, width_mm=None):
    """Лекало вшивной детали: перёд + зеркальный зад + гассет-полоска.
    Ширина гассета W = width_mm (длина ОТРЕЗКА-ширины от пользователя) или меньшая сторона bbox.
    Длина гассета H = свободный периметр (периметр − линия реза).
    opening (вшивается в отверстие тела) = 2·рез + 2·W (сумма коротких рёбер)."""
    front = affinity.translate(det_poly_mm, -det_poly_mm.bounds[0], -det_poly_mm.bounds[1])
    mnx, mny, mxx, mxy = front.bounds; fw, fh = mxx - mnx, mxy - mny
    W = max(float(width_mm) if width_mm else min(fw, fh), 1.0)          # ширина детали = отрезок или меньшая сторона
    H = max(front.exterior.length - attach_len_mm, 1.0)                 # свободный периметр
    back = affinity.scale(front, xfact=-1.0, origin=(fw / 2.0, fh / 2.0))
    gusset = box(0, 0, W, H)
    back = affinity.translate(back, fw + gap_mm, 0)
    gusset = affinity.translate(gusset, 2 * fw + 2 * gap_mm, 0)
    opening = 2.0 * attach_len_mm + 2.0 * W
    return {"front": front, "back": back, "gusset": gusset, "W": W, "H": H,
            "fw": fw, "fh": fh, "opening": opening}


def _detail_main_color(page, poly_pt):
    """Доминирующий НАСЫЩЕННЫЙ цвет детали (не обводка/не белый фон) — для гассета."""
    b = poly_pt.bounds
    try:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(*b))
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        arr = arr.reshape(-1, 3).astype(int)
    except Exception:
        return (200, 200, 200)
    mx = arr.max(1); mn = arr.min(1); sat = mx - mn
    keep = arr[(mx < 240) & (mn > 15) & ((sat > 30) | ((mx < 210) & (mx > 60)))]   # не белый/чёрный
    if len(keep) < 10:
        keep = arr[(mx < 245) & (mn > 10)]
    if len(keep) < 1:
        return (180, 180, 180)
    q = (keep // 24) * 24 + 12                                          # мода по грубым бинам
    uniq, cnt = np.unique(q, axis=0, return_counts=True)
    c = uniq[int(cnt.argmax())]
    return (int(c[0]), int(c[1]), int(c[2]))


def place_detail(layout, det, src_pts, color, gap=120.0):
    """Положить детали вшивной детали (перёд|зад|боковина) РЯДОМ ВНИЗУ раскладки + отметить.
    src_pts — контур перёда в PT макета (для текстуры); color — основной цвет детали (гассет)."""
    norm = lambda p: affinity.translate(p, -p.bounds[0], -p.bounds[1])
    y = layout["sheet_h"] + gap; x = gap; placed = []                  # НИЖЕ всей раскладки, слева
    row = [("деталь-перёд", norm(det["front"]), src_pts), ("деталь-зад", norm(det["back"]), None),
           ("деталь-боковина", norm(det["gusset"]), None)]
    for name, p, src in row:
        pw, ph = p.bounds[2], p.bounds[3]
        placed.append({"name": name, "poly": affinity.translate(p, x, y),
                       "w": pw, "h": ph, "src": src, "fill": color})
        x += pw + gap
    layout.setdefault("detail_pieces", []).extend(placed)
    layout["sheet_w"] = max(layout["sheet_w"], x)
    layout["sheet_h"] = max(layout["sheet_h"], max(p["poly"].bounds[3] for p in placed) + gap)
    return placed


# ============ ЗАКРУГЛЕНИЕ ПЛЕЧА (купол у шеи) при верхнем резе руки — ТЗ_shoulder_round_2d ============

def round_shoulder(body_pt, A, B, cfg):
    """Заменить ПРЯМУЮ кромку реза плеча A–B на выпуклую дугу-КУПОЛ (полукруг h=d/2),
    асимметрично смещённую к верхнему концу B (шее). A/B — концы хорды реза (PT, на контуре).
    Возвращает (new_body_pt, dart_arc_pt) — dart_arc_pt: вогнутая пунктир-вытачка (PT) для отрисовки."""
    if not cfg.get("shoulder_round"):
        return body_pt, None
    from shapely.geometry import Polygon, Point
    ext = np.asarray(body_pt.exterior.coords, float)[:-1]
    n = len(ext)
    if n < 4:
        return body_pt, None
    A = np.asarray(A, float); B = np.asarray(B, float)
    ia = int(np.argmin(np.hypot(ext[:, 0] - A[0], ext[:, 1] - A[1])))
    ib = int(np.argmin(np.hypot(ext[:, 0] - B[0], ext[:, 1] - B[1])))
    if ia == ib:
        return body_pt, None

    def arc_idx(i, j):
        out = []; k = i
        while True:
            out.append(k)
            if k == j:
                break
            k = (k + 1) % n
            if len(out) > n:
                break
        return out
    f1 = arc_idx(ia, ib); f2 = arc_idx(ib, ia)
    cut = f1 if len(f1) <= len(f2) else f2            # кромка реза = КОРОТКАЯ дуга A↔B
    P0 = ext[cut[0]]; P1 = ext[cut[-1]]
    d = float(np.hypot(*(P1 - P0)))
    if d < 2:
        return body_pt, None
    u = (P1 - P0) / d; nrm = np.array([-u[1], u[0]])
    cen = body_pt.centroid
    if np.dot(nrm, (P0 + P1) / 2 - np.array([cen.x, cen.y])) < 0:   # нормаль НАРУЖУ от тела (купол вверх)
        nrm = -nrm
    # B (шея) = верхний конец (меньший y). Профиль смещён к B.
    toB = 1 if P1[1] < P0[1] else 0                    # 1 -> пик у t=1 (P1=B), иначе у t=0
    h = float(cfg.get("shoulder_dome_frac", 0.5)) * d
    N = max(12, int(d / 3))
    dome = []
    for t in np.linspace(0, 1, N):
        base = P0 + (P1 - P0) * t
        w = (0.35 + 0.65 * t) if toB else (0.35 + 0.65 * (1 - t))   # вес к шее (у шеи выше)
        off = h * math.sin(math.pi * t) * w
        dome.append(base + nrm * off)
    # новый контур: длинная дуга (P1..P0) + купол (P0..P1)
    long_i = arc_idx(cut[-1], cut[0])
    poly_pts = [tuple(ext[i]) for i in long_i] + [tuple(p) for p in dome[1:]]
    new = Polygon(poly_pts).buffer(0)
    if new.geom_type == "MultiPolygon":
        new = max(new.geoms, key=lambda p: p.area)
    # пунктир-вытачка: вогнутая дуга ВНУТРЬ (глубина shoulder_seam_frac·d), тот же асимм. профиль
    sf = float(cfg.get("shoulder_seam_frac", 0.35))
    dart = None
    if sf > 0:
        dart = []
        for t in np.linspace(0, 1, N):
            base = P0 + (P1 - P0) * t
            w = (0.35 + 0.65 * t) if toB else (0.35 + 0.65 * (1 - t))
            off = sf * d * math.sin(math.pi * t) * w
            dart.append(tuple(base - nrm * off))       # внутрь тела (минус нормаль)
    return Polygon(new.exterior), dart
