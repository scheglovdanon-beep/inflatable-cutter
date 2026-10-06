import sys, yaml, pymupdf, numpy as np, cv2, time
sys.path.insert(0,"src"); sys.path.insert(0,".")
from sides import front_drawings
from arm import detect_arms_v2
from shapely.geometry import Polygon
from PIL import Image, ImageDraw
t0=time.time();L=lambda m:print("%.1fs %s"%(time.time()-t0,m),flush=True)
U="/root/.claude/uploads/7c8b3fa0-489e-402f-a23e-51bb1964b20e/"
pdf=sys.argv[1] if len(sys.argv)>1 else "48f75d55-______.pdf"
out=sys.argv[2] if len(sys.argv)>2 else "out/_dbg_rdetect.png"
page=pymupdf.open(U+pdf)[0];L("open")
cfg=yaml.safe_load(open("config_n2.yaml"))
cfg.pop("manual_arms",None); cfg["expected_arms"]=1
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
bb=bound.bounds
im=Image.new("RGB",(int(bb[2]-bb[0]+40),int(bb[3]-bb[1]+40)),(255,255,255));dr=ImageDraw.Draw(im)
ext=[(x-bb[0]+20,y-bb[1]+20) for x,y in bound.exterior.coords];dr.line(ext+[ext[0]],fill=(180,180,180),width=2)
for a in arms:
    ap=[(x-bb[0]+20,y-bb[1]+20) for x,y in a["poly_pt"].exterior.coords];dr.line(ap+[ap[0]],fill=(220,0,0),width=3)
    f=a["f"];dr.ellipse([f[0]-bb[0]+12,f[1]-bb[1]+12,f[0]-bb[0]+28,f[1]-bb[1]+28],outline=(0,160,0),width=3)
    abx=a["poly_pt"].bounds;cx=abx[0] if abs(abx[0]-f[0])<abs(abx[2]-f[0]) else abx[2]
    dr.line([(cx-bb[0]+20,abx[1]-bb[1]+20),(cx-bb[0]+20,abx[3]-bb[1]+20)],fill=(255,0,255),width=3)
im.save(out);L("saved "+out)
