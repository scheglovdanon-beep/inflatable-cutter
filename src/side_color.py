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


def _merge_small_bands(bands, min_frac):
    """Схлопнуть скопления мелких зон в ОДИН основной цвет.
    1) прогон из >=2 подряд идущих коротких полос (каждая < min_frac по доле длины)
       заменяется одной полосой доминирующего по суммарной длине цвета;
    2) оставшиеся одиночные короткие полосы поглощаются более длинным соседом;
    3) соседние полосы одного цвета сливаются.
    Крупные однотонные зоны (вода, пояс) не трогаются."""
    if min_frac <= 0 or len(bands) <= 1:
        return bands
    small = lambda b: (b[1] - b[0]) < min_frac
    out, i = [], 0
    while i < len(bands):                                             # 1) прогоны мелких -> доминанта
        j = i
        while j < len(bands) and small(bands[j]):
            j += 1
        if j - i >= 2:
            cnt = {}
            for f0, f1, c in bands[i:j]:
                cnt[tuple(c)] = cnt.get(tuple(c), 0.0) + (f1 - f0)
            out.append([bands[i][0], bands[j - 1][1], max(cnt, key=cnt.get)])
            i = j
        else:
            out.append(list(bands[i])); i += 1
    changed = True
    while changed and len(out) > 1:                                   # 2) одиночные мелкие -> в длинного соседа
        changed = False
        for k in range(len(out)):
            if small(out[k]):
                neigh = [n for n in (out[k - 1] if k > 0 else None,
                                     out[k + 1] if k < len(out) - 1 else None) if n]
                cand = max(neigh, key=lambda n: n[1] - n[0])
                cand[0] = min(cand[0], out[k][0]); cand[1] = max(cand[1], out[k][1])
                out.pop(k); changed = True; break
    merged = []                                                       # 3) слить соседние одинаковые
    for b in out:
        if merged and tuple(merged[-1][2]) == tuple(b[2]):
            merged[-1][1] = b[1]
        else:
            merged.append(list(b))
    return [(g[0], g[1], tuple(g[2])) for g in merged]


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


def front_raster(page, front_drawings_fn, zoom=2.0, mg=6):
    """Реконструкция переда из векторных заливок в растр (pt-координаты).
    Возвращает (arr, bx0, by0, Z, mg). zoom=2 достаточно для сэмпла доминирующих цветов
    (при zoom=4 растр в 4× больше и медленнее). Кэшируется на page."""
    cache = getattr(page, "_raster_cache", None)
    if cache is not None and cache[0] == (zoom, mg):
        return cache[1]
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
    out = (np.asarray(img), bx0, by0, Z, mg)
    page._raster_cache = ((zoom, mg), out)
    return out


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


def hand_fill_color(page, hand_pt):
    """Основной цвет КИСТИ по 2D: доминирующая (по площади bbox) заливка в габарите кисти.
    Near-чёрные заливки (контур/силуэт кисти) исключаем — это обводка, а не «цвет кисти».
    Бельё НЕ исключаем — для перчатки основной цвет белый. Возвращает (r,g,b).
    Используется для полоски-боковины кисти (она одного цвета с кистью)."""
    from collections import defaultdict
    A = np.asarray(hand_pt, float)
    bx0, by0, bx1, by1 = A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max()
    inbb = lambda r: r.x0 >= bx0 - 2 and r.x1 <= bx1 + 2 and r.y0 >= by0 - 2 and r.y1 <= by1 + 2
    areas = defaultdict(float)
    for dd in page.get_drawings():
        f = dd.get("fill")
        if f is None or not inbb(dd["rect"]):
            continue
        c = tuple(int(255 * v) for v in f)
        if sum(c) < 60:                                              # near-чёрный = контур/силуэт, не цвет кисти
            continue
        r = dd["rect"]; areas[c] += r.width * r.height
    return max(areas, key=areas.get) if areas else (245, 245, 245)


def arm_zone_color(page, arm_poly, origin, u, s0, s1):
    """Доминирующая заливка 2D ВНУТРИ руки в осевой полосе [s0,s1): s = проекция центра
    заливки на ось u от origin (origin=середина запястья, u -> к подмышке). Делит руку на
    предплечье [0..локоть] и плечо [локоть..подмышка]. near-чёрный контур исключаем, бельё
    (белый рукав/перчатка) оставляем. Возвращает (r,g,b) или None."""
    from collections import defaultdict
    from shapely.geometry import Point
    from shapely.prepared import prep
    pp = prep(arm_poly.buffer(1.0)); o = np.asarray(origin, float); uu = np.asarray(u, float)
    areas = defaultdict(float)
    for dd in page.get_drawings():
        f = dd.get("fill")
        if f is None:
            continue
        r = dd["rect"]; cx, cy = (r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0
        s = (np.array([cx, cy]) - o) @ uu
        if not (s0 <= s < s1) or not pp.contains(Point(cx, cy)):
            continue
        c = tuple(int(255 * v) for v in f)
        if sum(c) < 60:                                              # near-чёрный = контур, не цвет зоны
            continue
        areas[c] += r.width * r.height
    return max(areas, key=areas.get) if areas else None


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
        bands = _simplify_bands(raw, fallback, cfg.get("hue_merge_deg", 25.0))        # 1 цвет на акцент
        s["bands"] = _merge_small_bands(bands, cfg.get("band_merge_small_frac", 0.0)) # скопления мелких -> 1 цвет


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
