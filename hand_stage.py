"""
Этап «кисть»: детект запястья -> рез руки -> деталь кисти (перёд+зеркальный зад+боковина).
Воспроизводимо от PDF. См. TZ_hand.md.

Запуск: python hand_stage.py examples/arm_sep.pdf --config config.yaml
"""
import argparse, os, sys
import pymupdf, yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import get_front_figure, get_arm_contour, PT2MM                  # noqa: E402
from arm import detect_wrist, cut_arm, build_hand, render_hand             # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs("out", exist_ok=True)

    page = pymupdf.open(args.input_pdf)[0]
    fig = get_front_figure(page, cfg)
    scale = fig.s * PT2MM                                                     # pt -> финальные мм
    arm_pts, body_x = get_arm_contour(page)

    wr = detect_wrist(arm_pts.tolist(), body_x=body_x)
    if not wr:
        print("запястье не найдено"); return
    hand_pts, fore_pts = cut_arm(arm_pts, wr["wrist"], wr["tip"])
    h = build_hand(hand_pts, fore_pts, wr["wrist"], scale)

    print("=" * 60)
    print("КИСТЬ (деталь «печенье»: перёд + зеркальный зад + боковина)")
    print("=" * 60)
    print(f"масштаб pt->мм: {scale:.4f}")
    print(f"ровное ребро кисти (срез запястья): {h['straight']:.1f} мм")
    print(f"кривая кромка кисти (= высота боковины H): {h['H']:.1f} мм")
    print(f"верхнее ребро предплечья: {h['fore_top']:.1f} мм (нижнее: {h['fore_bot']:.1f})")
    print(f"ШИРИНА боковины W = (верх − 2·ровн)/2 = ({h['fore_top']:.1f} − 2·{h['straight']:.1f})/2 "
          f"= {h['W']:.1f} мм")
    d = abs(h["opening"] - h["fore_top"])
    print(f"ИНВАРИАНТ открытый край кисти == верх.ребро предпл: "
          f"{h['opening']:.1f} == {h['fore_top']:.1f}  Δ={d:.2f} ({'OK' if d < 1 else 'FAIL'})")
    gl = h["gusset"].bounds
    gH = gl[3] - gl[1]
    d2 = abs(gH - h["H"])
    print(f"ИНВАРИАНТ длинное ребро боковины == кривая кромка: "
          f"{gH:.1f} == {h['H']:.1f}  Δ={d2:.2f} ({'OK' if d2 < 1 else 'FAIL'})")

    render_hand(h, "out/hand_piece.png")
    print("файл: out/hand_piece.png")


if __name__ == "__main__":
    main()
