import bpy, bmesh, json, math, numpy as np, mathutils, os
FIG=os.environ.get("FIG","cosmo"); BASE="/opt/inflatable-3d/output/"+FIG; FIGDIR="/opt/inflatable-3d/output/"+FIG+"_figure"
spec=json.load(open(BASE+"/panels.json")); MM2M=0.001
hb=json.load(open(BASE+"/hand_build.json"))
NS=dict(tension=2000,comp=2000,shear=1500,bend=0.0,mass=0.20,air=2.5,quality=16); PRESS=70.0
def norm(v):
    v=np.asarray(v,float); nn=np.linalg.norm(v); return v/nn if nn>1e-9 else v
def R_from_to(a,b):
    a=norm(a); b=norm(b); v=np.cross(a,b); c=np.dot(a,b); s=np.linalg.norm(v)
    if s<1e-9: return np.eye(3) if c>0 else -np.eye(3)
    vx=np.array([[0,-v[2],v[1]],[v[2],0,-v[0]],[-v[1],v[0],0]]); return np.eye(3)+vx+vx@vx*((1-c)/(s*s))
def rot_axis(ang,axis):
    axis=norm(axis); x,y,z=axis; c=math.cos(ang); s=math.sin(ang); C=1-c
    return np.array([[c+x*x*C,x*y*C-z*s,x*z*C+y*s],[y*x*C+z*s,c+y*y*C,y*z*C-x*s],[z*x*C-y*s,z*y*C+x*s,c+z*z*C]])
def seam_align(G, want):
    cb=G[0].mean(0); ct=G[-1].mean(0); ax=norm(ct-cb)
    mid=G[len(G)//2]; ds=mid[0]-mid.mean(0); ds=ds-ax*np.dot(ds,ax); ds=norm(ds)
    wp=np.array(want,float); wp=wp-ax*np.dot(wp,ax); wp=norm(wp)
    ang=math.atan2(np.dot(np.cross(ds,wp),ax), np.dot(ds,wp)); return (G-cb)@rot_axis(ang,ax).T+cb
ch=spec["arm"]["chain"]; names=[c["name"] for c in ch]
def fillcol(nm):
    f=next((c.get("fill",[255,255,255]) for c in ch if c["name"]==nm),[255,255,255]); return tuple(max(v/255,0.02) for v in f)
pl=np.array(ch[names.index("plecho")]["grid"]); pr0=np.array(ch[names.index("predplechie")]["grid"])
pl=seam_align(pl,[0,0,-1]); pl_ct=pl[-1].mean(0); pl_Tt=norm(pl[-1].mean(0)-pl[-2].mean(0))
pr0=seam_align(pr0,[0,0,1]); R1=R_from_to(norm(pr0[1].mean(0)-pr0[0].mean(0)),pl_Tt)
pr=(pr0-pr0[0].mean(0))@R1.T+pl_ct
Pt=pr[-1].mean(0); Tt=norm(pr[-1].mean(0)-pr[-2].mean(0)); V=len(pr[0])
up=np.array([0,0,1.0])                                         # палец СТРОГО ВВЕРХ (как в расстановке)
face=np.array([0,-1.0,0])                                       # ладонь вперёд
side=norm(np.cross(up,face))
c0=np.array(hb["contour"]); faces=hb["faces"]; si=hb["straight_i"]; n=hb["n"]; W=hb["W"]
w=c0[:,0].max()-c0[:,0].min(); h=c0[:,1].max()-c0[:,1].min(); x0=c0[:,0].min(); y0=c0[:,1].min()
uv=[(float((p[0]-x0)/w), float(1.0-(p[1]-y0)/h)) for p in c0]
cc=c0-c0.mean(0); loc=np.array([[p[0],-p[1],0.0] for p in cc]); Ms=(loc[si]+loc[0])/2
thumb=loc[int(np.argmax(loc[:,1]))]                            # кончик большого пальца = верхняя точка контура
h_up=norm([thumb[0]-Ms[0],thumb[1]-Ms[1],0.0]); h_face=np.array([0,0,1.0]); h_side=norm(np.cross(h_up,h_face))
Rh=np.array([up,face,side]).T @ np.array([h_up,h_face,h_side])
Rh=rot_axis(math.pi, up) @ Rh                                  # поворот кисти на 180° вокруг своей оси
Hc=35.0; Oc=Pt+norm(Tt+up)*Hc                                  # кисть приподнята над кольцом -> короткое запястье, без залезания
def place(p): return Rh@(np.asarray(p,float)-Ms)+Oc
Cmid=(Pt+Oc)/2.0
def ang_of(P):
    q=np.asarray(P,float)-Cmid; q=q-Tt*np.dot(q,Tt); return math.atan2(np.dot(q,side),np.dot(q,face))
bpy.ops.wm.read_factory_settings(use_empty=True); scn=bpy.context.scene
def mat(nm,col,em=0,img=None):
    m=bpy.data.materials.new(nm); m.use_nodes=True; nt=m.node_tree; b=nt.nodes["Principled BSDF"]; b.inputs["Roughness"].default_value=0.5
    if img is not None:
        tx=nt.nodes.new("ShaderNodeTexImage"); tx.image=img; nt.links.new(tx.outputs["Color"],b.inputs["Base Color"])
    else:
        b.inputs["Base Color"].default_value=(*col,1)
        if em: b.inputs["Emission Color"].default_value=(*col,1); b.inputs["Emission Strength"].default_value=em
    return m
def add_cloth(obj):
    cl=obj.modifiers.new("cloth","CLOTH"); cs=cl.settings
    cs.quality=NS["quality"]; cs.use_pressure=True; cs.uniform_pressure_force=PRESS
    cs.tension_stiffness=NS["tension"]; cs.compression_stiffness=NS["comp"]; cs.shear_stiffness=NS["shear"]
    cs.bending_stiffness=NS["bend"]; cs.mass=NS["mass"]; cs.air_damping=NS["air"]
    cl.settings.effector_weights.gravity=0.0; cl.collision_settings.use_self_collision=False
objs=[]; img=bpy.data.images.load(BASE+"/hand.png")
def build_plecho():
    G=pl; Vv=len(G[0]); me=bpy.data.meshes.new("plecho"); ob=bpy.data.objects.new("plecho",me); scn.collection.objects.link(ob); bm=bmesh.new()
    rings=[[bm.verts.new((p[0]*MM2M,p[1]*MM2M,p[2]*MM2M)) for p in r] for r in G]
    for a,b in zip(rings,rings[1:]):
        for j in range(Vv): bm.faces.new((a[j],a[(j+1)%Vv],b[(j+1)%Vv],b[j]))
    cb=bm.verts.new(tuple(G[0].mean(0)*MM2M)); ct=bm.verts.new(tuple(G[-1].mean(0)*MM2M))
    for j in range(Vv): bm.faces.new((rings[0][j],cb,rings[0][(j+1)%Vv]))
    for j in range(Vv): bm.faces.new((rings[-1][j],rings[-1][(j+1)%Vv],ct))
    bmesh.ops.recalc_face_normals(bm,faces=bm.faces[:]); bm.to_mesh(me); bm.free()
    me.materials.append(mat("plecho",fillcol("plecho"))); add_cloth(ob); objs.append(ob)
build_plecho()

# --- БАЗА руки (Ø250): вшита в отверстие тела; стык с низом плеча кольцо-к-кольцу ---
ba=np.array(ch[names.index('baza')]['grid']); ba=seam_align(ba,[0,0,-1])
ba_axis=norm(ba[-1].mean(0)-ba[0].mean(0)); pl_up=norm(pl[1].mean(0)-pl[0].mean(0))
ba=(ba-ba[-1].mean(0))@R_from_to(ba_axis,pl_up).T + pl[0].mean(0)   # верх базы -> низ плеча
def build_baza():
    G=ba; Vv=len(G[0]); me=bpy.data.meshes.new("baza"); ob=bpy.data.objects.new("baza",me); scn.collection.objects.link(ob); bm=bmesh.new()
    rings=[[bm.verts.new((p[0]*MM2M,p[1]*MM2M,p[2]*MM2M)) for p in r] for r in G]
    for a,b in zip(rings,rings[1:]):
        for j in range(Vv): bm.faces.new((a[j],a[(j+1)%Vv],b[(j+1)%Vv],b[j]))
    cb=bm.verts.new(tuple(G[0].mean(0)*MM2M)); ct=bm.verts.new(tuple(G[-1].mean(0)*MM2M))
    for j in range(Vv): bm.faces.new((rings[0][j],cb,rings[0][(j+1)%Vv]))
    for j in range(Vv): bm.faces.new((rings[-1][j],rings[-1][(j+1)%Vv],ct))
    bmesh.ops.recalc_face_normals(bm,faces=bm.faces[:]); bm.to_mesh(me); bm.free()
    me.materials.append(mat("baza",fillcol("baza"))); add_cloth(ob); objs.append(ob)
build_baza()

me=bpy.data.meshes.new("fore_hand"); ob=bpy.data.objects.new("fore_hand",me); scn.collection.objects.link(ob); bm=bmesh.new()
me.materials.append(mat("tube",fillcol("predplechie"))); me.materials.append(mat("hf",None,img=img)); me.materials.append(mat("hbk",None,img=img)); me.materials.append(mat("hg",(0.86,0.86,0.88)))
uvl=bm.loops.layers.uv.new("UVMap")
rings=[[bm.verts.new((p[0]*MM2M,p[1]*MM2M,p[2]*MM2M)) for p in r] for r in pr]
for a,b in zip(rings,rings[1:]):
    for j in range(V): f=bm.faces.new((a[j],a[(j+1)%V],b[(j+1)%V],b[j])); f.material_index=0
cb=bm.verts.new(tuple(pr[0].mean(0)*MM2M))
for j in range(V): f=bm.faces.new((rings[0][j],cb,rings[0][(j+1)%V])); f.material_index=0
topring=rings[-1]
fv=[bm.verts.new(tuple(place(loc[i]+np.array([0,0,+W/2]))*MM2M)) for i in range(n)]
bv=[bm.verts.new(tuple(place(loc[i]+np.array([0,0,-W/2]))*MM2M)) for i in range(n)]
um={}
for i in range(n): um[fv[i]]=uv[i]; um[bv[i]]=uv[i]
for a,b,d in faces:
    f=bm.faces.new((fv[a],fv[b],fv[d])); f.material_index=1
    f=bm.faces.new((bv[a],bv[d],bv[b])); f.material_index=2
for i in range(n):
    j=(i+1)%n
    if i==si: continue
    f=bm.faces.new((fv[i],fv[j],bv[j],bv[i])); f.material_index=3
# UV до дробления (новые вершины среза интерполируются)
for f in bm.faces:
    for lo in f.loops:
        if lo.vert in um: lo[uvl].uv=um[lo.vert]
def edge(a,b):
    for e in a.link_edges:
        if e.other_vert(a)==b: return e
e_fs=edge(fv[si],fv[0]); e_bs=edge(bv[si],bv[0]); e_g0=edge(fv[0],bv[0]); e_gs=edge(fv[si],bv[si])
bm.verts.index_update(); topring_idx=[v.index for v in topring]           # subdivide инвалидирует python-ссылки -> запоминаем индексы
bmesh.ops.subdivide_edges(bm,edges=[e_fs,e_bs,e_g0,e_gs],cuts=15,use_grid_fill=False)
bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table()
ring_loop=[bm.verts[i] for i in topring_idx]
bedges=[e for e in bm.edges if e.is_boundary]
adj={}
for e in bedges:
    adj.setdefault(e.verts[0],[]).append(e.verts[1]); adj.setdefault(e.verts[1],[]).append(e.verts[0])
def walk(start):
    loop=[start]; prev=None; cur=start
    while True:
        nxts=[x for x in adj[cur] if x!=prev]
        if not nxts: break
        nxt=nxts[0]
        if nxt==start: break
        loop.append(nxt); prev,cur=cur,nxt
        if len(loop)>600: break
    return loop
seen=set(); loops=[]
for v in list(adj.keys()):
    if v in seen: continue
    lp=walk(v)
    for x in lp: seen.add(x)
    loops.append(lp)
loops.sort(key=lambda lp: np.linalg.norm(np.mean([np.array(v.co) for v in lp],0)-Oc*MM2M))
hand_loop=loops[0]
# равномерно перераспределить кромку кисти по ДЛИНЕ -> шаг как у кольца (убирает складку-дверку)
hp=[np.array(v.co) for v in hand_loop]; Hn0=len(hand_loop)
seg=[float(np.linalg.norm(hp[(i+1)%Hn0]-hp[i])) for i in range(Hn0)]
cum=[0.0]
for i in range(Hn0-1): cum.append(cum[-1]+seg[i])
tot=cum[-1]+seg[-1]
def _interp(t):
    L=(t%1.0)*tot
    for i in range(Hn0):
        if L<=cum[i]+seg[i] or i==Hn0-1:
            f=(L-cum[i])/seg[i] if seg[i]>1e-12 else 0.0
            return hp[i]+(hp[(i+1)%Hn0]-hp[i])*f
    return hp[0]
for k in range(Hn0): hand_loop[k].co=tuple(_interp(k/Hn0))
Hn=len(hand_loop); Rn=len(ring_loop)
aR=[ang_of(np.array(v.co)/MM2M) for v in ring_loop]
aH=[ang_of(np.array(v.co)/MM2M) for v in hand_loop]
def adiff(a,b):
    d=abs(a-b)%(2*math.pi); return min(d,2*math.pi-d)
best=None
for dd in (1,-1):
    for o in range(Hn):
        cost=sum(adiff(aR[j], aH[(o+dd*j)%Hn]) for j in range(min(Rn,Hn)))
        if best is None or cost<best[0]: best=(cost,o,dd)
_,o,dd=best
for j in range(Rn):                                            # ПРЯМОЙ ШОВ: кромку кисти сажаем на кольцо предплечья
    hv=hand_loop[(o+dd*j)%Hn]; hv.co=ring_loop[j].co
bmesh.ops.remove_doubles(bm,verts=bm.verts[:],dist=1e-4)       # сварить совпавшие вершины -> единая оболочка без дыры
bmesh.ops.recalc_face_normals(bm,faces=bm.faces[:])
bm.normal_update()
nb=len([e for e in bm.edges if e.is_boundary]); print("FOREHAND boundary edges:",nb)
bm.to_mesh(me); bm.free(); add_cloth(ob); objs.append(ob)
print("MESH built hand_loop=%d ring=%d off=%d dir=%d"%(Hn,Rn,o,dd))

steps=55; scn.frame_start=1; scn.frame_end=steps
for f in range(1,steps+1): scn.frame_set(f)
dg=bpy.context.evaluated_depsgraph_get()
for ob in objs:
    baked=bpy.data.meshes.new_from_object(ob.evaluated_get(dg)); mats=list(ob.data.materials)
    ob.modifiers.clear(); ob.data=baked
    if not ob.data.materials:
        for m in mats: ob.data.materials.append(m)
    bpy.ops.object.select_all(action='DESELECT'); ob.select_set(True); bpy.context.view_layer.objects.active=ob; bpy.ops.object.shade_smooth()
# --- перенос руки на тело: монтаж базы Ø250 в отверстие гассета ---
ringA=np.array(spec["body"]["contour_ring"],float); Nr=len(ringA)
ax,ay=spec["arm"]["mounts"][0]["armpit_mm"]
ii=int(np.argmin((ringA[:,0]-ax)**2+(ringA[:,1]-ay)**2))
tang=ringA[(ii+1)%Nr]-ringA[ii-1]; nrm=np.array([tang[1],-tang[0]])
if np.dot(nrm,ringA[ii])<0: nrm=-nrm
nrm=nrm/(np.linalg.norm(nrm) or 1)
hole=np.array([ringA[ii,0]*MM2M, 0.0, -ringA[ii,1]*MM2M])       # центр отверстия (м)
ex=np.array([nrm[0],0.0,-nrm[1]]); ex=ex/np.linalg.norm(ex)     # наружу от гассета
ez=np.array([0,0,1.0]); ey=ez-ez.dot(ex)*ex; ey=ey/np.linalg.norm(ey)
Cm=ba[0].mean(0)                                               # монтажное кольцо базы (мм, arm-local)
Am=norm(ba[-1].mean(0)-ba[0].mean(0))                          # ось руки
R1=R_from_to(Am,ex)
zc=R1@np.array([0,0,1.0]); zc=zc-ex*np.dot(zc,ex)
roll=math.atan2(np.dot(np.cross(zc,ey),ex), np.dot(zc,ey))
R_arm=rot_axis(roll,ex)@R1; Cm_m=Cm*MM2M
Mrot=mathutils.Matrix([[float(R_arm[r,c]) for c in range(3)]+[0.0] for r in range(3)]+[[0.0,0.0,0.0,1.0]])
Taff=mathutils.Matrix.Translation(mathutils.Vector(hole)) @ Mrot @ mathutils.Matrix.Translation(-mathutils.Vector(Cm_m))
for ob in objs: ob.matrix_world = Taff @ ob.matrix_world
# --- загрузить тело с вырезанным отверстием ---
bpy.ops.import_scene.gltf(filepath=BASE+"/model.glb")
bpy.ops.object.select_all(action='SELECT')
os.makedirs(FIGDIR,exist_ok=True)
bpy.ops.export_scene.gltf(filepath=FIGDIR+"/model.glb",export_format='GLB',export_apply=True)
allv=[o.matrix_world@v.co for o in scn.objects if o.type=='MESH' for v in o.data.vertices]
mn=mathutils.Vector((min(p.x for p in allv),min(p.y for p in allv),min(p.z for p in allv)))
mx=mathutils.Vector((max(p.x for p in allv),max(p.y for p in allv),max(p.z for p in allv)))
ctr=(mn+mx)/2; Rr=max(mx-mn)
def shot(fp,loc):
    cd=bpy.data.cameras.new("c"); cam=bpy.data.objects.new("c",cd); scn.collection.objects.link(cam); scn.camera=cam; cd.lens=50
    cam.location=loc; cam.rotation_euler=(ctr-mathutils.Vector(loc)).to_track_quat('-Z','Y').to_euler()
    scn.render.filepath=fp; bpy.ops.render.render(write_still=True); bpy.data.objects.remove(cam,do_unlink=True)
for a,en in [((55,25,0),3.0),((40,-55,0),1.5),((12,165,0),1.1)]:
    L=bpy.data.lights.new("s",'SUN'); L.energy=en; lo=bpy.data.objects.new("s",L); scn.collection.objects.link(lo); lo.rotation_euler=tuple(math.radians(v) for v in a)
wd=bpy.data.worlds.new("w"); scn.world=wd; wd.use_nodes=True; wd.node_tree.nodes["Background"].inputs["Strength"].default_value=0.6
scn.render.engine='CYCLES'; scn.cycles.device='CPU'; scn.cycles.samples=20; scn.render.film_transparent=True
scn.render.resolution_x=1000; scn.render.resolution_y=1300
shot(BASE+"/figure_front.png",(ctr.x,ctr.y-Rr*1.8,ctr.z+Rr*0.0))
shot(BASE+"/figure_34.png",(ctr.x+Rr*1.0,ctr.y-Rr*1.5,ctr.z+Rr*0.25))
print("FIGURE OK hole=",hole.round(3)," ex=",ex.round(2)," roll=%.1f"%math.degrees(roll))
