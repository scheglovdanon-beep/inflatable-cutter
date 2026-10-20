"""
PDF с ГРУППАМИ по деталям для CorelDRAW: каждая деталь (контур + текстура/цвет + её
припуск 10 мм) кладётся отдельным Form XObject -> Corel импортирует каждую как ГРУППУ.
Запасная рука (копия всех деталей руки) — ОДНОЙ группой, размещена ниже оригинала.

Запуск: python export_pdf_grouped.py examples/arm_sep.pdf --config config.yaml -o out/layout_grouped.pdf
"""
import argparse, os, sys
import pymupdf, yaml, numpy as np, cairosvg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import front_drawings, PT2MM, _palette                                    # noqa: E402
from side_color import strip_band_quads                                              # noqa: E402
from export_svg import build_layout, _design_group, _poly, HEX, _tex_tf                # noqa: E402

MM2PT = 72.0 / 25.4


def _circle(cx, cy, r, stroke, sw):
    return f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="none" stroke="{stroke}" stroke-width="{sw:.3f}"/>'


def _palette_vivid(i):
    """МАКСИМАЛЬНО ЯРКИЙ различимый ключ-цвет зоны (для seam_color_bands) — в отличие от тусклого
    _palette: полная насыщенность и яркость, цвета по золотому сечению (не повторяются)."""
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((i * 0.61803398875) % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


# ---------- ПОРЯДОК СЛОЁВ ДЕТАЛИ: цвета СВЕРХУ (чтобы правки цветов кликались первыми в Corel) ----------
# Сзади -> вперёд: припуск(+10) -> цветные линии вшива -> контур реза -> ЦВЕТА/текстура -> ОТВЕРСТИЯ(верх).
# holes (круги отверстия руки и днища/вентилятора) — ПОВЕРХ ВСЕГО, чтобы были видны над заливкой.
_LAYER_Z = {"seam": 0, "seamlines": 1, "contour": 2, "colors": 3, "holes": 4}


def _emit_layers(tagged, colors_top):
    """tagged=[(layer, svg), ...] в ПОРЯДКЕ ДОБАВЛЕНИЯ (legacy). colors_top=True -> стабильно
    пересортировать сзади→вперёд по _LAYER_Z (цвета наверх). colors_top=False -> как было."""
    if colors_top:
        tagged = sorted(tagged, key=lambda te: _LAYER_Z.get(te[0], 2))
    return [e for _l, e in tagged]


def _filled(pts, fill, sw, colors_top, stroke="#000"):
    """Залитый контур. colors_top=True -> РАЗДЕЛИТЬ на заливку(цвет, слой colors) и обводку
    (контур, слой contour), чтобы цвет был сверху и кликался первым. Иначе — один путь (legacy).
    Возвращает список [(layer, svg), ...]."""
    if colors_top and fill and fill != "none":
        return [("colors", _poly(pts, fill=fill, stroke="none", sw=0)),
                ("contour", _poly(pts, fill="none", stroke=stroke, sw=sw))]
    return [("contour", _poly(pts, fill=fill, stroke=stroke, sw=sw))]


# ---------- НОЖКА-КОНСТАНТА: ГОТОВАЯ деталь (вектор как есть) вместо старого прямоугольника ----------
# Деталь из examples/leg_const.svg (1540×170мм: чёрный-сшив 1520×150 + рамка-припуск 10мм) копируется
# как ВЕКТОР (ничего не перерисовываем/не растрируем): углы чёрного прямоугольника детали отображаем
# на углы нашей ножки (с учётом поворота в раскладке), контент pt -> мм (scale PT2MM).
_LEG_CONST_SVG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "leg_const.svg")
_LEG_CONST_INNER = None
_LEG_CONST_SCALE = None
_LEG_CONST_W_MM = 1540.0                                              # реальная ширина контента детали (чёрный 1520 + рамка 10)


def _leg_const_svg(leg_pts):
    """Вставить готовую деталь-ножку (вектор) на место ножки leg_pts (сшив 1520×150). Возвращает
    [svg]. Чёрный прямоугольник детали (рамка 10мм -> углы (10,10)..(1530,160) в мм контента)
    совмещаем с углами ножки; рамка-припуск выходит наружу на 10мм (как наш seam). Масштаб кадра
    детали (единицы viewBox) -> мм считаем из viewBox (ассет = inkscape plain SVG, контент = 1540мм)."""
    global _LEG_CONST_INNER, _LEG_CONST_SCALE
    if _LEG_CONST_INNER is None:
        import re
        s = open(_LEG_CONST_SVG, encoding="utf-8").read()
        m = re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) [\d.]+"', s)
        vbw = float(m.group(1)) if m else _LEG_CONST_W_MM
        _LEG_CONST_SCALE = _LEG_CONST_W_MM / vbw                      # единицы кадра детали -> мм
        _LEG_CONST_INNER = s[s.find(">", s.find("<svg")) + 1: s.rfind("</svg>")]
    src = np.array([[10.0, 10.0], [1530.0, 10.0], [10.0, 160.0]])     # TL,TR,BL чёрного в контент-мм
    dst = np.array([leg_pts[0], leg_pts[1], leg_pts[3]], float)       # соответств. углы ножки
    Mx = np.hstack([src, np.ones((3, 1))])
    ax = np.linalg.solve(Mx, dst[:, 0]); ay = np.linalg.solve(Mx, dst[:, 1])
    return [f'<g transform="matrix({ax[0]:.6f},{ay[0]:.6f},{ax[1]:.6f},{ay[1]:.6f},{ax[2]:.3f},{ay[2]:.3f})">'
            f'<g transform="scale({_LEG_CONST_SCALE:.8f})">{_LEG_CONST_INNER}</g></g>']


def _bez(p0, p1, p2, p3, n=6):
    """Точки кубической кривой Безье (сэмплинг для перевода текстуры в полигоны)."""
    out = []
    for k in range(1, n + 1):
        t = k / n; mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def _subpaths(dd, tf):
    """Подпути пути макета как списки точек (кривые Безье сэмплятся), координаты tf(pt)->мм."""
    subs, pts, cur = [], [], None
    P = lambda p: tf(p.x, p.y)
    def flush():
        if len(pts) >= 2:
            subs.append(list(pts))
    for it in dd["items"]:
        k = it[0]
        if k == "l":
            a, b = P(it[1]), P(it[2])
            if cur != it[1]:
                flush(); pts.clear(); pts.append(a)
            pts.append(b); cur = it[2]
        elif k == "c":
            p0, p1, p2, p3 = P(it[1]), P(it[2]), P(it[3]), P(it[4])
            if cur != it[1]:
                flush(); pts.clear(); pts.append(p0)
            pts.extend(_bez(p0, p1, p2, p3)); cur = it[4]
        elif k == "re":
            r = it[1]; c = [tf(r.x0, r.y0), tf(r.x1, r.y0), tf(r.x1, r.y1), tf(r.x0, r.y1)]
            flush(); pts.clear(); subs.append(c + [c[0]]); cur = None
        elif k == "qu":
            q = it[1]; c = [tf(q.ul.x, q.ul.y), tf(q.ur.x, q.ur.y), tf(q.lr.x, q.lr.y), tf(q.ll.x, q.ll.y)]
            flush(); pts.clear(); subs.append(c + [c[0]]); cur = None
    flush()
    return subs


def _ring_d(coords):
    return "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in coords) + " Z"


def _fill_path(geom, fill):
    """Polygon/MultiPolygon -> SVG path (с дырами, fill-rule evenodd)."""
    polys = list(geom.geoms) if geom.geom_type in ("MultiPolygon", "GeometryCollection") else [geom]
    ds = []
    for p in polys:
        if getattr(p, "geom_type", "") != "Polygon" or p.is_empty:
            continue
        ds.append(_ring_d(list(p.exterior.coords)))
        for r in p.interiors:
            ds.append(_ring_d(list(r.coords)))
    return f'<path d="{" ".join(ds)}" fill="{fill}" fill-rule="evenodd"/>' if ds else None


def _stroke_paths(geom, stroke, sw):
    """LineString/MultiLineString -> SVG обводки."""
    lines = []
    def add(g):
        t = getattr(g, "geom_type", "")
        if t == "LineString" and len(g.coords) >= 2:
            lines.append(list(g.coords))
        elif t in ("MultiLineString", "GeometryCollection"):
            for gg in g.geoms:
                add(gg)
    add(geom)
    out = []
    for c in lines:
        d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in c)
        out.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw:.3f}"/>')
    return out


_BG_COLS = []                                                         # цвета фоновых прямоугольников макета (заполняется в main)


def _bg_fill_colors(page, cfg):
    """Цвета фоновых прямоугольников макета = БОЛЬШИЕ заливки во всю страницу (нижний слой, на
    котором лежат перёд и зад). Берём fill прямо из вектора (надёжнее угадывания по рамке);
    критерий «большой» — тот же, что в _drop_background."""
    R = page.rect; pf = (cfg or {}).get("bg_page_frac", 0.95); sf = (cfg or {}).get("bg_soft_frac", 0.80)
    pa = R.width * R.height; cols = []
    for x in page.get_drawings():
        f = x.get("fill")
        if f is None:
            continue
        rc = x["rect"]
        big = ((rc.width > pf * R.width and rc.height > pf * R.height) or
               ((rc.width * rc.height) > sf * pa and rc.width > 0.6 * R.width))
        if big:
            cols.append(tuple(int(round(min(1.0, max(0.0, v)) * 255)) for v in f[:3]))
    return cols


def _pix_png_nobg(pix, bg_cols, tol=40):
    """Убрать фон макета из растра. Цвет фона берём из вектора (bg_cols = _bg_fill_colors —
    большой прямоугольник нижнего слоя), но прозрачим ТОЛЬКО область этого цвета, СВЯЗАННУЮ С
    КРАЕМ растра (фон окружает фигуру снаружи). Карман того же цвета ВНУТРИ контура (напр. серые
    штаны охотников) с краем не связан -> остаётся. near-белый фон (бумага) не трогаем."""
    import numpy as np, cv2, io
    from PIL import Image
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    img = a.copy() if pix.n == 4 else np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
    H, W = img.shape[:2]; rgb = img[:, :, :3].astype(int)
    for c in bg_cols or []:
        c = np.array(c[:3], int)
        if c.sum() >= 720:                                           # near-белый фон = бумага, не трогаем
            continue
        m = (np.abs(rgb - c).sum(2) < tol).astype(np.uint8)          # маска пикселей цвета фона
        n, lbl = cv2.connectedComponents(m, connectivity=4)
        border = set(lbl[0, :]).union(lbl[H - 1, :], lbl[:, 0], lbl[:, W - 1]); border.discard(0)
        if not border:
            continue
        img[np.isin(lbl, list(border)), 3] = 0                       # прозрачим только фон у края (не внутр. карманы)
    buf = io.BytesIO(); Image.fromarray(img, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def _design_raster(page, src_bbox_pt, tf, contour, cid, zoom=4.0):
    """Текстура = РАСТР оригинального рендера макета КАК ЕСТЬ (идеальные цвета, градиенты,
    чёрная обводка/плашки — всё точно из макета), обрезанный по контуру детали через clipPath.
    Фон макета убирается универсально (_pix_png_nobg): цветной/серый фон -> прозрачный,
    near-белый (бумага) и чёрная обводка фигуры сохраняются."""
    import base64
    clip = pymupdf.Rect(*src_bbox_pt)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=True)
    png = base64.b64encode(_pix_png_nobg(pix, _BG_COLS)).decode()     # убрать фон макета (по цвету из вектора)
    x0, y0 = tf(clip.x0, clip.y0); x1, y1 = tf(clip.x1, clip.y1)       # угол панели (аффин без поворота)
    cpstr = " ".join(f"{x:.2f},{y:.2f}" for x, y in contour.exterior.coords)
    return (f'<clipPath id="{cid}"><polygon points="{cpstr}"/></clipPath>'
            f'<image clip-path="url(#{cid})" x="{min(x0, x1):.2f}" y="{min(y0, y1):.2f}" '
            f'width="{abs(x1 - x0):.2f}" height="{abs(y1 - y0):.2f}" preserveAspectRatio="none" '
            f'xlink:href="data:image/png;base64,{png}"/>')


def _vtiles(poly, max_w, seam_mm):
    """РЕЗ панели (перёд/зад) на вертикальные полосы под печатный холст max_w (1440). Если ширина
    W>max_w -> N=ceil(W/max_w) РАВНЫХ полос на всю высоту (высота уходит вдоль рулона — там без
    ограничений), рез строго вертикальный встык. Каждая полоса = (контур ∩ полоса, +припуск seam
    по всем краям вкл. линию реза). Возвращает [(tile_poly, tile_seam, bx0, bx1)...] (bx0/bx1 — границы
    полосы по X, для обрезки цветовых маркеров) или None если резать не надо."""
    from shapely.geometry import box as _bx
    x0, y0, x1, y1 = poly.bounds; Wp = x1 - x0
    if Wp <= max_w + 1e-6:
        return None
    N = int(-(-Wp // max_w)); tw = Wp / N                              # ceil(W/max_w) равных полос
    tiles = []
    for k in range(N):
        bx0, bx1 = x0 + k * tw, x0 + (k + 1) * tw
        band = _bx(bx0, y0 - 1, bx1, y1 + 1)                           # вертикальная полоса (встык, без нахлёста)
        tp = poly.intersection(band)
        if tp.is_empty:
            continue
        if tp.geom_type != "Polygon":                                 # взять крупнейший кусок (на случай вогнутостей)
            tp = max((g for g in tp.geoms if g.geom_type == "Polygon"), key=lambda g: g.area, default=None)
            if tp is None:
                continue
        tiles.append((tp, tp.buffer(seam_mm, join_style=2, mitre_limit=3.0), bx0, bx1))
    return tiles if len(tiles) > 1 else None


def _design_native(dds, tf, f_mm, contour, cid):
    """Оригинальный арт КАК ЕСТЬ (точные цвета и правило заливки) + клип по контуру через
    clipPath. Без геометрической пересборки заливок -> идеальная текстура (красное тело розы
    и т.п. не теряются). Для переда/зада — там важна точность исходных цветов."""
    from export_svg import _path_d
    cpstr = " ".join(f"{x:.2f},{y:.2f}" for x, y in contour.exterior.coords)
    out = [f'<clipPath id="{cid}"><polygon points="{cpstr}"/></clipPath>',
           f'<g clip-path="url(#{cid})">']
    for dd in dds:
        d = _path_d(dd, tf)
        if not d:
            continue
        fl = HEX(tuple(int(255 * v) for v in dd["fill"])) if dd.get("fill") else "none"
        fr = "evenodd" if dd.get("even_odd") else "nonzero"            # правило заливки КАК В МАКЕТЕ
        if dd.get("color"):
            st = HEX(tuple(int(255 * v) for v in dd["color"])); sw = max(dd.get("width", 0.3) * f_mm, 0.05)
            out.append(f'<path d="{d}" fill="{fl}" fill-rule="{fr}" stroke="{st}" stroke-width="{sw:.3f}"/>')
        else:
            out.append(f'<path d="{d}" fill="{fl}" fill-rule="{fr}"/>')
    out.append("</g>")
    return "\n".join(out)


def _design_clipped(dds, tf, f_mm, contour, title):
    """Текстура макета, ГЕОМЕТРИЧЕСКИ ОБРЕЗАННАЯ по контуру детали (всё снаружи удалено).
    Заливки: even-odd регион = XOR подпутей, пересечённый с контуром. Обводки: клип ломаных.
    Надёжнее clipPath (Corel игнорирует clip при импорте -> текстура вылезала за контур)."""
    from shapely.geometry import Polygon, LineString, box as _box
    from shapely.prepared import prep
    from shapely.ops import unary_union
    from export_svg import _path_d
    cp = contour.buffer(0)
    pp = prep(cp)
    out = [f'<g id="{title}">']
    for dd in dds:
        r = dd.get("rect")                                            # быстрый путь: bbox детали vs контур
        if r is not None:
            cs = [tf(r.x0, r.y0), tf(r.x1, r.y0), tf(r.x1, r.y1), tf(r.x0, r.y1)]
            xs = [c[0] for c in cs]; ys = [c[1] for c in cs]
            bb = _box(min(xs), min(ys), max(xs), max(ys))
            if not pp.intersects(bb):
                continue                                             # целиком снаружи -> выкинуть
            if pp.contains(bb):                                       # целиком внутри -> отдать как есть (кривые, без shapely)
                d = _path_d(dd, tf)
                if d:
                    fl = HEX(tuple(int(255 * v) for v in dd["fill"])) if dd.get("fill") else "none"
                    fr = "evenodd" if dd.get("even_odd") else "nonzero"   # правило заливки КАК В МАКЕТЕ (иначе
                    if dd.get("color"):                                   # nonzero-заливка с перекрытием подпутей белеет)
                        st = HEX(tuple(int(255 * v) for v in dd["color"])); sw = max(dd.get("width", 0.3) * f_mm, 0.05)
                        out.append(f'<path d="{d}" fill="{fl}" fill-rule="{fr}" stroke="{st}" stroke-width="{sw:.3f}"/>')
                    else:
                        out.append(f'<path d="{d}" fill="{fl}" fill-rule="{fr}"/>')
                continue
        subs = _subpaths(dd, tf)                                      # пограничный -> геометрическая обрезка
        if not subs:
            continue
        if dd.get("fill"):
            region = None
            if dd.get("even_odd"):                                    # even-odd: XOR подпутей
                for s in subs:
                    if len(s) >= 3:
                        try:
                            poly = Polygon(s).buffer(0)
                        except Exception:
                            continue
                        if not poly.is_empty:
                            region = poly if region is None else region.symmetric_difference(poly)
            else:                                                    # nonzero: внешние подпути (+) добавляют, внутренние
                pos, neg = [], []                                    # (−, противоположный обход = дырки) вычитаются
                for s in subs:
                    if len(s) < 3:
                        continue
                    a = 0.0                                          # знаковая площадь (ориентация обхода)
                    for i in range(len(s)):
                        x0, y0 = s[i]; x1, y1 = s[(i + 1) % len(s)]
                        a += x0 * y1 - x1 * y0
                    try:
                        poly = Polygon(s).buffer(0)
                    except Exception:
                        continue
                    if not poly.is_empty:
                        (pos if a >= 0 else neg).append(poly)
                if pos:
                    region = unary_union(pos)
                    if neg:
                        region = region.difference(unary_union(neg))
                elif neg:                                            # все подпути одного (обратного) знака -> их объединение
                    region = unary_union(neg)
            if region is not None and not region.is_empty:
                clipped = region.intersection(cp)
                if not clipped.is_empty:
                    el = _fill_path(clipped, HEX(tuple(int(255 * v) for v in dd["fill"])))
                    if el:
                        out.append(el)
        if dd.get("color"):
            stroke = HEX(tuple(int(255 * v) for v in dd["color"]))
            sw = max(dd.get("width", 0.3) * f_mm, 0.05)
            for s in subs:
                ci = LineString(s).intersection(cp)
                if not ci.is_empty:
                    out += _stroke_paths(ci, stroke, sw)
    out.append("</g>")
    return "\n".join(out)


def _hand_tex(page, poly, src, cid, zoom=6.0):
    """Текстура кисти = РАСТР оригинала (идеальные цвета вкл. градиенты), размещённый аффином
    src(pt)->poly через SVG matrix (корректно и для зеркальной «правой стороны»), клип по
    контуру кисти. Вектор-пересборка теряла градиенты (ладонь выходила серой/без контура)."""
    import base64
    s = np.asarray(src, float); dst = np.array(poly.exterior.coords[:-1])
    if len(s) != len(dst):
        return []
    Mx = np.hstack([s, np.ones((len(s), 1))])
    ax = np.linalg.lstsq(Mx, dst[:, 0], rcond=None)[0]; ay = np.linalg.lstsq(Mx, dst[:, 1], rcond=None)[0]
    x0, y0 = float(s[:, 0].min()), float(s[:, 1].min()); x1, y1 = float(s[:, 0].max()), float(s[:, 1].max())
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=pymupdf.Rect(x0, y0, x1, y1), alpha=True)
    png = base64.b64encode(_pix_png_nobg(pix, _BG_COLS)).decode()
    a = ax[0] / zoom; c = ax[1] / zoom; e = ax[0] * x0 + ax[1] * y0 + ax[2]   # пиксель -> src -> аффин(poly)
    b = ay[0] / zoom; d = ay[1] / zoom; ff = ay[0] * x0 + ay[1] * y0 + ay[2]
    cpstr = " ".join(f"{x:.2f},{y:.2f}" for x, y in poly.exterior.coords)
    return [f'<clipPath id="{cid}"><polygon points="{cpstr}"/></clipPath>'      # клип на ГРУППЕ (мм-простр.),
            f'<g clip-path="url(#{cid})">'                                       # трансформ image — ВНУТРИ
            f'<image width="{pix.width}" height="{pix.height}" '
            f'transform="matrix({a:.5f},{b:.5f},{c:.5f},{d:.5f},{e:.3f},{ff:.3f})" '
            f'preserveAspectRatio="none" xlink:href="data:image/png;base64,{png}"/></g>']


def _piece_svg(page, p, kind, arm_dds, f_mm, lw, cid, arm_fill="none", hand_fill="#f5f5f5", colors_top=True):
    """SVG-элементы одной детали руки/кисти: текстура/контур + припуск. Порядок слоёв — через
    _emit_layers (цвета сверху при colors_top)."""
    poly = p["poly"]; tagged = []
    if kind == "hand":
        if p.get("src") is not None:
            for el in _hand_tex(page, poly, p["src"], cid):
                tagged.append(("colors", el))
            tagged.append(("contour", _poly(list(poly.exterior.coords), stroke="#000000", sw=lw)))
        else:                                                        # полоска-боковина = цвет кисти
            tagged += _filled(list(poly.exterior.coords), hand_fill, lw, colors_top, stroke="#000000")
    else:                                                            # база/плечо/предплечье — цвет своей зоны
        pc = p.get("fill")
        fill = ("#%02x%02x%02x" % tuple(int(v) for v in pc)) if pc else arm_fill
        tagged += _filled(list(poly.exterior.coords), fill, lw, colors_top, stroke="#000000")
    if p.get("seam"):
        tagged.append(("seam", _poly(list(p["seam"].exterior.coords), stroke="#000000", sw=lw)))
    return _emit_layers(tagged, colors_top)


_RU = {"predplechie": "Предплечье", "plecho": "Плечо", "baza": "База",
       "левая сторона": "Кисть Л", "правая сторона": "Кисть П", "боковина": "Полоска"}


def _label_positions(lay):
    """Список (русская_подпись, cx, cy) — центр каждой детали в координатах раскладки (мм)."""
    from shapely.geometry import Polygon as _P
    labs = []
    if lay.get("front") is not None:
        c = lay["front"].centroid; labs.append(("Перёд", c.x, c.y))
    bk = lay.get("back")
    if bk:
        c = bk["poly"].centroid; labs.append(("Зад", c.x, c.y))
    for s in lay.get("strips", []):
        c = _P(s["poly"]).centroid; labs.append((f"Боковина {s['i']}", c.x, c.y))
    for p in lay.get("hand_pieces", []):
        c = p["poly"].centroid; labs.append((_RU.get(p.get("name"), str(p.get("name"))), c.x, c.y))
    for p in lay.get("arm_pieces", []):
        c = p["poly"].centroid; labs.append((_RU.get(p.get("name"), str(p.get("name"))), c.x, c.y))
    sp = lay.get("spare_arm", [])                                     # запасная рука = копия -> ОДНА подпись блоком
    if sp:
        cs = [p["poly"].centroid for p in sp]
        labs.append(("Запасная рука", sum(c.x for c in cs) / len(cs), min(c.y for c in cs) - 40))
    b = lay.get("base")
    if b:
        c = b["dnishe"].centroid; labs.append(("Днище", c.x, c.y))
        lx, ly, lw, lh = b["leg"]; labs.append(("Ножка", lx + lw / 2, ly + lh / 2))
    return labs


def _instruction(pieces, lay, W, H, x0, y0, scale, font_mm):
    """ИНСТРУКЦИЯ ПО СБОРКЕ: уменьшенная КОПИЯ всех деталей (одной группой) в прямоугольнике
    шириной instruction_width_mm, + русские подписи каждой детали. Размещается рядом справа."""
    out = [f'<text x="{x0:.2f}" y="{y0 - font_mm * 1.2:.2f}" font-size="{font_mm * 1.8:.2f}" '
           f'fill="#000" font-family="sans-serif" font-weight="bold">ИНСТРУКЦИЯ ПО СБОРКЕ</text>',
           f'<g transform="translate({x0:.3f},{y0:.3f}) scale({scale:.6f})">']        # копия всех деталей, уменьшенная
    for _name, body in pieces:
        out.extend(body)
    out.append("</g>")
    out.append(f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{W * scale:.2f}" height="{H * scale:.2f}" '
               f'fill="none" stroke="#000" stroke-width="0.6"/>')
    for lab, cx, cy in _label_positions(lay):                                          # подписи поверх (не масштабируются)
        tx = x0 + cx * scale; ty = y0 + cy * scale
        esc = lab.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        common = (f'x="{tx:.2f}" y="{ty:.2f}" font-size="{font_mm:.2f}" font-family="sans-serif" '
                  f'text-anchor="middle" font-weight="bold"')
        # белый ореол (рисуем ПЕРВЫМ) + красный текст сверху — надёжно в любом вьюере (без paint-order)
        out.append(f'<text {common} fill="#ffffff" stroke="#ffffff" stroke-width="{font_mm * 0.45:.2f}" '
                   f'stroke-linejoin="round">{esc}</text>')
        out.append(f'<text {common} fill="#c00000">{esc}</text>')
    return out


def _poly_pool(lay):
    """name -> СПИСОК полигонов (несколько деталей с ОДНИМ именем: 2 руки дают ruka_plecho ×2).
    Порядок в списке = порядок добавления деталей в parts -> k-е вхождение имени = k-й полигон."""
    from shapely.geometry import Polygon as _P, box as _bx
    from shapely.ops import unary_union
    from collections import defaultdict
    pool = defaultdict(list)
    for nm, p in (lay.get("_tiles") or {}).items():                   # печатные полосы переда/зада
        pool[nm].append(p)
    if "front" not in pool:
        pool["front"].append(lay.get("front_seam") or lay["front"])
    if lay.get("back") and "back" not in pool:
        bk = lay["back"]; pool["back"].append(bk.get("seam") or bk["poly"])
    for s in lay.get("strips", []):
        pool[f"bokovina_{s['i']}"].append(s.get("seam") or _P(s["poly"]))
    for p in lay.get("hand_pieces", []):
        pool[f"kist_{p.get('name')}"].append(p.get("seam") or p["poly"])
    for p in lay.get("arm_pieces", []):
        pool[f"ruka_{p.get('name')}"].append(p.get("seam") or p["poly"])
    for i, p in enumerate(lay.get("spare_arm", [])):                  # запаска — каждая деталь своей группой
        pool[f"zapasnaya_{i}"].append(p.get("seam") or p["poly"])
    if lay.get("base"):
        b = lay["base"]
        pool["dnishe"].append(b.get("dnishe_seam") or b["dnishe"])
        lx, ly, lw, lh = b["leg"]; pool["nozhka"].append(b.get("leg_seam") or _bx(lx, ly, lx + lw, ly + lh))
        for i, tp in enumerate(b.get("transition", [])):
            pool[f"perehod_{i}"].append(tp)
    return pool


def _rollpack_svg(parts, roll_h, gap, lay, y_off=0.0, suffix="_печать", inst=None, inst_bbox=None, pool=None):
    """КОПИЯ всех деталей (+ инструкция), компактно упакованная в РУЛОН высотой roll_h по
    РЕАЛЬНОЙ ФОРМЕ (polygon-nesting: детали ложатся в вогнутости друг друга) — это САМА ПЕЧАТЬ,
    НИЖЕ фигуры (y_off). Детали-копии двигаются SVG-трансформом. Возвращает (pieces_копии, ширина).
    pool: {name:[Polygon,...]} — полигоны напрямую (импорт готовых лекал); None -> из фигуры (_poly_pool)."""
    if pool is None:
        pool = _poly_pool(lay)
    seen = {}
    shapes = []                                                       # (idx, poly) — КЛЮЧ ПО ИНДЕКСУ (уникально)
    for idx, (name, _b) in enumerate(parts):
        lst = pool.get(name)
        if not lst:
            continue
        k = seen.get(name, 0); seen[name] = k + 1                     # k-е вхождение имени -> k-й полигон (2 руки)
        if k >= len(lst):
            continue
        poly = lst[k]
        if poly is not None and not poly.is_empty:
            shapes.append((idx, poly))
    try:                                                              # плотная укладка по РЕАЛЬНОМУ контуру (NFP)
        from nest_true import nest_true_shape
        placements, nested_w = nest_true_shape(shapes, roll_h, gap)
    except Exception:                                                 # фолбэк на сеточный нестер
        from pack import nest_shapes
        placements, nested_w = nest_shapes(shapes, roll_h, gap)
    try:                                                              # ЧЕКЕР ПОЛНОТЫ: все детали выложены на холст?
        from seam_check import check_print_complete
        _pc = check_print_complete(parts, [i for i, _ in shapes], placements, roll_h)
        _nf = [it for it in _pc if not it["ok"]]
        lay.setdefault("print_issues", []).extend(_pc)
        if _nf:
            print(f"[seam_check] НА ХОЛСТ {int(roll_h)}мм НЕ ВЫЛОЖЕНЫ {len(_nf)}/{len(shapes)} деталей: "
                  + ", ".join(it["a"] for it in _nf))
        else:
            print(f"[seam_check] печать: все {len(shapes)} деталей выложены на холст {int(roll_h)}мм (ширина рулона {nested_w:.0f}мм)")
    except Exception as _e:
        print("[seam_check] проверка полноты печати не выполнена:", _e)
    gy = f'translate(0,{y_off:.3f}) '                                  # весь рулон — ниже фигуры
    out = []
    for idx, (name, body) in enumerate(parts):
        M = placements.get(idx)
        if M is None:
            continue
        a, b, d, e, xo, yo = M                                         # shapely affine -> SVG matrix(a,d,b,e,e_x,f_y)
        tf = f'matrix({a:.6f},{d:.6f},{b:.6f},{e:.6f},{xo:.3f},{yo:.3f})'
        out.append((name + suffix, [f'<g transform="{gy}{tf}">'] + body + ["</g>"]))
    W = nested_w + gap
    if inst is not None and inst_bbox is not None:                     # инструкция — в конце рулона, без поворота
        ix0, iy0, ix1, iy1 = inst_bbox
        tf = f'translate({W + gap - ix0:.3f},{gap - iy0:.3f})'
        out.append((inst[0] + suffix, [f'<g transform="{gy}{tf}">'] + inst[1] + ["</g>"]))
        W = W + gap + (ix1 - ix0) + gap
    out.insert(0, (f"rulon_{int(roll_h)}mm",
                   [f'<rect x="0" y="{y_off:.2f}" width="{W:.2f}" height="{roll_h:.2f}" fill="none" stroke="#888" stroke-width="1.5"/>']))
    return out, W


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml"); ap.add_argument("-o", "--out", default="out/layout_grouped.pdf")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]
    global _BG_COLS
    _BG_COLS = _bg_fill_colors(page, cfg)                             # цвета фона из вектора (нижний слой-прямоугольник)

    lay, fig, contour_mm, g, arm_pts, seam_mm = build_layout(page, cfg)
    lw = cfg.get("line_width_mm", 0.2); f_mm = fig.s * PT2MM
    W, H = lay["sheet_w"], lay["sheet_h"]
    ah = lay.get("arm_hole")

    all_dds = [dd for dd in page.get_drawings()
               if dd.get("fill") is not None or dd.get("color") is not None]
    _pr = page.rect; _pa = (_pr.width * _pr.height) or 1.0; _bgf = cfg.get("bg_page_frac", 0.95)
    def _dds_for_src(src_pts):                                     # заливки макета, чей bbox ПЕРЕСЕКАЕТ bbox куска
        s = np.asarray(src_pts, float)                            # (пересечение, не «целиком внутри» — иначе заливка
        x0, y0, x1, y1 = s[:, 0].min(), s[:, 1].min(), s[:, 0].max(), s[:, 1].max()  # ладони/контур, чуть вылезающие
        def ok(dd):                                               # за тугой bbox, терялись); обрежет _design_clipped
            r = dd["rect"]
            if (r.x1 - r.x0) * (r.y1 - r.y0) >= _bgf * _pa:       # фон во всю страницу -> выкинуть
                return False
            return not (r.x1 < x0 - 2 or r.x0 > x1 + 2 or r.y1 < y0 - 2 or r.y0 > y1 + 2)
        return [dd for dd in all_dds if ok(dd)]
    arm_dds = []
    if arm_pts is not None:
        A = arm_pts; abx0, aby0, abx1, aby1 = A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max()
        def _inarm(dd):                                           # bbox ПЕРЕСЕКАЕТ bbox руки (не «целиком внутри»):
            r = dd["rect"]                                        # заливка ладони/контур, вылезающие за тугой bbox,
            if (r.x1 - r.x0) * (r.y1 - r.y0) >= _bgf * _pa:       # не терялись; фон во всю страницу выкидываем
                return False
            return not (r.x1 < abx0 - 2 or r.x0 > abx1 + 2 or r.y1 < aby0 - 2 or r.y0 > aby1 + 2)
        arm_dds = [dd for dd in all_dds if _inarm(dd)]
    from side_color import arm_fill_color, base_fill_color                    # цвета руки/днища по 2D
    from sides import get_arm_contour
    arm_fill = HEX(arm_fill_color(page, arm_pts if arm_pts is not None else get_arm_contour(page)[0])) \
        if (lay.get("arm_pieces") or lay.get("spare_arm")) else "none"
    base_fill = HEX(base_fill_color(page, front_drawings, fig.contour_pt)) if lay.get("base") else "none"
    from side_color import hand_fill_color
    _hsrc = next((hp.get("src") for hp in lay.get("hand_pieces", []) if hp.get("src") is not None), None)
    hand_fill = HEX(hand_fill_color(page, _hsrc)) if _hsrc is not None else "#f5f5f5"  # полоска-боковина кисти
    cc = [0]
    nextid = lambda: (cc.__setitem__(0, cc[0] + 1) or f"clip{cc[0]}")

    pieces = []                                                       # (name, [svg-элементы])
    colors_top = cfg.get("colors_top_layer", True)                    # цвета — ВЕРХНИМ слоём (правки в Corel)

    if lay.get("roll"):                                               # фон-прямоугольник = рулон (1440 x длина)
        pieces.append((f"rulon_{int(lay['roll'])}mm",
                       [f'<rect x="0" y="0" width="{W:.2f}" height="{H:.2f}" fill="none" stroke="#888" stroke-width="{lw}"/>']))

    # фильтр заливок: оставить те, чей центр ВНУТРИ обрезанного контура (без руки) — убирает
    # текстуру отрезанной руки БЕЗ clipPath (clipPath заставляет cairosvg растеризовать -> 180MB).
    from shapely.geometry import Point as _Pt, box as _box
    from shapely.prepared import prep as _prep
    def _inside_fills(dds, poly_pt):
        pp = _prep(poly_pt.buffer(2))
        return [dd for dd in dds if pp.contains(_Pt((dd["rect"].x0 + dd["rect"].x1) / 2, (dd["rect"].y0 + dd["rect"].y1) / 2))]
    def _near_fills(dds, poly_pt):                                     # заливки, чей bbox ПЕРЕСЕКАЕТ контур
        pp = _prep(poly_pt)                                            # (вкл. пограничные — для чистой обрезки)
        return [dd for dd in dds if pp.intersects(_box(dd["rect"].x0, dd["rect"].y0, dd["rect"].x1, dd["rect"].y1))]

    # КЛЮЧ СБОРКИ: дуги стыков с боковинами — цвет = цвет боковины. Подсветка лежит В КОЛЬЦЕ
    # ПРИПУСКА (смещена НАРУЖУ на ~половину припуска, ширина < припуска) — НЕ заходит за контур
    # (рекламная конструкция, строго). На переде по gusset_line, на заде — зеркало переда.
    # Считаем ДО сборки, кладём дуги ВНУТРЬ группы переда/зада -> деталь цельная.
    from shapely.geometry import Point as _Pt2
    from shapely.prepared import prep as _prep2
    seam_mm_v = cfg.get("seam_allowance_mm", 10)
    # полоса-ключ ЦЕЛИКОМ в кольце припуска, с зазором от чёрного контура (не заходит внутрь детали):
    # центр на 0.6·припуска наружу, ширина 0.5·припуска -> внутр. край ≈0.35·припуска снаружи контура.
    off = seam_mm_v * 0.6; sw_seam = max(2.0, seam_mm_v * 0.5)

    def _offset_out(pts, panel_prep, d):
        arr = np.asarray(pts, float); res = []
        for i in range(len(arr)):
            a = arr[max(0, i - 1)]; b = arr[min(len(arr) - 1, i + 1)]
            t = b - a; L = float(np.hypot(*t))
            if L < 1e-6:
                res.append((arr[i][0], arr[i][1])); continue
            nrm = np.array([-t[1], t[0]]) / L
            if panel_prep.contains(_Pt2(*(arr[i] + nrm * 0.6))):     # наружу = где НЕ внутри панели
                nrm = -nrm
            res.append(tuple(arr[i] + nrm * d))
        return res

    # Цветные линии ВШИВА (цвет-ключ, где перёд сшивается с боковинами). Два режима:
    #   seam_color_lines (деф. True) — ПО ДЕТАЛЯМ: одна дуга-ключ на боковину (цвет _palette).
    #   seam_color_bands (деф. False) — ПО ЦВЕТАМ: дуга на КАЖДУЮ цветовую зону боковины в ЕЁ цвете
    #     (куда этот цвет прилегает на переде/заде). Зад — зеркало front_arcs (ниже).
    # Дуги лежат В КОЛЬЦЕ ПРИПУСКА (не заходят за контур). Маркер «рука здесь» оставляем всегда.
    seam_lines = cfg.get("seam_color_lines", True)
    seam_bands = cfg.get("seam_color_bands", False)
    tf_f = _tex_tf(contour_mm, lay["front"], f_mm); gl = g["gusset_line"]

    def _arc_on(a_from, a_to):                                        # точки дуги gusset_line (кадр переда)
        n = max(2, int(abs(a_to - a_from) / 6))
        return [tf_f(*(np.asarray(gl.interpolate(a_from + (a_to - a_from) * k / n).coords[0]) / f_mm))
                for k in range(n + 1)]
    band_keys = {}                                                    # (strip_i, band_j) -> различимый ключ-цвет зоны
    front_arcs = []
    if seam_bands:                                                    # ПО ЦВЕТАМ: каждой ЗОНЕ свой ключ, дуга на переде
        _ctr = 0
        for s in lay.get("strips", []):
            a0, a1 = s.get("arc0", 0.0), s.get("arc1", 0.0); span = (a1 - a0) or 1.0
            for j, (f0, f1, _rgb) in enumerate(s.get("bands", []) or []):
                band_keys[(s["i"], j)] = HEX(_palette_vivid(_ctr)); _ctr += 1
                front_arcs.append((band_keys[(s["i"], j)], _arc_on(a0 + f0 * span, a0 + f1 * span)))
    elif seam_lines:                                                  # ПО ДЕТАЛЯМ: одна дуга-ключ на боковину
        for s in g["segments"]:
            front_arcs.append((HEX(_palette(s["i"])), _arc_on(s["arc0"], s["arc1"])))
    for seg in lay.get("arm_cut_marks", []):                          # ЛИНИЯ РЕЗА ПЛЕЧА (где пристыковывается рука)
        front_arcs.append(("#cc5500", list(seg)))                     # dull-оранжевый = «рука здесь»

    def _arc_paths(arcs, panel, gid):
        pp = _prep2(panel); out = [f'<g id="{gid}">']
        for col, pts in arcs:
            d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in _offset_out(pts, pp, off))
            out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{sw_seam}" stroke-linecap="round"/>')
        out.append("</g>")
        return "\n".join(out)

    # ПЕРЁД/ЗАД как КАНОНИЧЕСКИЕ детали. Если панель шире печатного холста (roll_width_mm, 1440) —
    # режем ВЕРТИКАЛЬНО встык (+припуск 10мм) на N полос и кладём их РЯДОМ (зазор tgap) как отдельные
    # детали. Печать (рулон) потом КОПИРУЕТ эти же детали трансформом (_rollpack_svg) — без повторной
    # отрисовки. Дуги-маркеры стыка с боковинами режутся по X-зоне полосы (_clip_arcs_x).
    from shapely import affinity as _aff
    fr_bbox = fig.contour_pt.bounds                                   # bbox КОНТУРА (покрывает всю панель, без зазоров)
    lay["_tiles"] = {}                                                 # tilename -> seam-полигон (для _poly_pool/рулона)
    _split = cfg.get("print_split", True)
    # порог реза = ПОЛЕЗНАЯ ширина холста: вычитаем припуск (2·seam на деталь) + зазор нестинга
    # (rgap=2·seam+20, буфер gap/2 с каждой стороны), иначе панель чуть у́же 1440 не влезает в рулон
    # повёрнутой (с припуском+зазором >1440) -> ложится НЕповёрнутой и торчит (видна только голова).
    holst = cfg.get("print_split_width_mm") or (cfg.get("roll_width_mm", 1440) - 4 * seam_mm_v - 20)
    tgap = max(2 * seam_mm_v + 20, 40)                                 # зазор между соседними деталями-полосами

    def _clip_arcs_x(arcs, x0, x1):
        """Обрезать дуги-маркеры по X-зоне полосы [x0,x1] (маркеры лежат в кольце припуска ВНЕ
        контура, по ~0.6·припуска наружу -> расширяем зону на припуск). Режем на непрерывные куски."""
        m = seam_mm_v * 1.2; lo, hi = x0 - m, x1 + m; out = []
        for col, pts in arcs:
            run = []
            for x, y in pts:
                if lo <= x <= hi:
                    run.append((x, y))
                elif len(run) >= 2:
                    out.append((col, run)); run = []
                else:
                    run = []
            if len(run) >= 2:
                out.append((col, run))
        return out

    def _add_panel(prefix, panel_poly, panel_seam, src_bbox, tf, arcs):
        """Добавить деталь переда/зада в pieces. Шире холста -> N полос РЯДОМ (зазор tgap), иначе целиком."""
        tl = _vtiles(panel_poly, holst, seam_mm_v) if _split else None
        if not tl:                                                    # целиком
            tagged = [("colors", _design_raster(page, src_bbox, tf, panel_poly, nextid())),
                      ("contour", _poly(list(panel_poly.exterior.coords), stroke="#000", sw=lw)),
                      ("seam", _poly(list(panel_seam.exterior.coords), stroke="#000000", sw=lw)),
                      ("seamlines", _arc_paths(arcs, panel_poly, f"seam_{prefix}"))]
            pieces.append((prefix, _emit_layers(tagged, colors_top)))
            return
        cursor = None
        for k, (tp, ts, bx0, bx1) in enumerate(tl):
            nm = f"{prefix}_t{k}"
            if cursor is None:
                cursor = ts.bounds[0]
            dx = cursor - ts.bounds[0]                                # сдвиг так, чтобы полосы легли В РЯД с зазором
            tagged = [("colors", _design_raster(page, src_bbox, tf, tp, nextid())),
                      ("contour", _poly(list(tp.exterior.coords), stroke="#000", sw=lw)),
                      ("seam", _poly(list(ts.exterior.coords), stroke="#000000", sw=lw)),
                      ("seamlines", _arc_paths(_clip_arcs_x(arcs, bx0, bx1), tp, f"seam_{nm}"))]
            body = _emit_layers(tagged, colors_top)                    # цветовые маркеры в припуске
            if abs(dx) > 1e-6:                                        # та же деталь, просто сдвинута РЯДОМ
                body = [f'<g transform="translate({dx:.3f},0)">'] + body + ["</g>"]
            pieces.append((nm, body))
            lay["_tiles"][nm] = _aff.translate(ts, xoff=dx) if abs(dx) > 1e-6 else ts
            cursor += (ts.bounds[2] - ts.bounds[0]) + tgap

    _add_panel("front", lay["front"], lay["front_seam"],
               fr_bbox, _tex_tf(contour_mm, lay["front"], f_mm), front_arcs)

    bk = lay.get("back")
    if bk:
        bk_bbox = bk["cut_pt"].bounds if bk.get("cut_pt") else bk["src"].bounds   # bbox КОНТУРА зада
        fb = lay["front"].bounds; bbx = bk["poly"].bounds              # зеркало дуг переда в bbox зада
        fw = (fb[2] - fb[0]) or 1.0; fh = (fb[3] - fb[1]) or 1.0; bw = bbx[2] - bbx[0]; bh = bbx[3] - bbx[1]
        mir = lambda p: (bbx[0] + (1 - (p[0] - fb[0]) / fw) * bw, bbx[1] + (p[1] - fb[1]) / fh * bh)
        back_arcs = [(col, [mir(p) for p in pts]) for col, pts in front_arcs]
        _add_panel("back", bk["poly"], bk.get("seam") or bk["poly"],
                   bk_bbox, _tex_tf(bk["src"], bk["poly"], bk["f_mm"]), back_arcs)

    # БОКОВИНЫ (цвет-зоны + ЧЁРНЫЙ контур + цветной ключ-полоса вдоль стыков в припуске + припуск)
    from side_color import _edges_top_down
    from shapely.geometry import Polygon as _Poly

    def _edge_out(edge, centroid, d):
        """Сместить ребро детали НАРУЖУ на d: нормаль ориентируем ОТ центра детали (надёжно
        для прямых рёбер/углов, в отличие от panel.contains, что врёт на углах)."""
        arr = np.asarray(edge, float); cen = np.asarray(centroid, float); res = []
        for i in range(len(arr)):
            a = arr[max(0, i - 1)]; b = arr[min(len(arr) - 1, i + 1)]
            t = b - a; L = float(np.hypot(*t))
            nrm = (arr[i] - cen) if L < 1e-6 else np.array([-t[1], t[0]]) / L
            if np.dot(nrm, arr[i] - cen) < 0:                        # наружу = прочь от центра
                nrm = -nrm
            res.append(tuple(arr[i] + nrm * d))
        return res

    def _edge_sub(edge, f0, f1):
        """Под-полилиния ребра между долями длины [f0,f1] (для ключа цветовой зоны на ребре)."""
        arr = np.asarray(edge, float)
        if len(arr) < 2:
            return list(edge)
        seg = np.hypot(*np.diff(arr, axis=0).T); L = seg.sum() or 1.0
        cum = np.concatenate([[0.0], np.cumsum(seg)]) / L

        def at(f):
            k = min(max(int(np.searchsorted(cum, f)) - 1, 0), len(arr) - 2)
            t = (f - cum[k]) / max(cum[k + 1] - cum[k], 1e-9)
            return tuple(arr[k] + t * (arr[k + 1] - arr[k]))
        pts = [at(f0)] + [tuple(arr[k]) for k in range(len(arr)) if f0 < cum[k] < f1] + [at(f1)]
        return pts

    for s in lay["strips"]:
        tagged = [("colors", _poly(q, fill=HEX(c), stroke="none", sw=0)) for q, c in strip_band_quads(s)]
        # цветной ключ: тот же цвет, что дуга переда (_palette[i]), вдоль ОБОИХ длинных рёбер
        # (стык с передом и с задом), ЦЕЛИКОМ в кольце припуска, с зазором от чёрного контура.
        cen = np.asarray(_Poly(list(s["poly"])).centroid.coords[0], float)
        nd = s.get("dome_n", 0)
        if nd >= 2:                                                   # купол: poly = дуга(nd) + [низ-право, низ-лево]
            pp = list(s["poly"])
            le = [pp[0], pp[-1]]                                      # начало дуги (верх-лево) -> низ-лево
            re = [pp[nd - 1], pp[nd]]                                 # конец дуги (верх-право) -> низ-право
        else:
            le, re = _edges_top_down(list(s["poly"]), s.get("waist") is not None)
        keycol = HEX(_palette(s["i"]))
        if seam_bands:                                                 # ПО ЦВЕТАМ: ключ зоны на под-участке ОБОИХ рёбер
            for edge in (le, re):
                for j, (f0, f1, _rgb) in enumerate(s.get("bands", []) or []):
                    sub = _edge_sub(edge, f0, f1)
                    if len(sub) < 2:
                        continue
                    d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in _edge_out(sub, cen, off))
                    tagged.append(("seamlines", f'<path d="{d}" fill="none" stroke="{band_keys.get((s["i"], j), keycol)}" '
                                   f'stroke-width="{sw_seam}" stroke-linecap="round"/>'))
        elif seam_lines:                                               # ПО ДЕТАЛЯМ: один ключ вдоль рёбер (seam_color_lines)
            for edge in (le, re):
                d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in _edge_out(edge, cen, off))
                tagged.append(("seamlines", f'<path d="{d}" fill="none" stroke="{keycol}" stroke-width="{sw_seam}" stroke-linecap="round"/>'))
        tagged.append(("contour", _poly(list(s["poly"]), stroke="#000", sw=lw)))   # контур детали — чёрный
        if s.get("seam"):
            tagged.append(("seam", _poly(list(s["seam"].exterior.coords), stroke="#000000", sw=lw)))
        if ah and ah.get("piece") == s["i"]:                          # отверстие руки — ПОВЕРХ всего (слой holes)
            tagged.append(("holes", _circle(ah["cx"], ah["cy"], ah["r"], "#000000", lw)))
            if ah.get("seam_r"):
                tagged.append(("holes", _circle(ah["cx"], ah["cy"], ah["seam_r"], "#000000", lw)))
        for h in lay.get("arm_holes", []):                            # отверстия рук (список) на своей боковине
            if h.get("piece") == s["i"]:
                tagged.append(("holes", _circle(h["cx"], h["cy"], h["r"], "#000000", lw)))
                if h.get("seam_r"):
                    tagged.append(("holes", _circle(h["cx"], h["cy"], h["seam_r"], "#000000", lw)))
        # ЗАКРУГЛЕНИЕ ПЛЕЧА: цветная линия купола по зонам сшива — В ТЕЛЕ боковины (едет с деталью в рулон).
        # Смещаем НАРУЖУ в кольцо припуска (как ключ у всех деталей) -> не заходит за контур реза.
        for seg in (lay.get("shoulder_arc_segments", []) if seam_lines else []):
            if seg.get("i") != s["i"]:
                continue
            r, gg, bb = seg["rgb"]
            dd = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in _edge_out(seg["pts"], cen, off))
            tagged.append(("seamlines", f'<path d="{dd}" fill="none" stroke="#{r:02x}{gg:02x}{bb:02x}" '
                        f'stroke-width="{sw_seam}" stroke-linecap="round"/>'))
        pieces.append((f"bokovina_{s['i']}", _emit_layers(tagged, colors_top)))

    # КИСТЬ и РУКА — каждая деталь своей группой (текстура по bbox src этого куска).
    # ЗАДНЯЯ грань кисти («правая сторона») берёт ТЕКСТУРУ с ЗАДНЕГО тела фигуры (контур тот же,
    # меняется только источник текстуры) — иначе ладонь выходит с ОБЕИХ сторон, а на заде должна
    # быть ТЫЛЬНАЯ сторона руки. src переда отражаем в положение заднего тела (bbox переда->bbox
    # зада на странице, с X-зеркалом — как mir для панели зада). Флаг hand_back_from_back (деф.True).
    _bk = lay.get("back")
    _hand_back = cfg.get("hand_back_from_back", True) and _bk is not None
    def _src_to_back(src):
        bb = _bk["cut_pt"].bounds if _bk.get("cut_pt") else _bk["src"].bounds
        fw2 = (fr_bbox[2] - fr_bbox[0]) or 1.0; fh2 = (fr_bbox[3] - fr_bbox[1]) or 1.0
        return [(bb[0] + (1.0 - (x - fr_bbox[0]) / fw2) * (bb[2] - bb[0]),
                 bb[1] + (y - fr_bbox[1]) / fh2 * (bb[3] - bb[1])) for x, y in src]
    for hp in lay.get("hand_pieces", []):
        src = hp.get("src")
        if src is not None and _hand_back and (hp.get("name") or "").startswith("правая сторона"):
            src = _src_to_back(src); hp = {**hp, "src": src}        # зад кисти = текстура с заднего тела
        dds = _dds_for_src(src) if src is not None else arm_dds
        pieces.append((f"kist_{hp['name']}", _piece_svg(page, hp, "hand", dds, f_mm, lw, nextid(), hand_fill=hand_fill, colors_top=colors_top)))
    for apc in lay.get("arm_pieces", []):
        pieces.append((f"ruka_{apc.get('name','?')}", _piece_svg(page, apc, "arm", arm_dds, f_mm, lw, nextid(), arm_fill, colors_top=colors_top)))

    # ЗАПАСНАЯ РУКА — КАЖДАЯ деталь своей группой (чтобы в рулон укладывались по отдельности,
    # иначе разбросанная колонка-запаска как единый блок не влезает в 1440). Позиции в раскладке те же.
    spare = lay.get("spare_arm", [])
    for i, p in enumerate(spare):
        if _hand_back and p.get("kind") == "hand" and p.get("src") is not None \
                and (p.get("name") or "").startswith("правая сторона"):
            p = {**p, "src": _src_to_back(p["src"])}                # зад кисти запаски — тоже с заднего тела
        body = _piece_svg(page, p, p["kind"], arm_dds, f_mm, lw, nextid(), arm_fill, hand_fill, colors_top=colors_top)
        pieces.append((f"zapasnaya_{i}", body))

    # ДНИЩЕ (+ отверстие) и НОЖКА. Заливка (цвет) отделяется от контура (_filled) -> цвет сверху.
    b = lay.get("base")
    if b:
        db = _filled(list(b["dnishe"].exterior.coords), base_fill, lw, colors_top)
        fx, fy = b["fan_c"]; r = b["fan_d"] / 2
        db.append(("holes", _circle(fx, fy, r, "#000000", lw)))        # отверстие вентилятора/днища — ПОВЕРХ всего
        if b.get("fan_seam_r"):
            db.append(("holes", _circle(fx, fy, b["fan_seam_r"], "#000000", lw)))
        if b.get("dnishe_seam"):
            db.append(("seam", _poly(list(b["dnishe_seam"].exterior.coords), stroke="#000000", sw=lw)))
        pieces.append(("dnishe", _emit_layers(db, colors_top)))
        if b.get("leg_poly") is not None:                            # ножка повёрнута (nesting)
            leg_pts = list(b["leg_poly"].exterior.coords)
        else:
            lx, ly, lwd, lh = b["leg"]
            leg_pts = [(lx, ly), (lx + lwd, ly), (lx + lwd, ly + lh), (lx, ly + lh)]
        if cfg.get("leg_const", True) and os.path.isfile(_LEG_CONST_SVG):   # ГОТОВАЯ деталь-ножка (вектор как есть)
            lb = [("colors", el) for el in _leg_const_svg(leg_pts[:4])]     # деталь уже содержит контур+припуск
        else:
            lb = _filled(leg_pts, base_fill, lw, colors_top)
            if b.get("leg_seam"):
                lb.append(("seam", _poly(list(b["leg_seam"].exterior.coords), stroke="#000000", sw=lw)))
        pieces.append(("nozhka", _emit_layers(lb, colors_top)))
        for ti, tp in enumerate(b.get("transition", [])):               # переход-воронка Ø300->Ø460 (4 части)
            pieces.append((f"perehod_{ti}", _emit_layers(_filled(list(tp.exterior.coords), base_fill, lw, colors_top), colors_top)))

    # ИНСТРУКЦИЯ ПО СБОРКЕ: копия всех деталей, уменьшенная в прямоугольник шириной 30 см,
    # с русскими подписями. Готовим её контент (кладём либо рядом, либо в рулон печати).
    inst = None; inst_bbox = None
    if cfg.get("assembly_instruction"):
        inst_w = cfg.get("instruction_width_mm", 300)                  # 30 см
        iscale = inst_w / W
        igap = 2 * seam_mm_v + 150
        fnt = cfg.get("instruction_font_mm", 6)
        ix0 = W + igap; iy0 = max(seam_mm_v + 60, 60)
        inst_body = _instruction(pieces, lay, W, H, ix0, iy0, iscale, fnt)
        inst = ("instrukciya_sborki", inst_body)
        inst_bbox = (ix0, iy0 - fnt * 2.5, ix0 + inst_w, iy0 + H * iscale)

    if cfg.get("roll_print"):                                          # САМА ПЕЧАТЬ = копия деталей в рулон НИЖЕ фигуры
        roll_h = cfg.get("roll_width_mm", 1440)
        rgap = cfg.get("roll_print_gap_mm", 2 * seam_mm_v)             # зазор между деталями в колбасе (деф. 20мм)
        y_off = H + 2 * seam_mm_v + 200                                # ниже фигуры
        roll_pieces, roll_W = _rollpack_svg(pieces, roll_h, rgap, lay,   # печать = КОПИЯ тех же деталей (вкл. полосы)
                                            y_off=y_off, inst=inst, inst_bbox=inst_bbox)
        pieces = pieces + roll_pieces                                  # фигура (верх) + печать-копия (низ)
        W = max(W, roll_W); H = y_off + roll_h + 2 * seam_mm_v + 100
    elif inst is not None:                                             # без рулона — инструкция рядом справа
        pieces.append(inst)
        W = inst_bbox[2] + igap; H = max(H, inst_bbox[3] + igap)

    # сборка: каждая запись pieces -> отдельный Form XObject (= группа в Corel)
    hdr = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           f'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd" '
           f'width="{W:.2f}mm" height="{H:.2f}mm" viewBox="0 0 {W:.2f} {H:.2f}">')

    if args.out.lower().endswith(".svg"):                             # SVG: каждая деталь = <g id=..> (НАСТОЯЩАЯ
        def _gid(n):                                                  # группа; Corel сохраняет при переводе в кривые)
            return "".join(c if (c.isalnum() or c in "_-") else "_" for c in n)
        out = [hdr, f'<rect width="{W:.2f}" height="{H:.2f}" fill="white"/>']
        for name, body in pieces:
            out.append(f'<g id="{_gid(name)}">')
            out.extend(body)
            out.append("</g>")
        out.append("</svg>")
        open(args.out, "w", encoding="utf-8").write("\n".join(out))
        sz = os.path.getsize(args.out) / 1024
        print(f"saved {args.out} | групп <g>: {len(pieces)} | лист {W:.0f}x{H:.0f} мм | {sz:.0f} KB")
        for nm, _ in pieces:
            print("   группа:", nm)
        return

    master = pymupdf.open(); mpage = master.new_page(width=W * MM2PT, height=H * MM2PT)
    mpage.draw_rect(mpage.rect, color=None, fill=(1, 1, 1))
    # каждая деталь -> свой слой (PDF OCG) с именем детали: Corel импортирует деталь ЦЕЛИКОМ
    # (контур+заливка+текстура+припуск на одном слое), её можно переносить как единое целое.
    for name, body in pieces:
        svg = hdr + "\n".join(body) + "</svg>"
        src = pymupdf.open("pdf", cairosvg.svg2pdf(bytestring=svg.encode("utf-8")))
        ocg = master.add_ocg(name)                                    # слой-группа детали
        mpage.show_pdf_page(mpage.rect, src, 0, oc=ocg)
        src.close()
    master.save(args.out, garbage=3, deflate=True)
    sz = os.path.getsize(args.out) / 1024
    print(f"saved {args.out} | групп: {len(pieces)} | лист {W:.0f}x{H:.0f} мм | {sz:.0f} KB")
    for nm, _ in pieces:
        print("   группа:", nm)


if __name__ == "__main__":
    main()
