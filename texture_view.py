"""
Вид всей раскладки «в текстуре»: перёд — реальная текстура макета, боковины — акцентные
цвета (как при печати), кисть — текстура кулака (перёд + зеркало) + боковина, днище/рука — контур.
Запуск: python texture_view.py examples/arm_sep.pdf --config config.yaml
"""
import argparse, os, sys
import pymupdf, yaml, numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import (get_front_figure, contour_to_mm, build_gusset, classify_sides,         # noqa: E402
                   build_fitting_layout, front_drawings, PT2MM, _palette)
from side_color import color_sides, strip_band_quads, front_raster, _flat                  # noqa: E402
from export_svg import _tex_tf                                                              # noqa: E402
from arm import (detect_wrist, cut_arm, build_hand, place_hand, build_arm, place_arm)       # noqa: E402
from sides import get_arm_contour                                                           # noqa: E402


def _fist_texture(page, arm_pts):
    """Клип текстуры кулака по контуру кисти (RGBA) + масштаб px/pt."""
    fr = [x for x in page.get_drawings() if x.get("fill") is not None]
    A = arm_pts; bx0, by0 = A[:, 0].min(), A[:, 1].min()
    bx1, by1 = A[:, 0].max(), A[:, 1].max()
    inbb = lambda r: r.x0 >= bx0 - 2 and r.x1 <= bx1 + 2 and r.y0 >= by0 - 2 and r.y1 <= by1 + 2
    Z, mg = 5.0, 8
    W, H = int((bx1 - bx0 + 2 * mg) * Z), int((by1 - by0 + 2 * mg) * Z)
    img = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(img)
    col = lambda c: tuple(int(255 * v) for v in c) if c else None
    Tr = lambda x, y: ((x - bx0 + mg) * Z, (y - by0 + mg) * Z)
    for dd in page.get_drawings():
        if dd.get("fill") is None and dd.get("color") is None:
            continue
        if not inbb(dd["rect"]):
            continue
        f = col(dd.get("fill")); s = col(dd.get("color"))
        for sub in _flat(dd):
            if len(sub) < 2:
                continue
            pts = [Tr(x, y) for x, y in sub]
            if f is not None and len(pts) >= 3:
                dr.polygon(pts, fill=f)
            if s is not None:
                dr.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=s, width=max(1, int(dd.get("width", .5) * Z)))
    return img, bx0, by0, Z, mg


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("input_pdf"); ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs("out", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]
    from export_svg import build_layout
    lay, fig, contour_mm, g, arm_pts, _seam = build_layout(page, cfg)  # единый конвейер (вкл. arm_cut armpit)

    M = 40
    sc = min(1500 / lay["sheet_h"], 2400 / lay["sheet_w"])          # вписать (длинный рулон тоже)
    W = int(lay["sheet_w"] * sc) + 2 * M; H = int(lay["sheet_h"] * sc) + 2 * M
    im = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(im)
    T = lambda x, y: (M + x * sc, M + y * sc)
    lw = max(1, round(cfg.get("line_width_mm", 0.2) * sc))          # ширина всех контуров лекал
    from side_color import arm_fill_color, base_fill_color
    arm_col = arm_fill_color(page, arm_pts if arm_pts is not None else get_arm_contour(page)[0]) \
        if (lay.get("arm_pieces") or lay.get("spare_arm")) else (0, 0, 0)   # цвет руки по 2D (чёрный)
    base_col = base_fill_color(page, front_drawings) if lay.get("base") else (0, 0, 0)  # цвет днища по 2D

    _NM = {"baza": "BAZA", "plecho": "PLECHO", "predplechie": "PREDPLECHIE",
           "левая сторона": "KIST-L", "боковина": "KIST-poloska", "правая сторона": "KIST-R"}
    def lbl(name):
        z = " ZAPAS" if "(запас)" in name else ""
        return _NM.get(name.replace(" (запас)", ""), name.replace(" (запас)", "")) + z
    def label(obj, txt):
        if hasattr(obj, "bounds"):
            b = obj.bounds
        elif isinstance(obj, (list, tuple)) and obj and isinstance(obj[0], (list, tuple)):
            xs = [p[0] for p in obj]; ys = [p[1] for p in obj]; b = (min(xs), min(ys), max(xs), max(ys))
        else:
            b = obj
        px, py = T(b[0], b[1])
        dr.text((px + 2, py - 13), txt, fill=(180, 0, 0))

    # ----- ПЕРЁД: реальная текстура (рисуем заливки макета прямо в координатах раскладки) -----
    f_mm = fig.s * PT2MM
    if lay.get("roll"):                                            # рамка рулона (фон-прямоугольник 1440)
        dr.rectangle([T(0, 0), T(lay["sheet_w"], lay["sheet_h"])], outline=(120, 120, 120), width=lw)
    _ftf = _tex_tf(contour_mm, lay["front"], f_mm)                 # аффин контур->панель (учитывает поворот)
    pt2cv = lambda x, y: T(*_ftf(x, y))
    col = lambda c: tuple(int(255 * v) for v in c) if c else None
    fr, _ = front_drawings(page, cfg)
    for dd in fr:
        f = col(dd.get("fill")); s = col(dd.get("color"))
        for sub in _flat(dd):
            if len(sub) < 2:
                continue
            pts = [pt2cv(x, y) for x, y in sub]
            if f is not None and len(pts) >= 3:
                dr.polygon(pts, fill=f)
            if s is not None:
                dr.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=s, width=max(1, int(dd.get("width", .5) * f_mm * sc)))

    # ----- ПЕРЁД: контур-шов (сплошной, 0.2мм) + подпись -----
    dr.line([T(x, y) for x, y in lay["front"].exterior.coords], fill=(0, 0, 0), width=lw)
    label(lay["front"], "PERED")

    # ----- ЗАД: зеркальная текстура переда + контур + подпись -----
    bk = lay.get("back")
    if bk:
        from sides import back_drawings
        bmap = _tex_tf(bk["src"], bk["poly"], bk["f_mm"])         # аффин контур зада->панель (поворот)
        for dd in back_drawings(page, cfg)[0]:                     # текстура ЗАДНЕЙ фигуры макета
            f = col(dd.get("fill")); s = col(dd.get("color"))
            for sub in _flat(dd):
                if len(sub) < 2:
                    continue
                pts = [T(*bmap(x, y)) for x, y in sub]
                if f is not None and len(pts) >= 3:
                    dr.polygon(pts, fill=f)
                if s is not None:
                    dr.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=s, width=max(1, int(dd.get("width", .5) * f_mm * sc)))
        dr.line([T(x, y) for x, y in bk["poly"].exterior.coords], fill=(0, 0, 0), width=lw)
        label(bk["poly"], "ZAD")

    # ----- БОКОВИНЫ: акцентные цвета + подпись -----
    for s in lay["strips"]:
        for quad, c in strip_band_quads(s):
            dr.polygon([T(x, y) for x, y in quad], fill=tuple(c))
        dr.line([T(x, y) for x, y in s["poly"]] + [T(*s["poly"][0])], fill=_palette(s["i"]), width=lw)
        label(s["poly"], f"bok#{s['i']}")

    # ----- ДНИЩЕ + ножка — ЧЁРНЫЕ (цвет с 2D) + отверстие руки -----
    b = lay.get("base")
    if b:
        dr.polygon([T(x, y) for x, y in b["dnishe"].exterior.coords], fill=base_col, outline=(0, 0, 0))
        label(b["dnishe"], "DNISHE")
        fx, fy = b["fan_c"]; r = b["fan_d"] / 2
        dr.ellipse([T(fx - r, fy - r), T(fx + r, fy + r)], outline=(220, 0, 0), width=lw)
        if b.get("leg_poly") is not None:                           # ножка повёрнута (nesting)
            dr.polygon([T(x, y) for x, y in b["leg_poly"].exterior.coords], fill=base_col, outline=(0, 0, 0))
            label(b["leg_poly"], "NOZHKA")
        else:
            lx, ly, lwd, lh = b["leg"]
            dr.rectangle([T(lx, ly), T(lx + lwd, ly + lh)], fill=base_col, outline=(0, 0, 0))
            dr.text((T(lx, ly)[0] + 2, T(lx, ly)[1] - 13), "NOZHKA", fill=(180, 0, 0))
    ah = lay.get("arm_hole")
    if ah:
        dr.ellipse([T(ah["cx"] - ah["r"], ah["cy"] - ah["r"]), T(ah["cx"] + ah["r"], ah["cy"] + ah["r"])],
                   outline=(220, 0, 0), width=lw)
    for ah in lay.get("arm_holes", []):                             # отверстия рук (по одному на руку)
        dr.ellipse([T(ah["cx"] - ah["r"], ah["cy"] - ah["r"]), T(ah["cx"] + ah["r"], ah["cy"] + ah["r"])],
                   outline=(220, 0, 0), width=lw)

    # ----- РУКА (база/плечо/предплечье) — ЧЁРНАЯ (цвет рукава с 2D) + подпись -----
    for ap in lay.get("arm_pieces", []):
        dr.polygon([T(x, y) for x, y in ap["poly"].exterior.coords], fill=arm_col, outline=(150, 0, 150))
        label(ap["poly"], lbl(ap.get("name", "")))

    # ----- КИСТЬ: текстура жёстко привязана к контуру через аффин src(pt)->poly(раскладка) -----
    all_dds = [dd for dd in page.get_drawings()
               if dd.get("fill") is not None or dd.get("color") is not None]
    def dds_for(src_pts):                                           # заливки макета в bbox куска (по его src)
        s = np.asarray(src_pts, float)
        x0, y0, x1, y1 = s[:, 0].min(), s[:, 1].min(), s[:, 0].max(), s[:, 1].max()
        return [dd for dd in all_dds if (dd["rect"].x0 >= x0 - 2 and dd["rect"].x1 <= x1 + 2
                                         and dd["rect"].y0 >= y0 - 2 and dd["rect"].y1 <= y1 + 2)]
    from side_color import hand_fill_color
    hand_src = next((hp.get("src") for hp in lay.get("hand_pieces", []) if hp.get("src") is not None), None)
    hand_col = hand_fill_color(page, hand_src) if hand_src is not None else (245, 245, 245)  # цвет полоски-боковины кисти

    arm_dds = []
    if arm_pts is not None:
        A = arm_pts; abx0, aby0, abx1, aby1 = A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max()
        inbb = lambda r: r.x0 >= abx0 - 2 and r.x1 <= abx1 + 2 and r.y0 >= aby0 - 2 and r.y1 <= aby1 + 2
        arm_dds = [dd for dd in all_dds if inbb(dd["rect"])]

    def solve_affine(src, dst):                                     # dst = Amat @ [x,y,1]
        Mx = np.hstack([src, np.ones((len(src), 1))])
        ax = np.linalg.lstsq(Mx, dst[:, 0], rcond=None)[0]
        ay = np.linalg.lstsq(Mx, dst[:, 1], rcond=None)[0]
        return np.array([ax, ay])

    def render_piece(poly, kind, src):
        nonlocal im, dr
        if kind == "arm":                                           # база/плечо/предплечье — ЧЁРНАЯ
            dr.polygon([T(x, y) for x, y in poly.exterior.coords], fill=arm_col, outline=(150, 0, 150))
            return
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).polygon([T(x, y) for x, y in poly.exterior.coords], fill=255)
        if src is not None:                                         # лево/право = текстура кулака
            dst = np.array(poly.exterior.coords[:-1]); s = np.asarray(src, float)
            if len(s) == len(dst):
                piece_dds = dds_for(src) if len(all_dds) else arm_dds
                Am = solve_affine(s, dst)
                m = lambda x, y: T(*(Am @ np.array([x, y, 1.0])))
                layer = Image.new("RGB", im.size, (255, 255, 255)); ld = ImageDraw.Draw(layer)
                for dd in piece_dds:
                    f = col(dd.get("fill")); st = col(dd.get("color"))
                    for sub in _flat(dd):
                        if len(sub) < 2:
                            continue
                        pts = [m(x, y) for x, y in sub]
                        if f is not None and len(pts) >= 3:
                            ld.polygon(pts, fill=f)
                        if st is not None:
                            ld.line(pts + ([pts[0]] if len(pts) >= 3 else []), fill=st, width=max(1, int(dd.get("width", .5) * f_mm * sc)))
                im.paste(layer, (0, 0), mask)
        else:                                                       # боковина-полоска — цвет кисти
            im.paste(Image.new("RGB", im.size, hand_col), (0, 0), mask)
        dr.line([T(x, y) for x, y in poly.exterior.coords], fill=(0, 150, 90), width=lw)

    for hpiece in lay.get("hand_pieces", []):
        render_piece(hpiece["poly"], "hand", hpiece.get("src"))
        label(hpiece["poly"], lbl(hpiece["name"]))
    for sp in lay.get("spare_arm", []):                             # ЗАПАСНАЯ РУКА (копия)
        render_piece(sp["poly"], sp["kind"], sp.get("src"))
        label(sp["poly"], lbl(sp.get("name", "")))

    # ----- ПРИПУСК НА ШОВ +Nмм: контур реза вокруг каждого лекала (сплошной, 0.2мм) -----
    from seam import add_seams, seam_rings
    seam_mm = cfg.get("seam_allowance_mm", 10)
    add_seams(lay, seam_mm)
    for ring in seam_rings(lay):
        dr.line([T(x, y) for x, y in ring], fill=(220, 0, 0), width=lw)
    b2 = lay.get("base")                                            # отверстия: припуск внутрь
    if b2 and b2.get("fan_seam_r"):
        fx, fy = b2["fan_c"]; r = b2["fan_seam_r"]
        dr.ellipse([T(fx - r, fy - r), T(fx + r, fy + r)], outline=(220, 0, 0), width=lw)
    ah2 = lay.get("arm_hole")
    if ah2 and ah2.get("seam_r"):
        r = ah2["seam_r"]; cx, cy = ah2["cx"], ah2["cy"]
        dr.ellipse([T(cx - r, cy - r), T(cx + r, cy + r)], outline=(220, 0, 0), width=lw)
    for ah2 in lay.get("arm_holes", []):
        if ah2.get("seam_r"):
            r = ah2["seam_r"]; cx, cy = ah2["cx"], ah2["cy"]
            dr.ellipse([T(cx - r, cy - r), T(cx + r, cy + r)], outline=(220, 0, 0), width=lw)
    dr.text((M, 10), f"kontury {cfg.get('line_width_mm',0.2)} mm sploshnye | pripusk +{seam_mm:.0f} mm", fill=(0, 0, 0))

    out_png = getattr(args, "out", None) or "out/texture_view.png"
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    im.save(out_png)
    print(f"saved {out_png} {im.size} | linii {cfg.get('line_width_mm',0.2)} mm sploshnye")


if __name__ == "__main__":
    main()
