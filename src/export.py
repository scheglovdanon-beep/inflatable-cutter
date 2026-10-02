"""
Экспорт фигуры: PDF (оригинальный вектор, обтравленный по контуру + слой линии реза),
SVG (clipPath), PNG-превью. Обтравка = clipping path (W n) в потоке содержимого.
"""
import pymupdf
import pikepdf

PT2MM = 25.4 / 72.0


# ---------- общие эмиттеры путей ----------
def _pdf_path(items, P):
    """Операторы пути PDF (координаты через P(x,y)->'X Y'). Отслеживаем текущую точку."""
    out = []; cur = None
    def close(a, b): return a and abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3
    for it in items:
        k = it[0]
        if k == "l":
            a = (it[1].x, it[1].y); b = (it[2].x, it[2].y)
            if not close(cur, a): out.append(f"{P(*a)} m")
            out.append(f"{P(*b)} l"); cur = b
        elif k == "c":
            a = (it[1].x, it[1].y)
            if not close(cur, a): out.append(f"{P(*a)} m")
            out.append(f"{P(it[2].x, it[2].y)} {P(it[3].x, it[3].y)} {P(it[4].x, it[4].y)} c")
            cur = (it[4].x, it[4].y)
        elif k == "re":
            r = it[1]
            out.append(f"{P(r.x0, r.y0)} m {P(r.x1, r.y0)} l {P(r.x1, r.y1)} l {P(r.x0, r.y1)} l h"); cur = None
        elif k == "qu":
            q = it[1]
            out.append(f"{P(q.ul.x, q.ul.y)} m {P(q.ur.x, q.ur.y)} l {P(q.lr.x, q.lr.y)} l {P(q.ll.x, q.ll.y)} l h"); cur = None
    return " ".join(out)


def _contour_pdf(coords, P):
    return " ".join(f"{P(x, y)} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(coords)) + " h"


# ---------- PDF ----------
def figure_pdf_content(fig) -> bytes:
    H, s, x0, y1 = fig.page_H, fig.s, fig.x0, fig.y1
    def P(x, y): return f"{x:.3f} {H - y:.3f}"
    cm = f"{s:.5f} 0 0 {s:.5f} {-x0 * s:.3f} {-(H - y1) * s:.3f} cm"
    cp = _contour_pdf(fig.contour_pt.exterior.coords, P)

    buf = [f"q {cm}", f"{cp} W n"]                        # масштаб + обтравка по контуру
    for d in fig.drawings:
        ops = _pdf_path(d["items"], P)
        if not ops:
            continue
        f, st, eo = d.get("fill"), d.get("color"), d.get("even_odd")
        pre = []
        if f is not None:
            pre.append(f"{f[0]:.4f} {f[1]:.4f} {f[2]:.4f} rg")
        if st is not None:
            pre.append(f"{st[0]:.4f} {st[1]:.4f} {st[2]:.4f} RG {max(d.get('width', 0.5) or 0.5, 0.3):.2f} w")
        paint = "B" if (f is not None and st is not None) else ("f" if f is not None else "S")
        if eo and paint[0] in "fB":
            paint += "*"
        buf.append(" ".join(pre) + " " + ops + " " + paint)
    buf.append("Q")
    # линия реза — отдельным слоем поверх (синяя, не обтравливается)
    buf.append(f"q {cm} {cp} 0 0 1 RG 1.5 w S Q")
    return "\n".join(buf).encode()


def save_pdf(fig, path):
    pdf = pikepdf.Pdf.new()
    pg = pdf.add_blank_page(page_size=(round(fig.out_w_pt, 2), round(fig.out_h_pt, 2)))
    pg.Contents = pdf.make_stream(figure_pdf_content(fig))
    pg.Resources = pikepdf.Dictionary()
    pdf.save(path)


# ---------- SVG ----------
def _svg_path_d(items, M):
    out = []; cur = None
    def close(a, b): return a and abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3
    for it in items:
        k = it[0]
        if k == "l":
            a = (it[1].x, it[1].y); b = (it[2].x, it[2].y)
            if not close(cur, a): out.append("M%.2f %.2f" % M(*a))
            out.append("L%.2f %.2f" % M(*b)); cur = b
        elif k == "c":
            a = (it[1].x, it[1].y)
            if not close(cur, a): out.append("M%.2f %.2f" % M(*a))
            out.append("C%.2f %.2f %.2f %.2f %.2f %.2f" % (M(it[2].x, it[2].y) + M(it[3].x, it[3].y) + M(it[4].x, it[4].y)))
            cur = (it[4].x, it[4].y)
        elif k == "re":
            r = it[1]
            out.append("M%.2f %.2f L%.2f %.2f L%.2f %.2f L%.2f %.2f Z" %
                       (M(r.x0, r.y0) + M(r.x1, r.y0) + M(r.x1, r.y1) + M(r.x0, r.y1))); cur = None
        elif k == "qu":
            q = it[1]
            out.append("M%.2f %.2f L%.2f %.2f L%.2f %.2f L%.2f %.2f Z" %
                       (M(q.ul.x, q.ul.y) + M(q.ur.x, q.ur.y) + M(q.lr.x, q.lr.y) + M(q.ll.x, q.ll.y))); cur = None
    return " ".join(out)


def _hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in c)


def save_svg(fig, path):
    smm = fig.s * PT2MM
    def M(x, y): return ((x - fig.x0) * smm, (y - fig.y0) * smm)
    W, Hh = fig.out_w_mm, fig.out_h_mm
    cd = " ".join("%s%.2f %.2f" % ("M" if i == 0 else "L", *M(x, y))
                  for i, (x, y) in enumerate(fig.contour_pt.exterior.coords)) + " Z"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}mm" height="{Hh:.1f}mm" '
             f'viewBox="0 0 {W:.2f} {Hh:.2f}">',
             f'<defs><clipPath id="cut"><path d="{cd}"/></clipPath></defs>',
             '<g clip-path="url(#cut)">']
    for d in fig.drawings:
        dd = _svg_path_d(d["items"], M)
        if not dd:
            continue
        f, st, eo = d.get("fill"), d.get("color"), d.get("even_odd")
        attrs = [f'fill="{_hex(f)}"' if f is not None else 'fill="none"']
        if eo:
            attrs.append('fill-rule="evenodd"')
        if st is not None:
            attrs.append(f'stroke="{_hex(st)}" stroke-width="{max((d.get("width") or 0.5), 0.3) * smm:.2f}"')
        parts.append(f'<path d="{dd}" {" ".join(attrs)}/>')
    parts.append('</g>')
    parts.append(f'<path d="{cd}" fill="none" stroke="#0000ff" stroke-width="{1.5 * smm:.2f}"/>')  # линия реза
    parts.append('</svg>')
    open(path, "w", encoding="utf-8").write("\n".join(parts))


# ---------- PNG ----------
def save_png(pdf_path, png_path, target_px_h=1600):
    doc = pymupdf.open(pdf_path)
    pg = doc[0]
    zoom = target_px_h / pg.rect.height
    doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(png_path)


# ---------- боковые полосы-лекала ----------
MM2PT = 72.0 / 25.4


def save_sides_pdf(strips, sheet_w_mm, sheet_h_mm, path):
    """Полосы-лекала: прямоугольники (линия реза) + подписи №/длина. Единицы — мм."""
    doc = pymupdf.open()
    pg = doc.new_page(width=sheet_w_mm * MM2PT, height=sheet_h_mm * MM2PT)
    sh = pg.new_shape()
    for st in strips:
        r = pymupdf.Rect(st["x"] * MM2PT, st["y"] * MM2PT,
                         (st["x"] + st["w"]) * MM2PT, (st["y"] + st["len"]) * MM2PT)
        sh.draw_rect(r)
    sh.finish(color=(0, 0, 1), width=1.0)          # линия реза
    sh.commit()
    for st in strips:
        pg.insert_text(pymupdf.Point(st["x"] * MM2PT, (st["y"] - 8) * MM2PT),
                       f'#{st["i"]}  {st["len"]:.0f}x{st["w"]:.0f}mm', fontsize=8 * MM2PT)
    doc.save(path)


def save_fitting_svg(layout, path):
    """Примерочная раскладка: перёд-контур в центре + боковины по сторонам (вектор)."""
    W, H = layout["sheet_w"], layout["sheet_h"]
    front = layout["front"]
    d = " ".join("%s%.2f %.2f" % ("M" if i == 0 else "L", x, y)
                 for i, (x, y) in enumerate(front.exterior.coords)) + " Z"
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}mm" height="{H:.1f}mm" '
         f'viewBox="0 0 {W:.2f} {H:.2f}">',
         f'<path d="{d}" fill="none" stroke="#777777" stroke-width="2"/>']
    for s in layout["strips"]:
        col = "#c80000" if s["side"] == "left" else "#0000d2"
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in s["poly"])
        p.append(f'<polygon points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
        p.append(f'<text x="{s["poly"][0][0]:.2f}" y="{s["poly"][0][1] - 10:.2f}" font-size="24" '
                 f'fill="{col}">#{s["i"]} ребро{s["edge"]:.0f} т{s["t_top"]:.0f}/{s["t_bot"]:.0f}</text>')
    b = layout.get("base")
    if b:
        poly = " ".join(f"{x:.2f},{y:.2f}" for x, y in b["dnishe"].exterior.coords)
        fx, fy = b["fan_c"]; lx, ly, lw, lh = b["leg"]
        p.append(f'<polygon points="{poly}" fill="none" stroke="#000" stroke-width="2"/>')
        p.append(f'<circle cx="{fx:.2f}" cy="{fy:.2f}" r="{b["fan_d"]/2:.2f}" fill="none" stroke="#e00000" stroke-width="3"/>')
        p.append(f'<rect x="{lx:.2f}" y="{ly:.2f}" width="{lw:.2f}" height="{lh:.2f}" fill="none" stroke="#000" stroke-width="2"/>')
    ah = layout.get("arm_hole")
    if ah:
        p.append(f'<circle cx="{ah["cx"]:.2f}" cy="{ah["cy"]:.2f}" r="{ah["r"]:.2f}" fill="none" stroke="#e00000" stroke-width="3"/>')
    for ap in layout.get("arm_pieces", []):
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in ap["poly"].exterior.coords)
        p.append(f'<polygon points="{pts}" fill="none" stroke="#b000b0" stroke-width="2"/>')
    p.append('</svg>')
    open(path, "w", encoding="utf-8").write("\n".join(p))


def _base_sheet(b, margin, gap):
    dnx0, dny0, dnx1, dny1 = b["dnishe"].bounds
    dw, dh = dnx1 - dnx0, dny1 - dny0
    sheet_w = dw + gap + b["leg_w"] + 2 * margin
    sheet_h = max(dh, b["leg_h"]) + 2 * margin + 40
    return dw, dh, sheet_w, sheet_h


def save_base_pdf(b, path, margin=60, gap=120):
    """Днище (полоса по нижней кромке + красное отверстие) и ножка (прямоугольник)."""
    dw, dh, sheet_w, sheet_h = _base_sheet(b, margin, gap)
    doc = pymupdf.open()
    pg = doc.new_page(width=sheet_w * MM2PT, height=sheet_h * MM2PT)
    ox, oy = margin, margin + 40
    sh = pg.new_shape()
    pts = [pymupdf.Point((ox + x) * MM2PT, (oy + y) * MM2PT) for x, y in b["dnishe"].exterior.coords]
    sh.draw_polyline(pts); sh.finish(color=(0, 0, 0), width=1.0, closePath=True)
    fx, fy = b["fan_c"]
    sh.draw_circle(pymupdf.Point((ox + fx) * MM2PT, (oy + fy) * MM2PT), b["fan_d"] / 2 * MM2PT)
    sh.finish(color=(0.88, 0, 0), width=1.5)
    lx, ly = margin + dw + gap, margin + 40
    sh.draw_rect(pymupdf.Rect(lx * MM2PT, ly * MM2PT, (lx + b["leg_w"]) * MM2PT, (ly + b["leg_h"]) * MM2PT))
    sh.finish(color=(0, 0, 0), width=1.0)
    sh.commit()
    doc.save(path)


def save_base_svg(b, path, margin=60, gap=120):
    """Днище (полигон по нижней кромке + красное отверстие Ø fan) и ножка (прямоугольник)."""
    dw, dh, sheet_w, sheet_h = _base_sheet(b, margin, gap)
    ox, oy = margin, margin + 40
    poly = " ".join(f"{ox + x:.2f},{oy + y:.2f}" for x, y in b["dnishe"].exterior.coords)
    fx, fy = b["fan_c"]
    lx, ly = margin + dw + gap, margin + 40
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w:.1f}mm" height="{sheet_h:.1f}mm" '
         f'viewBox="0 0 {sheet_w:.2f} {sheet_h:.2f}">',
         f'<polygon points="{poly}" fill="none" stroke="#000" stroke-width="2"/>',
         f'<circle cx="{ox+fx:.2f}" cy="{oy+fy:.2f}" r="{b["fan_d"]/2:.2f}" fill="none" stroke="#e00000" stroke-width="3"/>',
         f'<rect x="{lx:.2f}" y="{ly:.2f}" width="{b["leg_w"]:.2f}" height="{b["leg_h"]:.2f}" fill="none" stroke="#000" stroke-width="2"/>',
         f'<text x="{margin}" y="{margin+25}" font-size="26">dnishe {dw:.0f}x{dh:.0f} / fan D{b["fan_d"]:.0f}</text>',
         f'<text x="{lx:.0f}" y="{margin+25}" font-size="26">nozhka {b["leg_w"]:.0f} x {b["leg_h"]:.0f}</text>',
         '</svg>']
    open(path, "w", encoding="utf-8").write("\n".join(p))


def save_sides_svg(strips, sheet_w_mm, sheet_h_mm, path):
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w_mm:.1f}mm" '
         f'height="{sheet_h_mm:.1f}mm" viewBox="0 0 {sheet_w_mm:.2f} {sheet_h_mm:.2f}">']
    for st in strips:
        p.append(f'<rect x="{st["x"]:.2f}" y="{st["y"]:.2f}" width="{st["w"]:.2f}" '
                 f'height="{st["len"]:.2f}" fill="none" stroke="#0000ff" stroke-width="1"/>')
        p.append(f'<text x="{st["x"]:.2f}" y="{st["y"] - 8:.2f}" font-size="24" '
                 f'fill="#000">#{st["i"]} {st["len"]:.0f}x{st["w"]:.0f}mm</text>')
    p.append('</svg>')
    open(path, "w", encoding="utf-8").write("\n".join(p))
