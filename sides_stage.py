"""
Этап «боковые части» (гассет).
ЭТАП A: детект днища + точки разреза (отчёт + sides_debug.png).
ЭТАП B: полосы-лекала + экспорт sides.pdf/.svg + превью.

Запуск: python sides_stage.py examples/arm_sep.pdf --config config.yaml
"""
import argparse, os, sys
import pymupdf, yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import (get_front_figure, contour_to_mm, build_gusset, build_strips, render_debug,  # noqa: E402
                   classify_sides, build_fitting_layout, render_fitting)
import export                                                                                 # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs("out", exist_ok=True)

    page = pymupdf.open(args.input_pdf)[0]
    fig = get_front_figure(page, cfg)
    contour_mm = contour_to_mm(fig)
    g = build_gusset(contour_mm, cfg)
    seg = g["segments"]
    total = sum(s["len"] for s in seg)

    print("=" * 60)
    print("БОКОВИНА (гассет)")
    print("=" * 60)
    print(f"перёд {fig.out_w_mm:.0f}x{fig.out_h_mm:.0f} мм | толщина: "
          f"1-я часть (оба конца)={cfg['fan_diameter_mm']:.0f} → переход → основная={cfg['main_width_mm']:.0f} мм")
    print(f"периметр={g['perimeter']:.0f}  днище={g['bottom_len']:.0f}  гассет={g['gusset_len']:.0f} мм")
    print(f"полос: {len(seg)} | длина×толщина:")
    for s in sorted(seg, key=lambda s: s["i"]):
        print(f"    #{s['i']}: {s['len']:.0f} × {s['thickness']:.0f} мм  (y_norm={s['y_norm']:.2f})")
    d1 = abs(g['gusset_len'] + g['bottom_len'] - g['perimeter'])
    d2 = abs(total - g['gusset_len'])
    print(f"ИНВАРИАНТ гассет+днище==периметр: Δ={d1:.2f} ({'OK' if d1 < 1 else 'FAIL'})")
    print(f"ИНВАРИАНТ Σполос==гассет:         Δ={d2:.2f} ({'OK' if d2 < 1 else 'FAIL'})")
    print(f"макс.полоса={max(s['len'] for s in seg):.0f} ≤ {cfg['side_max_len_mm']} "
          f"({'OK' if max(s['len'] for s in seg) <= cfg['side_max_len_mm'] + 1 else 'FAIL'})")

    # ЭТАП A: debug
    render_debug(contour_mm, g, "out/sides_debug.png")
    # ЭТАП B: полосы-лекала + экспорт
    strips, sw, sh = build_strips(g)
    export.save_sides_pdf(strips, sw, sh, "out/sides.pdf")
    export.save_sides_svg(strips, sw, sh, "out/sides.svg")
    export.save_png("out/sides.pdf", "out/sides_preview.png", target_px_h=1000)
    # «примерочная» раскладка: перёд по центру, боковины по сторонам, днище+ножка под передом
    from base import build_base
    classify_sides(g, contour_mm)
    b = build_base(g, cfg)
    lay = build_fitting_layout(contour_mm, g, base=b)
    nL = sum(1 for s in g["segments"] if s["side"] == "left")
    render_fitting(lay, "out/sides_fitting.png")
    export.save_fitting_svg(lay, "out/sides_fitting.svg")
    print(f"боковин: слева {nL}, справа {len(g['segments']) - nL}")
    print(f"лист полос: {sw:.0f}x{sh:.0f} мм | примерочный лист: {lay['sheet_w']:.0f}x{lay['sheet_h']:.0f} мм")
    print("файлы: out/sides_debug.png | out/sides.pdf/.svg | out/sides_fitting.png")


if __name__ == "__main__":
    main()
