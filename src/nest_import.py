"""Импорт готовой фигуры (CDR/SVG/PDF) и раскладка её ДЕТАЛЕЙ в печатный рулон 1440.
Отдельный пайплайн (не из макета): берём уже готовые контуры деталей -> плотная укладка
(nest_true_shape) -> проверки (полнота/рамки/касание) -> PDF/SVG + инструкция.

parse_details(path)   -> [Polygon,...] (мм)   — извлечь контуры деталей (CDR->SVG через inkscape)
nest_details(polys)   -> (placed, roll_w)     — уложить в рулон roll_h (nest_true_shape)
check_nest(placed...) -> [issue,...]          — все детали на месте / в рамках / не касаются
"""
import os
import subprocess
import numpy as np
from shapely.geometry import Polygon
from shapely.affinity import affine_transform


# ---------------------------------------------------------------- парсинг входа
def _polys_from_pdf(path, min_area_mm2=2000.0, max_stroke_pt=1.0):
    """Контуры-РЕЗЫ из PDF: замкнутые ТОНКИЕ обводки (width<=max_stroke_pt) — это линии реза
    деталей (толстые рамки/рулон и заливки без обводки отбрасываем). В мм (1pt=25.4/72)."""
    import pymupdf
    PT2MM = 25.4 / 72.0
    page = pymupdf.open(path)[0]
    out = []
    for d in page.get_drawings():
        if not d.get("color"):                                     # без обводки -> заливка, не рез
            continue
        if (d.get("width") or 0) > max_stroke_pt:                  # толстая линия -> рамка/рулон, не деталь
            continue
        pts = []
        for it in d.get("items", []):
            if it[0] == "l":
                pts.append((it[1].x, it[1].y)); pts.append((it[2].x, it[2].y))
            elif it[0] == "c":
                for p in it[1:]:
                    pts.append((p.x, p.y))
            elif it[0] == "re":
                r = it[1]; pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        if len(pts) < 3:
            continue
        try:
            poly = Polygon([(x * PT2MM, y * PT2MM) for x, y in pts]).buffer(0)
        except Exception:
            continue
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if not poly.is_empty and poly.area >= min_area_mm2:
            out.append(poly)
    return out


def dedupe_details(polys, area_tol=0.01, concentric_ratio=0.80):
    """Схлопнуть ДУБЛИ и КОНЦЕНТРИЧНЫЕ пары одной детали:
    - дубль: та же площадь+габарит (читаемая копия vs рулон);
    - концентричная пара рез/припуск: один контур содержит другой при похожей площади
      (area_min/area_max > concentric_ratio) -> оставляем ВНЕШНИЙ (припуск = граница материала)."""
    seam_tol = 25.0                                                # ±(2·припуск) на габарит: рез vs припуск — одна деталь
    kept = []
    for p in sorted(polys, key=lambda g: -g.area):                 # от больших к малым -> первым берём ВНЕШНИЙ контур
        b = p.bounds; w, h = b[2] - b[0], b[3] - b[1]
        ar = w / max(h, 1e-9)
        skip = False
        for q in kept:
            qb = q.bounds; qw, qh = qb[2] - qb[0], qb[3] - qb[1]; qar = qw / max(qh, 1e-9)
            same_shape = (abs(w - qw) <= seam_tol and abs(h - qh) <= seam_tol
                          and p.area / q.area > concentric_ratio
                          and abs(ar - qar) <= 0.08 * max(ar, qar))  # тот же силуэт (позиция не важна)
            concentric = (p.area / q.area > concentric_ratio and q.buffer(3.0).contains(p))
            if same_shape or concentric:
                skip = True; break
        if not skip:
            kept.append(p)
    return kept


def _polys_from_svg(path, min_area_mm2=2000.0):
    """Замкнутые контуры из SVG (svgpathtools), в мм (предполагаем пользовательские единицы = мм)."""
    from svgpathtools import svg2paths2
    paths, attrs, _ = svg2paths2(path)
    out = []
    for p in paths:
        if not p.isclosed():
            continue
        try:
            pts = [(seg.start.real, seg.start.imag) for seg in p]
            pts.append((p[-1].end.real, p[-1].end.imag))
            poly = Polygon(pts).buffer(0)
        except Exception:
            continue
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda g: g.area)
        if not poly.is_empty and poly.area >= min_area_mm2:
            out.append(poly)
    return out


def parse_details(path, min_area_mm2=2000.0):
    """Контуры деталей из CDR/SVG/PDF (мм). CDR -> SVG через inkscape.
    ВНИМАНИЕ: отбор «что есть деталь» зависит от структуры файла — настраивается под образец."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".cdr":
        svg = path + ".svg"
        subprocess.run(["inkscape", path, "--export-type=svg", "--export-filename=" + svg],
                       check=True, capture_output=True, timeout=120)
        path, ext = svg, ".svg"
    if ext == ".svg":
        return _polys_from_svg(path, min_area_mm2)
    if ext == ".pdf":
        return _polys_from_pdf(path, min_area_mm2)
    raise ValueError(f"формат не поддержан: {ext}")


# ---------------------------------------------------------------- укладка
def nest_details(polys, roll_h=1440.0, gap=20.0):
    """Уложить контуры в рулон высотой roll_h по РЕАЛЬНОЙ форме (nest_true_shape).
    Возвращает ({idx: размещённый Polygon}, ширина_рулона)."""
    from nest_true import nest_true_shape
    shapes = list(enumerate(polys))
    placements, W = nest_true_shape(shapes, roll_h, gap)
    placed = {}
    for idx, M in placements.items():
        a, b, d, e, xo, yo = M                                     # shapely affine: [a,b,d,e,xo,yo]
        placed[idx] = affine_transform(polys[idx], [a, b, d, e, xo, yo])
    return placed, W


# ---------------------------------------------------------------- проверки
def check_nest(placed, polys, roll_h=1440.0, min_gap=5.0, tol=0.5):
    """Проверки раскладки: 1) все детали выложены; 2) в рамках холста [0..roll_h] по высоте;
    3) детали НЕ ПЕРЕКРЫВАЮТСЯ и НЕ КАСАЮТСЯ (зазор >= min_gap — жёсткий минимум под нож плоттера;
    близко, но с зазором — ок). Возвращает [issue,...]."""
    gap = min_gap
    issues = []

    def add(stuk, ok, a="", a_len=0.0, b="", b_len=0.0, delta=0.0):
        issues.append({"stuk": stuk, "a": a, "a_len": a_len, "b": b, "b_len": b_len,
                       "delta": delta, "ok": bool(ok)})

    for i in range(len(polys)):                                    # 1) полнота
        add(f"деталь {i} выложена", i in placed,
            a="на холсте" if i in placed else "НЕ ВЫЛОЖЕНА", a_len=1.0 if i in placed else 0.0)

    for i, p in placed.items():                                    # 2) в рамках холста (высота рулона)
        x0, y0, x1, y1 = p.bounds
        ok = (y0 >= -tol) and (y1 <= roll_h + tol) and (x0 >= -tol)
        add(f"деталь {i} в рамках холста", ok,
            a=f"y[{y0:.0f}..{y1:.0f}]", a_len=y1, b=f"холст 0..{roll_h:.0f}", b_len=roll_h,
            delta=max(0.0, y1 - roll_h, -y0))

    items = list(placed.items())                                   # 3) касание/перекрытие
    for a in range(len(items)):
        ia, pa = items[a]
        for b2 in range(a + 1, len(items)):
            ib, pb = items[b2]
            if pa.bounds[2] < pb.bounds[0] - gap or pb.bounds[2] < pa.bounds[0] - gap:
                continue                                           # bbox далеко по X -> пропуск
            dist = pa.distance(pb)
            if dist < gap - tol:
                add(f"детали {ia}↔{ib} зазор", False,
                    a=f"зазор={dist:.1f}", a_len=dist, b=f"мин={gap:.0f}", b_len=gap, delta=gap - dist)
    return issues
