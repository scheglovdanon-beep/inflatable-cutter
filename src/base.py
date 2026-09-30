"""
Днище — деталь, закрывающая низ фигуры: стыкуется с нижней кромкой переда/зада (длинные
стороны) и с боковинами (торцы = толщина низа). Форма = форма нижнего проёма (НЕ хардкод-
квадрат): строим полосу вдоль реальной нижней кромки. Отверстие Ø fan под вентилятор.
Ножка — прямоугольник: высота leg_height, ширина = длина окружности вентилятора (π·Ø).
Всё в мм.
"""
import math
from shapely.geometry import Polygon


def build_base(g, cfg):
    fan = cfg["fan_diameter_mm"]
    leg_h = cfg["leg_height_mm"]
    # лекало-днище: длина = нижняя кромка переда/зада (стык с ними),
    # ширина = толщина боковины внизу (стык с боковинами). Размеры ВЫВЕДЕНЫ из деталей.
    length = g["bottom_len"]                       # шов с передом и задом
    width = fan                                    # шов с боковинами (= толщине низа)
    dnishe = Polygon([(0, 0), (length, 0), (length, width), (0, width)])
    c = (length / 2.0, width / 2.0)                # отверстие вентилятора по центру
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
