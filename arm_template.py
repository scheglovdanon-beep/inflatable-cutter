"""
ИЗОЛИРОВАННЫЙ отладчик: РУКА ПО ШАБЛОНУ (как у космонавта/chiro), но ИСТОЧНИК —
НАШ вырезанный полигон руки (detect_armpits), а НЕ плавающий get_arm_contour.

Для одного PDF, для каждой руки (A/B/D/E — 1, C — 2):
 1) detect_armpits(body_pt) -> полигон руки (PT), tip (кончик кисти);
 2) ЗАПЯСТЬЕ: detect_wrist_color (светлая перчатка↔рукав) ИЛИ convexity detect_wrist;
    cut_arm -> (кисть, предплечье);
 3) развернуть ПРЕДПЛЕЧЬЕ и ПЛЕЧО в усечённые конусы по реальным сечениям рукава
    (скелет вдоль оси + distance transform = радиусы на запястье/локте/плече);
    БАЗА = прямоугольный переход окружность-плеча ↔ Ø250 (периметр = π·250);
 4) КИСТЬ build_hand(hand, forearm, wrist, scale) — сама даёт боковину W так, что
    opening == верхнее ребро предплечья (инвариант стыковки);
 5) ОДИН PNG out/<X>_arm_tpl.png: рядом подписанные детали BAZA(с Ø250)|PLECHO|
    PREDPLECHIE|KIST(перёд|зад|боковина) + длины стыкуемых рёбер и расхождения.

Переиспользует механику src/arm.py (build_hand, _develop_frustum, cut_arm,
detect_wrist/detect_wrist_color, measure_edges). Новое — build_arm_from_poly()
в src/arm.py (разворот конусов из НАШЕГО полигона руки). Старые пути не трогаются.

Запуск:  venv/bin/python arm_template.py <pdf> --config config_X.yaml -o out/X_arm_tpl.png
"""
import argparse, os, sys, math
import pymupdf, yaml, numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import get_front_figure, PT2MM            # noqa: E402
from arm import (detect_armpits, build_hand, build_arm_from_poly)  # noqa: E402


def _poly_xy(poly):
    return [(float(x), float(y)) for x, y in poly.exterior.coords]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-o", "--out", default="out/arm_tpl.png")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]

    fig = get_front_figure(page, cfg)
    body0 = fig.contour_pt
    scale = fig.s * PT2MM                      # PT исходника -> мм финала

    arms = detect_armpits(body0, cfg)
    print(f"== {os.path.basename(args.input_pdf)} (config {os.path.basename(args.config)}) ==")
    print(f"   target_height={cfg['target_height_mm']}mm  scale(pt->mm)={scale:.4f}  "
          f"Ø_hole={cfg.get('arm_hole_diameter_mm',250)}mm")
    print(f"   найдено рук: {len(arms)}")

    built = []       # по руке: dict(name, pieces, hand, diag)
    for i, a in enumerate(arms):
        print(f"\n   --- РУКА #{i}  f=({a['f'][0]:.0f},{a['f'][1]:.0f}) "
              f"tip=({a['tip'][0]:.0f},{a['tip'][1]:.0f}) side={a.get('side')} ---")
        try:
            res = build_arm_from_poly(page, a["poly"], a["tip"], scale, cfg)
        except Exception as e:
            print(f"   ! ошибка построения руки #{i}: {e}")
            import traceback; traceback.print_exc()
            continue
        pieces = res["pieces"]; hand = res["hand"]; d = res["diag"]
        hole_c = math.pi * cfg.get("arm_hole_diameter_mm", 250)
        print(f"   запястье(straight)={hand['straight']:.0f}  кисть H(curved)={hand['H']:.0f}  "
              f"W боковина={hand['W']:.0f}")
        print(f"   СТЫКОВКА (мм):")
        print(f"     база.низ = π·Ø250            = {hole_c:.0f}   (ребро под отверстие)")
        print(f"     база.верх ↔ плечо.низ        = {d['base_top']:.0f} ↔ {d['sh_bottom']:.0f}  "
              f"Δ={abs(d['base_top']-d['sh_bottom']):.0f} ({100*abs(d['base_top']-d['sh_bottom'])/max(d['base_top'],1):.1f}%)")
        print(f"     плечо.верх(локоть) ↔ предпл.низ = {d['sh_top']:.0f} ↔ {d['fore_bottom']:.0f}  "
              f"Δ={abs(d['sh_top']-d['fore_bottom']):.0f} ({100*abs(d['sh_top']-d['fore_bottom'])/max(d['sh_top'],1):.1f}%)")
        print(f"     предпл.верх(запястье) ↔ кисть.opening = {d['fore_top']:.0f} ↔ {hand['opening']:.0f}  "
              f"Δ={abs(d['fore_top']-hand['opening']):.0f} ({100*abs(d['fore_top']-hand['opening'])/max(d['fore_top'],1):.1f}%)")
        print(f"     [wrist метод: {d['wrist_method']}]")
        built.append({"name": f"RUKA#{i}", "pieces": pieces, "hand": hand, "diag": d})

    if not built:
        print("   нет построенных рук — PNG не создаётся")
        return

    render(built, cfg, args.out)


# ---------------------------------------------------------------------------
#  Рендер: по одной руке — строка деталей BAZA | PLECHO | PREDPLECHIE | KIST(3)
# ---------------------------------------------------------------------------
def render(built, cfg, out_png, target_w=2000, margin=60, gap=90):
    hole_d = cfg.get("arm_hole_diameter_mm", 250)
    # собрать все детали по рукам как (label, poly, color, extra_circle?)
    rows = []
    for b in built:
        pm = {p["name"]: p for p in b["pieces"]}
        h = b["hand"]
        items = [
            ("BAZA", pm["baza"]["poly"], (190, 60, 0), hole_d),
            ("PLECHO", pm["plecho"]["poly"], (0, 140, 70), None),
            ("PREDPLECHIE", pm["predplechie"]["poly"], (30, 90, 220), None),
            ("KIST pered", h["front"], (150, 0, 150), None),
            ("KIST zad", h["back"], (150, 0, 150), None),
            ("KIST bok", h["gusset"], (200, 60, 0), None),
        ]
        rows.append((b["name"], items, b["diag"], h))

    # нормализуем каждую деталь к (0,0), считаем общий масштаб по суммарной ширине
    def norm(poly):
        b = poly.bounds
        from shapely import affinity
        return affinity.translate(poly, -b[0], -b[1])

    row_dims = []
    for name, items, diag, h in rows:
        ws = []; hmax = 0
        normed = []
        for lbl, poly, col, circ in items:
            p = norm(poly); b = p.bounds
            normed.append((lbl, p, col, circ, b[2], b[3]))
            ws.append(b[2]); hmax = max(hmax, b[3])
        row_w = sum(ws) + gap * (len(items) - 1)
        row_dims.append((name, normed, row_w, hmax, diag, h))

    content_w = max(rw for _, _, rw, _, _, _ in row_dims)
    sc = (target_w - 2 * margin) / content_w
    label_h = 80
    total_h = margin
    for _, _, _, hmax, _, _ in row_dims:
        total_h += int(hmax * sc) + label_h + gap
    total_h += margin
    W = target_w
    im = Image.new("RGB", (W, int(total_h)), (255, 255, 255))
    dr = ImageDraw.Draw(im)

    y = margin
    for name, normed, row_w, hmax, diag, h in row_dims:
        dr.text((margin, y), name, fill=(0, 0, 0))
        y += 22
        x = margin
        base_y = y + label_h
        for lbl, p, col, circ, pw, ph in normed:
            T = lambda px, py, x=x, base_y=base_y: (x + px * sc, base_y + py * sc)
            pts = [T(px, py) for px, py in _poly_xy(p)]
            dr.line(pts, fill=col, width=2)
            # подпись детали + её ширина/высота
            dr.text((x, base_y - 20), f"{lbl}", fill=col)
            dr.text((x, base_y + ph * sc + 4), f"w={pw:.0f} h={ph:.0f}", fill=(90, 90, 90))
            # круг Ø250 на базе (периметр нижнего ребра)
            if circ is not None:
                cx = x + pw * sc / 2.0
                cy = base_y + ph * sc + 60
                r = circ / 2.0 * sc
                dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(190, 60, 0), width=2)
                dr.text((cx - 30, cy - 6), f"Ø{circ:.0f}", fill=(190, 60, 0))
            x += pw * sc + gap * sc
        # численная стыковка строкой под рядом
        hole_c = math.pi * hole_d
        info = (f"stykovka: baza.niz=pi*{hole_d:.0f}={hole_c:.0f} | "
                f"baza.verh={diag['base_top']:.0f}<->plecho.niz={diag['sh_bottom']:.0f} | "
                f"plecho.verh={diag['sh_top']:.0f}<->predpl.niz={diag['fore_bottom']:.0f} | "
                f"predpl.verh={diag['fore_top']:.0f}<->kist.open={h['opening']:.0f}")
        dr.text((margin, base_y + hmax * sc + 30), info, fill=(0, 0, 0))
        y = base_y + int(hmax * sc) + label_h + gap

    im.save(out_png)
    print(f"\n   saved {out_png} {im.size}")


if __name__ == "__main__":
    main()
