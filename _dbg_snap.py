import sys, yaml, pymupdf, numpy as np, cv2
sys.path.insert(0,"src"); sys.path.insert(0,".")
from sides import front_drawings
from arm import _arm_has_hand
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import split, unary_union
from PIL import Image, ImageDraw
U="/root/.claude/uploads/7c8b3fa0-489e-402f-a23e-51bb1964b20e/"
page=pymupdf.open(U+"2a061675-_________2700.pdf")[0]; cfg=yaml.safe_load(open("config_n2.yaml"))
cfg.update({"union_batch_size":300,"raster_zoom":1.5,"arm_raster_z":3.0})
dds,_=front_drawings(page,cfg); rs=[d["rect"] for d in dds if d.get("fill")]
b=(min(r.x0 for r in rs),min(r.y0 for r in rs),max(r.x1 for r in rs),max(r.y1 for r in rs))
Z=2.0;pad=8;clip=pymupdf.Rect(b[0]-pad,b[1]-pad,b[2]+pad,b[3]+pad)
pix=page.get_pixmap(matrix=pymupdf.Matrix(Z,Z),clip=clip)
img=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)[:,:,:3]
bg=img[2,2].astype(int);mask=((np.abs(img.astype(int)-bg).sum(2))>40).astype(np.uint8)*255
mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
cnts,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
c=cv2.approxPolyDP(max(cnts,key=cv2.contourArea),1.5,True)
bound=Polygon([(clip.x0+p[0][0]/Z,clip.y0+p[0][1]/Z) for p in c]).buffer(0)
ext=bound.exterior
def snap(p):
    return np.array(ext.interpolate(ext.project(Point(*p))).coords[0])
g0=snap((208,294)); g1=snap((191,386))   # привязка к контуру
d=g1-g0; d=d/(np.hypot(*d) or 1)
bl=LineString([tuple(g0-d*40),tuple(g1+d*40)])
parts=list(getattr(split(bound,bl),"geoms",[bound]))
tip=np.asarray(bound.exterior.coords)[np.argmax([np.hypot(x-g1[0],y-g1[1]) for x,y in bound.exterior.coords])]
armp=min(parts,key=lambda p:p.distance(Point(*(66,185)))) if len(parts)>=2 else None  # кусок у поднятой кисти
print("snapped chord parts",len(parts),"armfrac",round(armp.area/bound.area,3) if armp else None)
im=Image.frombytes("RGB",[pix.width,pix.height],pix.samples).convert("RGB");dr=ImageDraw.Draw(im)
T=lambda x,y:((x-clip.x0)*Z,(y-clip.y0)*Z)
e=[T(x,y) for x,y in bound.exterior.coords];dr.line(e+[e[0]],fill=(0,80,255),width=2)
if armp: ap=[T(x,y) for x,y in armp.exterior.coords];dr.line(ap+[ap[0]],fill=(220,0,0),width=4)
dr.line([T(*g0),T(*g1)],fill=(255,0,255),width=4)
X,Y=T(*g1);dr.ellipse([X-9,Y-9,X+9,Y+9],outline=(0,200,0),width=4)
im.save("out/_m4_snap.png");print("DONE")
