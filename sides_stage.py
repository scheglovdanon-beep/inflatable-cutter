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
    from sides import smooth_front
    contour_mm = smooth_front(contour_to_mm(fig), cfg)
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
    from sides import render_sew_map, detect_neck, render_neck, detect_arm_side, place_arm_hole
    neck = detect_neck(contour_mm, g, cfg)
    arm_side = detect_arm_side(page, cfg)
    place_arm_hole(lay, neck, arm_side, cfg)                       # круг-отверстие под руку
    from arm import build_arm, place_arm, detect_wrist, cut_arm, build_hand, place_hand
    from sides import get_arm_contour, PT2MM
    arm_pieces = build_arm(cfg)
    place_arm(lay, arm_pieces, arm_side)                          # 3 детали руки на стороне руки
    # кисть: детект запястья -> рез -> деталь «печенье», ряд над колонкой руки
    arm_pts, body_x = get_arm_contour(page)
    wr = detect_wrist(arm_pts.tolist(), body_x=body_x)
    hand = None
    if wr:
        hand_pts, fore_pts = cut_arm(arm_pts, wr["wrist"], wr["tip"])
        hand = build_hand(hand_pts, fore_pts, wr["wrist"], fig.s * PT2MM)
        place_hand(lay, hand, arm_side)                           # лево -> боковина -> право, над рукой
    # раскраска боковин под стыковку с передом (цвет = макет на кромке, обводка перешагнута)
    from side_color import color_sides
    from sides import front_drawings
    color_sides(lay, g, contour_mm, fig, page, front_drawings, cfg)
    from seam import add_seams
    add_seams(lay, cfg.get("seam_allowance_mm", 10))              # припуск +Nмм на каждое лекало
    render_fitting(lay, "out/sides_fitting.png")
    export.save_fitting_svg(lay, "out/sides_fitting.svg")
    render_sew_map(contour_mm, g, "out/sew_map.png")
    render_neck(contour_mm, g, neck, lay, "out/neck_debug.png")
    if neck.get("found"):
        print(f"шея (корпус->голова): провал {neck['drop']*100:.0f}%; переходные детали: "
              f"лево #{neck['seg_left']}, право #{neck['seg_right']}")
    ah = lay.get("arm_hole")
    print(f"рука: сторона {arm_side}" + (f", отверстие Ø{ah['r']*2:.0f} на детали #{ah['piece']}" if ah else ""))
    if hand:
        print(f"кисть: боковина {hand['W']:.0f}x{hand['H']:.0f} мм; открытый край {hand['opening']:.0f} "
              f"== верх.ребро предпл {hand['fore_top']:.0f} мм")
    print(f"боковин: слева {nL}, справа {len(g['segments']) - nL}")
    print(f"лист полос: {sw:.0f}x{sh:.0f} мм | примерочный лист: {lay['sheet_w']:.0f}x{lay['sheet_h']:.0f} мм")
    print("файлы: out/sides_debug.png | out/sides.pdf/.svg | out/sides_fitting.png")


if __name__ == "__main__":
    main()
