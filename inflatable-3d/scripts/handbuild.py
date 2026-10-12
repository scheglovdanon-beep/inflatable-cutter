import json, sys, numpy as np
from shapely.geometry import Polygon
from shapely.ops import triangulate
name=sys.argv[1] if len(sys.argv)>1 else "cosmo"
base=f"/opt/inflatable-3d/output/{name}"
s=json.load(open(f"{base}/panels.json")); h=s['arm']['hand']
c=np.array(h['contour']); n=len(c); poly=Polygon(c)
tris=[]
for t in triangulate(poly):
    if poly.contains(t.representative_point()):
        xy=list(t.exterior.coords)[:3]
        idx=[int(np.argmin(np.hypot(*(c-np.array(p)).T))) for p in xy]
        tris.append(idx)
seg=np.hypot(*(np.roll(c,-1,0)-c).T); si=int(np.argmax(seg))
out={'contour':c.tolist(),'faces':tris,'straight_i':si,'W':h['gusset_w'],'n':n}
json.dump(out,open(f"{base}/hand_build.json",'w'))
print(f"{name}: faces={len(tris)} straight_i={si} W={h['gusset_w']:.1f} n={n}")
