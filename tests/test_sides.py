import os, sys, math
import pymupdf, yaml
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from sides import get_front_figure, contour_to_mm, build_gusset, build_strips

ROOT = os.path.join(os.path.dirname(__file__), "..")
PDF = os.path.join(ROOT, "examples", "arm_sep.pdf")
CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
FAN, MAIN = CFG["fan_diameter_mm"], CFG["main_width_mm"]

def _g():
    page = pymupdf.open(PDF)[0]
    fig = get_front_figure(page, CFG)
    return build_gusset(contour_to_mm(fig), CFG)

def test_gusset_plus_bottom_equals_perimeter():
    g = _g()
    assert abs(g["gusset_len"] + g["bottom_len"] - g["perimeter"]) < 1.0

def test_sum_of_strips_equals_gusset():
    g = _g()
    assert abs(sum(s["len"] for s in g["segments"]) - g["gusset_len"]) < 1.0

def test_pieces_within_limits():
    g = _g()
    seg = g["segments"]
    assert len(seg) >= 4
    assert max(s["len"] for s in seg) <= CFG["side_max_len_mm"] + 1
    # зоны выступов и соседние полосы могут быть короче min_len
    prot_idx = {s["i"] for s in seg if s.get("protrusion")}
    skip = prot_idx | {i - 1 for i in prot_idx} | {i + 1 for i in prot_idx}
    normal = [s for s in seg if s["i"] not in skip]
    assert min(s["len"] for s in normal) >= CFG["side_min_len_mm"] - 1

def test_protrusion_is_single_waisted_piece():
    g = _g()
    prot = [s for s in g["segments"] if s.get("protrusion")]
    assert len(prot) >= 1, "антенна должна детектиться на примере"
    for s in prot:                                                   # одна деталь с талией=tip
        assert abs(s["waist"] - CFG["tip_width_mm"]) < 1
        assert abs(s["t0"] - CFG["main_width_mm"]) < 1 and abs(s["t1"] - CFG["main_width_mm"]) < 1
        assert s["len"] >= CFG["protrusion_min_piece_mm"] - 1        # не короче 50 см (плавный переход)

def test_first_part_each_end_is_fan_width():
    g = _g()
    seg = sorted(g["segments"], key=lambda s: s["i"])
    # первая часть с обоих концов = 460 (прямоугольник)
    for s in (seg[0], seg[-1]):
        assert abs(s["t0"] - FAN) < 1 and abs(s["t1"] - FAN) < 1

def test_second_part_each_end_is_transition():
    g = _g()
    seg = sorted(g["segments"], key=lambda s: s["i"])
    for s in (seg[1], seg[-2]):                                    # 2-я часть с каждого конца
        assert {round(s["t0"]), round(s["t1"])} == {round(FAN), round(MAIN)}

def test_middle_parts_are_main_width():
    g = _g()
    seg = sorted(g["segments"], key=lambda s: s["i"])
    for s in seg[2:-2]:                                            # корпус + голова
        assert abs(s["t0"] - MAIN) < 1 and abs(s["t1"] - MAIN) < 1

def test_only_fan_or_main_widths():
    g = _g()
    for s in g["segments"]:
        for t in (s["t0"], s["t1"]):
            assert abs(t - FAN) < 1 or abs(t - MAIN) < 1

def test_neighbor_widths_match_at_seams():
    g = _g()
    seg = sorted(g["segments"], key=lambda s: s["arc0"])
    for a, b in zip(seg, seg[1:]):
        assert abs(a["t1"] - b["t0"]) < 1e-6

def test_trapezoid_leg_equals_arc_length():
    g = _g()
    for s in g["segments"]:
        h = math.sqrt(max(s["len"]**2 - ((s["t0"] - s["t1"]) / 2)**2, 1.0))
        leg = math.sqrt(h**2 + ((s["t0"] - s["t1"]) / 2)**2)
        assert abs(leg - s["len"]) < 1.0

def _layout():
    import pymupdf
    from sides import classify_sides, build_fitting_layout
    page = pymupdf.open(PDF)[0]
    fig = get_front_figure(page, CFG)
    C = contour_to_mm(fig); g = build_gusset(C, CFG); classify_sides(g, C)
    return g, build_fitting_layout(C, g)

def test_bottom_pieces_symmetric():
    g, _ = _layout()
    seg = sorted(g["segments"], key=lambda s: s["i"])
    fan = CFG["fan_diameter_mm"]
    first = [s for s in seg if abs(s["t0"] - fan) < 1 and abs(s["t1"] - fan) < 1]   # детали 460
    assert len(first) == 2 and abs(first[0]["len"] - first[1]["len"]) < 1           # 1-е детали равны Л/П
    trans = [s for s in seg if {round(s["t0"]), round(s["t1"])} == {round(fan), round(CFG["main_width_mm"])}
             and not s.get("waist")]
    assert len(trans) == 2                                                          # 2 перехода
    for s in trans:
        assert abs(s["len"] - CFG["transition_piece_len_mm"]) < 1                   # оба = 500
    assert abs(trans[0]["len"] - trans[1]["len"]) < 1

def test_each_edge_equals_arc_and_sum_perimeter():
    g, lay = _layout()
    for s in lay["strips"]:
        assert abs(s["edge_len"] - s["edge"]) < 1.0                                 # ребро = длине дуги
    total = sum(s["edge_len"] for s in lay["strips"])
    assert abs(total - g["gusset_len"]) < len(lay["strips"])                        # Σ рёбер = периметр-днище

def test_neck_detected():
    import pymupdf
    from sides import detect_neck
    page = pymupdf.open(PDF)[0]
    C = contour_to_mm(get_front_figure(page, CFG))
    g = build_gusset(C, CFG)
    neck = detect_neck(C, g, CFG)
    assert neck["found"]                                       # шея есть на примере
    assert neck["seg_left"] is not None and neck["seg_right"] is not None
    assert neck["seg_left"] != neck["seg_right"]               # разные детали Л/П
    assert neck["drop"] >= CFG["neck_min_drop"]

def test_arm_hole_placed():
    import pymupdf
    from sides import classify_sides, build_fitting_layout, detect_neck, detect_arm_side, place_arm_hole
    page = pymupdf.open(PDF)[0]
    C = contour_to_mm(get_front_figure(page, CFG)); g = build_gusset(C, CFG); classify_sides(g, C)
    lay = build_fitting_layout(C, g)
    neck = detect_neck(C, g, CFG); side = detect_arm_side(page, CFG)
    place_arm_hole(lay, neck, side, CFG)
    ah = lay["arm_hole"]
    assert abs(ah["r"]*2 - CFG["arm_hole_diameter_mm"]) < 1                 # Ø250
    assert ah["piece"] == (neck["seg_left"] if side == "left" else neck["seg_right"])
    s = next(x for x in lay["strips"] if x["i"] == ah["piece"])
    xs=[p[0] for p in s["poly"]]; ys=[p[1] for p in s["poly"]]
    assert abs(ah["cx"] - (min(xs)+max(xs))/2) < 1                          # по центру ширины
    assert abs((ah["cy"]-ah["r"]) - (min(ys)+CFG["arm_hole_top_margin_mm"])) < 1  # верх = margin от края
    assert min(xs) <= ah["cx"]-ah["r"] and ah["cx"]+ah["r"] <= max(xs)      # круг внутри детали

def test_arm_pieces_built():
    import math
    from arm import build_arm
    ps = build_arm(CFG)
    assert {p["name"] for p in ps} == {"baza", "plecho", "predplechie"}      # 3 детали, без кисти
    base = next(p for p in ps if p["name"] == "baza")
    assert abs(base["w"] - math.pi * CFG["arm_hole_diameter_mm"]) < 10        # база = π·D_hole
    plecho = next(p for p in ps if p["name"] == "plecho")
    assert plecho["area"] > base["area"]                                     # плечо крупнее базы

def test_arm_edges_mate():
    import math
    from arm import build_arm
    ps = {p["name"]: p for p in build_arm(CFG)}
    base, plecho, pred = ps["baza"], ps["plecho"], ps["predplechie"]
    circ = math.pi * CFG["arm_hole_diameter_mm"]
    assert abs(base["w"] - circ) < 8                              # база.ширина = π·D (отверстие)
    assert abs(plecho["edge_bottom"] - base["w"]) < 8            # плечо.низ = база.ширина
    assert abs(plecho["edge_top"] - pred["edge_bottom"]) < 8     # плечо.верх = предплечье.низ
