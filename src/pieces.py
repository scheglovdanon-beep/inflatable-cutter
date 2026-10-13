"""
Построение фигуры: контур (union -> закрытие щелей buffer(+d).buffer(-d) -> один Polygon)
и параметры масштаба к target_height_mm. Высота считается по объединению (вместе с плашкой).
"""
from dataclasses import dataclass
import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

MM2PT = 72.0 / 25.4
PT2MM = 25.4 / 72.0


def _bezier(p0, p1, p2, p3, n=16):
    t = np.linspace(0, 1, n)
    return list(zip((1 - t) ** 3 * p0.x + 3 * (1 - t) ** 2 * t * p1.x + 3 * (1 - t) * t * t * p2.x + t ** 3 * p3.x,
                    (1 - t) ** 3 * p0.y + 3 * (1 - t) ** 2 * t * p1.y + 3 * (1 - t) * t * t * p2.y + t ** 3 * p3.y))


def _poly_of(d):
    if "_poly" in d:                                   # кэш на самом drawing (парсится и в _bodies,
        return d["_poly"]                              # и в build_contour) — освобождается вместе с dict
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
    res = None
    if len(pts) >= 3:
        try:
            p = Polygon(pts).buffer(0)
            res = p if p.area > 0 else None
        except Exception:
            res = None
    d["_poly"] = res
    return res


@dataclass
class Figure:
    side: str
    drawings: list
    contour_pt: Polygon           # контур в исходных пунктах (y вниз)
    page_H: float                 # высота исходного листа (pt) для переворота Y
    target_height_mm: float
    n_parts: int
    is_valid: bool
    outside_mm2: float = 0.0       # площадь макета вне контура (должна быть ~0)
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    s: float = 1.0                # масштаб pt->pt (к целевой высоте)

    def __post_init__(self):
        self.x0, self.y0, self.x1, self.y1 = self.contour_pt.bounds
        self.s = (self.target_height_mm * MM2PT) / (self.y1 - self.y0)

    @property
    def out_w_pt(self): return (self.x1 - self.x0) * self.s

    @property
    def out_h_pt(self): return (self.y1 - self.y0) * self.s

    @property
    def out_w_mm(self): return self.out_w_pt * PT2MM

    @property
    def out_h_mm(self): return self.out_h_pt * PT2MM


def build_contour(drawings, gap_mm: float, target_height_mm: float, exclude_outline: bool = False, cfg=None):
    """union заливок -> закрытие щелей buffer(+g).buffer(-g) -> один Polygon без дыр.
    Контур строится «в финальном масштабе»: зазор g пересчитан так, чтобы после
    увеличения фигуры до target_height_mm он равнялся ровно gap_mm (в реальных мм).
    exclude_outline=True — выкинуть near-black заливки (чёрные обводки составным путём
    парсятся в сплошной блоб с прямым верхом -> контур спрямляет руку). Точный контур.
    Возвращает (contour, union, n_parts, is_valid)."""
    def _keep(d):
        if d.get("fill") is None:
            return False
        if exclude_outline and sum(int(255 * v) for v in d["fill"]) < 60:
            return False
        return True
    polys = [p for p in (_poly_of(d) for d in drawings if _keep(d)) if p]
    if not polys:
        raise ValueError("нет заливок для контура")
    bs = (cfg or {}).get("union_batch_size", 0)                  # батчевый union: меньше пик памяти
    if bs and len(polys) > bs:                                   # (результат идентичен — union ассоциативен)
        import gc
        parts = [unary_union(polys[i:i + bs]) for i in range(0, len(polys), bs)]
        u = unary_union(parts)
        del parts; gc.collect()
    else:
        u = unary_union(polys)
    y0, y1 = u.bounds[1], u.bounds[3]
    s = (target_height_mm * MM2PT) / (y1 - y0)    # масштаб pt->pt к финалу
    g = gap_mm * MM2PT / s                         # зазор в исходных pt, чтобы в финале = gap_mm
    c = u.buffer(g).buffer(-g)
    if (cfg or {}).get("contour_clip_outline"):   # цветные заливки нарисованы с запасом ЗА чёрной
        thr = (cfg or {}).get("contour_outline_black_frac", 0.18)   # обводкой (keyline сверху визуально режет).
        blk = [p for p in (_poly_of(d) for d in drawings            # Обрезаем контур по силуэту near-чёрной
                           if d.get("fill") is not None and sum(d["fill"]) / 3.0 < thr) if p]  # обводки -> по чёрной линии.

        def _polys(geom):                                           # полигоны из ЛЮБОЙ геометрии (GC/Multi/Poly)
            if geom.is_empty:
                return []
            if isinstance(geom, Polygon):
                return [geom]
            return [gg for gg in getattr(geom, "geoms", []) if isinstance(gg, Polygon) and not gg.is_empty]
        if blk:
            try:
                ks = unary_union([b.buffer(0) for b in blk])
                ksp = _polys(ks)
                if ksp:
                    ksf = max((Polygon(p.exterior) for p in ksp), key=lambda p: p.area)  # силуэт обводки без дыр
                    c2p = _polys(c.intersection(ksf.buffer(0)))
                    if c2p:
                        cand = max(c2p, key=lambda x: x.area)
                        if cand.area >= 0.5 * c.area:               # устойчивость: обрезка не должна «съесть» фигуру
                            c = cand                                # иначе откат к необрезанному (напр. фото-голова)
            except Exception:
                pass                                                # при любой ошибке — необрезанный контур
    n_parts = 1 if isinstance(c, Polygon) else len(c.geoms)
    if isinstance(c, MultiPolygon):
        c = max(c.geoms, key=lambda x: x.area)
    contour = Polygon(c.exterior)                 # один замкнутый, без дыр
    return contour, u, n_parts, bool(contour.is_valid)


def build_figure(side, drawings, page_H, target_height_mm, gap_mm, exclude_outline=False, cfg=None) -> Figure:
    contour, union, n_parts, valid = build_contour(drawings, gap_mm, target_height_mm, exclude_outline, cfg)
    outside_pt2 = union.difference(contour).area          # макет вне контура (в pt^2)
    return Figure(side=side, drawings=drawings, contour_pt=contour, page_H=page_H,
                  target_height_mm=target_height_mm, n_parts=n_parts, is_valid=valid,
                  outside_mm2=outside_pt2 * PT2MM * PT2MM)
