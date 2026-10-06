import sys, yaml, pymupdf, numpy as np, cv2
sys.path.insert(0,"src"); sys.path.insert(0,".")
from sides import front_drawings
from arm import _contour_concavities
from shapely.geometry import Polygon
from PIL import Image, ImageDraw
U="/root/.claude/uploads/7c8b3fa0-489e-402f-a23e-51bb1964b20e/"
pdf=sys.argv[1]; out=sys.argv[2]
page=pymupdf.open(U+pdf)[0]; cfg=yaml.safe_load(open("config_n2.yaml"))
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
cons=sorted(_contour_concavities(bound,cfg),key=lambda t:-t[2])
print("concavities (x,y,depth):")
for i,(x,y,d) in enumerate(cons[:12]): print(" %d (%d,%d) d=%d"%(i,x,y,d))
im=Image.frombytes("RGB",[pix.width,pix.height],pix.samples).convert("RGB");dr=ImageDraw.Draw(im)
T=lambda x,y:((x-clip.x0)*Z,(y-clip.y0)*Z)
ext=[T(x,y) for x,y in bound.exterior.coords];dr.line(ext+[ext[0]],fill=(0,80,255),width=2)
for i,(x,y,d) in enumerate(cons[:12]):
    X,Y=T(x,y);col=(255,0,0) if i==0 else (255,140,0)
    dr.ellipse([X-9,Y-9,X+9,Y+9],outline=col,width=3);dr.text((X+10,Y-6),"%d:%d"%(i,d),fill=col)
im.save(out);print("DONE")
