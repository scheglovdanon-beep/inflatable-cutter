"""
Шаг 1: чтение векторного PDF-макета.
- проверка «вектор, а не растр» (иначе стоп);
- внешний контур фигуры (силуэт = union заливок, без фона);
- внутренние цветные области (для раскраски).
Координаты — в пунктах PDF (origin сверху-слева, как в PyMuPDF).
"""
from dataclasses import dataclass, field
from typing import List, Tuple
import numpy as np
import pymupdf
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union


class NotVectorError(RuntimeError):
    pass


@dataclass
class ColorRegion:
    poly: Polygon
    rgb: Tuple[float, float, float]


@dataclass
class DesignRead:
    pdf_path: str
    page_w_pt: float
    page_h_pt: float
    outer_contour: Polygon          # силуэт фигуры (в пунктах)
    color_regions: List[ColorRegion] = field(default_factory=list)
    n_drawings: int = 0
    n_fills: int = 0
    figure_side: str = "left"

    @property
    def pt_to_mm(self) -> float:
        return 25.4 / 72.0


def _bezier(p0, p1, p2, p3, n=12):
    t = np.linspace(0, 1, n)
    x = (1 - t) ** 3 * p0.x + 3 * (1 - t) ** 2 * t * p1.x + 3 * (1 - t) * t * t * p2.x + t ** 3 * p3.x
    y = (1 - t) ** 3 * p0.y + 3 * (1 - t) ** 2 * t * p1.y + 3 * (1 - t) * t * t * p2.y + t ** 3 * p3.y
    return list(zip(x, y))


def _drawing_to_polygon(d) -> Polygon | None:
    pts = []
    for it in d["items"]:
        k = it[0]
        if k == "l":
            pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
        elif k == "c":
            pts += _bezier(it[1], it[2], it[3], it[4])
        elif k == "re":
            r = it[1]; pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        elif k == "qu":
            q = it[1]; pts += [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
    if len(pts) < 3:
        return None
    try:
        poly = Polygon(pts).buffer(0)
        return poly if poly.area > 0 else None
    except Exception:
        return None


BG_RECT_MIN_W_PT = 990.0        # фон = прямоугольник шире этого (лист ~1000pt)


def split_figures(page, page_w: float):
    """Разделить чертёж на левую/правую фигуру по центру x=page_w/2, отсеяв фон.
    Возвращает (dict side->[raw drawings], drawings_total, images_total)."""
    drawings = page.get_drawings()
    images = page.get_images()
    cx_page = page_w / 2.0
    out = {"left": [], "right": []}
    for d in drawings:
        if d.get("fill") is None and d.get("color") is None:
            continue
        r = d["rect"]
        if r.width > BG_RECT_MIN_W_PT:          # фоновый прямоугольник — исключить
            continue
        cx = (r.x0 + r.x1) / 2.0
        out["left" if cx < cx_page else "right"].append(d)
    return out, len(drawings), len(images)


def read_design(pdf_path: str, figure_side: str = "left") -> DesignRead:
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    R = page.rect
    drawings = page.get_drawings()
    images = page.get_images()
    fills = [d for d in drawings if d.get("fill") is not None]

    # «вектор, а не растр»: есть векторные заливки; если только картинка — стоп
    if not fills and images:
        raise NotVectorError(
            f"PDF выглядит растровым (изображений: {len(images)}, векторных заливок: 0). "
            "Нужен векторный макет."
        )
    if not fills:
        raise NotVectorError("В PDF не найдено векторных заливок — нечего раскраивать.")

    page_area = R.width * R.height
    cx_page = R.width / 2

    # собираем полигоны фигуры (без фона, только нужная сторона листа)
    region_polys: List[ColorRegion] = []
    fig_polys = []
    for d in fills:
        r = d["rect"]
        if r.width * r.height > 0.6 * page_area:        # фон на весь лист
            continue
        cx = (r.x0 + r.x1) / 2
        on_left = cx <= cx_page
        if (figure_side == "left") != on_left:          # чужая фигура (другая сторона)
            continue
        poly = _drawing_to_polygon(d)
        if poly is None:
            continue
        fig_polys.append(poly)
        region_polys.append(ColorRegion(poly=poly, rgb=tuple(d["fill"])))

    if not fig_polys:
        raise NotVectorError("Не удалось выделить фигуру (после отсева фона).")

    u = unary_union(fig_polys)
    if isinstance(u, MultiPolygon):
        u = max(u.geoms, key=lambda g: g.area)
    outer = Polygon(u.exterior)                          # внешний контур (дыры залиты)

    return DesignRead(
        pdf_path=pdf_path, page_w_pt=R.width, page_h_pt=R.height,
        outer_contour=outer, color_regions=region_polys,
        n_drawings=len(drawings), n_fills=len(fills), figure_side=figure_side,
    )
