"""Отладчик детекта РУКИ по частям: вырезает машущую руку из силуэта и дробит её на
КИСТЬ / ПРЕДПЛЕЧЬЕ / ПЛЕЧО по линиям ЗАПЯСТЬЯ (детект кисти) и ЛОКТЯ (детект зон).
Рисует 3 части разными цветами поверх макета + линии реза + цвета зон (predplechie/plecho).
Запуск: venv/bin/python arm_parts.py <pdf> --config config_may.yaml -o out/arm_parts.png"""
import sys, os, argparse
import pymupdf, yaml, numpy as np
sys.path.insert(0, "src"); sys.path.insert(0, ".")
from sides import get_front_figure
from arm import (cut_waving_arms, _wrist_on_poly, cut_arm, _arm_zone_colors)
from shapely.geometry import Polygon
from PIL import Image, ImageDraw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf"); ap.add_argument("--config", default="config_new.yaml")
    ap.add_argument("-o", "--out", default="out/arm_parts.png")
    args = ap.parse_args(); os.makedirs("out", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    fig = get_front_figure(page, cfg); body = fig.contour_pt
    _, cut = cut_waving_arms(page, body, cfg, strict=False)           # настоящие руки (с цветовым фильтром)
    if not cut:
        print("руки не найдены"); return

    Z = 1.4; pix = page.get_pixmap(matrix=pymupdf.Matrix(Z, Z))
    im = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGB")
    dr = ImageDraw.Draw(im)
    TP = lambda p: (p[0] * Z, p[1] * Z)
    COL = {"kist": (0, 170, 0), "predplechie": (255, 140, 0), "plecho": (120, 0, 200)}

    allb = [1e9, 1e9, -1e9, -1e9]
    for a in cut:
        arm_poly = Polygon(a["poly_pt"]) if not hasattr(a["poly_pt"], "exterior") else a["poly_pt"]
        arr = np.asarray(arm_poly.exterior.coords[:-1], float)
        wrist, tip, _ = _wrist_on_poly(page, arr, a["tip"], cfg)
        hand_pts, rest_pts = cut_arm(arr, wrist, tip)                 # КИСТЬ | (предплечье+плечо)
        elbow, fore_c, sh_c = _arm_zone_colors(page, arm_poly, arr, wrist, tip, cfg)
        wmid = (np.asarray(wrist[0], float) + np.asarray(wrist[1], float)) / 2.0
        parts = {"kist": hand_pts}
        if elbow is not None:                                        # ЛОКОТЬ делит остаток
            fore_pts, upper_pts = cut_arm(rest_pts, (np.asarray(elbow[0]), np.asarray(elbow[1])), wmid)
            parts["predplechie"] = fore_pts; parts["plecho"] = upper_pts
        else:
            parts["predplechie"] = rest_pts
        # контуры частей
        for name, pts in parts.items():
            poly = [TP(p) for p in pts]
            if len(poly) >= 3:
                dr.line(poly + [poly[0]], fill=COL[name], width=4)
                cx = sum(p[0] for p in poly) / len(poly); cy = sum(p[1] for p in poly) / len(poly)
                dr.text((cx, cy), name.upper(), fill=COL[name])
        # линии реза
        dr.line([TP(wrist[0]), TP(wrist[1])], fill=(0, 0, 255), width=5)          # ЗАПЯСТЬЕ
        dr.text(TP(wrist[0]), "ZAPYASTIE", fill=(0, 0, 255))
        if elbow is not None:
            dr.line([TP(elbow[0]), TP(elbow[1])], fill=(200, 0, 0), width=5)       # ЛОКОТЬ
            dr.text(TP(elbow[0]), "LOKOT", fill=(200, 0, 0))
        b = arm_poly.bounds
        allb = [min(allb[0], b[0]), min(allb[1], b[1]), max(allb[2], b[2]), max(allb[3], b[3])]
        print(f"arm tip={tuple(round(v) for v in tip)}: fore_color(predplechie)={fore_c} sh_color(plecho)={sh_c}")

    # свотчи цветов зон внизу
    im.crop((int(allb[0] * Z) - 30, int(allb[1] * Z) - 30, int(allb[2] * Z) + 30, int(allb[3] * Z) + 30)).save(args.out)
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
