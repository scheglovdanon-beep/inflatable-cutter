"""
Боковые части (гассет): из контура переда строим кривую вдоль контура (минус днище)
и дробим её на полосы. Всё в финальных мм. Пока без припуска и раскраски.
"""
import math
import colorsys
import numpy as np
import cv2


def _palette(i, n=14):
    """Различимый, но ТЁМНЫЙ ТУСКЛЫЙ цвет-ключ места сшива (яркие просвечивают ткань)."""
    h = (i * 0.61803398875) % 1.0                      # золотое сечение — цвета не повторяются
    r, g, b = colorsys.hsv_to_rgb(h, 0.5, 0.42)        # низкая насыщенность/яркость -> тёмный тусклый
    return (int(r * 255), int(g * 255), int(b * 255))
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


def _drop_background(drs, R, cfg=None):
    """Убрать заливки-фон: (1) bbox ≥ bg_page_frac по ширине И высоте страницы;
    (2) страховка — заливка с bbox ≥ bg_soft_frac по ПЛОЩАДИ страницы. Универсально,
    заменяет дублированный фильтр фона в 4 функциях детекта."""
    pf = (cfg or {}).get("bg_page_frac", 0.95)
    sf = (cfg or {}).get("bg_soft_frac", 0.80)
    page_area = R.width * R.height
    out = []
    for x in drs:
        rc = x["rect"]
        if rc.width > pf * R.width and rc.height > pf * R.height:
            continue
        if (rc.width * rc.height) > sf * page_area and rc.width > 0.6 * R.width:
            continue
        out.append(x)
    return out


_BODIES_CACHE = {}


def _bodies(page, cfg):
    """ДОРОГОЙ общий шаг: заливки -> полигоны -> union -> 2 крупнейших тела (левое=перёд,
    правое=зад). Кэшируется на (page, пороги фона) — union тысяч заливок делаем ОДИН раз
    на оба (front_drawings и back_drawings). Возвращает (polys, front, back)."""
    key = (id(page), (cfg or {}).get("bg_page_frac", 0.95), (cfg or {}).get("bg_soft_frac", 0.80))
    cached = _BODIES_CACHE.get(key)
    if cached is not None:
        return cached
    drs = _drop_background([x for x in page.get_drawings() if x.get("fill") is not None], page.rect, cfg)
    polys = [(p, x) for p, x in ((_poly_of(x), x) for x in drs) if p]
    ubase = [p for p, _ in polys]
    if (cfg or {}).get("front_all_details", False):                  # классификация по центроидам тел:
        shp = sorted(ubase, key=lambda p: -p.area)                   # для поиска 2 тел достаточно КРУПНЫХ
        tot = sum(p.area for p in shp); acc = 0.0; big = []          # заливок (покрыть ~98% площади) —
        for p in shp:                                                # union 14000 мелких -> ~19с, крупных -> ~1с.
            big.append(p); acc += p.area
            if acc >= 0.98 * tot:
                break
        ubase = big or ubase
    u = unary_union(ubase)
    comps = sorted((u.geoms if isinstance(u, MultiPolygon) else [u]), key=lambda g: -g.area)
    front, back = sorted(comps[:2], key=lambda g: g.centroid.x)
    res = (polys, front, back)
    _BODIES_CACHE[key] = res
    return res


def front_drawings(page, cfg=None):
    """Перёд = левое из 2 крупнейших связных тел (руки/мусор отсекаются)."""
    R = page.rect
    polys, front, back = _bodies(page, cfg)
    from shapely.prepared import prep
    fcx, fcy = front.centroid.x, front.centroid.y
    bcx, bcy = back.centroid.x, back.centroid.y
    # all_details: ВСЕ детали переда (локоть, шланг, брызги) — по ближайшему телу, без строгого
    # contains. Ложную «руку» (шланг) отсекает цветовой фильтр детекта (кожа vs вода), не контур.
    all_details = (cfg or {}).get("front_all_details", False)
    region = None if all_details else prep(front.buffer(2))
    out = []
    for p, x in polys:
        cen = p.centroid
        near_front = ((cen.x - fcx) ** 2 + (cen.y - fcy) ** 2) <= ((cen.x - bcx) ** 2 + (cen.y - bcy) ** 2)
        if near_front and (region is None or region.contains(cen)):
            out.append(x)
    return out, R.height


def front_target_height_mm(cfg):
    """Рост переда/зада = ПОЛНЫЙ target_height_mm. Ножка — отдельная деталь и в размер фигуры
    НЕ входит (сказали не учитывать): если фигура 3300 — перёд строим на 3300.
    Старое поведение (target = фигура + ножка, перёд = target − leg) — флаг subtract_leg_height."""
    if cfg.get("subtract_leg_height", False):
        return cfg["target_height_mm"] - cfg.get("leg_height_mm", 0)
    return cfg["target_height_mm"]


def get_front_figure(page, cfg):
    fr, page_H = front_drawings(page, cfg)
    return build_figure("front", fr, page_H, front_target_height_mm(cfg), cfg["contour_gap_close_mm"],
                        exclude_outline=cfg.get("contour_exclude_outline", False), cfg=cfg)


def back_drawings(page, cfg=None):
    """Зад = ПРАВОЕ из 2 крупнейших связных тел (уже отзеркаленная задняя фигура макета)."""
    R = page.rect
    polys, front, back = _bodies(page, cfg)
    from shapely.prepared import prep
    fcx, fcy = front.centroid.x, front.centroid.y
    bcx, bcy = back.centroid.x, back.centroid.y
    all_details = (cfg or {}).get("front_all_details", False)       # все детали зада в контур (как у переда)
    region = None if all_details else prep(back.buffer(2))
    out = []
    for p, x in polys:
        cen = p.centroid
        near_back = ((cen.x - bcx) ** 2 + (cen.y - bcy) ** 2) <= ((cen.x - fcx) ** 2 + (cen.y - fcy) ** 2)
        if near_back and (region is None or region.contains(cen)):
            out.append(x)
    return out, R.height


def get_back_figure(page, cfg):
    br, page_H = back_drawings(page, cfg)
    return build_figure("back", br, page_H, front_target_height_mm(cfg), cfg["contour_gap_close_mm"],
                        exclude_outline=cfg.get("contour_exclude_outline", False), cfg=cfg)


def detect_arm_side(page, cfg):
    """Сторона машущей руки (универсально): деталь-рука привязана к ближайшему телу;
    сторона = слева/справа от центра переда. Или из cfg['arm_side']."""
    s = cfg.get("arm_side", "auto")
    if s in ("left", "right"):
        return s
    R = page.rect
    drs = [x for x in page.get_drawings() if x.get("fill") is not None]
    drs = _drop_background(drs, R, cfg)
    polys = [p for p in (_poly_of(x) for x in drs) if p]
    comps = unary_union(polys)
    comps = sorted((comps.geoms if isinstance(comps, MultiPolygon) else [comps]), key=lambda gg: -gg.area)
    if len(comps) < 3:
        return "left"
    front = sorted(comps[:2], key=lambda gg: gg.centroid.x)[0]
    arms = [gg for gg in comps[2:] if gg.area > 0.01 * front.area]
    if not arms:
        return "left"
    arm = min(arms, key=lambda a: abs(a.centroid.x - front.centroid.x))   # рука переда = ближайшая
    return "left" if arm.centroid.x < front.centroid.x else "right"


def get_arm_contour(page, cfg=None):
    """Контур машущей руки переда (pt) + X центра переда (pt). Рука = компонент,
    ближайший к переду среди не-тел; перёд = левое из двух крупнейших тел."""
    R = page.rect
    drs = [x for x in page.get_drawings() if x.get("fill") is not None]
    drs = _drop_background(drs, R, cfg)
    polys = [(p, x) for p, x in ((_poly_of(x), x) for x in drs) if p]
    u = unary_union([p for p, _ in polys])
    comps = sorted((u.geoms if isinstance(u, MultiPolygon) else [u]), key=lambda g: -g.area)
    front = sorted(comps[:2], key=lambda g: g.centroid.x)[0]
    arms = [g for g in comps[2:] if g.area > 0.01 * front.area]
    arm = min(arms, key=lambda a: abs(a.centroid.x - front.centroid.x))      # рука переда
    members = [p for p, _ in polys if arm.buffer(2).contains(p.centroid)]
    ring = unary_union(members)
    if isinstance(ring, MultiPolygon):
        ring = max(ring.geoms, key=lambda g: g.area)
    return np.array(ring.exterior.coords[:-1]), float(front.centroid.x)


def place_arm_hole(layout, neck, arm_side, cfg):
    """Круг-отверстие под руку на боковой детали со стороны руки: по центру ширины,
    у верха детали (верх круга = margin от верхней кромки). Универсально (формула)."""
    if not neck.get("found"):
        return
    piece_i = neck["seg_left"] if arm_side == "left" else neck["seg_right"]
    s = next((x for x in layout["strips"] if x["i"] == piece_i), None)
    if s is None:
        return
    xs = [p[0] for p in s["poly"]]; ys = [p[1] for p in s["poly"]]
    R = cfg.get("arm_hole_diameter_mm", 250) / 2.0
    margin = cfg.get("arm_hole_top_margin_mm", 20)
    layout["arm_hole"] = {"cx": (min(xs) + max(xs)) / 2.0, "cy": min(ys) + margin + R,
                          "r": R, "piece": piece_i, "side": arm_side}


def add_back_panel(layout, contour_mm, fig, cfg, page, gap=120.0):
    """Задняя основная сторона = РЕАЛЬНАЯ задняя фигура макета (правая, уже отзеркаленная) —
    её контур (сглаженный, к 2700) + её текстура. Ставится чистой полосой ниже всей раскладки.
    Вызывать ПОСЛЕ размещения руки, ДО запаски."""
    if not cfg.get("make_back"):
        return
    from_front = bool(cfg.get("back_from_front"))                    # симметричная фигура: зад = копия ПЕРЕДА
    if from_front:                                                   # (та же обрезанная форма и текстура -> без
        bfig = fig                                                  # рассинхрона формы/зазоров/ключа)
    else:
        bfig = get_back_figure(page, cfg)
    if not from_front:                                              # РЕАЛЬНЫЙ зад — зеркалим точки реза переда в коорд. зада
        fx0, fy0, fx1, fy1 = fig.x0, fig.y0, fig.x1, fig.y1
        bx0, by0, bx1, by1 = bfig.x0, bfig.y0, bfig.x1, bfig.y1
        fw = (fx1 - fx0) or 1.0; fh = (fy1 - fy0) or 1.0; bw = bx1 - bx0; bh = by1 - by0
        mir = lambda p: [bx1 - (p[0] - fx0) / fw * bw, by0 + (p[1] - fy0) / fh * bh]
        if cfg.get("arm_cut") == "armpit":                          # зад тоже обрезаем по контуру БЕЗ руки
            from arm import cut_waving_arms
            bcfg = cfg
            man = cfg.get("manual_arms")
            if man:
                man2 = []
                for m in man:
                    nm = {"f": mir(m["f"]), "tip": mir(m["tip"])}
                    if m.get("blade"):
                        nm["blade"] = [mir(m["blade"][0]), mir(m["blade"][1])]
                    man2.append(nm)
                bcfg = {**cfg, "manual_arms": man2}
            bfig.contour_pt, _ = cut_waving_arms(page, bfig.contour_pt, bcfg, strict=False)
        if cfg.get("manual_details"):                               # вшивные детали — тоже из зада
            from arm import cut_details
            mdet2 = []
            for m in cfg["manual_details"]:
                nm = {"f": mir(m["f"])}
                if m.get("blade"):
                    nm["blade"] = [mir(m["blade"][0]), mir(m["blade"][1])]
                if m.get("width"):
                    nm["width"] = [mir(m["width"][0]), mir(m["width"][1])]
                mdet2.append(nm)
            bfig.contour_pt, _ = cut_details(page, bfig.contour_pt, {**cfg, "manual_details": mdet2}, strict=False)
    bcm = smooth_front(contour_to_mm(bfig), cfg)                     # контур зада в мм (как у переда)
    gmax = layout["front"].bounds[3]
    for s in layout.get("strips", []):
        gmax = max(gmax, max(y for _, y in s["poly"]))
    for key in ("arm_pieces", "hand_pieces"):
        for p in layout.get(key, []):
            gmax = max(gmax, p["poly"].bounds[3])
    b = layout.get("base")
    if b:
        gmax = max(gmax, b["dnishe"].bounds[3], b["leg"][1] + b["leg"][3])
    by = gmax + gap
    bx = layout["front"].bounds[0]
    bdx = bx - bcm.bounds[0]; bdy = by - bcm.bounds[1]
    back = affinity.translate(bcm, bdx, bdy)
    layout["back"] = {"poly": back, "f_mm": bfig.s * PT2MM, "bdx": bdx, "bdy": bdy,
                      "src": affinity.translate(bcm, 0, 0),         # исходный контур (для solve-affine текстуры)
                      "cut_pt": bfig.contour_pt,                    # обрезанный контур зада (PT) — фильтр заливок руки
                      "from_front": from_front}                     # True -> текстуру зада брать с ПЕРЕДА (симметр.)
    layout["sheet_h"] = max(layout["sheet_h"], by + (bcm.bounds[3] - bcm.bounds[1]) + gap)


def back_texmap(back):
    """Маппинг точки задней ФИГУРЫ макета (pt) -> координаты задней панели (мм). Без зеркала —
    задняя фигура уже отзеркалена в макете."""
    fm, bdx, bdy = back["f_mm"], back["bdx"], back["bdy"]
    return lambda x, y: (x * fm + bdx, y * fm + bdy)


def place_arm_hole_xy(layout, g, contour_mm, shoulder_xy, cfg):
    """Отверстие Ø arm_hole_diameter_mm на боковине, ближайшей к точке плеча shoulder_xy
    (для фигур без шеи). По центру ширины детали, у верхнего края."""
    from shapely.geometry import Point
    line = g["gusset_line"]
    arc = line.project(Point(shoulder_xy))                          # позиция плеча вдоль гассета
    s = next((x for x in layout["strips"] if x["arc0"] <= arc <= x["arc1"]), None)
    if s is None:
        return
    xs = [p[0] for p in s["poly"]]; ys = [p[1] for p in s["poly"]]
    R = cfg.get("arm_hole_diameter_mm", 250) / 2.0
    margin = cfg.get("arm_hole_top_margin_mm", 20)
    layout["arm_hole"] = {"cx": (min(xs) + max(xs)) / 2.0, "cy": min(ys) + margin + R,
                          "r": R, "piece": s["i"], "side": s.get("side", "left")}


def place_armpit_hole(layout, g, contour_mm, armpit_pt, fig, cfg, dy_mm=0.0, r_override=None, hole_list="arm_holes", one_point=True):
    """Ø arm_hole_diameter_mm на боковине, ближайшей к точке подмышки armpit_pt (в PT).
    Пишет в СПИСОК arm_holes (не перезатирает при нескольких руках). Боковина — по индексу
    сегмента гассета (seg['i']), как place_arm_hole. dy_mm — ручной сдвиг центра отверстия
    вверх(-)/вниз(+) вдоль полоски (из UI: перетаскивание отрезка).
    r_override — радиус отверстия в мм (для вшивных деталей; иначе arm_hole_diameter_mm).
    hole_list — ключ списка отверстий в layout (arm_holes / detail_holes)."""
    from shapely.geometry import Point
    f_mm = fig.s * PT2MM
    ax = armpit_pt[0] * f_mm; ay = armpit_pt[1] * f_mm              # подмышка в мм (как contour_mm)
    arc = g["gusset_line"].project(Point(ax, ay))                  # позиция вдоль гассета
    seg = next((s for s in g["segments"] if s["arc0"] <= arc <= s["arc1"]), None)
    if seg is None:
        return
    piece_i = seg["i"]
    s = next((x for x in layout["strips"] if x["i"] == piece_i), None)
    if s is None:
        return
    xs = [p[0] for p in s["poly"]]; ys = [p[1] for p in s["poly"]]
    R = float(r_override) if r_override else cfg.get("arm_hole_diameter_mm", 250) / 2.0
    m = cfg.get("arm_hole_top_margin_mm", 20)
    stop, sbot = min(ys), max(ys); Ls = sbot - stop
    if cfg.get("arm_hole_top_at_seam"):                            # ВЕРХ отверстия = верхнее ребро боковины
        cy = min(max(stop + R + dy_mm, stop + R), sbot - R)        # (= крайняя точка сшива с передом)
    elif cfg.get("arm_hole_at_armpit"):                            # НИЗ отверстия = точка подмышки (круг вверх)
        flip = seg["yn0"] < seg["yn1"]                            # верх полоски -> к макушке фигуры
        a_top, a_bot = (seg["arc1"], seg["arc0"]) if flip else (seg["arc0"], seg["arc1"])
        y_arm = stop + (arc - a_top) / ((a_bot - a_top) or 1.0) * Ls   # подмышка на полоске
        cy = min(max(y_arm - R + dy_mm, stop + m + R), sbot - m - R)   # низ круга на подмышке (+ ручной сдвиг), в пределах полоски
    else:
        cy = min(max(stop + m + R + dy_mm, stop + m + R), sbot - m - R)   # у верха полоски + ручной сдвиг
    layout.setdefault(hole_list, []).append(
        {"cx": (min(xs) + max(xs)) / 2.0, "cy": cy, "r": R, "piece": piece_i, "one_point": one_point})


def contour_to_mm(fig):
    """Контур переда в финальных мм (масштаб фигуры применён)."""
    f = fig.s * PT2MM
    return affinity.scale(fig.contour_pt, f, f, origin=(0, 0))


def smooth_front(contour_mm, cfg):
    """Сгладить передний контур ТОЛЬКО в зигзагообразных частях (волосы, мелкие выступы,
    бугры), не обрезая макет и не трогая крупные плавные части.
    Логика: морфологическое ЗАМЫКАНИЕ (closing) радиусом front_smooth_mm — заполняет впадины
    между выступами плавной внешней дугой; closing расширяющая, поэтому ВЕСЬ исходный контур
    (и весь макет) гарантированно остаётся внутри. Крупные вогнутости (> ~2r, талия и т.п.)
    и выпуклости сохраняются как обычная обводка. 0 = без сглаживания."""
    r = cfg.get("front_smooth_mm", 70)
    if not r or r <= 0:
        return contour_mm
    p = contour_mm.buffer(r, join_style=1).buffer(-r, join_style=1)   # closing (round): впадины -> плавная дуга
    if p.geom_type == "MultiPolygon":
        p = max(p.geoms, key=lambda g: g.area)
    return p.buffer(0)


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

    # fan_w = ширина боковины внизу (= ширина днища, стык с ним); base_width_mm развязывает её от
    # вент-отверстия fan_diameter_mm (деф. = fan_diameter_mm — старое поведение). 650 для 4200.
    fan_w = cfg.get("base_width_mm", cfg["fan_diameter_mm"])
    main_w, tip_w = cfg["main_width_mm"], cfg.get("tip_width_mm", 250)
    C = len(final)
    # edge_taper_single: 460 только на крайнем срезе -> сужение 460->350 в ПЕРВОЙ части (seg0).
    # Иначе (по умолчанию): 460 на двух крайних срезах -> сужение во ВТОРОЙ части.
    edge = 0 if cfg.get("edge_taper_single", False) else 1
    gw = cfg.get("gusset_width_mm")                                 # фикс. ширина боковины (все сегменты)
    def t_cut(k):
        if gw:
            return gw
        return fan_w if min(k, C - 1 - k) <= edge else main_w
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
        "thickness_max": cfg.get("base_width_mm", cfg["fan_diameter_mm"]), "segments": segments, "cuts": final,
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
        trans = base.get("transition")                 # переход-воронка (4 части) — ряд ниже ножки
        if trans:
            ty = leg_y + leg_h + gap_mm
            widths = [t.bounds[2] - t.bounds[0] for t in trans]
            row_w = sum(widths) + gap_mm * (len(trans) - 1)
            x = cx - row_w / 2.0; placed_t = []
            for t in trans:
                tb = t.bounds
                placed_t.append(_aff.translate(t, x - tb[0], ty - tb[1]))
                x += (tb[2] - tb[0]) + gap_mm
            base_out["transition"] = placed_t
            th_max = max(t.bounds[3] - t.bounds[1] for t in trans)
            sheet_h = max(sheet_h, ty + th_max + gap_mm)
            sheet_w = max(sheet_w, cx + row_w / 2.0 + gap_mm)

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
    # полосы — трапеции; если есть bands (раскраска под перед) — заливаем зоны реальным цветом
    for s in layout["strips"]:
        col = _palette(s["i"])
        if s.get("bands"):
            from side_color import strip_band_quads
            for quad, c in strip_band_quads(s):
                dr.polygon([T(x, y) for x, y in quad], fill=tuple(c))
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
    ah = layout.get("arm_hole")                                     # отверстие под руку (красное)
    if ah:
        dr.ellipse([T(ah["cx"] - ah["r"], ah["cy"] - ah["r"]), T(ah["cx"] + ah["r"], ah["cy"] + ah["r"])],
                   outline=(220, 0, 0), width=3)
        dr.text(T(ah["cx"] - ah["r"], ah["cy"] - ah["r"] - 14), f'ruka D{ah["r"] * 2:.0f} (#{ah["piece"]})', fill=(200, 0, 0))
    for ap in layout.get("arm_pieces", []):                         # детали руки (база/плечо/предплечье)
        dr.polygon([T(x, y) for x, y in ap["poly"].exterior.coords], outline=(170, 0, 170))
        bb = ap["poly"].bounds
        dr.text(T(bb[0], bb[1] - 14), f'{ap["name"]} {ap["w"]:.0f}x{ap["h"]:.0f}', fill=(150, 0, 150))
    for hp in layout.get("hand_pieces", []):                        # детали кисти (лево/боковина/право)
        dr.polygon([T(x, y) for x, y in hp["poly"].exterior.coords], outline=(0, 150, 90))
        bb = hp["poly"].bounds
        dr.text(T(bb[0], bb[1] - 14), f'kist:{hp["name"]} {hp["w"]:.0f}x{hp["h"]:.0f}', fill=(0, 130, 70))
    if any("seam" in s for s in layout.get("strips", [])) or "front_seam" in layout:  # припуск (сплошной)
        from seam import seam_rings
        lw = max(1, round(0.2 * sc))
        for ring in seam_rings(layout):
            dr.line([T(x, y) for x, y in ring], fill=(220, 0, 0), width=lw)
    im.save(out_png)
    return W, H


# ---------- детект перехода корпус->голова (шея) ----------
def detect_neck(contour_mm, g, cfg):
    """Переход корпус->голова, ОТДЕЛЬНО слева и справа (каждая точка на своём изгибе-пинче,
    не на общей горизонтали). Плюс боковые детали, переходящие корпус->голова.
    Возвращает {found, left, right, seg_left, seg_right, drop}."""
    from shapely.geometry import Point
    top_frac = cfg.get("neck_search_top_frac", 0.5)
    smooth = int(cfg.get("neck_smooth_mm", 40)) | 1
    min_drop = cfg.get("neck_min_drop", 0.12)
    x0, y0, x1, y1 = contour_mm.bounds
    W, H = int(x1 - x0) + 20, int(y1 - y0) + 20
    ring = np.array([[x - x0 + 10, y - y0 + 10] for x, y in contour_mm.exterior.coords], np.int32)
    mask = np.zeros((H, W), np.uint8); cv2.fillPoly(mask, [ring], 1)
    ys = np.where(mask.any(1))[0]; yt, yb = ys.min(), ys.max(); fh = yb - yt
    rows = [np.where(mask[y] > 0)[0] for y in range(H)]
    w = np.array([(r.max() - r.min()) if len(r) else 0 for r in rows], float)
    ws = np.convolve(w, np.ones(smooth) / smooth, mode="same")
    y_end = int(yt + top_frac * fh)
    y_head = yt + int(np.argmax(ws[yt:y_end]))
    y_wmin = y_head + int(np.argmin(ws[y_head:y_end]))
    drop = (ws[y_head] - ws[y_wmin]) / ws[y_head] if ws[y_head] > 0 else 0.0
    if not (drop >= min_drop and y_wmin > y_head + 5):
        return {"found": False}
    y_sh = y_wmin + int(np.argmax(ws[y_wmin:y_end])) if y_wmin < y_end - 1 else y_end
    lo, hi = y_head, max(y_sh, y_head + 10)
    lx = np.array([rows[y].min() if len(rows[y]) else 1e9 for y in range(H)])
    rx = np.array([rows[y].max() if len(rows[y]) else -1e9 for y in range(H)])
    yL = lo + int(np.argmax(lx[lo:hi]))                 # левый край дальше вправо = пинч слева
    yR = lo + int(np.argmin(rx[lo:hi]))                 # правый край дальше влево = пинч справа
    left = (float(lx[yL] - 10 + x0), float(yL - 10 + y0))
    right = (float(rx[yR] - 10 + x0), float(yR - 10 + y0))

    line = g["gusset_line"]
    def body_piece(pt):
        """Нижняя (корпусная) деталь под шеей = сосед детали-с-точкой с меньшим y_norm."""
        a = line.project(Point(*pt))
        ci = next((s["i"] for s in g["segments"] if s["arc0"] <= a <= s["arc1"]), None)
        if ci is None:
            return None
        neigh = [s for s in g["segments"] if s["i"] in (ci - 1, ci + 1)]
        return min(neigh, key=lambda s: s["y_norm"])["i"] if neigh else ci
    return {"found": True, "left": left, "right": right, "drop": float(drop),
            "seg_left": body_piece(left), "seg_right": body_piece(right)}


def render_neck(contour_mm, g, neck, layout, out_png, target_h=1300, margin=40, gap=120):
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = contour_mm.bounds
    sc = target_h / (y1 - y0)
    Cw = (x1 - x0) * sc
    strips = []
    if neck.get("found") and layout:
        want = {neck["seg_left"], neck["seg_right"]}
        strips = [s for s in layout["strips"] if s["i"] in want]
    col_w = (max([(max(p[0] for p in s["poly"]) - min(p[0] for p in s["poly"])) for s in strips] + [0])) * sc
    W = int(Cw + (gap + col_w if strips else 0)) + 2 * margin
    H = int((y1 - y0) * sc) + 2 * margin
    def T(x, y): return (margin + (x - x0) * sc, margin + (y - y0) * sc)
    im = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(im)
    dr.line([T(x, y) for x, y in contour_mm.exterior.coords], fill=(210, 210, 210), width=2)
    if neck.get("found"):
        line = g["gusset_line"]
        for si in (neck["seg_left"], neck["seg_right"]):                 # дуги нижних деталей
            s = next((x for x in g["segments"] if x["i"] == si), None)
            if not s:
                continue
            L = s["arc1"] - s["arc0"]; nn = max(8, int(L / 15))
            pts = [line.interpolate(s["arc0"] + L * t / nn).coords[0] for t in range(nn + 1)]
            dr.line([T(*p) for p in pts], fill=(230, 140, 0), width=6)
            mid = line.interpolate((s["arc0"] + s["arc1"]) / 2).coords[0]
            dr.text((T(*mid)[0] + 8, T(*mid)[1]), f'#{si}', fill=(200, 110, 0))
        for px, py in (neck["left"], neck["right"]):                     # точки шеи
            x, y = T(px, py); dr.ellipse([x - 11, y - 11, x + 11, y + 11], outline=(220, 0, 0), width=4)
        px0, ry = margin + Cw + gap, margin + 40                          # сами детали справа
        for s in sorted(strips, key=lambda s: s["i"]):
            pminx = min(p[0] for p in s["poly"]); pminy = min(p[1] for p in s["poly"])
            poly = [(px0 + (p[0] - pminx) * sc, ry + (p[1] - pminy) * sc) for p in s["poly"]]
            dr.polygon(poly, outline=(230, 140, 0))
            dr.text((px0, ry - 16), f'detal #{s["i"]}  L{s["edge"]:.0f} w{s["t_top"]:.0f}/{s["t_bot"]:.0f}', fill=(200, 110, 0))
            ry += (max(p[1] for p in s["poly"]) - pminy) * sc + gap
        dr.text((margin, 8), "nizhnie detali korpus->golova: L=#%s  R=#%s" % (neck["seg_left"], neck["seg_right"]), fill=(0, 0, 0))
    im.save(out_png)
    return W, H


# ---------- карта швов: контур переда, раскрашенный по боковым деталям ----------
def render_sew_map(contour_mm, g, out_png, target_h=1400, margin=50):
    """Главный контур переда разбит по цветам отрезков, которые сшиваются с боковинами:
    цвет дуги = цвету соответствующей боковой детали (#i). Днище — серым (без боковины)."""
    from PIL import Image, ImageDraw
    line = g["gusset_line"]
    x0, y0, x1, y1 = contour_mm.bounds
    sc = target_h / (y1 - y0)
    W, H = int((x1 - x0) * sc) + 2 * margin, int((y1 - y0) * sc) + 2 * margin
    def T(x, y): return (margin + (x - x0) * sc, margin + (y - y0) * sc)
    im = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(im)
    dr.line([T(x, y) for x, y in g["bottom_pts"]], fill=(150, 150, 150), width=7)   # днище
    for s in g["segments"]:
        col = _palette(s["i"])
        L = s["arc1"] - s["arc0"]
        nn = max(8, int(L / 15))
        pts = [line.interpolate(s["arc0"] + L * t / nn).coords[0] for t in range(nn + 1)]
        dr.line([T(*p) for p in pts], fill=col, width=6)
        mid = line.interpolate((s["arc0"] + s["arc1"]) / 2).coords[0]
        px, py = T(*mid)
        dr.text((px + 7, py - 6), f'#{s["i"]}', fill=col)
    dr.text((margin, 8), "kontur perednej chasti: cvet dugi = svoja bokovaja detal (#i)", fill=(0, 0, 0))
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


# ============ ЗАКРУГЛЕНИЕ ГАССЕТА У ПЛЕЧА (ТЗ_shoulder_round_2d) ============

def _round_strip_top(poly, dome_frac=0.5, preserve_len=False):
    """Заменить ПРЯМОЕ верхнее ребро полоски-гассета на выпуклую дугу-КУПОЛ (вверх, −y).
    poly — трапеция [верх-лево, верх-право, низ-право, низ-лево] (или больше).
    preserve_len: купол ВХОДИТ в длину боковины (прямая часть короче на пол-дуги), чтобы
    сторона боковины (прямая+дуга до вершины) = исходной длине сегмента, а не длиннее.
    Возвращает (new_poly, n_dome, arc_len, chord_d): дуга = new_poly[:n_dome]."""
    pts = [tuple(p) for p in poly]
    if len(pts) < 4:
        return pts, 0, 0.0, 0.0
    top_y = min(p[1] for p in pts)
    top = [i for i, p in enumerate(pts) if abs(p[1] - top_y) < 1e-6]
    if len(top) < 2:
        return pts, 0, 0.0, 0.0
    i0, i1 = top[0], top[-1]
    P0 = np.array(pts[i0], float); P1 = np.array(pts[i1], float)
    if P0[0] > P1[0]:
        P0, P1 = P1, P0; i0, i1 = i1, i0
    d = float(np.hypot(*(P1 - P0)))
    if d < 2:
        return pts, 0, 0.0, 0.0
    cx = (P0[0] + P1[0]) / 2.0
    h = dome_frac * d
    R = ((d / 2.0) ** 2 + h * h) / (2.0 * h)                          # дуга ОКРУЖНОСТИ (не синус)
    N = max(16, int(math.pi * R / 3))
    dome = []
    for t in np.linspace(0, 1, N):
        s = -d / 2.0 + d * t
        y = top_y + (R - h) - math.sqrt(max(R * R - s * s, 0.0))
        dome.append((cx + s, y))
    arc_len = R * 2.0 * math.asin(min(1.0, (d / 2.0) / R))
    lower = [pts[k] for k in range(len(pts)) if k not in (i0, i1)]
    return dome + lower, len(dome), arc_len, d


def _clip_bands(bands, f0, f1):
    """Полосы цвета (frac вдоль сегмента) -> подрезать к под-диапазону [f0,f1] и перенормировать."""
    if not bands:
        return bands
    w = (f1 - f0) or 1.0; out = []
    for bf0, bf1, c in bands:
        n0 = (min(max(bf0, f0), f1) - f0) / w
        n1 = (min(max(bf1, f0), f1) - f0) / w
        if n1 - n0 > 1e-3:
            out.append([n0, n1, c])
    return out or None


def narrow_between_arms(g, arc_a, arc_b, ratio=1.7, snap_mm=150.0):
    """2 руки: гассет по дуге МЕЖДУ верхними точками реза рук (поверху, через голову) × 1/ratio.
    Режем сегменты на arc_a/arc_b; боковина, касающаяся [lo,hi], целиком × 1/ratio (без тейпера).
    Точки реза ПРИВЯЗЫВАЕМ к ближайшей границе сегмента (snap_mm), чтобы не плодить ломтики.
    Модифицирует g['segments']."""
    lo, hi = sorted([float(arc_a), float(arc_b)])
    bounds = sorted(set([s["arc0"] for s in g["segments"]] + [s["arc1"] for s in g["segments"]]))
    def _snap(a):                                                     # к ближайшей границе сегмента, если в пределах snap_mm
        nb = min(bounds, key=lambda b: abs(b - a))
        return nb if abs(nb - a) <= snap_mm else a
    lo, hi = _snap(lo), _snap(hi)
    cross = [lo, hi]
    gl = g["gusset_line"]; yb = gl.bounds; ymin, ymax = yb[1], yb[3]
    def _yn(arc):
        return (ymax - gl.interpolate(arc).coords[0][1]) / ((ymax - ymin) or 1.0)
    new = []
    for s in g["segments"]:
        a0, a1 = s["arc0"], s["arc1"]; span = (a1 - a0) or 1.0
        cuts = sorted(a for a in cross if a0 + 10.0 < a < a1 - 10.0)
        t0s, t1s = s.get("t0", s["thickness"]), s.get("t1", s["thickness"])
        for b0, b1 in zip([a0] + cuts, cuts + [a1]):
            f0 = (b0 - a0) / span; f1 = (b1 - a0) / span
            nt0 = t0s + (t1s - t0s) * f0; nt1 = t0s + (t1s - t0s) * f1
            yn0, yn1 = _yn(b0), _yn(b1)
            ns = {**s, "arc0": b0, "arc1": b1, "len": b1 - b0,
                  "t0": nt0, "t1": nt1, "thickness": max(nt0, nt1),
                  "yn0": yn0, "yn1": yn1, "y_norm": (yn0 + yn1) / 2}
            if cuts:
                ns["bands"] = _clip_bands(s.get("bands"), f0, f1)
            new.append(ns)
    inv = 1.0 / ratio; eps = 1.0
    for i, s in enumerate(new):
        s["i"] = i
        in0 = lo - eps <= s["arc0"] <= hi + eps                        # конец В верхнем отрезке?
        in1 = lo - eps <= s["arc1"] <= hi + eps
        if in0 and in1:                                              # ЦЕЛИКОМ в отрезке (голова) -> равномерно 1.7x
            s["t0"] *= inv; s["t1"] *= inv
        elif in0 or in1:                                             # ПОГРАНИЧНАЯ (деталь руки) -> ПЕРЕХОД: конец у
            if in0:                                                 # отрезка = 1.7x, другой конец = стандарт
                s["t0"] *= inv
            if in1:
                s["t1"] *= inv
        s["thickness"] = max(s["t0"], s["t1"])
    g["segments"] = new


def _merge_two_segments(A, B):
    """Слить соседние сегменты A(левый, меньший arc)+B(правый) в один. bands ещё не заданы
    (color_sides считает их позже) -> объединяем только геометрию. Σ длины сохраняется."""
    la = A["len"] or 1e-9; lb = B["len"] or 1e-9; tot = la + lb
    m = dict(A)
    m["arc1"] = B["arc1"]; m["len"] = B["arc1"] - A["arc0"]
    m["t0"] = A["t0"]; m["t1"] = B["t1"]
    m["thickness"] = max(A["t0"], A["t1"], B["t0"], B["t1"])
    m["yn0"] = A["yn0"]; m["yn1"] = B["yn1"]; m["y_norm"] = (A["yn0"] + B["yn1"]) / 2
    if B.get("protrusion") and B.get("waist_frac") is not None:            # выступ справа: талия сдвигается
        m["protrusion"] = True; m["waist"] = B.get("waist")
        m["waist_frac"] = min(max((la + B["waist_frac"] * lb) / tot, 0.05), 0.95)
    elif A.get("protrusion") and A.get("waist_frac") is not None:          # выступ слева
        m["protrusion"] = True; m["waist"] = A.get("waist")
        m["waist_frac"] = min(max((A["waist_frac"] * la) / tot, 0.05), 0.95)
    else:
        m["protrusion"] = False; m.pop("waist", None); m.pop("waist_frac", None)
    m.pop("bands", None)
    return m


def merge_short_segments(g, min_len=150.0):
    """ФИНАЛЬНЫЙ ПАС против слайверов (после всех резов сужения): сегмент короче min_len сливаем
    со СЛЕДУЮЩИМ (последний — с предыдущим). Σ длин = периметр сохраняется (убираем внутр. границу).
    Выступы («песочные часы») сами слияние не инициируют. Модифицирует g['segments']."""
    segs = g["segments"]
    if not segs or len(segs) <= 1 or min_len <= 0:
        return
    for _ in range(200):                                                   # backstop от зацикливания
        idx = next((i for i, s in enumerate(segs)
                    if s["len"] < min_len and not s.get("protrusion")), None)
        if idx is None or len(segs) <= 1:
            break
        j = idx + 1 if idx + 1 < len(segs) else idx - 1                    # сосед: следующий, иначе предыдущий
        a, b = (idx, j) if j > idx else (j, idx)                           # a — левый, b — правый
        segs[a] = _merge_two_segments(segs[a], segs[b]); del segs[b]
    for k, s in enumerate(segs):
        s["i"] = k
    g["segments"] = segs


def narrow_upper_gusset(g, cut_top_y, ratio=1.7, blend_mm=400.0):
    """1 рука + рез по 1 точке. Линия реза — горизонталь y=cut_top_y. Боковину, ПЕРЕСЕКАЮЩУЮ
    линию, режем на 2 детали по линии. Выше линии ширина × 1/ratio; ниже узкая сторона (× 1/ratio
    у линии) ПЛАВНО (длина blend_mm вниз) расширяется до стандарта. Модифицирует g['segments']."""
    gl = g["gusset_line"]; L = gl.length
    cross = []                                                        # arc-позиции пересечения горизонтали с гассетом
    N = max(400, int(L / 2.0)); prev = None
    for k in range(N + 1):
        a = L * k / N; y = gl.interpolate(a).coords[0][1]
        if prev is not None and (prev[1] - cut_top_y) * (y - cut_top_y) < 0:
            t = (cut_top_y - prev[1]) / ((y - prev[1]) or 1e-9)
            cross.append(prev[0] + (a - prev[0]) * t)
        prev = (a, y)
    cross = sorted(cross); ded = []                                  # дедуп близких пересечений (без 1-мм ломтиков)
    for a in cross:
        if not ded or a - ded[-1] > 10.0:
            ded.append(a)
    cross = ded
    yb = gl.bounds; ymin, ymax = yb[1], yb[3]
    def _yn(arc):
        return (ymax - gl.interpolate(arc).coords[0][1]) / ((ymax - ymin) or 1.0)
    new = []                                                          # разрез сегментов по cross
    for s in g["segments"]:
        a0, a1 = s["arc0"], s["arc1"]; span = (a1 - a0) or 1.0
        cuts = sorted(a for a in cross if a0 + 10.0 < a < a1 - 10.0)  # не резать у границы -> без микро-ломтиков
        t0s, t1s = s.get("t0", s["thickness"]), s.get("t1", s["thickness"])
        for b0, b1 in zip([a0] + cuts, cuts + [a1]):
            f0 = (b0 - a0) / span; f1 = (b1 - a0) / span
            nt0 = t0s + (t1s - t0s) * f0; nt1 = t0s + (t1s - t0s) * f1
            yn0, yn1 = _yn(b0), _yn(b1)
            ns = {**s, "arc0": b0, "arc1": b1, "len": b1 - b0,
                  "t0": nt0, "t1": nt1, "thickness": max(nt0, nt1),
                  "yn0": yn0, "yn1": yn1, "y_norm": (yn0 + yn1) / 2}
            if cuts:                                                  # резаный -> подрезать полосы цвета
                ns["bands"] = _clip_bands(s.get("bands"), f0, f1)
            new.append(ns)
    inv = 1.0 / ratio; eps = 1.0                                      # переиндексация + ширина по положению к линии
    for i, s in enumerate(new):
        s["i"] = i
        y0 = gl.interpolate(s["arc0"]).coords[0][1]
        y1 = gl.interpolate(s["arc1"]).coords[0][1]
        a0 = y0 <= cut_top_y + eps; a1 = y1 <= cut_top_y + eps        # конец на/выше линии реза?
        if a0 and a1:                                                 # ДЕТАЛЬ ЦЕЛИКОМ ВЫШЕ -> равномерно 1.7x
            s["t0"] *= inv; s["t1"] *= inv
        elif a0 or a1:                                                # ПЕРВАЯ ниже линии -> тейпер: конец у линии 1.7x,
            if a0:                                                    # другой конец = стандарт (плавный переход вниз)
                s["t0"] *= inv
            if a1:
                s["t1"] *= inv
        # иначе (целиком ниже) -> стандарт, без изменений
        s["thickness"] = max(s["t0"], s["t1"])
    g["segments"] = new


def round_gusset_shoulder(layout, cfg):
    """Купол верха боковин с отверстием руки + подгонка соседней боковины (её ребро = d/ratio).
    Метки/вытачку НЕ считаем здесь (полоска ещё переедет при раскладке) — см. shoulder_overlays."""
    if not cfg.get("shoulder_round"):
        return
    frac = float(cfg.get("shoulder_dome_frac", 0.5))
    # КУПОЛ только при резе по 1 точке (подмышка/вертикаль). Рез 2 точками (хорда) -> стык НЕ закругляем.
    pieces = {h["piece"] for h in layout.get("arm_holes", []) if h.get("one_point", True)}
    if not pieces:
        return
    ratio = float(cfg.get("shoulder_next_ratio", 1.7))
    strips = layout.get("strips", [])
    for s in strips:
        if s.get("i") not in pieces:
            continue
        new_poly, n_dome, arc_len, chord_d = _round_strip_top(s["poly"], frac)
        if n_dome < 2:
            continue
        s["poly"] = new_poly
        s["dome_n"] = n_dome; s["dome_chord"] = chord_d; s["dome_arc_len"] = arc_len; s["dome_frac"] = frac
        if "arc0" in s and "yn0" in s:                               # соседняя боковина — ребро = d/ratio
            flip = s["yn0"] < s["yn1"]
            top_arc = s["arc1"] if flip else s["arc0"]
            adj = adj_key = None
            for t in strips:
                if t is s or "arc0" not in t:
                    continue
                for key in ("arc0", "arc1"):
                    if abs(t.get(key, -1e9) - top_arc) < 1e-6:
                        adj, adj_key = t, key; break
                if adj:
                    break
            if adj is not None:
                s["dome_adj_i"] = adj.get("i")                       # соседняя боковина (её цвет -> средняя зона купола)
                if cfg.get("arm_narrow_upper") or cfg.get("arm_narrow_between"):  # соседняя уже равномерно 1.7× —
                    mate_w = adj["t0"] if adj_key == "arc0" else adj["t1"]  # НЕ сужаем её, а среднюю зону купола
                    s["dome_mid_len"] = float(mate_w)                # подгоняем ПОД её ребро (сшив совпадает)
                else:                                                # старый путь (космонавт): ребро соседней = d/ratio
                    _fit_edge_to_len(adj, adj_key, chord_d / ratio)
                    adj["gusset_mate_w"] = chord_d / ratio           # для 3D: глубина гассета у стыка = d/ratio
                    adj["gusset_mate_end"] = adj_key                 # на каком конце сегмента это ребро
        if cfg.get("dome_in_gusset_len"):                            # купол ВХОДИТ в длину боковины: опустить дугу на
            _mid = s.get("dome_mid_len") or (chord_d / ratio)        # ЗАХОД=(дуга−середина)/2 -> сторона(прямая+заход)=сегмент
            _zh = max(0.0, (arc_len - _mid) / 2.0)                    # (заход — часть купола, что сшивается с передом)
            _pp = list(s["poly"])
            s["poly"] = [(x, y + _zh) for (x, y) in _pp[:n_dome]] + _pp[n_dome:]


def _split_polyline_by_len(pts, a, b):
    """Вернуть кусок полилинии pts между долями длины a..b (0..1) от полной длины."""
    segd = [float(np.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))
            for i in range(len(pts) - 1)]
    total = sum(segd) or 1.0
    la, lb = a * total, b * total
    out = []; acc = 0.0
    for i in range(len(pts) - 1):
        p0, p1 = pts[i], pts[i + 1]; dl = segd[i]
        s0, s1 = acc, acc + dl
        if s1 >= la and s0 <= lb and dl > 1e-9:
            t0 = max(0.0, (la - s0) / dl); t1 = min(1.0, (lb - s0) / dl)
            q0 = (p0[0] + (p1[0] - p0[0]) * t0, p0[1] + (p1[1] - p0[1]) * t0)
            q1 = (p0[0] + (p1[0] - p0[0]) * t1, p0[1] + (p1[1] - p0[1]) * t1)
            if not out:
                out.append(q0)
            out.append(q1)
        acc = s1
    return out


def shoulder_overlays(layout, cfg):
    """ПОСЛЕ раскладки: раскрасить ЛИНИЮ купола боковины по зонам сшива (из ФИНАЛЬНОЙ полоски).
    3 зоны вдоль дуги: заход(перёд) | середина d/apex_ratio (соседняя боковина) | заход(зад).
    Цвет повторяет линию: заход = цвет этой боковины, середина = цвет соседней боковины.
    Купол = poly[:dome_n]. Пишет layout['shoulder_arc_segments'] = [{pts, rgb, role}]."""
    if not cfg.get("shoulder_round"):
        return
    apex_ratio = float(cfg.get("shoulder_apex_ratio", 1.7))
    segs = []
    for s in layout.get("strips", []):
        nd = s.get("dome_n", 0)
        if nd < 2:
            continue
        arc = [tuple(p) for p in s["poly"][:nd]]
        arc_len = sum(float(np.hypot(arc[i + 1][0] - arc[i][0], arc[i + 1][1] - arc[i][1]))
                      for i in range(len(arc) - 1)) or 1.0
        d = s.get("dome_chord") or float(np.hypot(arc[-1][0] - arc[0][0], arc[-1][1] - arc[0][1]))
        mid_len = min(s.get("dome_mid_len") or (d / apex_ratio), arc_len)  # середина = ребро соседней боковины (или d/1.7)
        fa = max(0.0, (arc_len - mid_len) / 2.0 / arc_len)          # граница захода (доля длины)
        fb = 1.0 - fa
        col_self = _palette(s["i"])                                 # цвет ЭТОЙ боковины (заходы)
        adj_i = s.get("dome_adj_i")
        col_adj = _palette(adj_i) if adj_i is not None else _palette(s["i"] + 7)
        left = _split_polyline_by_len(arc, 0.0, fa)
        mid = _split_polyline_by_len(arc, fa, fb)
        right = _split_polyline_by_len(arc, fb, 1.0)
        if len(left) >= 2:
            segs.append({"pts": left, "rgb": col_self, "role": "zahod_front", "i": s["i"]})
        if len(mid) >= 2:
            segs.append({"pts": mid, "rgb": col_adj, "role": "mid_adj", "i": s["i"]})
        if len(right) >= 2:
            segs.append({"pts": right, "rgb": col_self, "role": "zahod_back", "i": s["i"]})
    if segs:
        layout["shoulder_arc_segments"] = segs


def raise_hole_to_zahod_top(layout, cfg):
    """ПОСЛЕ round_gusset_shoulder: поднять верх отверстия руки на уровень ВЕРХА захода с
    передом — передняя часть вшивается, заходя на купол, и верх дыры = верх этого захода.
    Заход = крайние зоны дуги купола (до доли fa / после fb, как в shoulder_overlays).
    Верх захода = самая высокая (min y) граничная точка зон. Ставим cy = y_zahod_top + r."""
    if not (cfg.get("shoulder_round") and cfg.get("arm_hole_top_at_seam")):
        return
    apex_ratio = float(cfg.get("shoulder_apex_ratio", 1.7))
    strips = {s.get("i"): s for s in layout.get("strips", [])}
    for h in layout.get("arm_holes", []):
        s = strips.get(h["piece"])
        if not s:
            continue
        nd = s.get("dome_n", 0)
        if nd < 2:
            continue
        arc = [tuple(p) for p in s["poly"][:nd]]
        arc_len = sum(float(np.hypot(arc[i + 1][0] - arc[i][0], arc[i + 1][1] - arc[i][1]))
                      for i in range(len(arc) - 1)) or 1.0
        d = s.get("dome_chord") or float(np.hypot(arc[-1][0] - arc[0][0], arc[-1][1] - arc[0][1]))
        mid_len = min(s.get("dome_mid_len") or (d / apex_ratio), arc_len)
        fa = max(0.0, (arc_len - mid_len) / 2.0 / arc_len)
        fb = 1.0 - fa
        pa = _split_polyline_by_len(arc, 0.0, fa)                   # заход-перёд (конец у доли fa)
        pb = _split_polyline_by_len(arc, fb, 1.0)                   # заход-зад (начало у доли fb)
        cands = []
        if len(pa) >= 1:
            cands.append(pa[-1][1])
        if len(pb) >= 1:
            cands.append(pb[0][1])
        if not cands:
            continue
        y_zahod_top = min(cands)                                    # самый верх захода (min y)
        r = h["r"]
        ys = [p[1] for p in s["poly"]]
        cy = min(max(y_zahod_top + r, min(ys) + r), max(ys) - r)    # не выезжать за боковину
        h["cy"] = cy


def _fit_edge_to_len(strip, at_arc_key, target_len):
    """Подогнать РЕБРО полоски (у конца at_arc_key) под длину target_len (центрируя);
    второе ребро не трогаем -> полоска плавно таперится к своему размеру."""
    poly = [tuple(p) for p in strip.get("poly", [])]
    if len(poly) < 4:
        return
    aflip = strip.get("yn0", 0) < strip.get("yn1", 0)
    top_is = "arc1" if aflip else "arc0"                              # верх полоски = этот arc-конец
    edge_top = (at_arc_key == top_is)
    ys = [p[1] for p in poly]
    yv = min(ys) if edge_top else max(ys)                            # y нужного ребра (верх/низ)
    idxs = [i for i, p in enumerate(poly) if abs(p[1] - yv) < 1e-6]
    if len(idxs) != 2:                                               # только прямое ребро (трапеция)
        return
    i0, i1 = idxs
    left, right = (i0, i1) if poly[i0][0] <= poly[i1][0] else (i1, i0)  # сохранить winding (лево/право)
    cx = (poly[i0][0] + poly[i1][0]) / 2.0
    poly[left] = (cx - target_len / 2.0, yv)
    poly[right] = (cx + target_len / 2.0, yv)
    strip["poly"] = poly
