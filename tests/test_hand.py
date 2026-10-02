import os, sys
import pymupdf, yaml, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from sides import get_front_figure, get_arm_contour, PT2MM
from arm import detect_wrist, cut_arm, build_hand

ROOT = os.path.join(os.path.dirname(__file__), "..")
PDF = os.path.join(ROOT, "examples", "arm_sep.pdf")
CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))


def _hand():
    page = pymupdf.open(PDF)[0]
    fig = get_front_figure(page, CFG)
    scale = fig.s * PT2MM
    arm_pts, body_x = get_arm_contour(page)
    wr = detect_wrist(arm_pts.tolist(), body_x=body_x)
    hand_pts, fore_pts = cut_arm(arm_pts, wr["wrist"], wr["tip"])
    return build_hand(hand_pts, fore_pts, wr["wrist"], scale)


def test_width_formula_positive():
    h = _hand()
    assert h["W"] > 0
    assert abs(h["W"] - (h["fore_top"] - 2 * h["straight"]) / 2) < 1e-6


def test_opening_matches_forearm_top_edge():
    h = _hand()                                             # 2*straight + 2*W == верхнее ребро предпл.
    assert abs(h["opening"] - h["fore_top"]) < 1.0


def test_gusset_height_equals_curved_edge():
    h = _hand()
    gx0, gy0, gx1, gy1 = h["gusset"].bounds
    assert abs((gy1 - gy0) - h["H"]) < 1.0                  # длинное ребро боковины == кривая кромка
    assert abs(h["H"] - h["curved"]) < 1e-6


def test_back_is_mirror_of_front():
    h = _hand()                                             # зад — зеркало переда: та же площадь и габариты
    f, b = h["front"], h["back"]
    assert abs(f.area - b.area) < 1.0
    fb, bb = f.bounds, b.bounds
    assert abs((fb[2] - fb[0]) - (bb[2] - bb[0])) < 1e-6
    assert abs((fb[3] - fb[1]) - (bb[3] - bb[1])) < 1e-6
    assert not f.overlaps(b) and f.disjoint(b)              # разложены без наложения
