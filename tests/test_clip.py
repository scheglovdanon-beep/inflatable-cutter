import os, sys
import pymupdf
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from read_pdf import split_figures
from pieces import build_figure

PDF = os.path.join(os.path.dirname(__file__), "..", "examples", "cosmonaut_2700.pdf")

def _fig(side):
    page = pymupdf.open(PDF)[0]
    figs, *_ = split_figures(page, page.rect.width)
    return build_figure(side, figs[side], page.rect.height, 2700, 10.0)

def test_both_sides_present():
    page = pymupdf.open(PDF)[0]
    figs, n_draw, n_img = split_figures(page, page.rect.width)
    assert n_img == 0 and n_draw > 0
    assert len(figs["left"]) > 100 and len(figs["right"]) > 100

def test_height_2700_single_closed_contour():
    for side in ("left", "right"):
        f = _fig(side)
        assert abs(f.out_h_mm - 2700) <= 1.0
        assert f.n_parts == 1 and f.is_valid
        assert f.contour_pt.exterior.is_closed

def test_artwork_inside_contour():
    for side in ("left", "right"):
        assert _fig(side).outside_mm2 < 1.0
