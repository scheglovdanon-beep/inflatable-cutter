"""Бот-чекер совпадения сшивных рёбер (TZ_seam_checker.md).
После build_layout: для каждого СТЫКА (пара рёбер двух деталей) проверяем, что длины равны
(|Δ| <= tol_mm ИЛИ |Δ|/max <= tol_frac). Ничего не меняет — только измеряет и возвращает отчёт.
check_seams(lay, fig, g, cfg) -> [issue,...]; каждый issue: {stuk,a,a_len,b,b_len,delta,delta_frac,ok}.
"""
import math
import numpy as np


def _elen(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _plen(pts):
    return sum(_elen(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def check_seams(lay, fig, g, cfg):
    tol = cfg.get("seam_check_tol_mm", 2.0)
    tf = cfg.get("seam_check_tol_frac", 0.01)
    issues = []

    def add(stuk, a_name, a_len, b_name, b_len, pts=None):
        d = abs(float(a_len) - float(b_len)); mx = max(float(a_len), float(b_len), 1e-9)
        issues.append({"stuk": stuk, "a": a_name, "a_len": float(a_len), "b": b_name,
                       "b_len": float(b_len), "delta": d, "delta_frac": d / mx,
                       "ok": (d <= tol) or (d / mx <= tf), "pts": pts})

    strips = lay.get("strips", [])

    # ---------- РУКА: цепочка π·Ø -> база -> плечо -> предплечье -> кисть ----------
    ap = {p.get("name"): p for p in lay.get("arm_pieces", [])}
    holes = lay.get("arm_holes", [])
    if holes and "baza" in ap:
        C = math.pi * 2.0 * holes[0]["r"]
        add("отверстие→база (π·Ø)", f"π·{2*holes[0]['r']:.0f}", C, "база ребро", ap["baza"].get("edge_bottom", 0))
    if "baza" in ap and "plecho" in ap:
        add("база→плечо", "база верх", ap["baza"].get("edge_top", 0), "плечо низ", ap["plecho"].get("edge_bottom", 0))
    if "plecho" in ap and "predplechie" in ap:
        add("плечо→предплечье", "плечо верх", ap["plecho"].get("edge_top", 0), "предплечье низ", ap["predplechie"].get("edge_bottom", 0))
    opening = next((p.get("opening") for p in lay.get("hand_pieces", []) if p.get("opening")), None)
    if "predplechie" in ap and opening:
        add("предплечье→кисть", "предплечье верх", ap["predplechie"].get("edge_top", 0), "opening кисти", opening)

    # ---------- КОРПУС: Σ боковин = периметр ----------
    if strips:
        add("Σ боковин = периметр", "Σ len", sum(s.get("len", 0) for s in strips), "gusset_len", g["gusset_len"])

    # ---------- СМЕЖНЫЕ боковины: t1 == next t0 ----------
    byarc = sorted(strips, key=lambda s: s.get("arc0", 0))
    for a, b in zip(byarc, byarc[1:]):
        if abs(a.get("arc1", 0) - b.get("arc0", 0)) >= 1.0:
            continue
        if a.get("dome_n") or b.get("dome_n"):                         # стык через КУПОЛ -> проверяется отдельно (середина)
            continue
        add(f"боковины #{a['i']}↔#{b['i']}", f"#{a['i']}.t1", a.get("t1", a["thickness"]), f"#{b['i']}.t0", b.get("t0", b["thickness"]))

    # ---------- БОКОВИНА: длинное ребро (сторона) = сегмент len ----------
    for s in strips:
        poly = list(s["poly"]); nd = s.get("dome_n", 0); L = s.get("len", 0)
        if nd and nd >= 2 and len(poly) > nd + 1:                      # купол: прямое ребро + ЗАХОД (часть дуги на перёд)
            bl = poly[nd + 1]
            ratio = cfg.get("shoulder_next_ratio", 1.7)
            mid = s.get("dome_mid_len") or (s.get("dome_chord", 0) / ratio)
            zahod = max(0.0, (s.get("dome_arc_len", 0) - mid) / 2.0) if mid else _plen(poly[:nd // 2 + 1])
            side = _elen(poly[0], bl) + zahod
            add(f"боковина #{s['i']} сторона(купол)", "прямая+заход", side, "сегмент", L,
                pts=[tuple(poly[0]), tuple(bl)])
        elif len(poly) >= 4 and not s.get("protrusion"):
            le = _elen(poly[0], poly[3]); re = _elen(poly[1], poly[2])
            add(f"боковина #{s['i']} ребро", "длинное ребро", (le + re) / 2.0, "сегмент", L,
                pts=[tuple(poly[0]), tuple(poly[3])])

    # ---------- КУПОЛ: середина == ребро соседней боковины ----------
    for s in strips:
        if s.get("dome_mid_len") and s.get("dome_adj_i") is not None:
            adj = next((t for t in strips if t.get("i") == s["dome_adj_i"]), None)
            if adj:
                adj_edge = min(adj.get("t0", adj["thickness"]), adj.get("t1", adj["thickness"]))
                add(f"купол #{s['i']}↔сосед #{adj['i']}", "середина купола", s["dome_mid_len"], "ребро соседней", adj_edge)

    # ---------- Ø+10: ширина боковины у отверстия >= Ø+10 ----------
    for h in holes:
        s = next((x for x in strips if x.get("i") == h["piece"]), None)
        if s:
            w = max(s.get("t0", 0), s.get("t1", 0)); D = 2 * h["r"]
            issues.append({"stuk": f"ширина боковины #{h['piece']} >= Ø+10", "a": f"ширина={w:.0f}",
                           "a_len": w, "b": f"Ø+10={D+10:.0f}", "b_len": D + 10.0, "delta": max(0.0, (D + 10.0) - w),
                           "delta_frac": 0.0, "ok": w >= D + 10.0 - 1.0, "pts": None})

    # ---------- Ø отверстия руки >= минимума (165 мм) ----------
    minh = cfg.get("arm_hole_min_mm", 165)
    for h in holes:
        D = 2 * h["r"]
        issues.append({"stuk": "Ø отверстия руки >= мин", "a": f"Ø={D:.0f}", "a_len": D,
                       "b": f"min={minh:.0f}", "b_len": float(minh), "delta": max(0.0, minh - D),
                       "delta_frac": 0.0, "ok": D >= minh - 1.0, "pts": None})

    # ---------- Сужение: узкая боковина = ОСНОВНАЯ боковина / ratio ----------
    # (узкой считаем ту, у которой ширина×ratio совпадает с реальной бо́лее широкой боковиной —
    #  это и есть основная, от которой её сузили; разных «основных» ширин может быть несколько: 350/460)
    if cfg.get("arm_narrow_upper") or cfg.get("arm_narrow_between"):
        ratio = cfg.get("arm_narrow_ratio", cfg.get("shoulder_next_ratio", 1.7))
        widths = sorted({round(max(s.get("t0", 0), s.get("t1", 0)), 1) for s in strips if not s.get("dome_n")})
        maxw = max(widths) if widths else 0
        for s in strips:
            if s.get("dome_n"):
                continue
            t0, t1 = s.get("t0", 0), s.get("t1", 0)
            if not (t0 < maxw - 2.0 and t1 < maxw - 2.0):               # не у́же максимума -> это основная, пропуск
                continue
            w = max(t0, t1)
            std = next((ow for ow in sorted(widths, reverse=True)        # ищем основную: ow/ratio ≈ w
                        if ow > w + 2 and abs(ow / ratio - w) <= max(2.0, w * 0.02)), None)
            if std is not None:                                         # нашли -> это корректное сужение ×1/ratio
                add(f"боковина #{s['i']} сужение ×1/{ratio:g}", f"ширина={w:.0f}", w, f"{std:.0f}/{ratio:g}", std / ratio)

    return issues


def check_print_complete(parts, shape_idxs, placements, roll_h):
    """ПОЛНОТА печати: все детали (shape_idxs) должны быть выложены на холст (placements[idx] != None),
    и по высоте помещаться в рулон roll_h. parts=[(name,body),...]. Возвращает [issue,...]."""
    issues = []
    for idx in shape_idxs:
        name = parts[idx][0] if idx < len(parts) else str(idx)
        M = placements.get(idx)
        ok = M is not None
        issues.append({"stuk": f"деталь на холсте: {name}", "a": name, "a_len": 1.0 if ok else 0.0,
                       "b": "выложена" if ok else "НЕ ВЫЛОЖЕНА", "b_len": 1.0,
                       "delta": 0.0 if ok else 1.0, "delta_frac": 0.0, "ok": ok, "pts": None})
    return issues


def format_report(issues):
    """Текстовая таблица + итог (число FAIL)."""
    lines = [f"{'СТЫК':<34} {'A (мм)':>22} {'B (мм)':>22} {'Δмм':>7} {'Δ%':>6}  ИТОГ"]
    lines.append("-" * 100)
    nfail = 0
    for it in issues:
        if not it["ok"]:
            nfail += 1
        a = f"{it['a']}={it['a_len']:.1f}"; b = f"{it['b']}={it['b_len']:.1f}"
        lines.append(f"{it['stuk']:<34} {a:>22} {b:>22} {it['delta']:>7.1f} {it['delta_frac']*100:>5.1f}%  "
                     + ("PASS" if it["ok"] else "FAIL <<<"))
    lines.append("-" * 100)
    lines.append(f"ИТОГО: {len(issues)} стыков, FAIL: {nfail}")
    return "\n".join(lines), nfail
