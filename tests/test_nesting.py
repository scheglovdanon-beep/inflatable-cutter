"""
Сравнение раскладок: bbox (полки по ограничивающим прямоугольникам) vs True-Shape (NFP).
Проверяем на ОДНОМ наборе деталей: заполнение листа (%), время, геом. корректность
(нет пересечений сверх допуска касания, всё внутри полосы).
"""
import os, sys, time
import pytest
from shapely.geometry import Polygon
from shapely import affinity

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from nest_true import nest_true_shape                                     # noqa: E402

ROLL_H = 300.0
GAP = 6.0


def _shapes():
    """Набор с вогнутостями (L-образные) и треугольниками — где true-shape должен выигрывать."""
    L = Polygon([(0, 0), (80, 0), (80, 30), (30, 30), (30, 90), (0, 90)])          # L
    L2 = Polygon([(0, 0), (90, 0), (90, 90), (60, 90), (60, 30), (0, 30)])         # L зеркальная
    tri = Polygon([(0, 0), (70, 0), (0, 70)])                                      # прямоуг. треугольник
    tri2 = Polygon([(0, 0), (60, 0), (60, 60)])
    rect = Polygon([(0, 0), (50, 0), (50, 110), (0, 110)])                         # высокая полоса
    sq = Polygon([(0, 0), (55, 0), (55, 55), (0, 55)])
    out = []
    for i, p in enumerate([L, L2, tri, tri2, rect, sq, L, tri2, sq]):
        out.append((i, p))
    return out


def _bbox_strip_pack(shapes, roll_h, gap):
    """База: укладка ОГРАНИЧИВАЮЩИХ ПРЯМОУГОЛЬНИКОВ по колонкам (полки) в полосу высотой roll_h."""
    order = sorted(shapes, key=lambda t: -(t[1].bounds[3] - t[1].bounds[1]))
    x = 0.0; y = 0.0; colw = 0.0; width = 0.0; place = {}
    for sid, p in order:
        b = p.bounds; w = b[2] - b[0] + gap; h = b[3] - b[1] + gap
        if y > 0 and y + h > roll_h + 1e-9:
            x += colw; y = 0.0; colw = 0.0
        place[sid] = (1.0, 0.0, 0.0, 1.0, x - b[0] + gap / 2, y - b[1] + gap / 2)
        y += h; colw = max(colw, w); width = max(width, x + colw)
    return place, width


def _apply(shapes, placements):
    return [(sid, affinity.affine_transform(p, placements[sid])) for sid, p in shapes if sid in placements]


def _fill(polys, width, roll_h):
    area = sum(p.area for _, p in polys)
    return 100.0 * area / (width * roll_h) if width * roll_h else 0.0


def _validity(polys, width, roll_h, tol=1.0):
    overl = 0.0
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            overl += max(0.0, polys[i][1].intersection(polys[j][1]).area)
    oob = sum(1 for _, p in polys
              if p.bounds[0] < -tol or p.bounds[1] < -tol or p.bounds[2] > width + tol or p.bounds[3] > roll_h + tol)
    return overl, oob


def test_true_shape_denser_and_valid():
    shapes = _shapes()

    t0 = time.time()
    bb_place, bb_w = _bbox_strip_pack(shapes, ROLL_H, GAP)
    bb_t = time.time() - t0
    bb_polys = _apply(shapes, bb_place)
    bb_fill = _fill(bb_polys, bb_w, ROLL_H)

    t0 = time.time()
    tn_place, tn_w = nest_true_shape(shapes, ROLL_H, GAP)
    tn_t = time.time() - t0
    tn_polys = _apply(shapes, tn_place)
    tn_fill = _fill(tn_polys, tn_w, ROLL_H)

    overl, oob = _validity(tn_polys, tn_w, ROLL_H, tol=GAP)        # допуск касания в пределах зазора
    print(f"\nbbox : ширина {bb_w:7.1f} | заполнение {bb_fill:5.1f}% | время {bb_t:.3f}c")
    print(f"true : ширина {tn_w:7.1f} | заполнение {tn_fill:5.1f}% | время {tn_t:.3f}c | "
          f"наложения {overl:.1f} мм² | вне границ {oob}")

    assert len(tn_polys) == len(shapes), "все детали размещены"
    assert overl < 5.0, f"true-shape без наложений (overlap={overl:.1f} мм²)"
    assert oob == 0, "true-shape внутри полосы"
    assert tn_fill >= bb_fill - 0.5, f"true-shape не плотнее bbox: {tn_fill:.1f}% < {bb_fill:.1f}%"


def test_no_overlap_dense_set():
    """Плотный набор прямоугольников — строгая проверка отсутствия наложений."""
    shapes = [(i, Polygon([(0, 0), (40 + i * 3, 0), (40 + i * 3, 25 + i * 2), (0, 25 + i * 2)]))
              for i in range(12)]
    place, w = nest_true_shape(shapes, ROLL_H, GAP)
    polys = _apply(shapes, place)
    overl, oob = _validity(polys, w, ROLL_H, tol=GAP)
    assert len(polys) == len(shapes)
    assert overl < 5.0 and oob == 0


if __name__ == "__main__":
    test_true_shape_denser_and_valid()
    test_no_overlap_dense_set()
    print("OK")
