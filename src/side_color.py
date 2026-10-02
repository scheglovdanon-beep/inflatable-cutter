"""
Раскраска боковин под стыковку с передом (см. TZ_side_coloring.md).
Цвет полосы в точке = цвет макета на кромке переда, к которой она пришивается.
Чёрная декоративная обводка перешагивается (берём заливку за ней) -> основная боковина белая.
"""
import colorsys
import numpy as np
from shapely.geometry import Point

PT2MM = 25.4 / 72.0


def _hue(c):
    return colorsys.rgb_to_hsv(*[v / 255.0 for v in c])[0] * 360.0


def _simplify_bands(bands, base, hue_merge_deg=25.0):
    """Упростить оттенки: соседние зоны одного акцента (близкий оттенок) слить и
    покрасить ДОМИНИРУЮЩИМ цветом (которого больше по длине). Белый — своя группа."""
    groups = []                                                      # [f0, f1, {color: length}]
    for f0, f1, c in bands:
        white = (tuple(c) == tuple(base))
        same = False
        if groups:
            gf0, gf1, cnt = groups[-1]
            dom = max(cnt, key=cnt.get)
            gwhite = (tuple(dom) == tuple(base))
            if white and gwhite:
                same = True
            elif not white and not gwhite:
                dh = abs(_hue(c) - _hue(dom)); dh = min(dh, 360 - dh)
                same = dh <= hue_merge_deg
        if same:
            groups[-1][1] = f1
            groups[-1][2][tuple(c)] = groups[-1][2].get(tuple(c), 0.0) + (f1 - f0)
        else:
            groups.append([f0, f1, {tuple(c): f1 - f0}])
    return [(g0, g1, max(cnt, key=cnt.get)) for g0, g1, cnt in groups]


def _accentize(c, base, sat_min, val_min):
    """Оставить только насыщенные акценты (пояс/рюкзак/лямки/полоса на голове);
    белый/серый/чёрный (костюм, тени, обводка) -> base (белый)."""
    r, g, b = [v / 255.0 for v in c]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return tuple(int(x) for x in c) if (s >= sat_min and v >= val_min) else base


def _flat(dd, nn=8):
    subs, cur = [], []
    for it in dd["items"]:
        k = it[0]
        if k == "l":
            if not cur:
                cur = [(it[1].x, it[1].y)]
            cur.append((it[2].x, it[2].y))
        elif k == "c":
            p0, p1, p2, p3 = it[1], it[2], it[3], it[4]
            if not cur:
                cur = [(p0.x, p0.y)]
            for t in np.linspace(0, 1, nn)[1:]:
                cur.append(((1 - t) ** 3 * p0.x + 3 * (1 - t) ** 2 * t * p1.x + 3 * (1 - t) * t * t * p2.x + t ** 3 * p3.x,
                            (1 - t) ** 3 * p0.y + 3 * (1 - t) ** 2 * t * p1.y + 3 * (1 - t) * t * t * p2.y + t ** 3 * p3.y))
        elif k == "re":
            r = it[1]; subs.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)])
    if cur:
        subs.append(cur)
    return subs


def front_raster(page, front_drawings_fn, zoom=4.0, mg=6):
    """Реконструкция переда из векторных заливок в растр (pt-координаты).
    Возвращает (arr, bx0, by0, Z, mg)."""
    from PIL import Image, ImageDraw
    fr, _ = front_drawings_fn(page)
    xs, ys = [], []
    for d in fr:
        r = d["rect"]; xs += [r.x0, r.x1]; ys += [r.y0, r.y1]
    bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
    Z = zoom
    W, H = int((bx1 - bx0 + 2 * mg) * Z), int((by1 - by0 + 2 * mg) * Z)
    img = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(img)
    col = lambda c: tuple(int(255 * v) for v in c) if c else None
    Tr = lambda x, y: ((x - bx0 + mg) * Z, (y - by0 + mg) * Z)
    for dd in fr:
        f = col(dd.get("fill")); s = col(dd.get("color"))
        for sub in _flat(dd):
            if len(sub) < 2:
                continue
            pts = [Tr(x, y) for x, y in sub]
            if f is not None and len(pts) >= 3:
                dr.polygon(pts, fill=f)
            if s is not None:
                dr.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=s, width=max(1, int(dd.get("width", 0.5) * Z)))
    return np.asarray(img), bx0, by0, Z, mg


def arm_fill_color(page, arm_pt):
    """Цвет РУКИ по 2D: доминирующая НЕ-светлая заливка рукава (перчатка-белое исключается).
    Для рукавов тёмного цвета -> чёрный. Возвращает (r,g,b)."""
    from collections import defaultdict
    A = np.asarray(arm_pt, float)
    bx0, by0, bx1, by1 = A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max()
    inbb = lambda r: r.x0 >= bx0 - 2 and r.x1 <= bx1 + 2 and r.y0 >= by0 - 2 and r.y1 <= by1 + 2
    areas = defaultdict(float)
    for dd in page.get_drawings():
        f = dd.get("fill")
        if f is None or not inbb(dd["rect"]):
            continue
        c = tuple(int(255 * v) for v in f)
        if sum(c) > 620:                                             # пропустить белое (перчатка)
            continue
        r = dd["rect"]; areas[c] += r.width * r.height
    return max(areas, key=areas.get) if areas else (0, 0, 0)


def base_fill_color(page, front_drawings_fn):
    """Цвет ДНИЩА/ножки по 2D: доминирующий цвет низа фигуры (там обычно чёрная база/тень)."""
    arr, bx0, by0, Z, mg = front_raster(page, front_drawings_fn)
    H, W = arr.shape[:2]
    band = arr[int(H * 0.93):H, int(W * 0.3):int(W * 0.7)].reshape(-1, 3)
    band = band[band.sum(1) < 740]                                   # не белый фон
    if not len(band):
        return (0, 0, 0)
    from collections import Counter
    return tuple(int(x) for x in Counter(map(tuple, band)).most_common(1)[0][0])


def _edges_top_down(poly, has_waist):
    """Левая и правая кромки полосы, обе сверху вниз."""
    if has_waist:
        half = len(poly) // 2
        left = poly[:half]
        right = poly[half:][::-1]
    else:
        left = [poly[0], poly[3]]
        right = [poly[1], poly[2]]
    return left, right


def _interp(edge, f):
    """Точка на ломаной edge по доле длины f∈[0,1]."""
    p = np.asarray(edge, float)
    seg = np.hypot(*np.diff(p, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    total = cum[-1] if cum[-1] > 1e-9 else 1.0
    target = f * total
    k = int(np.searchsorted(cum, target)) - 1
    k = min(max(k, 0), len(p) - 2)
    t = (target - cum[k]) / max(cum[k + 1] - cum[k], 1e-9)
    return tuple(p[k] + t * (p[k + 1] - p[k]))


def color_sides(layout, g, contour_mm, fig, page, front_drawings_fn, cfg):
    """Залить каждую полосу цветными зонами = цвет макета на её дуге переда.
    Пишет в каждую полосу layout['strips'] список s['bands'] = [(f0,f1,(r,g,b))]."""
    arr, bx0, by0, Z, mg = front_raster(page, front_drawings_fn)
    H, W = arr.shape[:2]
    f_mm = fig.s * PT2MM                                              # 1 pt = f_mm мм
    dark_thr = cfg.get("outline_dark_thr", 170)
    skip_max = cfg.get("outline_skip_max_mm", 40.0)
    stab = cfg.get("color_stabilize_mm", 1.5)
    step_mm = 0.7
    fb = cfg.get("fallback_color", "#FFFFFF").lstrip("#")
    fallback = tuple(int(fb[i:i + 2], 16) for i in (0, 2, 4))
    line = g["gusset_line"]                                           # дуга контура (мм)
    glen = g["gusset_len"]

    def px_mm(x_mm, y_mm):                                            # цвет растра в точке (мм)
        x_pt, y_pt = x_mm / f_mm, y_mm / f_mm
        px, py = int((x_pt - bx0 + mg) * Z), int((y_pt - by0 + mg) * Z)
        return arr[min(H - 1, max(0, py)), min(W - 1, max(0, px))]

    is_dark = lambda c: int(c[0]) + int(c[1]) + int(c[2]) < dark_thr

    def sample(arc):
        p = np.array(line.interpolate(arc).coords[0])
        e = max(min(arc + 1.0, glen), 0.0); s0 = max(min(arc - 1.0, glen), 0.0)
        a = np.array(line.interpolate(s0).coords[0]); b = np.array(line.interpolate(e).coords[0])
        t = b - a; n = np.array([-t[1], t[0]]); nn = np.hypot(*n)
        if nn < 1e-9:
            return fallback
        n = n / nn
        if not contour_mm.contains(Point(p + n * 2.0)):             # внутрь силуэта
            n = -n
        d, last = 1.0, None
        while d <= skip_max:
            c = px_mm(*(p + n * d))
            if not is_dark(c):
                c2 = px_mm(*(p + n * (d + stab)))
                if not is_dark(c2):
                    return tuple(int(v) for v in c2), True          # нашли заливку
            last = c; d += step_mm
        return (tuple(int(v) for v in last) if last is not None else fallback), False  # провал (всё тёмное)

    sat_min = cfg.get("accent_sat_min", 0.35)                        # только насыщенные акценты
    val_min = cfg.get("accent_val_min", 0.35)
    for s in layout["strips"]:
        a0, a1 = s["arc0"], s["arc1"]
        flip = s["yn0"] < s["yn1"]
        a_top, a_bot = (a1, a0) if flip else (a0, a1)                 # верх полосы -> к макушке
        L = abs(a1 - a0)
        M = int(min(140, max(3, L / cfg.get("color_sample_step_mm", 6))))
        fs = np.linspace(0, 1, M + 1)
        sm = [sample(a_top + (a_bot - a_top) * ((fs[k] + fs[k + 1]) / 2)) for k in range(M)]
        raw_c = [c for c, _ in sm]; ok = [o for _, o in sm]
        for k in range(M):                                           # провалы -> ближайший валидный сосед
            if not ok[k]:
                nb = next((raw_c[j] for d in range(1, M)
                           for j in (k - d, k + d) if 0 <= j < M and ok[j]), None)
                if nb is not None:
                    raw_c[k] = nb
        cols = [_accentize(c, fallback, sat_min, val_min) for c in raw_c]
        raw = [(float(fs[k]), float(fs[k + 1]), cols[k]) for k in range(M)]
        s["bands"] = _simplify_bands(raw, fallback, cfg.get("hue_merge_deg", 25.0))  # 1 цвет на акцент


def strip_band_quads(s):
    """Для полосы с s['bands'] вернуть [(quad_pts, color)] в координатах раскладки."""
    poly = s["poly"]
    left, right = _edges_top_down(poly, s.get("waist") is not None)
    out = []
    for f0, f1, c in s.get("bands", []):
        l0, r0 = _interp(left, f0), _interp(right, f0)
        l1, r1 = _interp(left, f1), _interp(right, f1)
        out.append(([l0, r0, r1, l1], c))
    return out
