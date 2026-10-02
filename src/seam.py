"""
Припуск на шов (seam allowance): вокруг каждого лекала — контур реза с отступом +Nмм
(для отверстий — внутрь, -Nмм). Припуск хранится в самой детали (группируется с лекалом),
поэтому при переносе детали его достаточно сдвигать вместе с ней (или пересчитать buffer).
"""
from shapely.geometry import Polygon, box


def _out(poly, mm):
    return poly.buffer(mm, join_style=2, mitre_limit=3.0)        # параллельный отступ, острые углы


def add_seams(layout, seam_mm):
    """Добавить припуск каждому лекалу раскладки. Контуры реза пишутся в сами детали:
    front_seam / strips[i]['seam'] / arm_pieces[i]['seam'] / hand_pieces[i]['seam'] /
    base: dnishe_seam, leg_seam, fan_seam(внутрь), arm_hole seam(внутрь)."""
    if "front" in layout:
        layout["front_seam"] = _out(layout["front"], seam_mm)
    if layout.get("back"):
        layout["back"]["seam"] = _out(layout["back"]["poly"], seam_mm)
    for s in layout.get("strips", []):
        s["seam"] = _out(Polygon(s["poly"]), seam_mm)
    for ap in layout.get("arm_pieces", []):
        ap["seam"] = _out(ap["poly"], seam_mm)
    for hp in layout.get("hand_pieces", []):
        hp["seam"] = _out(hp["poly"], seam_mm)
    for sp in layout.get("spare_arm", []):
        sp["seam"] = _out(sp["poly"], seam_mm)
    b = layout.get("base")
    if b:
        b["dnishe_seam"] = _out(b["dnishe"], seam_mm)
        if b.get("leg_poly") is not None:                            # ножка повёрнута (nesting)
            b["leg_seam"] = _out(b["leg_poly"], seam_mm)
        else:
            lx, ly, lw, lh = b["leg"]
            b["leg_seam"] = _out(box(lx, ly, lx + lw, ly + lh), seam_mm)
        fx, fy = b["fan_c"]; fr = b["fan_d"] / 2.0
        b["fan_seam_r"] = max(1.0, fr - seam_mm)                  # отверстие вентилятора: припуск внутрь
    ah = layout.get("arm_hole")
    if ah:
        ah["seam_r"] = max(1.0, ah["r"] - seam_mm)               # отверстие руки: припуск внутрь
    for ah in layout.get("arm_holes", []):                        # отверстия рук (список): припуск внутрь
        ah["seam_r"] = max(1.0, ah["r"] - seam_mm)


def draw_dashed(dr, pts, color, dash=9, gap=6, width=2):
    """Пунктирная ломаная по точкам pts (в пикселях)."""
    import math
    for i in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        ux, uy = (x1 - x0) / seg, (y1 - y0) / seg
        d = 0.0
        while d < seg:
            a = d; bq = min(d + dash, seg)
            dr.line([(x0 + ux * a, y0 + uy * a), (x0 + ux * bq, y0 + uy * bq)], fill=color, width=width)
            d += dash + gap


def seam_rings(layout):
    """Все контуры-припуски раскладки как список (shapely_ring_coords) для отрисовки."""
    rings = []
    if "front_seam" in layout:
        rings.append(list(layout["front_seam"].exterior.coords))
    if layout.get("back") and layout["back"].get("seam"):
        rings.append(list(layout["back"]["seam"].exterior.coords))
    for s in layout.get("strips", []):
        if s.get("seam"):
            rings.append(list(s["seam"].exterior.coords))
    for ap in layout.get("arm_pieces", []):
        if ap.get("seam"):
            rings.append(list(ap["seam"].exterior.coords))
    for hp in layout.get("hand_pieces", []):
        if hp.get("seam"):
            rings.append(list(hp["seam"].exterior.coords))
    for sp in layout.get("spare_arm", []):
        if sp.get("seam"):
            rings.append(list(sp["seam"].exterior.coords))
    b = layout.get("base")
    if b:
        if b.get("dnishe_seam"):
            rings.append(list(b["dnishe_seam"].exterior.coords))
        if b.get("leg_seam"):
            rings.append(list(b["leg_seam"].exterior.coords))
    return rings
