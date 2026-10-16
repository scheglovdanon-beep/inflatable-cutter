#!/usr/bin/env python3
"""ДОП-ИНСТРУМЕНТ: загрузка УЖЕ ГОТОВОЙ раскладки лекал (CDR/SVG/PDF) -> выкладка её деталей
в печатный РУЛОН 1440 + инструкция. НИЧЕГО не строим из макета и НЕ перерисовываем: детали
копируются как есть (вектор+текстура) через SVG <use>+clip+transform. Повторно используем
НАШ код печати: export_pdf_grouped._rollpack_svg (укладка nest_true_shape + копия трансформом +
чекер полноты). Полезно когда правки (напр. цвета гассета) внесены прямо в готовый файл.

Выход: out.svg (+ out.pdf для Corel, + превью PNG). Кадр работы — PDF-мм (y вниз), как parse_details.
"""
import sys, os, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.dirname(__file__))
import pymupdf
from shapely.geometry import Polygon
from shapely.ops import unary_union
from nest_import import parse_details, dedupe_details
from export_pdf_grouped import _rollpack_svg

PT2MM = 25.4 / 72.0


def _svg_content_and_frame(pdf_path):
    """Содержимое готового файла как SVG (вектор+растр-текстура) + сдвиги кадра get_svg_image.
    Возвращает (inner_svg, DX, DY, bbox_pt). Кадр svg: x_svg=x_pdf+DX, y_svg=DY-y_pdf."""
    page = pymupdf.open(pdf_path)[0]
    dr = page.get_drawings()
    x0 = min(d["rect"].x0 for d in dr); y0 = min(d["rect"].y0 for d in dr)
    x1 = max(d["rect"].x1 for d in dr); y1 = max(d["rect"].y1 for d in dr)
    page.set_mediabox(pymupdf.Rect(x0, y0, x1, y1))
    svg = page.get_svg_image()
    inner = svg[svg.find(">", svg.find("<svg")) + 1: svg.rfind("</svg>")]
    return inner, -x0, y1, (x0, y0, x1, y1)


def _path_d(poly):
    return "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in poly.exterior.coords) + " Z"


def build_import_layout(pdf_path, out_svg, roll_h=1440.0, gap=20.0, min_area_mm2=800.0, inst_w=300.0):
    """Готовый файл -> рулон (копии деталей) + инструкция. Возвращает (roll_w_mm, n_details)."""
    polys = dedupe_details(parse_details(pdf_path, min_area_mm2=min_area_mm2))
    inner, DX, DY, _bb = _svg_content_and_frame(pdf_path)

    # #content: содержимое готового файла, приведённое из кадра svg (pt) в кадр PDF-мм (y вниз),
    # где живут контуры parse_details -> клипы и укладка в одном кадре.
    a = PT2MM; d = -PT2MM; e = -PT2MM * DX; f = PT2MM * DY
    content = (f'<g id="content"><g transform="matrix({a:.6f},0,0,{d:.6f},{e:.3f},{f:.3f})">'
               + inner + '</g></g>')

    clips, parts, pool = [], [], {}
    for i, poly in enumerate(polys):
        nm = f"detal_{i}"
        clips.append(f'<clipPath id="c{i}"><path d="{_path_d(poly)}"/></clipPath>')
        parts.append((nm, [f'<use xlink:href="#content" clip-path="url(#c{i})"/>']))
        pool[nm] = [poly]

    # ИНСТРУКЦИЯ: уменьшенная КОПИЯ всех деталей (как в пайплайне) шириной inst_w, в конце рулона.
    uni = unary_union([p.buffer(0) for p in polys]); bx0, by0, bx1, by1 = uni.bounds
    isc = inst_w / max(bx1 - bx0, 1e-6)
    inst_body = [f'<g transform="scale({isc:.6f}) translate({-bx0:.3f},{-by0:.3f})">']
    inst_body += [f'<use xlink:href="#content" clip-path="url(#c{i})"/>' for i in range(len(polys))]
    inst_body += ['</g>',
                  f'<rect x="0" y="0" width="{inst_w:.1f}" height="{(by1-by0)*isc:.1f}" fill="none" stroke="#555" stroke-width="0.6"/>',
                  f'<text x="2" y="-4" font-size="10" font-family="sans-serif" font-weight="bold">ИНСТРУКЦИЯ {int(inst_w)}мм</text>']
    inst = ("instrukciya", inst_body)
    inst_bbox = (0.0, 0.0, inst_w, (by1 - by0) * isc)

    roll, roll_w = _rollpack_svg(parts, roll_h, gap, lay={}, y_off=0.0,
                                 inst=inst, inst_bbox=inst_bbox, pool=pool)

    body = []
    for _nm, els in roll:
        body.extend(els)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           f'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd" '
           f'width="{roll_w:.1f}mm" height="{roll_h:.1f}mm" viewBox="0 0 {roll_w:.1f} {roll_h:.1f}">\n'
           f'<defs>\n{content}\n' + "\n".join(clips) + "\n</defs>\n"
           + "\n".join(body) + "\n</svg>\n")
    open(out_svg, "w", encoding="utf-8").write(svg)
    return roll_w, len(polys)


def main():
    ap = argparse.ArgumentParser(description="выкладка готовых лекал в рулон + инструкция")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default="out/import_arrange.svg")
    ap.add_argument("--roll-h", type=float, default=1440.0)
    ap.add_argument("--gap", type=float, default=20.0)
    ap.add_argument("--min-area", type=float, default=800.0)
    ap.add_argument("--inst-w", type=float, default=300.0)
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    w, n = build_import_layout(args.input, args.out, args.roll_h, args.gap, args.min_area, args.inst_w)
    print(f"деталей={n} рулон={w:.0f}мм -> {args.out}")


if __name__ == "__main__":
    main()
