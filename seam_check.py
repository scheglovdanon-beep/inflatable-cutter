#!/usr/bin/env python
"""Раннер бота-чекера сшивных рёбер (TZ_seam_checker.md).
Запуск: venv/bin/python seam_check.py <pdf> --config <cfg> [--png out.png]
Печатает таблицу стыков; exit 0 если все PASS, иначе 1. С --png — подсветка несовпадений на раскрое.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.dirname(__file__))
import argparse
import pymupdf
import yaml
from export_svg import build_layout
from seam_check import check_seams, format_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--config", required=True)
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    page = pymupdf.open(a.pdf)[0]
    lay, fig, cm, g, arm_pts, seam = build_layout(page, cfg)
    issues = check_seams(lay, fig, g, cfg)
    report, nfail = format_report(issues)
    print(report)
    print("\n(подсветка на раскрое: texture_view.py ... --config <cfg с seam_check: true>)")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
