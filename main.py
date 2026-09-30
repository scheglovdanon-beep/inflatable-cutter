"""
Раскрой надувной фигуры — точка входа.
ЭТАП: масштаб 2700 мм, один замкнутый контур, обтравка макета по контуру (PowerClip)
для ОБЕИХ фигур. Выход: front.pdf / back.pdf (+ .svg) и PNG-превью.

Запуск: python main.py examples/cosmonaut_2700.pdf --config config.yaml
"""
import argparse
import os
import sys

import pymupdf
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from read_pdf import split_figures                       # noqa: E402
from pieces import build_figure                           # noqa: E402
import export                                             # noqa: E402

# левая фигура -> перёд, правая -> зад
SIDES = [("left", "front"), ("right", "back")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    target = cfg["target_height_mm"]
    gap = cfg["contour_gap_close_mm"]
    os.makedirs("out", exist_ok=True)

    doc = pymupdf.open(args.input_pdf)
    page = doc[0]
    R = page.rect
    figs, n_draw, n_img = split_figures(page, R.width)

    # вектор, а не растр
    if not figs["left"] and not figs["right"]:
        print("СТОП: не найдено векторных фигур (возможно, растр). images:", n_img)
        sys.exit(2)

    print("=" * 64)
    print(f"Вход: {os.path.basename(args.input_pdf)}  |  лист {R.width:.0f}x{R.height:.0f} pt")
    print(f"Вектор: путей {n_draw}, растровых картинок {n_img}")
    print(f"Фигур: слева {len(figs['left'])} объектов, справа {len(figs['right'])}")
    print("=" * 64)

    all_ok = True
    for side, name in SIDES:
        fig = build_figure(side, figs[side], R.height, target, gap)
        export.save_pdf(fig, f"out/{name}.pdf")
        export.save_svg(fig, f"out/{name}.svg")
        export.save_png(f"out/{name}.pdf", f"out/{name}_preview.png")

        h_ok = abs(fig.out_h_mm - target) <= 1.0
        parts_ok = fig.n_parts == 1
        inside_ok = fig.outside_mm2 < 1.0          # макет практически не выходит за контур
        ok = h_ok and parts_ok and fig.is_valid and inside_ok
        all_ok &= ok
        print(f"[{name}]  {fig.out_w_mm:.0f} x {fig.out_h_mm:.0f} мм")
        print(f"    контур: частей={fig.n_parts} (нужно 1) | valid={fig.is_valid} | "
              f"замкнут=True")
        print(f"    высота=2700±1: {h_ok} | макет вне контура: {fig.outside_mm2:.2f} мм² ({'ok' if inside_ok else 'ПРЕВЫШЕНО'})")
        print(f"    файлы: out/{name}.pdf, out/{name}.svg, out/{name}_preview.png")
        print(f"    ИТОГ: {'OK' if ok else 'ЕСТЬ ПРОБЛЕМЫ'}")
        print("-" * 64)

    print("ОБЩИЙ ИТОГ:", "OK — обе фигуры готовы" if all_ok else "ЕСТЬ ПРОБЛЕМЫ (см. выше)")


if __name__ == "__main__":
    main()
