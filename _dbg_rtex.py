import sys, yaml, pymupdf, numpy as np, cv2, time
sys.path.insert(0,"src"); sys.path.insert(0,".")
from sides import front_drawings
from arm import detect_arms_v2
from shapely.geometry import Polygon
from PIL import Image, ImageDraw
t0=time.time();L=lambda m:print("%.1fs %s"%(time.time()-t0,m),flush=True)
U="/root/.claude/uploads/7c8b3fa0-489e-402f-a23e-51bb1964b20e/"
pdf=sys.argv[1] if len(sys.argv)>1 else "48f75d55-______.pdf"
out=sys.argv[2] if len(sys.argv)>2 else "out/_dbg_rtex.png"
page=pymupdf.open(U+pdf)[0];L("open")
cfg=yaml.safe_load(open("config_n2.yaml")); cfg.pop("manual_arms",None); cfg["expected_arms"]=1
cfg.update({"union_batch_size":300,"raster_zoom":1.5,"arm_raster_z":3.0,"free_poly_cache":True})
dds,_=front_drawings(page,cfg);L("front_drawings %d"%len(dds))
rs=[d["rect"] for d in dds if d.get("fill")]
b=(min(r.x0 for r in rs),min(r.y0 for r in rs),max(r.x1 for r in rs),max(r.y1 for r in rs))
Z=2.0;pad=8;clip=pymupdf.Rect(b[0]-pad,b[1]-pad,b[2]+pad,b[3]+pad)
pix=page.get_pixmap(matrix=pymupdf.Matrix(Z,Z),clip=clip)
img=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)[:,:,:3]
bg=img[2,2].astype(int);mask=((np.abs(img.astype(int)-bg).sum(2))>40).astype(np.uint8)*255
mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
cnts,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
c=cv2.approxPolyDP(max(cnts,key=cv2.contourArea),1.5,True)
bound=Polygon([(clip.x0+p[0][0]/Z,clip.y0+p[0][1]/Z) for p in c]).buffer(0)
L("boundary pts=%d"%len(bound.exterior.coords))
arms,rest=detect_arms_v2(page,bound,cfg);L("detect arms=%d"%len(arms))
for a in arms: print("  f",tuple(round(v) for v in a["f"]),"tip",tuple(round(v) for v in a["tip"]),"frac",round(a["poly_pt"].area/bound.area,3),flush=True)
# фон = реальная текстура (рендер)
im=Image.frombytes("RGB",[pix.width,pix.height],pix.samples).convert("RGB");dr=ImageDraw.Draw(im)
T=lambda x,y:((x-clip.x0)*Z,(y-clip.y0)*Z)
# внешняя граница — синим
ext=[T(x,y) for x,y in bound.exterior.coords];dr.line(ext+[ext[0]],fill=(0,80,255),width=3)
for a in arms:
    ap=[T(x,y) for x,y in a["poly_pt"].exterior.coords];dr.line(ap+[ap[0]],fill=(220,0,0),width=4)  # рука — красным
    f=a["f"];fx,fy=T(*f);dr.ellipse([fx-9,fy-9,fx+9,fy+9],outline=(0,200,0),width=4)                 # подмышка — зел
    abx=a["poly_pt"].bounds;cx=abx[0] if abs(abx[0]-f[0])<abs(abx[2]-f[0]) else abx[2]
    x1,y1=T(cx,abx[1]);x2,y2=T(cx,abx[3]);dr.line([(x1,y1),(x2,y2)],fill=(255,0,255),width=4)         # рез — магента
im.save(out);L("saved "+out)
