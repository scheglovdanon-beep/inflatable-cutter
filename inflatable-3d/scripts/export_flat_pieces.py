#!/usr/bin/env python3
"""Выгрузить ПОЛИГОНЫ всех деталей раскроя (перёд, зад, 8 боковин, рука, кисть, днище, ножка)
для показа плоскими панелями в 3D. Запуск в venv inflatable-cutter."""
import os, sys, json, argparse
CUTTER="/opt/inflatable-cutter"; sys.path.insert(0,CUTTER); sys.path.insert(0,os.path.join(CUTTER,"src"))
import yaml, pymupdf, numpy as np
import shapely.affinity as aff
from shapely.geometry import Polygon

def local(coords):
    xs=[c[0] for c in coords]; ys=[c[1] for c in coords]; ox,oy=min(xs),min(ys)
    return [[float(x-ox),float(y-oy)] for x,y in coords], ox, oy

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("macet"); ap.add_argument("--config",required=True)
    ap.add_argument("--name",default="cosmo"); ap.add_argument("--out",default="/opt/inflatable-3d/output")
    a=ap.parse_args(); a.macet=os.path.abspath(a.macet); a.config=os.path.abspath(a.config); a.out=os.path.abspath(a.out)
    os.chdir(CUTTER)
    from export_svg import build_layout
    from sides import PT2MM
    from side_color import strip_band_quads
    cfg=yaml.safe_load(open(a.config)); 
    for k in("roll_print","print_arrange","roll_pack","arrange_sides","repack"): cfg[k]=False
    cfg["front_smooth_mm"]=0
    page=pymupdf.open(a.macet)[0]; page._cfg=cfg
    lay,fig,contour_mm,g,arm_pts,seam=build_layout(page,cfg)
    outdir=os.path.join(a.out,a.name)
    pieces=[]
    # перёд/зад — текстуры уже есть (front.png/back.png), берём их bbox из контуров
    fp,_,_=local(list(contour_mm.exterior.coords)[:-1])
    fb=contour_mm.bounds
    pieces.append({"id":"ПЕРЁД","poly":fp,"kind":"tex","texture":"front.png"})
    bsrc=lay["back"]["src"]; bp,_,_=local(list(bsrc.exterior.coords)[:-1])
    pieces.append({"id":"ЗАД","poly":bp,"kind":"tex","texture":"back.png"})
    # 8 боковин с цветными полосами
    for s in sorted(lay["strips"],key=lambda s:s["i"]):
        poly=s["poly"]; pl,ox,oy=local(poly)
        quads=[]
        for quad,c in strip_band_quads(s):
            quads.append({"q":[[float(x-ox),float(y-oy)] for x,y in quad],"c":[int(v) for v in c]})
        pieces.append({"id":"bok%d"%s["i"],"poly":pl,"kind":"bands","bands":quads})
    # рука: baza/plecho/predplechie (заливка)
    for p in lay.get("arm_pieces",[]):
        pl,_,_=local(list(p["poly"].exterior.coords)[:-1])
        pieces.append({"id":p["name"],"poly":pl,"kind":"fill","fill":[int(v) for v in (p.get("fill") or (230,230,230))]})
    # кисть: 3 части
    for p in lay.get("hand_pieces",[]):
        pl,_,_=local(list(p["poly"].exterior.coords)[:-1])
        pieces.append({"id":"кисть:"+p["name"],"poly":pl,"kind":"fill","fill":[235,235,235]})
    # днище + ножка
    b=lay.get("base")
    if b:
        pl,_,_=local(list(b["dnishe"].exterior.coords)[:-1]); pieces.append({"id":"ДНИЩЕ","poly":pl,"kind":"outline"})
        lx,ly,lw,lh=b["leg"]; pieces.append({"id":"НОЖКА","poly":[[0,0],[lw,0],[lw,lh],[0,lh]],"kind":"outline"})
    json.dump({"figure":a.name,"pieces":pieces},open(os.path.join(outdir,"flat_pieces.json"),"w"))
    print("OK pieces=%d:"%len(pieces), ", ".join(p["id"] for p in pieces))

if __name__=="__main__": main()
