"""
Переразложка деталей БЕЗ НАЛОЖЕНИЙ (shelf-packing по bbox с учётом припусков + зазор).
Пакуем bbox контуров-припусков с зазором -> bbox не пересекаются -> детали не налазят.
Двигаем каждую деталь вместе со всеми её данными (припуск, отверстие, днище-круг и т.п.).
"""
import math
from shapely.geometry import Polygon, box
from shapely import affinity


def _movables(layout):
    """Все подвижные детали с категорией и своим move(dx,dy) (двигает poly + припуск + данные)."""
    items = []

    def add(cat, poly, mv):
        b = poly.bounds
        items.append({"cat": cat, "x": b[0], "y": b[1], "w": b[2] - b[0], "h": b[3] - b[1], "mv": mv})

    def mvf(dx, dy):
        layout["front"] = affinity.translate(layout["front"], dx, dy)
        if layout.get("front_seam") is not None:
            layout["front_seam"] = affinity.translate(layout["front_seam"], dx, dy)
        layout["front_x"] = layout.get("front_x", 0) + dx
    if "front" in layout:
        add("front", layout.get("front_seam") or layout["front"], mvf)

    bk = layout.get("back")
    if bk:
        def mvb(dx, dy):
            bk["poly"] = affinity.translate(bk["poly"], dx, dy)
            if bk.get("seam") is not None:
                bk["seam"] = affinity.translate(bk["seam"], dx, dy)
        add("back", bk.get("seam") or bk["poly"], mvb)

    for s in layout.get("strips", []):
        def mvs(dx, dy, s=s):
            s["poly"] = [(x + dx, y + dy) for x, y in s["poly"]]
            if s.get("seam") is not None:
                s["seam"] = affinity.translate(s["seam"], dx, dy)
            s["x"] = s.get("x", 0) + dx; s["y"] = s.get("y", 0) + dy
            ah = layout.get("arm_hole")
            if ah and ah.get("piece") == s["i"]:
                ah["cx"] += dx; ah["cy"] += dy
            for ah in layout.get("arm_holes", []):
                if ah.get("piece") == s["i"]:
                    ah["cx"] += dx; ah["cy"] += dy
        add("strip_" + (s.get("side") or "left"), s.get("seam") or Polygon(s["poly"]), mvs)

    for key in ("arm_pieces", "hand_pieces", "spare_arm", "detail_pieces"):
        cat = "arm_spare" if key == "spare_arm" else "arm_main"       # основная рука vs запасная (двигаем блоком)
        for p in layout.get(key, []):
            def mvp(dx, dy, p=p):
                p["poly"] = affinity.translate(p["poly"], dx, dy)
                if p.get("seam") is not None:
                    p["seam"] = affinity.translate(p["seam"], dx, dy)
            add(cat, p.get("seam") or p["poly"], mvp)

    b = layout.get("base")
    if b:
        def mvdn(dx, dy):
            b["dnishe"] = affinity.translate(b["dnishe"], dx, dy); b["fan_c"] = (b["fan_c"][0] + dx, b["fan_c"][1] + dy)
            if b.get("dnishe_seam") is not None:
                b["dnishe_seam"] = affinity.translate(b["dnishe_seam"], dx, dy)
        add("base", b.get("dnishe_seam") or b["dnishe"], mvdn)
        lx, ly, lw, lh = b["leg"]
        def mvleg(dx, dy):
            lx, ly, lw, lh = b["leg"]; b["leg"] = (lx + dx, ly + dy, lw, lh)
            if b.get("leg_seam") is not None:
                b["leg_seam"] = affinity.translate(b["leg_seam"], dx, dy)
        add("leg", b.get("leg_seam") or box(lx, ly, lx + lw, ly + lh), mvleg)
        for i, tp in enumerate(b.get("transition", [])):               # переход-воронка (части) — свой ряд
            def mvt(dx, dy, i=i):
                b["transition"][i] = affinity.translate(b["transition"][i], dx, dy)
            add("transition", b["transition"][i], mvt)
    return items


def arrange_sides(layout, gap=120.0):
    """Раскладка по колонкам: [руки(ориг+запас)] [левые боковины] [перёд+зад] [правые боковины],
    днище+ножка — внизу по центру. Каждая колонка — стопка сверху вниз."""
    items = _movables(layout)
    cats = {}
    for it in items:
        cats.setdefault(it["cat"], []).append(it)

    def column(lst, x, y0=gap):
        y, maxw = y0, 0.0
        for it in lst:
            it["mv"](x - it["x"], y - it["y"])
            y += it["h"] + gap; maxw = max(maxw, it["w"])
        return maxw, y - gap

    x = gap; bottom = gap
    for group in (cats.get("arm_spare", []) + cats.get("arm_main", []),
                  cats.get("strip_left", []),
                  cats.get("front", []) + cats.get("back", []),
                  cats.get("strip_right", [])):
        if not group:
            continue
        w, yb = column(group, x)
        x += w + gap; bottom = max(bottom, yb)

    base = cats.get("base", []) + cats.get("leg", [])            # днище+ножка — внизу по центру, в ряд
    if base:
        bx = gap; by = bottom + gap
        for it in base:
            it["mv"](bx - it["x"], by - it["y"]); bx += it["w"] + gap
        bottom = by + max(it["h"] for it in base)

    layout["sheet_w"] = x + gap
    layout["sheet_h"] = bottom + gap


def arrange_print(layout, gap=120.0):
    """ПЕЧАТНАЯ раскладка «в ряд» (по примеру заказчика). Колонки слева направо:
    [запасная рука] [рука] [левые боковины] [ПЕРЁД] [правые боковины] [ЗАД];
    днище+ножка — внизу по центру. Руки двигаются ЦЕЛЫМ блоком (сохраняют свой вид:
    кисти рядом, предплечье/плечо/база стопкой). Боковины — каждая своей стопкой."""
    items = _movables(layout)
    cats = {}
    for it in items:
        cats.setdefault(it["cat"], []).append(it)

    def bbox(lst):
        return (min(it["x"] for it in lst), min(it["y"] for it in lst),
                max(it["x"] + it["w"] for it in lst), max(it["y"] + it["h"] for it in lst))

    def place_blocks(blocks, x, y0=gap):
        """blocks — список блоков; блок = список items, двигается как единое целое. Стопка сверху вниз."""
        y, maxw = y0, 0.0
        for blk in blocks:
            if not blk:
                continue
            bx0, by0, bx1, by1 = bbox(blk)
            for it in blk:                                            # весь блок — на одну дельту (вид сохраняется)
                it["mv"](x - bx0, y - by0)
            y += (by1 - by0) + gap; maxw = max(maxw, bx1 - bx0)
        return maxw, y - gap

    def _cluster(lst, gapx=400.0):
        """Разбить детали на блоки по близости X (несколько ОТДЕЛЬНЫХ рук -> отдельные блоки,
        иначе одна рука у лев.края + другая у прав.края склеивались в один блок с дырой)."""
        if not lst:
            return []
        lst = sorted(lst, key=lambda it: it["x"]); blocks = [[lst[0]]]
        for it in lst[1:]:
            right = max(c["x"] + c["w"] for c in blocks[-1])
            (blocks[-1] if it["x"] - right <= gapx else blocks.append([]) or blocks[-1]).append(it)
        return blocks

    # колонки: каждая рука — свой блок (стопкой); боковины/перёд/зад — по одному элементу-блоку
    columns = [
        _cluster(cats.get("arm_spare", [])),                            # запасная рука (блок/и)
        _cluster(cats.get("arm_main", [])),                             # руки: каждая — свой блок, стопкой
        [[it] for it in cats.get("strip_left", [])],                    # левые боковины (стопка)
        [[it] for it in cats.get("front", [])],                         # ПЕРЁД
        [[it] for it in cats.get("strip_right", [])],                   # правые боковины (стопка)
        [[it] for it in cats.get("back", [])],                          # ЗАД
    ]
    x, bottom = gap, gap
    front_cx = None; front_bottom = gap
    FRONT_COL = 3                                                       # индекс колонки ПЕРЁД в columns
    for ci, col in enumerate(columns):
        if not col:
            continue
        w, yb = place_blocks(col, x)
        if w > 0:
            if ci == FRONT_COL:                                         # запомнить центр/низ переда
                front_cx = x + w / 2.0; front_bottom = yb
            x += w + gap; bottom = max(bottom, yb)
    if front_cx is None:
        front_cx = x / 2.0; front_bottom = bottom

    # ДНИЩЕ — под ПЕРЕДОМ (по центру оси переда), НОЖКА — под днищем (стопкой вниз).
    # Ставим НИЖЕ самой длинной колонки (bottom), чтобы ничего не было сбоку -> нет касаний.
    dn = cats.get("base", []); leg = cats.get("leg", []); tr = cats.get("transition", [])
    y = bottom + gap
    for it in dn:                                                     # 1) ДНИЩЕ
        it["mv"](front_cx - (it["x"] + it["w"] / 2.0), y - it["y"]); y += it["h"] + gap
    if tr:                                                            # 2) ПЕРЕХОД — рядом (по центру)
        row_w = sum(it["w"] for it in tr) + gap * (len(tr) - 1)
        xx = front_cx - row_w / 2.0
        for it in tr:
            it["mv"](xx - it["x"], y - it["y"]); xx += it["w"] + gap
        y += max(it["h"] for it in tr) + gap
    for it in leg:                                                    # 3) НОЖКА
        it["mv"](front_cx - (it["x"] + it["w"] / 2.0), y - it["y"]); y += it["h"] + gap
    bottom = max(bottom, y - gap)

    row_tr = (sum(it["w"] for it in tr) + gap * (len(tr) - 1)) if tr else 0
    layout["sheet_w"] = max(x + gap, front_cx + row_tr / 2.0 + gap)
    layout["sheet_h"] = bottom + gap


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

    for key in ("arm_pieces", "hand_pieces", "spare_arm", "detail_pieces"):
        for p in layout.get(key, []):
            def mv(dx, dy, p=p):
                p["poly"] = affinity.translate(p["poly"], dx, dy)
                if p.get("seam") is not None:
                    p["seam"] = affinity.translate(p["seam"], dx, dy)
            add(p.get("seam") or p["poly"], mv)

    bk = layout.get("back")
    if bk:
        def mv_bk(dx, dy, bk=bk):
            bk["poly"] = affinity.translate(bk["poly"], dx, dy)
            bk["bdx"] = bk.get("bdx", 0) + dx; bk["bdy"] = bk.get("bdy", 0) + dy
            if bk.get("seam") is not None:
                bk["seam"] = affinity.translate(bk["seam"], dx, dy)
        add(bk.get("seam") or bk["poly"], mv_bk)

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

    for key in ("arm_pieces", "hand_pieces", "spare_arm", "detail_pieces"):
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


def nest_shapes(shapes, roll_h, gap, rotations=(0, 90, 180, 270)):
    """ПОЛИГОНАЛЬНЫЙ нестинг по РЕАЛЬНОЙ форме (не по bbox): детали укладываются в вогнутые
    места друг друга -> компактнее. Bottom-left по кандидатным точкам (рёбра уложенных),
    повороты, зазор через buffer(gap/2). shapes=[(id, polygon)]. Возвращает ({id: affine6}, width).
    affine6 — shapely affine (a,b,d,e,xoff,yoff), двигает ИСХОДНЫЙ полигон детали на место."""
    from shapely import affinity
    from shapely.ops import unary_union
    from shapely.prepared import prep
    infl = [(sid, poly.buffer(gap / 2.0, join_style=2, quad_segs=2)) for sid, poly in shapes]
    infl.sort(key=lambda t: -(t[1].bounds[2] - t[1].bounds[0]) * (t[1].bounds[3] - t[1].bounds[1]))
    ROT = {0: (1.0, 0.0, 0.0, 1.0), 90: (0.0, -1.0, 1.0, 0.0),
           180: (-1.0, 0.0, 0.0, -1.0), 270: (0.0, 1.0, -1.0, 0.0)}
    step = max(gap * 0.8, 25.0)                                        # шаг сетки bottom-left
    placed, pu, pp, cur_w, res = [], None, None, 0.0, {}
    for sid, ip in infl:
        best = None                                                    # (x, y, M, cand) — самый ЛЕВЫЙ слот
        for ang in rotations:
            a, b, d, e = ROT[ang]
            rp = affinity.affine_transform(ip, (a, b, d, e, 0.0, 0.0))
            rb = rp.bounds; w = rb[2] - rb[0]; h = rb[3] - rb[1]
            if h > roll_h + 1e-6:
                continue
            xlim = cur_w + w + step                                    # дальше смысла нет
            x = 0.0; found = None
            while x <= xlim:
                y = 0.0
                while y + h <= roll_h + 1e-6:
                    M = (a, b, d, e, x - rb[0], y - rb[1])
                    cand = affinity.affine_transform(ip, M)
                    if pp is None or not pp.intersects(cand):
                        found = (x, y, M, cand); break                 # нижайший Y для этого X
                    y += step
                if found:
                    break                                              # самый левый X с допустимым слотом
                x += step
            if found and (best is None or found[0] < best[0] - 1e-6
                          or (abs(found[0] - best[0]) <= 1e-6 and found[1] < best[1])):
                best = found
        if best is None:                                               # не влезло (не должно) -> новая колонка
            a, b, d, e = ROT[0]; rb = ip.bounds
            M = (a, b, d, e, cur_w - rb[0], -rb[1]); cand = affinity.affine_transform(ip, M)
            best = (cur_w, 0.0, M, cand)
        _, _, M, cand = best
        res[sid] = M; placed.append(cand)
        pu = cand if pu is None else unary_union([pu, cand])
        pp = prep(pu); cur_w = max(cur_w, cand.bounds[2])
    return res, cur_w


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
