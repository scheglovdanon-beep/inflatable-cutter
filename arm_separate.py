"""
ИЗОЛИРОВАННАЯ отладка САМОЙ сложной части: детект машущей руки и ЧИСТОЕ отделение её от
переда. БЕЗ боковин/днища/зада/рулона/отверстий.

Для одного PDF:
 1) силуэт переда: front_drawings(page) -> build_figure -> fig.contour_pt (фон убран _drop_background);
 2) detect_armpits(body_pt, cfg) + cut_across_arm -> список отрезанных рук (PT) и ТЕЛО БЕЗ РУКИ;
 3) ОДИН PNG: СЛЕВА — контур переда БЕЗ руки + полупрозрачная текстура макета (видно, что рука
    удалена), красная линия реза + точка подмышки; СПРАВА — каждая рука отдельным контуром + текстура;
 4) печать: число рук, площадь тела до/после, угол лезвия к оси руки (~90°).

Запуск:  venv/bin/python arm_separate.py <pdf> --config config_X.yaml -o out/X_arm_sep.png
"""
import argparse, os, sys, math
import pymupdf, yaml, numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import LineString
from shapely.ops import split, unary_union

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import front_drawings, get_front_figure          # noqa: E402
from side_color import _flat                                 # noqa: E402
from arm import (detect_armpits, cut_across_arm, local_arm_thickness,     # noqa: E402
                 local_body_halfwidth, _poly_width, blade_perp)


def _blade_endpoints(body, f, tip, cfg, blade=None):
    """Повторяет геометрию лезвия из cut_across_arm — для отрисовки и замера угла.
    Если передан явный blade (стратегия «пара подмышек») — используем его концы."""
    f = np.asarray(f, float); tip = np.asarray(tip, float)
    u = tip - f; nu = np.hypot(*u) or 1.0; u = u / nu
    if blade is not None:
        p0, p1 = blade
    else:
        p0, p1 = blade_perp(body, f, tip, cfg)
    # угол лезвия к оси руки
    bv = np.asarray(p1) - np.asarray(p0); bv = bv / (np.hypot(*bv) or 1.0)
    ang = math.degrees(math.acos(min(1.0, abs(float(bv @ u)))))     # угол к оси
    blade_to_axis = 90.0 - ang                                      # ~90° если перпендикуляр
    return p0, p1, abs(90.0 - abs(90.0 - (90.0 - blade_to_axis)))  # см. печать ниже


def _draw_texture(dr, dds, tf, col, alpha_img=None, width_scale=1.0):
    """Нарисовать векторные заливки макета (dds) через tf(pt)->px."""
    C = lambda c: tuple(int(255 * v) for v in c) if c else None
    for dd in dds:
        f = C(dd.get("fill")); s = C(dd.get("color"))
        for sub in _flat(dd):
            if len(sub) < 2:
                continue
            pts = [tf(x, y) for x, y in sub]
            if f is not None and len(pts) >= 3:
                dr.polygon(pts, fill=f)
            if s is not None:
                dr.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=s,
                        width=max(1, int(dd.get("width", .5) * width_scale)))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-o", "--out", default="out/arm_sep.png")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]

    # 1) силуэт переда (PT, y вниз). фон уже убран в front_drawings/_drop_background
    fig = get_front_figure(page, cfg)
    body0 = fig.contour_pt
    area0 = body0.area

    # 2) детект + рез
    arms = detect_armpits(body0, cfg)
    cut_arms = []           # {poly, f, tip, blade}
    body = body0
    for a in arms:
        blade0, blade1, _ = _blade_endpoints(body, a["f"], a["tip"], cfg, a.get("blade"))
        arm_poly, body = cut_across_arm(body, a["f"], a["tip"], cfg, blade=a.get("blade"))
        if arm_poly is None:
            print(f"  ВНИМАНИЕ: рез не состоялся для f={a['f']}")
            continue
        cut_arms.append({"poly": arm_poly, "f": a["f"], "tip": a["tip"],
                         "blade": (blade0, blade1)})
    area1 = body.area

    # --- печать метрик ---
    print(f"== {os.path.basename(args.input_pdf)} (config {os.path.basename(args.config)}) ==")
    print(f"   найдено рук: {len(cut_arms)} (expected_arms={cfg.get('expected_arms','-')})")
    print(f"   площадь тела: до={area0:.0f}  после={area1:.0f}  Δ={area0-area1:.0f} "
          f"({100*(area0-area1)/area0:.1f}%)")
    sum_arm = sum(a["poly"].area for a in cut_arms)
    print(f"   суммарная площадь отрезанных рук: {sum_arm:.0f}")
    for i, a in enumerate(cut_arms):
        f = np.asarray(a["f"], float); tip = np.asarray(a["tip"], float)
        u = tip - f; u = u / (np.hypot(*u) or 1.0)
        (b0, b1) = a["blade"]
        bv = np.asarray(b1) - np.asarray(b0); bv = bv / (np.hypot(*bv) or 1.0)
        ang = math.degrees(math.acos(min(1.0, abs(float(bv @ u)))))
        print(f"   рука#{i}: f=({f[0]:.0f},{f[1]:.0f}) tip=({tip[0]:.0f},{tip[1]:.0f}) "
              f"armA={a['poly'].area:.0f} ({100*a['poly'].area/area0:.1f}%) "
              f"угол лезвия к оси руки={ang:.1f}° (идеал 90°)")

    # 3) рендер PNG: слева перёд-без-руки + текстура; справа руки + текстура
    fr_dds, _ = front_drawings(page, cfg)
    # bbox всей исходной фигуры (для текстуры переда)
    bx0, by0, bx1, by1 = body0.bounds
    pad = 40.0
    panel_w = (bx1 - bx0) + 2 * pad
    panel_h = (by1 - by0) + 2 * pad
    # масштаб рендера (вписать по высоте)
    TARGET_H = 1100
    sc = TARGET_H / panel_h
    gapx = 60
    # ширина правой зоны = макс. ширина руки * n (столбиком) — считаем по bbox рук
    right_w = 0
    for a in cut_arms:
        ax0, ay0, ax1, ay1 = a["poly"].bounds
        right_w = max(right_w, (ax1 - ax0) + 2 * pad)
    Wpx = int(panel_w * sc) + gapx + int(right_w * sc) + gapx
    Hpx = int(panel_h * sc) + 40
    im = Image.new("RGB", (Wpx, Hpx), (255, 255, 255))
    dr = ImageDraw.Draw(im, "RGBA")

    # --- ЛЕВО: текстура переда (полупрозрачно) обрезанная по body (без руки) ---
    L = lambda x, y: (int((x - bx0 + pad) * sc), int((y - by0 + pad) * sc) + 20)
    # текстура на отдельном слое -> приглушим альфой, затем вставим под маской тела-без-руки
    tex = Image.new("RGBA", im.size, (255, 255, 255, 0)); td = ImageDraw.Draw(tex)
    _draw_texture(td, fr_dds, L, None, width_scale=max(0.5, 0.3 * sc))
    # маска = тело БЕЗ руки
    bmask = Image.new("L", im.size, 0)
    ImageDraw.Draw(bmask).polygon([L(x, y) for x, y in body.exterior.coords], fill=130)  # полупрозрачно
    im.paste(Image.new("RGB", im.size, (255, 255, 255)), (0, 0))   # фон
    # наложить текстуру под маской тела (видно только внутри тела-без-руки)
    tex_rgb = Image.new("RGB", im.size, (255, 255, 255)); tex_rgb.paste(tex, (0, 0), tex)
    im.paste(tex_rgb, (0, 0), bmask)
    dr = ImageDraw.Draw(im, "RGBA")
    # контур тела БЕЗ руки — толстая чёрная линия (замкнутый силуэт)
    dr.line([L(x, y) for x, y in body.exterior.coords], fill=(0, 0, 0), width=max(2, int(2)))
    # линии реза (красные) + точки подмышки
    for a in cut_arms:
        (b0, b1) = a["blade"]
        dr.line([L(*b0), L(*b1)], fill=(220, 0, 0), width=max(3, int(3)))
        fx, fy = L(*a["f"]); r = 8
        dr.ellipse([fx - r, fy - r, fx + r, fy + r], fill=(255, 0, 0), outline=(120, 0, 0))
        tx, ty = L(*a["tip"]); dr.ellipse([tx - 5, ty - 5, tx + 5, ty + 5], outline=(0, 120, 220), width=3)
        dr.line([L(*a["f"]), L(*a["tip"])], fill=(0, 120, 220, 160), width=2)   # ось руки (синяя)
    dr.text((L(bx0, by0)[0], 2), "PERED BEZ RUKI (tekstura = tela net ruki)", fill=(0, 0, 0))

    # --- ПРАВО: каждая рука отдельным контуром + её текстура ---
    rx0 = int(panel_w * sc) + gapx
    ry = 40
    for i, a in enumerate(cut_arms):
        ap = a["poly"]
        ax0, ay0, ax1, ay1 = ap.bounds
        R = lambda x, y, ax0=ax0, ay0=ay0, ry=ry: (int(rx0 + (x - ax0 + pad) * sc),
                                                    int(ry + (y - ay0 + pad) * sc))
        # текстура руки: заливки макета в bbox руки
        adds = [dd for dd in fr_dds
                if dd["rect"].x0 >= ax0 - 3 and dd["rect"].x1 <= ax1 + 3
                and dd["rect"].y0 >= ay0 - 3 and dd["rect"].y1 <= ay1 + 3]
        amask = Image.new("L", im.size, 0)
        ImageDraw.Draw(amask).polygon([R(x, y) for x, y in ap.exterior.coords], fill=255)
        layer = Image.new("RGB", im.size, (255, 255, 255)); ld = ImageDraw.Draw(layer)
        _draw_texture(ld, adds, R, None, width_scale=max(0.5, 0.3 * sc))
        im.paste(layer, (0, 0), amask)
        dr = ImageDraw.Draw(im, "RGBA")
        dr.line([R(x, y) for x, y in ap.exterior.coords], fill=(0, 150, 90), width=max(2, 2))
        # линия реза на руке (красная)
        (b0, b1) = a["blade"]
        dr.line([R(*b0), R(*b1)], fill=(220, 0, 0), width=2)
        dr.text((rx0, ry - 2), f"RUKA #{i}", fill=(0, 120, 60))
        ry += int((ay1 - ay0 + 2 * pad) * sc) + 30

    im.save(args.out)
    print(f"   saved {args.out} {im.size}")


if __name__ == "__main__":
    main()
