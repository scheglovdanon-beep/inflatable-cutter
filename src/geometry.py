"""
Шаги 2-3: масштаб по target_height_mm, сглаживание, валидация контура.
После scale работаем в миллиметрах.
"""
from dataclasses import dataclass
from shapely.geometry import Polygon
from shapely import affinity

PT_TO_MM = 25.4 / 72.0


@dataclass
class ScaledContour:
    poly_mm: Polygon           # контур в мм (после масштаба и сглаживания)
    scale_factor: float        # множитель от исходных пунктов к мм-фигуре
    src_w_mm: float            # исходные размеры (в мм, до целевого масштаба)
    src_h_mm: float
    out_w_mm: float            # итоговые размеры
    out_h_mm: float


def scale_to_height(contour_pt: Polygon, target_height_mm: float) -> ScaledContour:
    """Масштабировать так, чтобы высота фигуры = target_height_mm. 1pt = 1/72 дюйма."""
    x0, y0, x1, y1 = contour_pt.bounds
    src_w_mm = (x1 - x0) * PT_TO_MM
    src_h_mm = (y1 - y0) * PT_TO_MM
    if src_h_mm <= 0:
        raise ValueError("Нулевая высота контура")
    factor = target_height_mm / src_h_mm                 # пункты -> целевые мм
    # переносим в (0,0) и масштабируем пункты сразу в целевые мм
    p = affinity.translate(contour_pt, -x0, -y0)
    p = affinity.scale(p, PT_TO_MM * factor, PT_TO_MM * factor, origin=(0, 0))
    ox0, oy0, ox1, oy1 = p.bounds
    return ScaledContour(
        poly_mm=p, scale_factor=PT_TO_MM * factor,
        src_w_mm=src_w_mm, src_h_mm=src_h_mm,
        out_w_mm=ox1 - ox0, out_h_mm=oy1 - oy0,
    )


def smooth(poly_mm: Polygon, tol_mm: float) -> Polygon:
    """Лёгкое сглаживание/упрощение без потери топологии."""
    s = poly_mm.simplify(tol_mm, preserve_topology=True)
    return s.buffer(0)


@dataclass
class ContourChecks:
    is_valid: bool
    is_simple: bool            # нет самопересечений
    is_closed: bool
    n_points: int
    reason: str = ""


def validate(poly_mm: Polygon) -> ContourChecks:
    ring = poly_mm.exterior
    closed = bool(ring.is_closed) if ring is not None else False
    return ContourChecks(
        is_valid=bool(poly_mm.is_valid),
        is_simple=bool(ring.is_simple) if ring is not None else False,
        is_closed=closed,
        n_points=len(ring.coords) if ring is not None else 0,
        reason="" if poly_mm.is_valid else "invalid geometry",
    )
