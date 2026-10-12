#!/usr/bin/env python3
"""ВСЕ детали раскроя плоскими панелями в 3D (перёд, зад, боковины с цветами, рука, кисть, днище, ножка).
Раскладка сеткой + подписи. Рендер + GLB. Запуск: blender -b --python flat_layout.py -- flat_pieces.json"""
import bpy, bmesh, sys, os, json, math, mathutils
SPEC=sys.argv[sys.argv.index("--")+1:][0]; OUT=os.path.dirname(os.path.abspath(SPEC))
data=json.load(open(SPEC)); MM2M=0.001
bpy.ops.wm.read_factory_settings(use_empty=True); scn=bpy.context.scene

def tex_mat(name,png):
    m=bpy.data.materials.new(name); m.use_nodes=True; b=m.node_tree.nodes["Principled BSDF"]
    p=os.path.join(OUT,png)
    if os.path.isfile(p):
        tn=m.node_tree.nodes.new("ShaderNodeTexImage"); tn.image=bpy.data.images.load(p); tn.extension='CLIP'
        m.node_tree.links.new(tn.outputs["Color"],b.inputs["Base Color"])
        m.node_tree.links.new(tn.outputs["Color"],b.inputs["Emission Color"]); b.inputs["Emission Strength"].default_value=0.5
    return m
def col_mat(name,rgb,emit=0.4):
    m=bpy.data.materials.new(name); m.use_nodes=True; b=m.node_tree.nodes["Principled BSDF"]
    c=(rgb[0]/255,rgb[1]/255,rgb[2]/255,1); b.inputs["Base Color"].default_value=c
    b.inputs["Emission Color"].default_value=c; b.inputs["Emission Strength"].default_value=emit
    return m

def poly_bbox(poly):
    xs=[p[0] for p in poly]; ys=[p[1] for p in poly]; return min(xs),min(ys),max(xs),max(ys)

def add_face(bm, pts2d, xoff, zoff):
    vs=[bm.verts.new((x*MM2M+xoff,0.0,-y*MM2M+zoff)) for x,y in pts2d]
    try: return bm.faces.new(vs)
    except: return None

def build_piece(pc, xoff, zoff):
    name=pc["id"]; poly=pc["poly"]; x0,y0,x1,y1=poly_bbox(poly)
    mesh=bpy.data.meshes.new(name); obj=bpy.data.objects.new(name,mesh); scn.collection.objects.link(obj)
    bm=bmesh.new()
    kind=pc["kind"]
    if kind=="bands":
        # каждая цветная полоса боковины = своя грань
        mats={}
        for i,bd in enumerate(pc["bands"]):
            f=add_face(bm,bd["q"],xoff,zoff)
        bmesh.ops.triangulate(bm,faces=bm.faces[:]); bmesh.ops.recalc_face_normals(bm,faces=bm.faces[:])
        bm.to_mesh(mesh); bm.free()
        # назначить материалы по полосам: перестроим по центрам граней
        me=obj.data
        cols=[]
        for bd in pc["bands"]:
            key=tuple(bd["c"]); 
            if key not in mats:
                mats[key]=len(me.materials); me.materials.append(col_mat(name+str(len(me.materials)),list(key)))
            cols.append((bd["q"],bd["c"]))
        # для каждой грани — ближайшая полоса по центру
        import statistics
        bcent=[]
        for bd in pc["bands"]:
            qs=bd["q"]; cx=sum(p[0] for p in qs)/len(qs); cy=sum(p[1] for p in qs)/len(qs); bcent.append((cx,cy,tuple(bd["c"])))
        for poly_f in me.polygons:
            c=poly_f.center; mx=(c.x-xoff)/MM2M; my=-(c.z-zoff)/MM2M
            best=min(bcent,key=lambda b:(b[0]-mx)**2+(b[1]-my)**2)
            poly_f.material_index=mats[best[2]]
        w=(x1-x0)*MM2M; h=(y1-y0)*MM2M; return obj,w,h
    add_face(bm,poly,xoff,zoff)
    bmesh.ops.triangulate(bm,faces=bm.faces[:]); bmesh.ops.recalc_face_normals(bm,faces=bm.faces[:])
    bm.to_mesh(mesh); bm.free(); me=obj.data
    if kind=="tex":
        me.materials.append(tex_mat(name,pc["texture"]))
        uvl=me.uv_layers.new(name="UV"); ww=(x1-x0)or 1; hh=(y1-y0)or 1
        for pf in me.polygons:
            for li in pf.loop_indices:
                co=me.vertices[me.loops[li].vertex_index].co; mx=(co.x-xoff)/MM2M; my=-(co.z-zoff)/MM2M
                uvl.data[li].uv=((mx-x0)/ww,1.0-(my-y0)/hh)
    elif kind=="fill":
        me.materials.append(col_mat(name,pc["fill"]))
    else:
        me.materials.append(col_mat(name,[245,245,245]))
    w=(x1-x0)*MM2M; h=(y1-y0)*MM2M; return obj,w,h

def label(txt,x,z,size=0.09):
    cu=bpy.data.curves.new(txt,'FONT'); cu.body=txt; cu.size=size
    o=bpy.data.objects.new(txt,cu); scn.collection.objects.link(o)
    o.location=(x,0,z); o.rotation_euler=(math.radians(90),0,0)
    o.data.materials.append(col_mat("t"+txt,[20,20,20],emit=0.8)); return o

# --- раскладка сеткой (полки) ---
pieces=sorted(data["pieces"], key=lambda p:-(poly_bbox(p["poly"])[3]-poly_bbox(p["poly"])[1])); gap=0.45; MAXW=11.0
x=0; z=0; rowh=0
for pc in pieces:
    x0,y0,x1,y1=poly_bbox(pc["poly"]); w=(x1-x0)*MM2M; h=(y1-y0)*MM2M
    if x>0 and x+w>MAXW:
        x=0; z-=rowh+gap+0.18; rowh=0
    build_piece(pc, x - x0*MM2M, z - y1*MM2M)   # верх детали на уровне z
    label(pc["id"], x, z+0.12)
    x+=w+gap; rowh=max(rowh,h)

# камера ортопроекция сверху-спереди (на плоскости XZ)
allo=[o for o in scn.objects if o.type=='MESH']
allv=[o.matrix_world@v.co for o in allo for v in o.data.vertices]
mn=mathutils.Vector((min(p.x for p in allv),0,min(p.z for p in allv)))
mx=mathutils.Vector((max(p.x for p in allv),0,max(p.z for p in allv)))
ctr=(mn+mx)/2; dim=mx-mn
cd=bpy.data.cameras.new("c"); cd.type='ORTHO'; cd.ortho_scale=max(dim.x,dim.z)*1.05
cam=bpy.data.objects.new("c",cd); scn.collection.objects.link(cam); scn.camera=cam
cam.location=(ctr.x,-8,ctr.z); cam.rotation_euler=(math.radians(90),0,0)
L=bpy.data.lights.new("s",'SUN'); L.energy=2.0; lo=bpy.data.objects.new("s",L); scn.collection.objects.link(lo); lo.rotation_euler=(math.radians(90),0,0)
w=bpy.data.worlds.new("w"); scn.world=w; w.use_nodes=True; w.node_tree.nodes["Background"].inputs["Strength"].default_value=1.0
scn.render.engine='CYCLES'; scn.cycles.device='CPU'; scn.cycles.samples=8; scn.render.film_transparent=True
scn.render.resolution_x=2000; scn.render.resolution_y=max(400,int(2000*dim.z/dim.x))
scn.render.filepath=os.path.join(OUT,"flat_all.png")
bpy.ops.render.render(write_still=True)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.export_scene.gltf(filepath=os.path.join(OUT,"flat_all.glb"),export_format='GLB',export_apply=True)
print("FLAT ALL OK", scn.render.resolution_x, scn.render.resolution_y)
