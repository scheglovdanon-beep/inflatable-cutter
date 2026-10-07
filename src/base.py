"""
Днище — деталь, закрывающая низ фигуры: стыкуется с нижней кромкой переда/зада (длинные
стороны) и с боковинами (торцы = толщина низа). Форма = форма нижнего проёма (НЕ хардкод-
квадрат): строим полосу вдоль реальной нижней кромки. Отверстие Ø fan под вентилятор.
Ножка — прямоугольник: высота leg_height, ширина = длина окружности вентилятора (π·Ø).
Всё в мм.
"""
import math
from shapely.geometry import Polygon


def _arc_pts(W, s, n=48):
    """Точки дуги окружности: хорда [0..W] по x, прогиб (сагитта) s вверх (y<0) в центре.
    Возвращает [(x,y)] от (0,0) до (W,0), центр (W/2,-s)."""
    if s < 1e-6:
        return [(0.0, 0.0), (W, 0.0)]
    R = (s * s + (W / 2.0) ** 2) / (2.0 * s)          # радиус по хорде и сагитте
    yc = (( W / 2.0) ** 2 - s * s) / (2.0 * s)         # центр дуги ниже (y>0)
    return [((W * k / n),
             yc - math.sqrt(max(R * R - (W * k / n - W / 2.0) ** 2, 0.0)))
            for k in range(n + 1)]


def _arclen(W, s):
    if s < 1e-6:
        return W
    R = (s * s + (W / 2.0) ** 2) / (2.0 * s)
    return 2.0 * R * math.asin(min(1.0, (W / 2.0) / R))


def build_base_v2(g, cfg):
    """Днище v2 (пример заказчика): торцы (стык с боковинами) = ширина боковины; центр —
    круг base_center_circle_mm; длинные рёбра ЗАКРУГЛЕНЫ (раздув центра до круга), длина
    каждого ребра == длине низа переда/зада (g['bottom_len']). Круг в центре (переход к венту)."""
    end_w = cfg.get("gusset_width_mm", cfg.get("main_width_mm", 220))   # торец = ширина боковины
    circ = cfg.get("base_center_circle_mm", 300)                         # центральный круг
    leg_h = cfg["leg_height_mm"]
    L = g["bottom_len"]                                                  # длина закруглённого ребра = низ переда/зада
    s = max((circ - end_w) / 2.0, 0.0)                                   # раздув (сагитта) на сторону
    lo, hi = 1.0, max(L, circ)                                           # подобрать хорду W так, чтобы длина дуги == L
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _arclen(mid, s) < L:
            lo = mid
        else:
            hi = mid
    W = (lo + hi) / 2.0
    top = _arc_pts(W, s)                                                 # верхнее ребро (прогиб вверх, y<0)
    bot = [(x, end_w - y) for x, y in top]                              # нижнее = зеркало к низу (прогиб вниз)
    ring = top + bot[::-1]                                               # замкнутый контур
    dnishe = Polygon(ring).buffer(0)
    c = (W / 2.0, end_w / 2.0)                                           # центр круга
    # ПЕРЕХОД-ВОРОНКА Ø circ -> Ø fan, 4 части (развёртка усечённого конуса, по 1/4 окружности)
    from arm import _develop_frustum
    fan = cfg["fan_diameter_mm"]                                         # 460 (под вентилятор + ножку)
    th = cfg.get("transition_height_mm", 150)                           # наклонная высота перехода
    nparts = cfg.get("transition_parts", 4)
    from shapely import affinity as _aff
    trans = []
    for _ in range(nparts):                                          # перевернуть: широкий край (460) смотрит ВВЕРХ
        t = _develop_frustum(math.pi * circ / nparts, math.pi * fan / nparts, th)
        t = _aff.scale(t, 1.0, -1.0, origin="center")
        trans.append(_aff.translate(t, -t.bounds[0], -t.bounds[1]))
    return {
        "dnishe": dnishe, "fan_c": c, "fan_d": circ,                     # fan_d здесь = центральный круг 300
        "bottom_len": L, "width": end_w, "transition": trans,
        "leg_w": math.pi * fan, "leg_h": leg_h,                          # ножка = π·460
        "v2": True,
    }


def build_base(g, cfg):
    if cfg.get("base_v2"):
        return build_base_v2(g, cfg)
    fan = cfg["fan_diameter_mm"]
    leg_h = cfg["leg_height_mm"]
    # лекало-днище: длина = нижняя кромка переда/зада (стык с ними),
    # ширина = толщина боковины внизу (стык с боковинами). Размеры ВЫВЕДЕНЫ из деталей.
    length = g["bottom_len"]                       # шов с передом и задом
    width = cfg.get("base_width_mm", fan)          # шов с боковинами (= толщине низа); 650 для 4200
    dnishe = Polygon([(0, 0), (length, 0), (length, width), (0, width)])
    c = (length / 2.0, width / 2.0)                # отверстие вентилятора по центру (Ø fan, 460)
    return {
        "dnishe": dnishe, "fan_c": c, "fan_d": fan,
        "bottom_len": length, "width": width,
        "leg_w": math.pi * fan, "leg_h": leg_h,
    }


def render_base(b, out_png, target_px=1100, margin=60, gap=120):
    from PIL import Image, ImageDraw
    dnx0, dny0, dnx1, dny1 = b["dnishe"].bounds
    dw, dh = dnx1 - dnx0, dny1 - dny0
    sheet_w = dw + gap + b["leg_w"] + 2 * margin
    sheet_h = max(dh, b["leg_h"]) + 2 * margin + 40
    sc = target_px / sheet_w
    W, H = int(sheet_w * sc), int(sheet_h * sc)
    def T(x, y): return (x * sc, y * sc)
    im = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(im)

    ox, oy = margin, margin + 40
    dr.polygon([T(ox + x, oy + y) for x, y in b["dnishe"].exterior.coords], outline=(0, 0, 0))
    fx, fy = b["fan_c"]; r = b["fan_d"] / 2
    dr.ellipse([T(ox + fx - r, oy + fy - r), T(ox + fx + r, oy + fy + r)], outline=(220, 0, 0), width=3)
    dr.text(T(margin, margin + 8), f'dnishe {dw:.0f}x{dh:.0f}  (red = fan hole D{b["fan_d"]:.0f})', fill=(0, 0, 0))

    lx = margin + dw + gap; ly = margin + 40
    dr.rectangle([T(lx, ly), T(lx + b["leg_w"], ly + b["leg_h"])], outline=(0, 0, 0), width=2)
    dr.text(T(lx, margin + 8), f'nozhka {b["leg_w"]:.0f} x {b["leg_h"]:.0f}', fill=(0, 0, 0))
    im.save(out_png)
    return W, H
