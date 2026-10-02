"""
ИЗОЛИРОВАННЫЙ отладчик ДЕТЕКТА КИСТИ (запястья) на вырезанной руке.
Рисует: контур руки (PT) + текстура макета, линию реза ЗАПЯСТЬЯ (B–T, красная),
tip (кончик кисти, синий), КИСТЬ (зелёная заливка) vs ПРЕДПЛЕЧЬЕ (синяя заливка).
Печатает метод, положение запястья вдоль руки (доля от tip), длины рёбер.

Запуск: venv/bin/python hand_detect.py <pdf> --config config_X.yaml -o out/X_hand.png
"""
import argparse, os, sys, math
import pymupdf, yaml, numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import get_front_figure, front_drawings, PT2MM          # noqa: E402
from side_color import _flat                                        # noqa: E402
from arm import detect_armpits, _wrist_on_poly, cut_arm, _wrist_by_width   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf"); ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-o", "--out", default="out/hand.png")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]
    fig = get_front_figure(page, cfg)
    arms = detect_armpits(fig.contour_pt, cfg)
    print(f"== {os.path.basename(args.input_pdf)} == рук: {len(arms)}")

    # глобальный bbox всех рук (в PT) для общего холста
    allpts = np.vstack([np.asarray(a["poly"].exterior.coords) for a in arms])
    bx0, by0 = allpts[:, 0].min(), allpts[:, 1].min()
    bx1, by1 = allpts[:, 0].max(), allpts[:, 1].max()
    Z, M = 900.0 / max(bx1 - bx0, by1 - by0), 60
    W = int((bx1 - bx0) * Z) + 2 * M + 600
    H = int((by1 - by0) * Z) + 2 * M
    im = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(im)
    T = lambda x, y: (M + (x - bx0) * Z, M + (y - by0) * Z)
    col = lambda c: tuple(int(255 * v) for v in c) if c else None

    for i, a in enumerate(arms):
        arm_poly = a["poly"]; tip = a["tip"]
        arm_arr = np.asarray(arm_poly.exterior.coords[:-1], float)
        wrist, tip_used, method = _wrist_on_poly(page, arm_arr, tip, cfg)
        hand_pts, fore_pts = cut_arm(arm_arr, wrist, tip_used)

        # текстура руки (заливки макета в bbox руки), клип по контуру руки
        ab = arm_poly.bounds
        inbb = lambda r: r.x0 >= ab[0] - 2 and r.x1 <= ab[2] + 2 and r.y0 >= ab[1] - 2 and r.y1 <= ab[3] + 2
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).polygon([T(x, y) for x, y in arm_poly.exterior.coords], fill=255)
        layer = Image.new("RGB", im.size, (255, 255, 255)); ld = ImageDraw.Draw(layer)
        for dd in page.get_drawings():
            if (dd.get("fill") is None and dd.get("color") is None) or not inbb(dd["rect"]):
                continue
            f = col(dd.get("fill")); s = col(dd.get("color"))
            for sub in _flat(dd):
                if len(sub) < 2:
                    continue
                pts = [T(x, y) for x, y in sub]
                if f is not None and len(pts) >= 3:
                    ld.polygon(pts, fill=f)
                if s is not None:
                    ld.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=s, width=1)
        im.paste(layer, (0, 0), mask)

        # кисть (зелёный полупрозрачный) и предплечье (синий) — заливка контуров
        for pts_arr, c in ((hand_pts, (0, 200, 0)), (fore_pts, (0, 80, 220))):
            ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
            ImageDraw.Draw(ov).polygon([T(x, y) for x, y in pts_arr], fill=c + (70,))
            im.paste(Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB"), (0, 0))
        dr = ImageDraw.Draw(im)
        # контур руки
        dr.line([T(x, y) for x, y in arm_poly.exterior.coords], fill=(0, 0, 0), width=2)
        # линия ЗАПЯСТЬЯ B–T (красная, толстая) + точки
        B, Tt = wrist[0], wrist[1]
        dr.line([T(*B), T(*Tt)], fill=(220, 0, 0), width=4)
        for p in (B, Tt):
            x, y = T(*p); dr.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(220, 0, 0))
        # АЛЬТЕРНАТИВА: запястье по профилю ширины (зелёная линия)
        ww = _wrist_by_width(arm_poly, tip_used, a["f"], cfg)
        if ww is not None:
            dr.line([T(*ww[0]), T(*ww[1])], fill=(0, 170, 0), width=4)
            for p in ww:
                x, y = T(*p); dr.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(0, 170, 0))
            dr.text((T(*ww[1])[0] + 6, T(*ww[1])[1]), "width", fill=(0, 140, 0))
        # tip (кончик кисти) — синий
        x, y = T(*tip_used); dr.ellipse([x - 7, y - 7, x + 7, y + 7], outline=(0, 0, 220), width=3)
        dr.text((x + 8, y - 6), "tip", fill=(0, 0, 220))

        # метрики
        tipv = np.asarray(tip_used, float)
        wmid = (np.asarray(B, float) + np.asarray(Tt, float)) / 2.0
        arm_len = a["len"]
        wrist_from_tip = float(np.hypot(*(wmid - tipv)))
        straight = float(np.hypot(*(np.asarray(B, float) - np.asarray(Tt, float))))
        s = fig.s * PT2MM
        txt = [f"РУКА #{i}", f"метод запястья: {method}",
               f"запястье от tip: {wrist_from_tip:.0f} pt = {wrist_from_tip*s:.0f} мм",
               f"длина руки (tip..подмышка): {arm_len:.0f} pt = {arm_len*s:.0f} мм",
               f"доля кисти (wrist_from_tip/arm_len): {100*wrist_from_tip/max(arm_len,1):.0f}%",
               f"ребро запястья (straight): {straight:.0f} pt = {straight*s:.0f} мм"]
        tx = M + (bx1 - bx0) * Z + 20
        for k, line in enumerate(txt):
            dr.text((tx, M + i * 160 + k * 20), line, fill=(0, 0, 0))
        print("  " + " | ".join(txt))

    im.save(args.out)
    print(f"saved {args.out} {im.size}")


if __name__ == "__main__":
    main()
