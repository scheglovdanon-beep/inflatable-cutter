import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from read_pdf import read_design
from geometry import scale_to_height, smooth, validate

PDF = os.path.join(os.path.dirname(__file__), "..", "examples", "cosmonaut_2700.pdf")

def test_reads_vector_and_valid_contour():
    d = read_design(PDF, figure_side="left")
    assert d.n_fills > 0
    assert d.outer_contour.area > 0

def test_scale_height_2700():
    d = read_design(PDF, figure_side="left")
    sc = scale_to_height(d.outer_contour, 2700)
    assert abs(sc.out_h_mm - 2700) < 1.0

def test_contour_valid():
    d = read_design(PDF, figure_side="left")
    sc = scale_to_height(d.outer_contour, 2700)
    chk = validate(smooth(sc.poly_mm, 1.0))
    assert chk.is_valid and chk.is_closed and chk.is_simple
