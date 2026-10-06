import sys, yaml, pymupdf, numpy as np, cv2
sys.path.insert(0,"src"); sys.path.insert(0,".")
from sides import front_drawings
from arm import cut_across_arm, _arm_has_hand
from shapely.geometry import Polygon
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
arr=np.asarray(bound.exterior.coords[:-1],float)
im=Image.frombytes("RGB",[pix.width,pix.height],pix.samples).convert("RGB");dr=ImageDraw.Draw(im)
T=lambda x,y:((x-clip.x0)*Z,(y-clip.y0)*Z)
ext=[T(x,y) for x,y in bound.exterior.coords];dr.line(ext+[ext[0]],fill=(0,80,255),width=2)
f=(191.0,386.0)
tipc=arr[int(np.argmax(np.hypot(arr[:,0]-f[0],arr[:,1]-f[1])))]  # дальняя точка руки
# но дальняя = нога; возьмём дальнюю В ВЕРХ-левом секторе (рука)
up=[p for p in arr if p[1]<f[1] and p[0]<f[0]+40]
tipc=max(up,key=lambda p:np.hypot(p[0]-f[0],p[1]-f[1])) if up else tipc
arm,rest=cut_across_arm(bound,f,tuple(tipc),cfg)
ok,tip,_=_arm_has_hand(page,arm,cfg) if arm else (False,None,None)
print("perp from armpit(191,386) frac",round(arm.area/bound.area,3) if arm else None,"hand",ok,"tip",tuple(round(v) for v in tipc))
if arm: ap=[T(x,y) for x,y in arm.exterior.coords];dr.line(ap+[ap[0]],fill=(220,0,0),width=4)
X,Y=T(*f);dr.ellipse([X-9,Y-9,X+9,Y+9],outline=(0,200,0),width=4)
im.save("out/_m4_perp.png");print("DONE")
