"""
Векторный экспорт всей раскладки в SVG (открывается в CorelDRAW 1:1, единицы = мм):
перёд (текстура макета, кривые Безье сохранены), боковины (акцентные цвета),
кисть (текстура через аффин), рука/днище/ножка — контуры, припуск +Nмм.
Все контуры лекал — line_width_mm (0.2мм) сплошные.

Запуск: python export_svg.py examples/arm_sep.pdf --config config.yaml -o out/layout.svg
"""
import argparse, os, sys
import pymupdf, yaml, numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from sides import (get_front_figure, contour_to_mm, build_gusset, classify_sides,          # noqa: E402
                   build_fitting_layout, front_drawings, back_drawings, detect_neck, detect_arm_side,
                   place_arm_hole, get_arm_contour, PT2MM, _palette)
from side_color import color_sides, strip_band_quads                                        # noqa: E402
from arm import detect_wrist, cut_arm, build_hand, place_hand, build_arm, place_arm          # noqa: E402
from base import build_base                                                                 # noqa: E402
from seam import add_seams                                                                  # noqa: E402

HEX = lambda c: "#%02x%02x%02x" % (int(c[0]), int(c[1]), int(c[2])) if c else "none"


def _path_d(dd, tf):
    """SVG path 'd' из векторного пути PDF (кривые Безье сохранены), координаты через tf(pt)->мм."""
    d, cur = [], None
    P = lambda p: tf(p.x, p.y)
    for it in dd["items"]:
        k = it[0]
        if k == "l":
            a, b = P(it[1]), P(it[2])
            if cur != it[1]:
                d.append(f"M{a[0]:.3f} {a[1]:.3f}")
            d.append(f"L{b[0]:.3f} {b[1]:.3f}"); cur = it[2]
        elif k == "c":
            p0, p1, p2, p3 = P(it[1]), P(it[2]), P(it[3]), P(it[4])
            if cur != it[1]:
                d.append(f"M{p0[0]:.3f} {p0[1]:.3f}")
            d.append(f"C{p1[0]:.3f} {p1[1]:.3f} {p2[0]:.3f} {p2[1]:.3f} {p3[0]:.3f} {p3[1]:.3f}"); cur = it[4]
        elif k == "re":
            r = it[1]; c = [tf(r.x0, r.y0), tf(r.x1, r.y0), tf(r.x1, r.y1), tf(r.x0, r.y1)]
            d.append("M%.3f %.3f L%.3f %.3f L%.3f %.3f L%.3f %.3f Z" %
                     (c[0][0], c[0][1], c[1][0], c[1][1], c[2][0], c[2][1], c[3][0], c[3][1])); cur = None
        elif k == "qu":
            q = it[1]; c = [tf(q.ul.x, q.ul.y), tf(q.ur.x, q.ur.y), tf(q.lr.x, q.lr.y), tf(q.ll.x, q.ll.y)]
            d.append("M%.3f %.3f L%.3f %.3f L%.3f %.3f L%.3f %.3f Z" %
                     (c[0][0], c[0][1], c[1][0], c[1][1], c[2][0], c[2][1], c[3][0], c[3][1])); cur = None
    return " ".join(d)


def _design_group(dds, tf, f_mm, title):
    """Группа SVG из векторных путей (заливки + обводки макета) с трансформом tf(pt)->мм."""
    out = [f'<g id="{title}">']
    for dd in dds:
        d = _path_d(dd, tf)
        if not d:
            continue
        fill = HEX(tuple(int(255 * v) for v in dd["fill"])) if dd.get("fill") else "none"
        if dd.get("color"):
            stroke = HEX(tuple(int(255 * v) for v in dd["color"]))
            sw = max(dd.get("width", 0.3) * f_mm, 0.05)
            out.append(f'<path d="{d}" fill="{fill}" fill-rule="evenodd" stroke="{stroke}" stroke-width="{sw:.3f}"/>')
        else:
            out.append(f'<path d="{d}" fill="{fill}" fill-rule="evenodd"/>')
    out.append("</g>")
    return "\n".join(out)


def _tex_tf(src_poly, dst_poly, f_mm):
    """tf точки макета (pt) -> координаты размещённой панели (мм): аффин src_контур->dst_контур
    (учитывает поворот/сдвиг при nesting)."""
    src = np.array(src_poly.exterior.coords[:-1]); dst = np.array(dst_poly.exterior.coords[:-1])
    n = min(len(src), len(dst)); src, dst = src[:n], dst[:n]
    M = np.hstack([src, np.ones((n, 1))])
    ax = np.linalg.lstsq(M, dst[:, 0], rcond=None)[0]; ay = np.linalg.lstsq(M, dst[:, 1], rcond=None)[0]
    return lambda x, y: (float(ax @ (x * f_mm, y * f_mm, 1.0)), float(ay @ (x * f_mm, y * f_mm, 1.0)))


def _poly(pts, fill="none", stroke="#000", sw=0.2, closed=True):
    p = " ".join(f"{x:.3f},{y:.3f}" for x, y in pts)
    tag = "polygon" if closed else "polyline"
    return f'<{tag} points="{p}" fill="{fill}" stroke="{stroke}" stroke-width="{sw:.3f}"/>'


def build_layout(page, cfg):
    """Полная раскладка: перёд, боковины(цвет), кисть, рука, днище + припуски.
    Возвращает (lay, fig, contour_mm, g, arm_pts, seam_mm)."""
    from sides import smooth_front
    fig = get_front_figure(page, cfg)

    cut_arms = []                                                # отрезанные руки (PT-контуры) — режем ДО раскладки
    if cfg.get("arm_cut") == "armpit":
        from arm import detect_armpits, cut_across_arm
        body_pt = fig.contour_pt                                 # силуэт переда в PT (фон уже убран)
        arms = detect_armpits(body_pt, cfg)
        if "expected_arms" in cfg and len(arms) != cfg["expected_arms"]:
            raise RuntimeError(f"ожидалось {cfg['expected_arms']} рук, найдено {len(arms)}")
        for a in arms:
            arm_poly, body_pt = cut_across_arm(body_pt, a["f"], a["tip"], cfg, blade=a.get("blade"))
            if arm_poly is None:
                raise RuntimeError("рез от подмышки не разделил тело — проверь пороги")
            cut_arms.append({"poly_pt": arm_poly, "f": a["f"], "tip": a["tip"]})
        fig.contour_pt = body_pt                                 # ТЕЛО БЕЗ РУКИ — дальше всё по нему

    contour_mm = smooth_front(contour_to_mm(fig), cfg)
    g = build_gusset(contour_mm, cfg); classify_sides(g, contour_mm)
    lay = build_fitting_layout(contour_mm, g, base=build_base(g, cfg))
    arm_pts = None
    if cfg.get("make_appendages", True):                         # рука/кисть/запаска (выкл для фигур без руки)
        arm_side = detect_arm_side(page, cfg)
        if cfg.get("arm_from_contour"):                          # рука-конусы из реальной формы
            from arm import build_arm_measured
            pieces, _ = build_arm_measured(page, fig, cfg)
        else:                                                    # ЭТАЛОННАЯ рука (шаблон, Ø250) — как у космонавта
            pieces = build_arm(cfg)
        place_arm(lay, pieces, arm_side)
        if cfg.get("arm_hole_at_shoulder"):                      # отверстие по плечу (фигуры без шеи)
            from arm import arm_shoulder_xy
            from sides import place_arm_hole_xy
            place_arm_hole_xy(lay, g, contour_mm, arm_shoulder_xy(page, fig, cfg), cfg)
        else:                                                    # отверстие по шее (космонавт)
            place_arm_hole(lay, detect_neck(contour_mm, g, cfg), arm_side, cfg)
        if cfg.get("make_hand", True):
            arm_pts, body_x = get_arm_contour(page)
            if cfg.get("wrist_by_color"):                        # кисть по цвету (перчатка↔рукав)
                from arm import detect_wrist_color
                wr = detect_wrist_color(page, arm_pts, cfg)
            else:
                wr = detect_wrist(arm_pts.tolist(), body_x=body_x)
            if wr:
                hp, fp = cut_arm(arm_pts, wr["wrist"], wr["tip"])
                place_hand(lay, build_hand(hp, fp, wr["wrist"], fig.s * PT2MM), arm_side)
    from sides import add_back_panel
    add_back_panel(lay, contour_mm, fig, cfg, page)             # ЗАД (реальная задняя фигура) — ниже руки

    if cfg.get("arm_cut") == "armpit":                          # разместить отрезанные руки + отверстия
        from sides import place_armpit_hole
        if cfg.get("arm_template"):                             # РУКА ПО ШАБЛОНУ: база→плечо→предплечье→кисть
            from arm import build_arm_from_poly, place_arm, place_hand
            scale = fig.s * PT2MM
            for i, ca in enumerate(cut_arms):
                res = build_arm_from_poly(page, ca["poly_pt"], ca["tip"], scale, cfg)
                side = "left" if i == 0 else "right"           # 1-я рука — колонка слева, 2-я — справа
                place_arm(lay, res["pieces"], side)            # снизу вверх: база, плечо, предплечье
                place_hand(lay, res["hand"], side)             # кисть (перёд|боковина|зад) над рукой
                if arm_pts is None:
                    arm_pts = np.asarray(res["hand"]["src_pts"], float)  # для текстуры кисти (arm_dds)
                place_armpit_hole(lay, g, contour_mm, ca["f"], fig, cfg)
        else:                                                  # отрезанная рука ОДНИМ куском (текстура)
            from arm import place_arm_piece
            for ca in cut_arms:
                ap = place_arm_piece(lay, ca["poly_pt"], fig, cfg, page)
                if arm_pts is None:
                    arm_pts = ap
                place_armpit_hole(lay, g, contour_mm, ca["f"], fig, cfg)

    if cfg.get("make_appendages", True) and cfg.get("make_spare_arm", True):
        from arm import add_spare_arm
        add_spare_arm(lay)                                        # запасная рука (копия) — ниже всего
    color_sides(lay, g, contour_mm, fig, page, front_drawings, cfg)
    seam_mm = cfg.get("seam_allowance_mm", 10)
    if cfg.get("roll_pack"):                                     # nesting в рулон (высота = ширина рулона)
        from pack import nest_roll
        nest_roll(lay, roll_h=cfg.get("roll_width_mm", 1440), gap=cfg.get("roll_gap_mm", 2 * seam_mm + 10))
    elif cfg.get("repack"):                                      # разложить без наложений
        from pack import repack_layout
        repack_layout(lay, gap=2 * seam_mm + 40)
    add_seams(lay, seam_mm)
    return lay, fig, contour_mm, g, arm_pts, seam_mm


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml"); ap.add_argument("-o", "--out", default="out/layout.svg")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]

    lay, fig, contour_mm, g, arm_pts, seam_mm = build_layout(page, cfg)
    lw = cfg.get("line_width_mm", 0.2)
    f_mm = fig.s * PT2MM

    W, H = lay["sheet_w"], lay["sheet_h"]
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}mm" height="{H:.2f}mm" '
           f'viewBox="0 0 {W:.2f} {H:.2f}">',
           '<rect width="100%" height="100%" fill="white"/>']

    # ПЕРЁД: текстура макета (вектор, кривые) — через аффин контур->панель (учитывает поворот)
    svg.append(_design_group(front_drawings(page)[0], _tex_tf(contour_mm, lay["front"], f_mm), f_mm, "front"))
    svg.append(_poly(list(lay["front"].exterior.coords), stroke="#000", sw=lw))      # линия шва переда
    bk0 = lay.get("back")
    if bk0:
        svg.append(_design_group(back_drawings(page)[0], _tex_tf(bk0["src"], bk0["poly"], bk0["f_mm"]), bk0["f_mm"], "back"))
        svg.append(_poly(list(bk0["poly"].exterior.coords), stroke="#000", sw=lw))

    # БОКОВИНЫ: акцентные цвета + контур
    svg.append('<g id="strips">')
    for s in lay["strips"]:
        for quad, c in strip_band_quads(s):
            svg.append(_poly(quad, fill=HEX(c), stroke="none", sw=0))
        svg.append(_poly(list(s["poly"]), stroke=HEX(_palette(s["i"])), sw=lw))
    svg.append("</g>")

    # КИСТЬ/РУКА: текстура через аффин src(pt)->poly + контур. arm_dds — по bbox КАЖДОГО куска
    # с src (несколько рук C → у каждой свой участок макета).
    all_dds = [dd for dd in page.get_drawings()
               if dd.get("fill") is not None or dd.get("color") is not None]
    def _dds_for(src_pts):
        s = np.asarray(src_pts, float)
        x0, y0, x1, y1 = s[:, 0].min(), s[:, 1].min(), s[:, 0].max(), s[:, 1].max()
        return [dd for dd in all_dds if (dd["rect"].x0 >= x0 - 2 and dd["rect"].x1 <= x1 + 2
                                         and dd["rect"].y0 >= y0 - 2 and dd["rect"].y1 <= y1 + 2)]
    svg.append('<g id="hand">')
    for hpiece in lay.get("hand_pieces", []):
        poly = hpiece["poly"]
        if hpiece.get("src") is not None:
            src = np.asarray(hpiece["src"], float); dst = np.array(poly.exterior.coords[:-1])
            if len(src) == len(dst):
                arm_dds = _dds_for(hpiece["src"])
                Mx = np.hstack([src, np.ones((len(src), 1))])
                ax = np.linalg.lstsq(Mx, dst[:, 0], rcond=None)[0]; ay = np.linalg.lstsq(Mx, dst[:, 1], rcond=None)[0]
                clip_id = f"clip{id(hpiece)}"
                svg.append(f'<clipPath id="{clip_id}">' + _poly(list(poly.exterior.coords), fill="#000", stroke="none", sw=0) + '</clipPath>')
                svg.append(f'<g clip-path="url(#{clip_id})">')
                svg.append(_design_group(arm_dds, lambda x, y: (ax @ [x, y, 1.0], ay @ [x, y, 1.0]), f_mm, "fist"))
                svg.append("</g>")
        else:
            svg.append(_poly(list(poly.exterior.coords), fill="#f5f5f5", stroke="none", sw=0))
        svg.append(_poly(list(poly.exterior.coords), stroke="#009659", sw=lw))
    svg.append("</g>")

    # РУКА (база/плечо/предплечье), ДНИЩЕ, ножка, отверстия — контуры
    svg.append('<g id="arm">')
    for apc in lay.get("arm_pieces", []):
        svg.append(_poly(list(apc["poly"].exterior.coords), stroke="#960096", sw=lw))
    svg.append("</g>")
    b = lay.get("base")
    if b:
        svg.append(_poly(list(b["dnishe"].exterior.coords), stroke="#000", sw=lw))
        lx, ly, lwd, lh = b["leg"]
        svg.append(_poly([(lx, ly), (lx + lwd, ly), (lx + lwd, ly + lh), (lx, ly + lh)], stroke="#000", sw=lw))
        fx, fy = b["fan_c"]; r = b["fan_d"] / 2
        svg.append(f'<circle cx="{fx:.3f}" cy="{fy:.3f}" r="{r:.3f}" fill="none" stroke="#dc0000" stroke-width="{lw}"/>')
        if b.get("fan_seam_r"):
            svg.append(f'<circle cx="{fx:.3f}" cy="{fy:.3f}" r="{b["fan_seam_r"]:.3f}" fill="none" stroke="#dc0000" stroke-width="{lw}"/>')
    ah = lay.get("arm_hole")
    if ah:
        svg.append(f'<circle cx="{ah["cx"]:.3f}" cy="{ah["cy"]:.3f}" r="{ah["r"]:.3f}" fill="none" stroke="#dc0000" stroke-width="{lw}"/>')
        if ah.get("seam_r"):
            svg.append(f'<circle cx="{ah["cx"]:.3f}" cy="{ah["cy"]:.3f}" r="{ah["seam_r"]:.3f}" fill="none" stroke="#dc0000" stroke-width="{lw}"/>')
    for ah in lay.get("arm_holes", []):                             # отверстия Ø250 (по одному на руку)
        svg.append(f'<circle cx="{ah["cx"]:.3f}" cy="{ah["cy"]:.3f}" r="{ah["r"]:.3f}" fill="none" stroke="#dc0000" stroke-width="{lw}"/>')
        if ah.get("seam_r"):
            svg.append(f'<circle cx="{ah["cx"]:.3f}" cy="{ah["cy"]:.3f}" r="{ah["seam_r"]:.3f}" fill="none" stroke="#dc0000" stroke-width="{lw}"/>')

    # ПРИПУСК НА ШОВ +Nмм (сплошной)
    from seam import seam_rings
    svg.append('<g id="seam_allowance">')
    for ring in seam_rings(lay):
        svg.append(_poly(ring, stroke="#dc0000", sw=lw))
    svg.append("</g>")

    svg.append("</svg>")
    open(args.out, "w", encoding="utf-8").write("\n".join(svg))
    sz = os.path.getsize(args.out) / 1024
    print(f"saved {args.out} | лист {W:.0f}x{H:.0f} мм | контуры {lw} мм | припуск +{seam_mm:.0f} мм | {sz:.0f} KB")


if __name__ == "__main__":
    main()
