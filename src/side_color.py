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


def _simplify_bands(bands, base, hue_merge_deg=25.0, val_merge_max=None):
    """Упростить оттенки: соседние зоны одного акцента (близкий оттенок) слить и
    покрасить ДОМИНИРУЮЩИМ цветом (которого больше по длине). Белый — своя группа.
    val_merge_max: если задан — НЕ сливать полосы с разницей яркости (max-канал) больше порога
    (тёмная сумка не сольётся в светлую кожу при близком тоне)."""
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
                if same and val_merge_max is not None:               # разная яркость -> не сливать
                    if abs(int(max(c)) - int(max(dom))) > val_merge_max:
                        same = False
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


def _drop_dark_bands(bands, dark_val):
    """C: тёмные полосы (max-канал < dark_val) поглотить в более крупного НЕ-тёмного соседа.
    Если не-тёмных соседей нет — оставить (реально тёмная деталь)."""
    if len(bands) <= 1:
        return bands
    out = [list(b) for b in bands]
    dark = lambda b: max(b[2]) < dark_val
    changed = True
    while changed and len(out) > 1:
        changed = False
        for k in range(len(out)):
            if dark(out[k]):
                neigh = [n for n in (out[k - 1] if k > 0 else None,
                                     out[k + 1] if k < len(out) - 1 else None)
                         if n and not dark(n)]
                if neigh:
                    cand = max(neigh, key=lambda n: n[1] - n[0])
                    cand[0] = min(cand[0], out[k][0]); cand[1] = max(cand[1], out[k][1])
                    out.pop(k); changed = True; break
    merged = []                                                      # слить соседние одинаковые
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


def posterize_arr(arr, ncolors=64):
    """Плоская упрощённая версия растра: квантование -> группы по тону+полосе насыщенности ->
    доминирующий по площади цвет группы (тени сливаются в основной); near-чёрный=контур, S<0.1=белый.
    Один основной цвет на участок. Возвращает uint8 HxWx3."""
    from PIL import Image
    im = Image.fromarray(arr).convert("RGB")
    q = im.quantize(colors=ncolors, method=Image.MEDIANCUT)
    pal = np.array(q.getpalette()[:ncolors * 3]).reshape(-1, 3)
    lbl = np.array(q); counts = np.bincount(lbl.ravel(), minlength=ncolors)

    def cls(c):
        h, s, v = colorsys.rgb_to_hsv(*[x / 255.0 for x in c])
        if v < 0.22:
            return "K"
        if s < 0.06:                                                # только ЧИСТО белый/серый; светлый беж -> цвет
            return "W" if v > 0.90 else "G"
        return f"H{int(h * 360) // 18}_{0 if s < 0.38 else 1}"
    groups = {}
    for i, c in enumerate(pal):
        groups.setdefault(cls(c), []).append(i)
    rep = {}
    for g, idxs in groups.items():
        rep[g] = (15, 15, 15) if g == "K" else (255, 255, 255) if g == "W" else \
            tuple(int(x) for x in pal[max(idxs, key=lambda i: counts[i])])
    newpal = np.array([rep[cls(c)] for c in pal], dtype=np.uint8)
    return newpal[lbl].astype(np.uint8)


def front_raster(page, front_drawings_fn, zoom=None, mg=6):
    """Реконструкция переда из векторных заливок в растр (pt-координаты).
    Возвращает (arr, bx0, by0, Z, mg). zoom=2 достаточно для сэмпла доминирующих цветов
    (при zoom=4 растр в 4× больше и медленнее). Кэшируется на page.
    zoom=None -> берётся raster_zoom из cfg (деф. 2.0) — меньше растр = меньше памяти."""
    if zoom is None:
        zoom = (getattr(page, "_cfg", None) or {}).get("raster_zoom", 2.0) or 2.0
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
    raw = np.asarray(img)
    if getattr(page, "_flat_colors", False):                        # боковины/днище из плоского варианта
        raw = posterize_arr(raw)
    out = (raw, bx0, by0, Z, mg)
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


def arm_zone_color_raster(page, arm_poly, origin, u, s0, s1, cfg):
    """Доминирующий цвет зоны по РЕАЛЬНЫМ ПИКСЕЛЯМ (не по bbox заливок — тонкая тень с большим
    bbox ложно «выигрывала»). Растрим область руки, маска = внутри arm_poly ∧ осевая s∈[s0,s1),
    исключаем near-чёрный контур и фон страницы; берём самый частый цвет (квант. до /8)."""
    import numpy as np, cv2, pymupdf
    o = np.asarray(origin, float); uu = np.asarray(u, float)
    b = arm_poly.bounds; Z = float(cfg.get("arm_raster_z", 5.0))
    clip = pymupdf.Rect(b[0]-2, b[1]-2, b[2]+2, b[3]+2)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(Z, Z), clip=clip)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    H, W = img.shape[:2]; ox, oy = clip.x0, clip.y0
    arm = np.asarray(arm_poly.exterior.coords[:-1], float)
    pm = np.zeros((H, W), np.uint8)
    cv2.fillPoly(pm, [((arm-[ox, oy])*Z).astype(np.int32)], 1)
    pm = cv2.erode(pm, np.ones((3, 3), np.uint8))                      # отойти от края (антиалиас/фон)
    gx, gy = np.meshgrid(np.arange(W), np.arange(H))
    wx = gx/Z+ox; wy = gy/Z+oy
    s = (wx-o[0])*uu[0] + (wy-o[1])*uu[1]
    m = (pm > 0) & (s >= s0) & (s < s1)
    px = img[m]
    if not len(px):
        return None
    ss = px.astype(int).sum(1)
    px = px[ss >= 40]                                                 # режем только чистый чёрный контур (тёмно-синий
    if not len(px):                                                  # рукав ~(6,14,45) оставляем — иначе зона ложно белеет)
        px = img[m]
    q = (px // 8) * 8                                                  # квант -> устойчивая мода
    cols, cnts = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
    return tuple(int(v) for v in cols[int(np.argmax(cnts))])


def base_fill_color(page, front_drawings_fn, contour_pt=None):
    """Цвет ДНИЩА (и перехода+ножки): ДОМИНИРУЮЩИЙ реальный цвет НИЗА ПЕРЕДА — по пикселям
    ВНУТРИ контура фигуры (сапоги/база/тень — что реально внизу), НЕ фон/земля под фигурой.
    Мода по нижней полосе фигуры, квант /8, без near-чёрного тонкого контура."""
    import numpy as np, cv2
    arr, bx0, by0, Z, mg = front_raster(page, front_drawings_fn)
    H, W = arr.shape[:2]
    if contour_pt is not None:                                       # маска силуэта в координатах растра
        pts = np.array([[(x - bx0 + mg) * Z, (y - by0 + mg) * Z] for x, y in contour_pt.exterior.coords], np.int32)
        mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [pts], 1)
    else:
        mask = (arr.astype(int).sum(2) < 740).astype(np.uint8)       # запас: не-белое
    ys = np.where(mask.any(1))[0]
    if not len(ys):
        return (0, 0, 0)
    y0b, y1b = ys.min(), ys.max()
    y_lo = int(y1b - 0.12 * (y1b - y0b))                             # нижние 12% ВЫСОТЫ фигуры
    reg = arr[y_lo:y1b + 1].reshape(-1, 3).astype(int)
    m = mask[y_lo:y1b + 1].reshape(-1).astype(bool)
    px = reg[m]
    px = px[px.sum(1) >= 48]                                         # без near-чёрного контура (тонкий)
    if not len(px):
        return (0, 0, 0)
    q = (px // 8) * 8
    cols, cnts = np.unique(q, axis=0, return_counts=True)
    return tuple(int(v) for v in cols[int(np.argmax(cnts))])


def _edges_top_down(poly, has_waist, dome_n=0):
    """Левая и правая кромки полосы, обе сверху вниз."""
    if dome_n and dome_n >= 2 and len(poly) >= dome_n + 2:
        # купол: poly = [дуга купола (dome_n)] + [низ-право, низ-лево]
        left = [poly[0], poly[dome_n + 1]]            # верх-лево (старт дуги, у базы) -> низ-лево
        right = [poly[dome_n - 1], poly[dome_n]]      # верх-право (конец дуги, у базы) -> низ-право
    elif has_waist:
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


def _snap_to_palette(c, pal, sat_thr=0.18, hue_w=2.0, class_penalty=4000.0):
    """Ближайший цвет палитры к c, но ПЕРЦЕПТИВНО (не чистый RGB — иначе светлая кожа утекает
    в серый). Базовое RGB-расстояние + штраф за разницу ТОНА (для насыщенных) + большой штраф,
    если один цвет насыщенный, а другой серый (сочное != серое). pal пуст -> c как есть."""
    import colorsys
    if not pal:
        return tuple(int(v) for v in c)
    ch, cs, cv = colorsys.rgb_to_hsv(*[v / 255.0 for v in c])
    c_sat = cs >= sat_thr

    def dist(p):
        d = sum((int(c[k]) - p[k]) ** 2 for k in range(3))           # база: RGB
        ph, ps, pv = colorsys.rgb_to_hsv(*[v / 255.0 for v in p])
        p_sat = ps >= sat_thr
        if c_sat and p_sat:                                           # оба цветные -> важен тон
            dh = abs(ch - ph); dh = min(dh, 1.0 - dh) * 360.0
            d += (dh * hue_w) ** 2
        if c_sat != p_sat:                                            # сочное <-> серое: не путать
            d += class_penalty
        return d
    return min(pal, key=dist)


def _coalesce_equal(bands):
    """Слить соседние полосы ТОЧНО одинакового цвета (после снэпа к палитре)."""
    out = []
    for f0, f1, c in bands:
        if out and tuple(out[-1][2]) == tuple(c):
            out[-1][1] = f1
        else:
            out.append([f0, f1, tuple(c)])
    return [(b[0], b[1], b[2]) for b in out]


def _cap_zones(bands, maxn):
    """Оставить не более maxn зон: самую короткую сливаем в более длинного соседа, повторяем."""
    if maxn <= 0 or len(bands) <= maxn:
        return [(b[0], b[1], tuple(b[2])) for b in bands]
    out = [list(b) for b in bands]
    while len(out) > maxn:
        k = min(range(len(out)), key=lambda i: out[i][1] - out[i][0])
        neigh = [j for j in (k - 1, k + 1) if 0 <= j < len(out)]
        j = max(neigh, key=lambda i: out[i][1] - out[i][0])
        lo, hi = min(out[k][0], out[j][0]), max(out[k][1], out[j][1])
        out[min(k, j)] = [lo, hi, out[j][2]]                          # цвет — у более длинного соседа
        out.pop(max(k, j))
    return [(b[0], b[1], tuple(b[2])) for b in out]


def design_palette(page, front_drawings_fn, contour_mm, fig, cfg):
    """R1: палитра ЛОГИЧЕСКИХ цветов дизайна. Квантуем пиксели ВНУТРИ силуэта (без near-чёрной
    обводки и near-белого фона) к небольшому числу цветов (gusset_palette_max) и выкидываем
    слишком близкие (gusset_palette_merge_dist). К этой палитре снэпятся сэмплы кромки, чтобы
    тени/градиенты/блики ОДНОГО цвета не плодили кучу полос-оттенков. Возвращает [(r,g,b)]."""
    import cv2
    from PIL import Image
    arr, bx0, by0, Z, mg = front_raster(page, front_drawings_fn)
    H, W = arr.shape[:2]
    f_mm = fig.s * PT2MM
    pts = np.array([[(x / f_mm - bx0 + mg) * Z, (y / f_mm - by0 + mg) * Z]
                    for x, y in contour_mm.exterior.coords], np.int32)
    mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [pts], 1)
    px = arr[mask > 0].reshape(-1, 3).astype(int)
    if not len(px):
        return []
    s = px.sum(1)
    dark = cfg.get("outline_dark_thr", 55)
    white = cfg.get("gusset_palette_white_sum", 735)
    px = px[(s >= dark) & (s <= white)]                              # без обводки и без фона
    if len(px) < 16:
        return []
    n = max(2, int(cfg.get("gusset_palette_max", 8)))
    q = Image.fromarray(px.astype(np.uint8).reshape(-1, 1, 3)).quantize(colors=n, method=Image.MEDIANCUT)
    pal_arr = np.array(q.getpalette()[:n * 3]).reshape(-1, 3)
    used = np.unique(np.asarray(q))
    pal = [tuple(int(v) for v in pal_arr[i]) for i in used if i < len(pal_arr)]
    mdist = int(cfg.get("gusset_palette_merge_dist", 40))            # слить близкие записи палитры
    merged = []
    for c in sorted(pal, key=lambda x: -(x[0] + x[1] + x[2])):
        if all(max(abs(c[k] - m[k]) for k in range(3)) > mdist for m in merged):
            merged.append(c)
    return merged or pal


def detect_wrap_rings(page, contour_mm, fig, cfg):
    """R2: обтекающие объекты-КОЛЬЦА (пояс и т.п.) — насыщенные АКЦЕНТНЫЕ заливки, идущие
    по ВСЕЙ ширине фигуры (суммарно широкие + тонкие по высоте). Их надо провести НЕПРЕРЫВНОЙ
    полосой на ОДНОЙ высоте через все боковины (целостность кольца). Возвращает список
    {'color':(r,g,b),'y0':мм,'y1':мм} в той же мм-системе, что и gusset_line/контур."""
    import colorsys
    from shapely.geometry import LineString
    f_mm = fig.s * PT2MM
    bx0, by0, bx1, by1 = contour_mm.bounds
    fw, fh = bx1 - bx0, by1 - by0
    etol = cfg.get("wrap_ring_edge_tol_frac", 0.12) * fw                  # «край-в-край»: допуск до боковой кромки

    def spans_edges(x0, x1, cy):                                          # заливка достаёт до ОБЕИХ кромок силуэта на высоте cy
        try:
            inter = contour_mm.intersection(LineString([(bx0 - 10, cy), (bx1 + 10, cy)]))
        except Exception:
            return False
        if inter.is_empty:
            return False
        lx, _, rx, _ = inter.bounds
        return x0 <= lx + etol and x1 >= rx - etol
    wmin = cfg.get("wrap_ring_min_width_frac", 0.45) * fw                 # кольцо суммарно широкое (базовый порог)
    hmax = cfg.get("wrap_ring_max_height_frac", 0.10) * fh                # и тонкое по высоте
    aspect = cfg.get("wrap_ring_min_aspect", 3.0)                         # каждая заливка — горизонтально вытянутая
    tmin = cfg.get("wrap_ring_min_thick_mm", 0.0)                         # мин.толщина (0=базовая: ловить всё, вкл.тонкие)
    sat_min = cfg.get("wrap_ring_sat_min", 0.35)
    val_min = cfg.get("wrap_ring_val_min", 0.35)
    qd = 24
    buckets = {}                                                          # бакет по квант.цвету -> тонкие широкие заливки
    for dd in page.get_drawings():
        f = dd.get("fill")
        if f is None:
            continue
        c = tuple(int(255 * v) for v in f)
        _h, sv, vv = colorsys.rgb_to_hsv(*[x / 255.0 for x in c])
        if sv < sat_min or vv < val_min:
            continue
        r = dd["rect"]
        y0, y1 = r.y0 * f_mm, r.y1 * f_mm
        x0, x1 = r.x0 * f_mm, r.x1 * f_mm
        w, hgt = x1 - x0, y1 - y0
        if not (0 < hgt <= hmax and w >= aspect * hgt):                   # горизонтально вытянутая и тонкая
            continue
        if not (by0 - 5 <= (y0 + y1) / 2.0 <= by1 + 5):
            continue
        buckets.setdefault((c[0] // qd, c[1] // qd, c[2] // qd), []).append(
            {"c": c, "y0": y0, "y1": y1, "x0": x0, "x1": x1})
    rings = []
    for parts in buckets.values():                                       # внутри цвета — слить по близкой высоте
        parts.sort(key=lambda rr: (rr["y0"] + rr["y1"]) / 2.0)
        groups = []
        for rr in parts:
            cy = (rr["y0"] + rr["y1"]) / 2.0
            if groups and cy - groups[-1]["cy"] < hmax:
                gq = groups[-1]
                gq["y0"] = min(gq["y0"], rr["y0"]); gq["y1"] = max(gq["y1"], rr["y1"])
                gq["x0"] = min(gq["x0"], rr["x0"]); gq["x1"] = max(gq["x1"], rr["x1"])
                gq["cy"] = (gq["y0"] + gq["y1"]) / 2.0
            else:
                groups.append({**rr, "cy": cy})
        e2e = cfg.get("wrap_ring_edge_to_edge", False)                   # строгий фильтр край-в-край (деф. выкл)
        for gq in groups:
            if (gq["x1"] - gq["x0"]) >= wmin and (gq["y1"] - gq["y0"]) >= tmin and \
               (not e2e or spans_edges(gq["x0"], gq["x1"], gq["cy"])):
                rings.append({"color": gq["c"], "y0": gq["y0"], "y1": gq["y1"]})
    return rings


def _splice_band(bands, fa, fb, color):
    """Вписать полосу color на долю [fa,fb], обрезав/подвинув перекрытые полосы (поверх R1)."""
    out = []
    for f0, f1, c in bands:
        if f1 <= fa or f0 >= fb:
            out.append((f0, f1, c)); continue
        if f0 < fa:
            out.append((f0, fa, c))
        if f1 > fb:
            out.append((fb, f1, c))
    out.append((fa, fb, tuple(color)))
    out.sort(key=lambda b: b[0])
    m = []
    for b in out:
        if m and tuple(m[-1][2]) == tuple(b[2]) and abs(m[-1][1] - b[0]) < 1e-6:
            m[-1] = (m[-1][0], b[1], m[-1][2])
        else:
            m.append(tuple(b))
    return m


def _fill_white_nearest(bands, white_sum=705):
    """Белые/near-белые зоны закрасить ближайшим ПО ПОЗИЦИИ не-белым соседом (закругления/вырезы
    не остаются белыми). Если не-белых зон нет совсем — оставить как есть (белым)."""
    if not bands:
        return bands
    is_white = lambda c: sum(int(v) for v in c) >= white_sum
    nonwhite = [i for i, b in enumerate(bands) if not is_white(b[2])]
    if not nonwhite:
        return bands
    out = []
    for i, (f0, f1, c) in enumerate(bands):
        if is_white(c):
            j = min(nonwhite, key=lambda k: abs(k - i))
            c = bands[j][2]
        out.append((f0, f1, tuple(c)))
    merged = []                                                         # слить соседние одинаковые
    for b in out:
        if merged and tuple(merged[-1][2]) == tuple(b[2]):
            merged[-1] = (merged[-1][0], b[1], merged[-1][2])
        else:
            merged.append(list(b))
    return [(g[0], g[1], tuple(g[2])) for g in merged]


def _cmyk2rgb(c):
    """CMYK (доли 0..1) -> RGB (0..255) для ПРЕВЬЮ (наивная конверсия; вывод печати — CMYK)."""
    cc, m, y, k = [float(v) for v in c]
    return (int(round(255 * (1 - cc) * (1 - k))),
            int(round(255 * (1 - m) * (1 - k))),
            int(round(255 * (1 - y) * (1 - k))))


def _bands_from_manual(s, manual, glen):
    """РУЧНАЯ покраска: bands боковины из отрезков manual=[(t0,t1,(c,m,y,k))] (t — доля периметра
    gusset_line [0..1]). Возвращает (bands_rgb, bands_cmyk) по пересечению дуги боковины с отрезком."""
    a0, a1 = s["arc0"], s["arc1"]
    flip = s.get("yn0", 0) < s.get("yn1", 0)
    a_top, a_bot = (a1, a0) if flip else (a0, a1)                     # f=0 у верхнего ребра
    span = a_bot - a_top
    if abs(span) < 1e-9 or glen <= 0:
        return [], []
    lo, hi = min(a_top, a_bot), max(a_top, a_bot)
    out = []
    for seg in manual:
        t0, t1, cmyk = seg[0], seg[1], seg[2]
        m0, m1 = t0 * glen, t1 * glen
        ov0, ov1 = max(lo, min(m0, m1)), min(hi, max(m0, m1))
        if ov1 - ov0 < 2.0:                                          # отбросить микро-полоску соседнего цвета на стыке
            continue                                                 # (суб-мм «хвост» от округления реза -> деталь строго одноцветна)
        fa = (ov0 - a_top) / span; fb = (ov1 - a_top) / span
        f0, f1 = sorted((fa, fb))
        out.append((max(0.0, f0), min(1.0, f1), tuple(float(v) for v in cmyk)))
    out.sort()
    bands_rgb = [(f0, f1, _cmyk2rgb(c)) for f0, f1, c in out]
    bands_cmyk = [(f0, f1, c) for f0, f1, c in out]
    return bands_rgb, bands_cmyk


def color_sides(layout, g, contour_mm, fig, page, front_drawings_fn, cfg):
    """Залить каждую полосу цветными зонами = цвет макета на её дуге переда.
    Пишет в каждую полосу layout['strips'] список s['bands'] = [(f0,f1,(r,g,b))].
    РУЧНОЙ режим (cfg['manual_paint']): bands строятся из отрезков пользователя (цвет CMYK)."""
    manual = cfg.get("manual_paint")
    if manual:                                                        # РУЧНАЯ покраска гассета (CMYK как в макете)
        glen = g.get("gusset_len") or 1.0
        for s in layout["strips"]:
            br, bc = _bands_from_manual(s, manual, glen)
            s["bands"] = br
            s["bands_cmyk"] = bc
        return
    arr, bx0, by0, Z, mg = front_raster(page, front_drawings_fn)
    H, W = arr.shape[:2]
    f_mm = fig.s * PT2MM                                              # 1 pt = f_mm мм
    dark_thr = cfg.get("outline_dark_thr", 55)                       # обводкой считаем только near-чёрное (цвета не теряем)
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
        if cfg.get("side_color_dominant"):                          # ДОМИНАНТ по ДЛИНЕ в поперечном рядке (луче внутрь)
            d0 = cfg.get("side_color_ray_start_mm", 3.0)            # крупные приграничные заливки: сумка/джинса
            d1 = cfg.get("side_color_depth_mm", 60.0)
            ds = cfg.get("side_color_ray_step_mm", 2.0)
            clu_thr = cfg.get("side_color_cluster_thr", 28)        # близость RGB (макс по каналу) для одного бакета
            white_sum = cfg.get("side_color_white_skip_sum", 735)  # near-белое = фон/зазор -> пропуск
            min_val = cfg.get("side_color_min_value", 0)           # A: яркостный порог — тёмное не считаем совсем
            dark_w = cfg.get("side_color_dark_weight", 1.0)        # B: вес тёмных бакетов (1.0=выкл, <1=проигрывают)
            dark_vthr = cfg.get("side_color_dark_val_thr", 140)    # граница «тёмного» для A/B (по max-каналу)
            clusters = []                                          # [sum_rgb(3), cnt, length]
            valid_len = 0.0; dd = d0
            while dd <= d1:
                c = px_mm(*(p + n * dd))
                s = int(c[0]) + int(c[1]) + int(c[2])
                if not is_dark(c) and s < white_sum and max(c) >= min_val:  # не обводка/фон/(A)тёмное
                    w = ds * (dark_w if max(c) < dark_vthr else 1.0)        # B: тёмное весит меньше
                    valid_len += w
                    for cl in clusters:
                        m = [cl[0][k] / cl[1] for k in range(3)]
                        if max(abs(m[k] - int(c[k])) for k in range(3)) < clu_thr:
                            for k in range(3):
                                cl[0][k] += int(c[k])
                            cl[1] += 1; cl[2] += w; break
                    else:
                        clusters.append([[int(c[0]), int(c[1]), int(c[2])], 1, w])
                dd += ds
            if clusters and valid_len > 0:
                dom = max(clusters, key=lambda cl: cl[2])           # бакет с макс суммарной длиной
                min_frac = cfg.get("side_color_dominant_min_frac", 0.45)
                if dom[2] / valid_len >= min_frac:                 # уверенный доминант -> его средний цвет
                    mean = tuple(int(dom[0][k] / dom[1]) // 4 * 4 for k in range(3))
                    return mean, True
            # неоднозначно / всё тёмное -> обычный кромочный путь ниже
        look = cfg.get("outline_fill_lookahead_mm", 16.0)           # заглянуть глубже на столько мм
        jump = cfg.get("outline_fill_min_jump", 120)                # «намного ярче» = скачок суммы RGB
        dark_fill = cfg.get("outline_fill_dark_max", 150)           # кромка считается «тёмной» (возможно цветная обводка)
        d, last = 1.0, None                                        # УНИВЕРСАЛЬНО: цвет ПРЯМО У КРОМКИ (первая
        while d <= skip_max:                                        # стабильная заливка после обводки) — верно для
            c = px_mm(*(p + n * d))                                 # тонких краевых цветов (зубцы/диски), не уходит в тело
            if not is_dark(c):                                     # near-чёрное = контур, пропускаем
                c2 = px_mm(*(p + n * (d + stab)))                 # стабильность: соседний пиксель тоже не тёмный
                if not is_dark(c2):                               # (тонкий блик/антиалиас в 1px не победит)
                    base = c2
                    s2 = int(c2[0]) + int(c2[1]) + int(c2[2])
                    if s2 < dark_fill:                            # кромка ТЁМНАЯ (цветная обводка ИЛИ реально тёмный цвет)
                        deep = px_mm(*(p + n * min(skip_max, d + look)))   # что ГЛУБЖЕ за ней
                        sd = int(deep[0]) + int(deep[1]) + int(deep[2])
                        if sd > s2 + jump and not is_dark(deep):  # глубже ЗАМЕТНО ярче -> кромка была обводкой, берём заливку
                            base = deep                           # (если глубже так же темно -> это реальный цвет, оставляем c2)
                    return tuple(int(v) // 4 * 4 for v in base), True
            last = c; d += step_mm
        return (tuple(int(v) for v in last) if last is not None else fallback), False

    pal = design_palette(page, front_drawings_fn, contour_mm, fig, cfg) if cfg.get("gusset_palette_snap") else []
    rings = detect_wrap_rings(page, contour_mm, fig, cfg) if cfg.get("gusset_wrap_rings") else []
    for s in layout["strips"]:
        a0, a1 = s["arc0"], s["arc1"]
        flip = s["yn0"] < s["yn1"]
        a_top, a_bot = (a1, a0) if flip else (a0, a1)                 # верх полосы -> к макушке
        L = abs(a1 - a0)
        M = int(min(160, max(3, L / cfg.get("color_sample_step_mm", 5))))
        fs = np.linspace(0, 1, M + 1)
        sm = [sample(a_top + (a_bot - a_top) * ((fs[k] + fs[k + 1]) / 2)) for k in range(M)]
        raw_c = [c for c, _ in sm]; ok = [o for _, o in sm]
        for k in range(M):                                           # провалы -> ближайший валидный сосед
            if not ok[k]:
                nb = next((raw_c[j] for d in range(1, M)
                           for j in (k - d, k + d) if 0 <= j < M and ok[j]), None)
                if nb is not None:
                    raw_c[k] = nb
        if pal:                                                      # R1: снэп к логической палитре дизайна
            raw_c = [_snap_to_palette(c, pal) for c in raw_c]
        raw = [(float(fs[k]), float(fs[k + 1]), raw_c[k]) for k in range(M)]          # РЕАЛЬНЫЕ цвета кромки (без accentize)
        if pal:                                                      # снэпнуто -> сливаем только ТОЧНО одинаковые соседние
            bands = _coalesce_equal(raw)
        else:
            vmm = cfg.get("band_value_merge_max") if cfg.get("side_color_dominant") else None  # не сливать разную яркость
            bands = _simplify_bands(raw, fallback, cfg.get("hue_merge_deg", 20.0), vmm)    # слить соседние близкие по тону
        small_frac = cfg.get("band_merge_small_frac", 0.03)
        mz = cfg.get("gusset_min_zone_mm", 0.0)                                       # R1: абсолютный минимум зоны (мм)
        if mz > 0 and L > 0:
            small_frac = max(small_frac, mz / L)
        bands = _merge_small_bands(bands, small_frac)                                 # убрать тонкие
        if cfg.get("side_color_drop_dark_bands"):                                     # C: тёмные полосы -> светлый сосед
            bands = _drop_dark_bands(bands, cfg.get("side_color_dark_band_val", 140))
        maxz = int(cfg.get("gusset_max_zones", 0) or 0)                               # R1: потолок числа зон на полосу
        if maxz > 0:
            bands = _cap_zones(bands, maxz)
        if rings:                                                                     # R2: непрерывное кольцо (пояс) на одной высоте
            ey = [line.interpolate(a_top + (a_bot - a_top) * ((fs[k] + fs[k + 1]) / 2)).coords[0][1]
                  for k in range(M)]                                                  # высота (мм) кромки на доле fs
            for rg in rings:
                ks = [k for k in range(M) if rg["y0"] <= ey[k] <= rg["y1"]]
                if ks:                                                                # эта боковина проходит высоту кольца
                    bands = _splice_band(bands, float(fs[min(ks)]), float(fs[max(ks) + 1]), rg["color"])
        if cfg.get("gusset_fill_white_nearest"):                                      # белые зоны -> ближайший не-белый
            bands = _fill_white_nearest(bands, cfg.get("gusset_white_sum", 705))
        s["bands"] = bands


def strip_band_quads(s, key="bands"):
    """Для полосы с s[key] вернуть [(quad_pts, color)] в координатах раскладки.
    key='bands_cmyk' -> цвет = CMYK-доли (для нативного CMYK-вывода в PDF)."""
    poly = s["poly"]
    dome_n = s.get("dome_n") or 0
    left, right = _edges_top_down(poly, s.get("waist") is not None, dome_n)
    out = []
    bands = s.get(key, [])
    if dome_n and dome_n >= 2 and bands:                                 # шапка-купол = цвет верхней полосы
        out.append((list(poly[:dome_n]), bands[0][2]))
    for f0, f1, c in bands:
        l0, r0 = _interp(left, f0), _interp(right, f0)
        l1, r1 = _interp(left, f1), _interp(right, f1)
        out.append(([l0, r0, r1, l1], c))
    return out


def strip_band_quads_cmyk(s):
    """Квады полосы с ЦВЕТОМ CMYK (s['bands_cmyk']) — для нативного CMYK-вывода."""
    return strip_band_quads(s, key="bands_cmyk")
