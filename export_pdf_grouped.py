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


def _hand_tex(poly, src, arm_dds, f_mm, cid):
    """Текстура кисти через аффин src(pt)->poly, обрезанная по контуру детали."""
    s = np.asarray(src, float); dst = np.array(poly.exterior.coords[:-1])
    if len(s) != len(dst):
        return []
    Mx = np.hstack([s, np.ones((len(s), 1))])
    ax = np.linalg.lstsq(Mx, dst[:, 0], rcond=None)[0]; ay = np.linalg.lstsq(Mx, dst[:, 1], rcond=None)[0]
    return [f'<clipPath id="{cid}">' + _poly(list(poly.exterior.coords), fill="#000", stroke="none", sw=0) + '</clipPath>',
            f'<g clip-path="url(#{cid})">'
            + _design_group(arm_dds, lambda x, y: (ax @ [x, y, 1.0], ay @ [x, y, 1.0]), f_mm, "fist") + '</g>']


def _piece_svg(p, kind, arm_dds, f_mm, lw, cid, arm_fill="none", hand_fill="#f5f5f5"):
    """SVG-элементы одной детали руки/кисти: текстура/контур + припуск."""
    poly = p["poly"]; body = []
    if kind == "hand":
        if p.get("src") is not None:
            body += _hand_tex(poly, p["src"], arm_dds, f_mm, cid)
        else:                                                        # полоска-боковина = цвет кисти
            body.append(_poly(list(poly.exterior.coords), fill=hand_fill, stroke="none", sw=0))
        body.append(_poly(list(poly.exterior.coords), stroke="#009659", sw=lw))
    else:                                                            # база/плечо/предплечье — цвет своей зоны
        pc = p.get("fill")
        fill = ("#%02x%02x%02x" % tuple(int(v) for v in pc)) if pc else arm_fill
        body.append(_poly(list(poly.exterior.coords), fill=fill, stroke="#960096", sw=lw))
    if p.get("seam"):
        body.append(_poly(list(p["seam"].exterior.coords), stroke="#dc0000", sw=lw))
    return body


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config.yaml"); ap.add_argument("-o", "--out", default="out/layout_grouped.pdf")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    page = pymupdf.open(args.input_pdf)[0]

    lay, fig, contour_mm, g, arm_pts, seam_mm = build_layout(page, cfg)
    lw = cfg.get("line_width_mm", 0.2); f_mm = fig.s * PT2MM
    W, H = lay["sheet_w"], lay["sheet_h"]
    ah = lay.get("arm_hole")

    all_dds = [dd for dd in page.get_drawings()
               if dd.get("fill") is not None or dd.get("color") is not None]
    def _dds_for_src(src_pts):                                     # заливки макета в bbox куска (по его src)
        s = np.asarray(src_pts, float)
        x0, y0, x1, y1 = s[:, 0].min(), s[:, 1].min(), s[:, 0].max(), s[:, 1].max()
        return [dd for dd in all_dds if (dd["rect"].x0 >= x0 - 2 and dd["rect"].x1 <= x1 + 2
                                         and dd["rect"].y0 >= y0 - 2 and dd["rect"].y1 <= y1 + 2)]
    arm_dds = []
    if arm_pts is not None:
        A = arm_pts; abx0, aby0, abx1, aby1 = A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max()
        inbb = lambda r: r.x0 >= abx0 - 2 and r.x1 <= abx1 + 2 and r.y0 >= aby0 - 2 and r.y1 <= aby1 + 2
        arm_dds = [dd for dd in all_dds if inbb(dd["rect"])]
    from side_color import arm_fill_color, base_fill_color                    # цвета руки/днища по 2D
    from sides import get_arm_contour
    arm_fill = HEX(arm_fill_color(page, arm_pts if arm_pts is not None else get_arm_contour(page)[0])) \
        if (lay.get("arm_pieces") or lay.get("spare_arm")) else "none"
    base_fill = HEX(base_fill_color(page, front_drawings)) if lay.get("base") else "none"
    from side_color import hand_fill_color
    _hsrc = next((hp.get("src") for hp in lay.get("hand_pieces", []) if hp.get("src") is not None), None)
    hand_fill = HEX(hand_fill_color(page, _hsrc)) if _hsrc is not None else "#f5f5f5"  # полоска-боковина кисти
    cc = [0]
    nextid = lambda: (cc.__setitem__(0, cc[0] + 1) or f"clip{cc[0]}")

    pieces = []                                                       # (name, [svg-элементы])

    if lay.get("roll"):                                               # фон-прямоугольник = рулон (1440 x длина)
        pieces.append((f"rulon_{int(lay['roll'])}mm",
                       [f'<rect x="0" y="0" width="{W:.2f}" height="{H:.2f}" fill="none" stroke="#888" stroke-width="{lw}"/>']))

    # фильтр заливок: оставить те, чей центр ВНУТРИ обрезанного контура (без руки) — убирает
    # текстуру отрезанной руки БЕЗ clipPath (clipPath заставляет cairosvg растеризовать -> 180MB).
    from shapely.geometry import Point as _Pt
    from shapely.prepared import prep as _prep
    def _inside_fills(dds, poly_pt):
        pp = _prep(poly_pt.buffer(2))
        return [dd for dd in dds if pp.contains(_Pt((dd["rect"].x0 + dd["rect"].x1) / 2, (dd["rect"].y0 + dd["rect"].y1) / 2))]

    # КЛЮЧ СБОРКИ: дуги стыков с боковинами — цвет = цвет боковины. Подсветка лежит В КОЛЬЦЕ
    # ПРИПУСКА (смещена НАРУЖУ на ~половину припуска, ширина < припуска) — НЕ заходит за контур
    # (рекламная конструкция, строго). На переде по gusset_line, на заде — зеркало переда.
    # Считаем ДО сборки, кладём дуги ВНУТРЬ группы переда/зада -> деталь цельная.
    from shapely.geometry import Point as _Pt2
    from shapely.prepared import prep as _prep2
    seam_mm_v = cfg.get("seam_allowance_mm", 10)
    off = seam_mm_v / 2.0; sw_seam = max(2.0, seam_mm_v - 3.0)       # в пределах кольца, не за контур

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

    tf_f = _tex_tf(contour_mm, lay["front"], f_mm); gl = g["gusset_line"]
    front_arcs = []
    for s in g["segments"]:
        a0, a1 = s["arc0"], s["arc1"]; n = max(2, int(abs(a1 - a0) / 6))
        pts = [tf_f(*(np.asarray(gl.interpolate(a0 + (a1 - a0) * k / n).coords[0]) / f_mm)) for k in range(n + 1)]
        front_arcs.append((HEX(_palette(s["i"])), pts))

    def _arc_paths(arcs, panel, gid):
        pp = _prep2(panel); out = [f'<g id="{gid}">']
        for col, pts in arcs:
            d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in _offset_out(pts, pp, off))
            out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{sw_seam}" stroke-linecap="round"/>')
        out.append("</g>")
        return "\n".join(out)

    # ПЕРЁД (текстура без руки + контур + припуск + цветной ключ-полоска — всё в одной группе)
    fr_in = _inside_fills(front_drawings(page, cfg)[0], fig.contour_pt)
    pieces.append(("front", [_design_group(fr_in, _tex_tf(contour_mm, lay["front"], f_mm), f_mm, "front_tex"),
                             _poly(list(lay["front"].exterior.coords), stroke="#000", sw=lw),
                             _poly(list(lay["front_seam"].exterior.coords), stroke="#dc0000", sw=lw),
                             _arc_paths(front_arcs, lay["front"], "seam_front")]))

    # ЗАД (текстура без руки + контур + припуск + ключ-полоска зеркально — всё в одной группе)
    bk = lay.get("back")
    if bk:
        from sides import back_drawings
        bk_in = _inside_fills(back_drawings(page, cfg)[0], bk["cut_pt"]) if bk.get("cut_pt") else back_drawings(page, cfg)[0]
        bbody = [_design_group(bk_in, _tex_tf(bk["src"], bk["poly"], bk["f_mm"]), bk["f_mm"], "back_tex"),
                 _poly(list(bk["poly"].exterior.coords), stroke="#000", sw=lw)]
        if bk.get("seam"):
            bbody.append(_poly(list(bk["seam"].exterior.coords), stroke="#dc0000", sw=lw))
        fb = lay["front"].bounds; bbx = bk["poly"].bounds              # зеркало дуг переда в bbox зада
        fw = (fb[2] - fb[0]) or 1.0; fh = (fb[3] - fb[1]) or 1.0; bw = bbx[2] - bbx[0]; bh = bbx[3] - bbx[1]
        mir = lambda p: (bbx[0] + (1 - (p[0] - fb[0]) / fw) * bw, bbx[1] + (p[1] - fb[1]) / fh * bh)
        back_arcs = [(col, [mir(p) for p in pts]) for col, pts in front_arcs]
        bbody.append(_arc_paths(back_arcs, bk["poly"], "seam_back"))
        pieces.append(("back", bbody))

    # БОКОВИНЫ (цвет-зоны + ЧЁРНЫЙ контур + цветной ключ-полоса вдоль стыков в припуске + припуск)
    from side_color import _edges_top_down
    from shapely.geometry import Polygon as _Poly
    for s in lay["strips"]:
        body = [_poly(q, fill=HEX(c), stroke="none", sw=0) for q, c in strip_band_quads(s)]
        # цветной ключ: тот же цвет, что дуга переда (_palette[i]), вдоль ОБОИХ длинных рёбер
        # (стык с передом и с задом), смещён НАРУЖУ в кольцо припуска — не заходит за контур.
        spp = _prep2(_Poly(list(s["poly"])))
        le, re = _edges_top_down(list(s["poly"]), s.get("waist") is not None)
        keycol = HEX(_palette(s["i"]))
        for edge in (le, re):
            d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in _offset_out(edge, spp, off))
            body.append(f'<path d="{d}" fill="none" stroke="{keycol}" stroke-width="{sw_seam}" stroke-linecap="round"/>')
        body.append(_poly(list(s["poly"]), stroke="#000", sw=lw))      # контур детали — чёрный
        if s.get("seam"):
            body.append(_poly(list(s["seam"].exterior.coords), stroke="#dc0000", sw=lw))
        if ah and ah.get("piece") == s["i"]:
            body.append(_circle(ah["cx"], ah["cy"], ah["r"], "#dc0000", lw))
            if ah.get("seam_r"):
                body.append(_circle(ah["cx"], ah["cy"], ah["seam_r"], "#dc0000", lw))
        for h in lay.get("arm_holes", []):                            # отверстия рук (список) на своей боковине
            if h.get("piece") == s["i"]:
                body.append(_circle(h["cx"], h["cy"], h["r"], "#dc0000", lw))
                if h.get("seam_r"):
                    body.append(_circle(h["cx"], h["cy"], h["seam_r"], "#dc0000", lw))
        pieces.append((f"bokovina_{s['i']}", body))

    # КИСТЬ и РУКА — каждая деталь своей группой (текстура по bbox src этого куска)
    for hp in lay.get("hand_pieces", []):
        dds = _dds_for_src(hp["src"]) if hp.get("src") is not None else arm_dds
        pieces.append((f"kist_{hp['name']}", _piece_svg(hp, "hand", dds, f_mm, lw, nextid(), hand_fill=hand_fill)))
    for apc in lay.get("arm_pieces", []):
        pieces.append((f"ruka_{apc.get('name','?')}", _piece_svg(apc, "arm", arm_dds, f_mm, lw, nextid(), arm_fill)))

    # ЗАПАСНАЯ РУКА — ВСЕ детали одной группой
    spare = lay.get("spare_arm", [])
    if spare:
        body = []
        for p in spare:
            body += _piece_svg(p, p["kind"], arm_dds, f_mm, lw, nextid(), arm_fill, hand_fill)
        pieces.append(("zapasnaya_ruka", body))

    # ДНИЩЕ (+ отверстие) и НОЖКА
    b = lay.get("base")
    if b:
        db = [_poly(list(b["dnishe"].exterior.coords), fill=base_fill, stroke="#000", sw=lw)]
        fx, fy = b["fan_c"]; r = b["fan_d"] / 2
        db.append(_circle(fx, fy, r, "#dc0000", lw))
        if b.get("fan_seam_r"):
            db.append(_circle(fx, fy, b["fan_seam_r"], "#dc0000", lw))
        if b.get("dnishe_seam"):
            db.append(_poly(list(b["dnishe_seam"].exterior.coords), stroke="#dc0000", sw=lw))
        pieces.append(("dnishe", db))
        if b.get("leg_poly") is not None:                            # ножка повёрнута (nesting)
            leg_pts = list(b["leg_poly"].exterior.coords)
        else:
            lx, ly, lwd, lh = b["leg"]
            leg_pts = [(lx, ly), (lx + lwd, ly), (lx + lwd, ly + lh), (lx, ly + lh)]
        lb = [_poly(leg_pts, fill=base_fill, stroke="#000", sw=lw)]
        if b.get("leg_seam"):
            lb.append(_poly(list(b["leg_seam"].exterior.coords), stroke="#dc0000", sw=lw))
        pieces.append(("nozhka", lb))

    # сборка: каждая запись pieces -> отдельный Form XObject (= группа в Corel)
    hdr = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}mm" height="{H:.2f}mm" '
           f'viewBox="0 0 {W:.2f} {H:.2f}">')

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
