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
    ap.add_argument("--json", action="store_true", help="выдать результат в JSON (для дашборда)")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    page = pymupdf.open(a.pdf)[0]
    lay, fig, cm, g, arm_pts, seam = build_layout(page, cfg)
    issues = check_seams(lay, fig, g, cfg)
    nfail = sum(1 for it in issues if not it["ok"])
    if a.json:
        import json
        out = [{"stuk": it["stuk"],
                "a": f"{it['a']}={it['a_len']:.0f}" if it.get("a") else "",
                "b": f"{it['b']}={it['b_len']:.0f}" if it.get("b") else "",
                "delta": round(it["delta"], 1), "ok": bool(it["ok"])} for it in issues]
        print(json.dumps({"issues": out, "total": len(issues), "fail": nfail}, ensure_ascii=False))
        sys.exit(0)
    report, _ = format_report(issues)
    print(report)
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
