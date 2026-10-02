"""
Переразложка деталей БЕЗ НАЛОЖЕНИЙ (shelf-packing по bbox с учётом припусков + зазор).
Пакуем bbox контуров-припусков с зазором -> bbox не пересекаются -> детали не налазят.
Двигаем каждую деталь вместе со всеми её данными (припуск, отверстие, днище-круг и т.п.).
"""
import math
from shapely.geometry import Polygon, box
from shapely import affinity


def _units(layout):
    """Список единиц раскладки: {bbox, move(dx,dy)} — деталь + её вложенные элементы."""
    U = []

    def add(poly, move):
        b = poly.bounds
        U.append({"bx": b[0], "by": b[1], "w": b[2] - b[0], "h": b[3] - b[1], "move": move})

    if "front" in layout:
        def mv(dx, dy, lay=layout):
            lay["front"] = affinity.translate(lay["front"], dx, dy)
            if lay.get("front_seam") is not None:
                lay["front_seam"] = affinity.translate(lay["front_seam"], dx, dy)
            lay["front_x"] = lay.get("front_x", 0) + dx
        add(layout.get("front_seam") or layout["front"], mv)

    for s in layout.get("strips", []):
        seam = s.get("seam") or Polygon(s["poly"])
        def mv(dx, dy, s=s, lay=layout):
            s["poly"] = [(x + dx, y + dy) for x, y in s["poly"]]
            if s.get("seam") is not None:
                s["seam"] = affinity.translate(s["seam"], dx, dy)
            s["x"] = s.get("x", 0) + dx; s["y"] = s.get("y", 0) + dy
            ah = lay.get("arm_hole")
            if ah and ah.get("piece") == s["i"]:
                ah["cx"] += dx; ah["cy"] += dy
            for ah in lay.get("arm_holes", []):
                if ah.get("piece") == s["i"]:
                    ah["cx"] += dx; ah["cy"] += dy
        add(seam, mv)

    for key in ("arm_pieces", "hand_pieces", "spare_arm"):
        for p in layout.get(key, []):
            def mv(dx, dy, p=p):
                p["poly"] = affinity.translate(p["poly"], dx, dy)
                if p.get("seam") is not None:
                    p["seam"] = affinity.translate(p["seam"], dx, dy)
            add(p.get("seam") or p["poly"], mv)

    b = layout.get("base")
    if b:
        def mv_dn(dx, dy, b=b):
            b["dnishe"] = affinity.translate(b["dnishe"], dx, dy)
            b["fan_c"] = (b["fan_c"][0] + dx, b["fan_c"][1] + dy)
            if b.get("dnishe_seam") is not None:
                b["dnishe_seam"] = affinity.translate(b["dnishe_seam"], dx, dy)
        add(b.get("dnishe_seam") or b["dnishe"], mv_dn)
        lx, ly, lw, lh = b["leg"]
        legbox = b.get("leg_seam") or box(lx, ly, lx + lw, ly + lh)
        def mv_leg(dx, dy, b=b):
            lx, ly, lw, lh = b["leg"]; b["leg"] = (lx + dx, ly + dy, lw, lh)
            if b.get("leg_seam") is not None:
                b["leg_seam"] = affinity.translate(b["leg_seam"], dx, dy)
        add(legbox, mv_leg)
    return U


def _apply_pt(aff, x, y):
    return (aff[0] * x + aff[1] * y + aff[4], aff[2] * x + aff[3] * y + aff[5])


def _units_aff(layout):
    """Единицы с apply(aff) — аффинное преобразование детали + всех её данных (для nesting с поворотом)."""
    from shapely.geometry import Polygon, box
    AT = affinity.affine_transform
    U = []

    def add(poly, apply):
        U.append({"poly": poly, "apply": apply})

    if "front" in layout:
        def ap(aff, lay=layout):
            lay["front"] = AT(lay["front"], aff)
            if lay.get("front_seam") is not None:
                lay["front_seam"] = AT(lay["front_seam"], aff)
        add(layout.get("front_seam") or layout["front"], ap)

    if layout.get("back"):
        def ap(aff, lay=layout):
            lay["back"]["poly"] = AT(lay["back"]["poly"], aff)
            if lay["back"].get("seam") is not None:
                lay["back"]["seam"] = AT(lay["back"]["seam"], aff)
        add(layout["back"].get("seam") or layout["back"]["poly"], ap)

    for s in layout.get("strips", []):
        def ap(aff, s=s, lay=layout):
            s["poly"] = [_apply_pt(aff, x, y) for x, y in s["poly"]]
            if s.get("seam") is not None:
                s["seam"] = AT(s["seam"], aff)
            ah = lay.get("arm_hole")
            if ah and ah.get("piece") == s["i"]:
                ah["cx"], ah["cy"] = _apply_pt(aff, ah["cx"], ah["cy"])
            for ah in lay.get("arm_holes", []):
                if ah.get("piece") == s["i"]:
                    ah["cx"], ah["cy"] = _apply_pt(aff, ah["cx"], ah["cy"])
        add(s.get("seam") or Polygon(s["poly"]), ap)

    for key in ("arm_pieces", "hand_pieces", "spare_arm"):
        for p in layout.get(key, []):
            def ap(aff, p=p):
                p["poly"] = AT(p["poly"], aff)
                if p.get("seam") is not None:
                    p["seam"] = AT(p["seam"], aff)
            add(p.get("seam") or p["poly"], ap)

    b = layout.get("base")
    if b:
        def ap_dn(aff, b=b):
            b["dnishe"] = AT(b["dnishe"], aff)
            b["fan_c"] = _apply_pt(aff, *b["fan_c"])
            if b.get("dnishe_seam") is not None:
                b["dnishe_seam"] = AT(b["dnishe_seam"], aff)
        add(b.get("dnishe_seam") or b["dnishe"], ap_dn)
        lx, ly, lw, lh = b["leg"]
        legbox = b.get("leg_seam") or box(lx, ly, lx + lw, ly + lh)
        def ap_leg(aff, b=b):
            lx, ly, lw, lh = b["leg"]
            b["leg_poly"] = AT(box(lx, ly, lx + lw, ly + lh), aff)   # ножка как полигон (повёрнутая)
            if b.get("leg_seam") is not None:
                b["leg_seam"] = AT(b["leg_seam"], aff)
        add(legbox, ap_leg)
    return U


def nest_roll(layout, roll_h=1440.0, gap=30.0):
    """Nesting в рулон: все детали упаковываются в полосу высотой roll_h (ширина рулона),
    длина минимизируется. Повороты разрешены (панели 2700 ложатся вдоль длины). Без касаний
    (зазор gap: между линиями реза остаётся gap-2*припуск). rectpack MaxRectsBaf + сорт по площади.
    Пишет sheet_w/sheet_h/roll."""
    from rectpack import newPacker, MaxRectsBaf, SORT_AREA
    units = _units_aff(layout)
    rects = []
    for i, u in enumerate(units):
        b = u["poly"].bounds
        rects.append((b[2] - b[0] + gap, b[3] - b[1] + gap, b[0], b[1]))
    packer = newPacker(rotation=True, pack_algo=MaxRectsBaf, sort_algo=SORT_AREA)
    for i, (w, h, _, _) in enumerate(rects):
        packer.add_rect(w, h, rid=i)
    packer.add_bin(sum(w for w, _, _, _ in rects) + gap, roll_h)
    packer.pack()
    placed = {}
    for b in packer:
        for r in b:
            placed[r.rid] = (r.x, r.y, r.width, r.height)
    maxx = 0.0
    for i, u in enumerate(units):
        if i not in placed:
            continue
        x, y, pw, ph = placed[i]
        ow, oh, obx, oby = rects[i]
        rotated = abs(pw - ow) > 1.0
        if rotated:                                                 # поворот 90° CCW + сдвиг
            R = (0.0, -1.0, 1.0, 0.0, 0.0, 0.0)
            rp = affinity.affine_transform(u["poly"], R)
            rbx, rby = rp.bounds[0], rp.bounds[1]
            aff = (0.0, -1.0, 1.0, 0.0, x + gap / 2 - rbx, y + gap / 2 - rby)
        else:
            aff = (1.0, 0.0, 0.0, 1.0, x + gap / 2 - obx, y + gap / 2 - oby)
        u["apply"](aff)
        maxx = max(maxx, x + pw)
    layout["sheet_w"] = maxx + gap
    layout["sheet_h"] = roll_h
    layout["roll"] = roll_h


def repack_layout(layout, gap=80.0, width=None):
    """Разложить все детали по полкам (shelf) без наложений: сортировка по высоте,
    слева-направо, перенос строки при переполнении ширины. Зазор gap между bbox-припусков."""
    U = _units(layout)
    if not U:
        return
    maxw = max(u["w"] for u in U)
    if width is None:                                                # ширина листа ~ баланс сторон
        area = sum((u["w"] + gap) * (u["h"] + gap) for u in U)
        width = max(maxw, math.sqrt(area) * 1.25)
    U.sort(key=lambda u: -u["h"])                                    # высокие первыми
    x, y, row_h, sheet_w = gap, gap, 0.0, 0.0
    for u in U:
        if x > gap and x + u["w"] + gap > width:                     # перенос строки
            x = gap; y += row_h + gap; row_h = 0.0
        u["move"](x - u["bx"], y - u["by"])
        x += u["w"] + gap; row_h = max(row_h, u["h"]); sheet_w = max(sheet_w, x)
    layout["sheet_w"] = sheet_w + gap - gap
    layout["sheet_h"] = y + row_h + gap
