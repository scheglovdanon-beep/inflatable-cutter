"use strict";
// Состояние мастера. Все точки храним в PT страницы PDF (как в manual_arms).
const S = { sid:null, meta:null, size:"2700", n:1, ident:false,
            arms:[],            // [{f,tip,blade,wrist,elbow}] в PT
            armCrops:[],        // метаданные кропов рук из /api/arm_preview
            cur:0 };

// ключ доступа (из URL или cookie) -> добавляем к картинкам, чтобы <img> точно прошёл гейт
const KEY = new URLSearchParams(location.search).get("key")
  || (document.cookie.match(/(?:^|; )dash_key=([^;]+)/) || [])[1] || "";
function imgURL(u){ return u + (u.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(KEY) + "&t=" + Date.now(); }

const $ = s => document.querySelector(s);
const show = n => { document.querySelectorAll(".panel").forEach(p=>p.classList.remove("on"));
  $("#s"+n).classList.add("on");
  document.querySelectorAll(".step").forEach(s=>s.classList.toggle("on", s.dataset.s==String(n))); };

// ---------- холст для выбора точек ----------
class Picker {
  constructor(canvas){ this.cv=canvas; this.ctx=canvas.getContext("2d"); this.img=null;
    this.meta=null; this.scale=1; this.overlay=()=>{};
    canvas.addEventListener("click", e=>this._click(e)); }
  load(url, meta, overlay, onClick){
    this.meta=meta; this.overlay=overlay; this.onClick=onClick; this.img=null;
    this.cv.width=880; this.cv.height=440;                         // плейсхолдер на время загрузки
    this._loading=true; this._tick=0; this._anim();
    const im=new Image();
    im.onload=()=>{ this._loading=false; this.img=im;
      const maxw=880; this.scale=Math.min(1, maxw/im.naturalWidth);
      this.cv.width=Math.round(im.naturalWidth*this.scale);
      this.cv.height=Math.round(im.naturalHeight*this.scale);
      this.draw(); };
    im.onerror=()=>{ this._loading=false; this._msg("⚠ не удалось загрузить фото — обновите страницу", "#c0392b"); };
    im.src=imgURL(url);
  }
  _anim(){ if(!this._loading) return;
    this._msg("Загрузка фото" + ".".repeat(1 + this._tick % 3), "#2b8cf0");
    this._tick++; setTimeout(()=>this._anim(), 350); }
  _msg(t, color){ const c=this.ctx; c.clearRect(0,0,this.cv.width,this.cv.height);
    c.fillStyle="#f0f3f7"; c.fillRect(0,0,this.cv.width,this.cv.height);
    c.fillStyle=color; c.font="bold 22px sans-serif"; c.textAlign="center";
    c.fillText(t, this.cv.width/2, this.cv.height/2); c.textAlign="left"; }
  // пиксель дисплея -> PT ;  PT -> пиксель дисплея
  toPT(dx,dy){ const m=this.meta; return [ dx/this.scale/m.zoom + m.ox, dy/this.scale/m.zoom + m.oy ]; }
  toPx(pt){ const m=this.meta; return [ (pt[0]-m.ox)*m.zoom*this.scale, (pt[1]-m.oy)*m.zoom*this.scale ]; }
  draw(){ if(!this.img) return; const c=this.ctx;
    c.clearRect(0,0,this.cv.width,this.cv.height);
    c.drawImage(this.img,0,0,this.cv.width,this.cv.height); this.overlay(this); }
  _click(e){ const r=this.cv.getBoundingClientRect();
    const dx=(e.clientX-r.left)*(this.cv.width/r.width), dy=(e.clientY-r.top)*(this.cv.height/r.height);
    this.onClick(this.toPT(dx,dy)); }
  dot(pt,color,label){ const [x,y]=this.toPx(pt); const c=this.ctx;
    c.fillStyle=color; c.strokeStyle="#fff"; c.lineWidth=2;
    c.beginPath(); c.arc(x,y,6,0,7); c.fill(); c.stroke();
    if(label){ c.fillStyle="#000"; c.font="bold 13px sans-serif"; c.fillText(label,x+8,y-8); } }
  line(p0,p1,color){ const a=this.toPx(p0), b=this.toPx(p1); const c=this.ctx;
    c.strokeStyle=color; c.lineWidth=3; c.beginPath(); c.moveTo(a[0],a[1]); c.lineTo(b[0],b[1]); c.stroke(); }
}

// ---------- ШАГ 1: загрузка ----------
$("#btnUpload").onclick = async () => {
  const f=$("#pdf").files[0];
  if(!f){ msg("#upMsg","выберите PDF","err"); return; }
  busy($("#btnUpload"),true,"загрузка"); msg("#upMsg","загрузка макета…","spin");
  try{
    const fd=new FormData(); fd.append("pdf", f);
    const r=await fetch("/api/upload",{method:"POST",body:fd}); const j=await r.json();
    if(!r.ok){ msg("#upMsg",j.error||"ошибка","err"); return; }
    S.sid=j.sid; S.meta=j; (function(){var _m=$("#s2macet"); if(_m) _m.src=imgURL(j.img);})(); msg("#upMsg","макет загружен ✓","ok"); show(2);
  }catch(e){ msg("#upMsg","ошибка загрузки","err"); }
  finally{ busy($("#btnUpload"),false); }
};

// ---------- ШАГ 2: параметры ----------
$("#narms").onchange = () => { $("#identWrap").style.display = $("#narms").value==="2" ? "inline-block":"none"; };
async function enterStep3(btn){                 // детект кандидатов + шаг 3 (вход и «переделать рез руки»)
  show(3); S.cur=0; msg("#s3msg","ищу кандидатные точки на макете…","spin");
  try {
    const r=await fetch("/api/candidates",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size})});
    const j=await r.json(); S.cands = j.pts || [];
    if(S.cands.length){ msg("#s3msg",`найдено ${S.cands.length} точек. Выберите подмышку (и верх плеча для хорды).`,"ok"); }
    else { msg("#s3msg","кандидаты не найдены — попробуйте другой размер/макет.","err"); }
  } catch(e){ msg("#s3msg","не удалось получить кандидатов.","err"); S.cands=[]; }
  finally{ busy(btn,false); }
  startCut();
}
$("#toS3").onclick = () => {
  S.size=$("#size").value; S.n=parseInt($("#narms").value); S.ident=$("#ident").checked;
  S.arms=Array.from({length:S.n},()=>({armpit:null,shoulder:null,wrist:[],elbow:[]}));
  S.cands=null;
  if(S.n===0){ goStep5(); return; }
  busy($("#toS3"),true,"определяю"); enterStep3($("#toS3"));
};
document.querySelectorAll(".back").forEach(b=> b.onclick=()=>show(parseInt(b.dataset.to)));

// ---------- ШАГ 3: рез руки (выбор точек по НОМЕРАМ) ----------
const pick3 = new Picker($("#cv3"));
function cand(i){ return (S.cands||[]).find(c=>c.i===i); }        // кандидат по номеру -> {i,x,y} PT
function startCut(){
  show(3);
  $("#armLabel").textContent = S.n>1 ? `(рука ${S.cur+1} из ${S.n})` : "";
  fillSelects(); updPrompt3();
  pick3.load(S.meta.img, S.meta, ov3, onPick3);
}
function fillSelects(){
  const a=S.arms[S.cur];
  const fill=(sel,ph)=>{ const el=$(sel); el.innerHTML="";
    const o0=document.createElement("option"); o0.value=""; o0.textContent=ph; el.appendChild(o0);
    (S.cands||[]).forEach(c=>{ const o=document.createElement("option"); o.value=c.i; o.textContent="№ "+c.i; el.appendChild(o); }); };
  fill("#selArmpit","—"); fill("#selShoulder","— (вертикальный рез)");
  $("#selArmpit").value = a.armpit!=null ? a.armpit : "";
  $("#selShoulder").value = a.shoulder!=null ? a.shoulder : "";
}
$("#selArmpit").onchange = ()=>{ const v=$("#selArmpit").value; S.arms[S.cur].armpit = v===""?null:parseInt(v); pick3.draw(); updPrompt3(); };
$("#selShoulder").onchange = ()=>{ const v=$("#selShoulder").value; S.arms[S.cur].shoulder = v===""?null:parseInt(v); pick3.draw(); updPrompt3(); };
function updPrompt3(){ const a=S.arms[S.cur];
  $("#prompt3").textContent = a.armpit==null ? "Выберите № точки подмышки (в списке или кликом по точке)."
    : (a.shoulder==null ? `Подмышка №${a.armpit} → вертикальный рез. Для хорды выберите верх плеча.`
       : `Хорда: подмышка №${a.armpit} ↔ верх плеча №${a.shoulder}.`); }
// клик по макету = выбрать ближайший кандидат (1-й -> подмышка, 2-й -> верх плеча, 3-й -> сброс)
function onPick3(pt){ if(!S.cands||!S.cands.length) return;
  let best=null,bd=1e18; S.cands.forEach(c=>{ const dd=Math.hypot(c.x-pt[0],c.y-pt[1]); if(dd<bd){bd=dd;best=c;} });
  if(!best) return; const a=S.arms[S.cur];
  if(a.armpit==null) a.armpit=best.i;
  else if(a.shoulder==null && best.i!==a.armpit) a.shoulder=best.i;
  else { a.armpit=best.i; a.shoulder=null; }
  fillSelects(); pick3.draw(); updPrompt3(); }
function ov3(p){
  (S.cands||[]).forEach(c=> p.dot([c.x,c.y], "#ffd000", String(c.i)));   // все кандидаты — жёлтые с номером
  const a=S.arms[S.cur]; const c0=cand(a.armpit), c1=cand(a.shoulder);
  if(c0) p.dot([c0.x,c0.y],"#e4002b","подмышка");
  if(c1) p.dot([c1.x,c1.y],"#ff8c00","верх плеча");
  if(c0&&c1) p.line([c0.x,c0.y],[c1.x,c1.y],"#ff8c00"); }
$("#armNext").onclick=async()=>{ const a=S.arms[S.cur];
  if(a.armpit==null){ msg("#s3msg","выберите № точки подмышки","err"); return; }
  msg("#s3msg","","");
  if(S.cur < S.n-1){ S.cur++; startCut(); return; }
  busy($("#armNext"),true,"проверяю рез"); msg("#s3msg","проверяю рез и готовлю руку…","spin");
  try{
    const r=await fetch("/api/arm_preview",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size,arms:S.arms.map(armPayload)})});
    const j=await r.json();
    if(!r.ok){ msg("#s3msg",j.error,"err"); return; }
    msg("#s3msg","","");  S.armCrops=j.arms; S.cur=0; startHand();
  }catch(e){ msg("#s3msg","ошибка подготовки руки","err"); }
  finally{ busy($("#armNext"),false); }
};

// ---------- ШАГ 4: запястье/локоть ----------
const pick4 = new Picker($("#cv4"));
const slots4=["wrist0","wrist1","elbow0","elbow1"];
function slotLabel(s){ return {wrist0:"запястье — точка 1", wrist1:"запястье — точка 2",
  elbow0:"локоть — точка 1", elbow1:"локоть — точка 2"}[s]; }
function startHand(){
  show(4);
  $("#armLabel4").textContent = S.n>1 ? `(рука ${S.cur+1} из ${S.n})` : "";
  const crop=S.armCrops[S.cur] || S.armCrops[0];
  renderList("#pickList4", slots4, slotLabel, slotFilled4);
  updPrompt4();
  pick4.load(crop.img, crop, ov4, onPick4);
}
function slotFilled4(s){ const a=S.arms[S.cur];
  if(s==="wrist0") return a.wrist.length>=1; if(s==="wrist1") return a.wrist.length>=2;
  if(s==="elbow0") return a.elbow.length>=1; if(s==="elbow1") return a.elbow.length>=2; return false; }
function nextSlot4(){ return slots4.find(s=>!slotFilled4(s)); }
function updPrompt4(){ const n=nextSlot4();
  $("#prompt4").textContent = n ? "Кликните: "+slotLabel(n) : "Все срезы заданы — подтвердите руку.";
  markActive("#pickList4", slots4, slotFilled4, n); }
function onPick4(pt){ const a=S.arms[S.cur]; const n=nextSlot4(); if(!n) return;
  if(n[0]==="w") a.wrist.push(pt); else a.elbow.push(pt);
  pick4.draw(); updPrompt4(); }
function ov4(p){ const a=S.arms[S.cur];
  a.wrist.forEach(w=>p.dot(w,"#2e9e2e")); if(a.wrist.length>=2) p.line(a.wrist[0],a.wrist[1],"#2e9e2e");
  a.elbow.forEach(w=>p.dot(w,"#8e44ad")); if(a.elbow.length>=2) p.line(a.elbow[0],a.elbow[1],"#8e44ad"); }
$("#undo4").onclick=()=>{ const a=S.arms[S.cur];
  if(a.elbow.length){ a.elbow.pop(); } else if(a.wrist.length){ a.wrist.pop(); }
  pick4.draw(); updPrompt4(); };
function s4done(){ msg("#s4msg","",""); if(S.cur < S.n-1){ S.cur++; startHand(); } else goStep5(); }
function goStep5(){ show(5); loadFrontPreview(); }
const FP = { cv:$("#fpCanvas"), ctx:$("#fpCanvas").getContext("2d"), img:null, data:null,
             scale:1, editing:false, drag:-1, cols:["#e4002b","#1560ff","#10a050","#f58c00"] };
async function loadFrontPreview(){
  const w=$("#frontPrevWrap");
  if(S.n===0){ w.style.display="none"; return; }
  w.style.display="block"; $("#fpWrap").style.display="none"; $("#fpBtns").style.display="none";
  msg("#frontPrevMsg","строю превью переда (где ляжет рука)…","spin");
  try{
    const r=await fetch("/api/front_preview",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size,arms_identical:S.ident,
        spare:$("#spare").checked,back_copy:$("#backCopy").checked,arms:S.arms.map(armPayload)})});
    const j=await r.json();
    if(!r.ok){ msg("#frontPrevMsg",j.error||"не удалось","err"); return; }
    msg("#frontPrevMsg",`цветом — где ляжет рука (${j.arms} шт., длина = Ø${j.hole_mm} мм отверстия)`,"ok");
    FP.data=j; fpInitGeom();
    const im=new Image();
    im.onload=()=>{ FP.img=im; FP.scale=Math.min(1,880/j.W);
      FP.cv.width=Math.round(j.W*FP.scale); FP.cv.height=Math.round(j.H*FP.scale);
      $("#fpWrap").style.display="block"; $("#fpBtns").style.display="block";
      $("#fpApply").style.display="none"; $("#fpReset").style.display="none"; $("#fpHint").style.display="none";
      FP.editing=false; fpDraw(); };
    im.src=imgURL(j.img);
  }catch(e){ msg("#frontPrevMsg","ошибка превью переда","err"); }
}
// ---- геометрия контура (длина по дуге) для перетаскивания отрезка ----
function fpInitGeom(){ const C=FP.data.contour; FP.cum=[0];
  for(let i=1;i<C.length;i++) FP.cum[i]=FP.cum[i-1]+Math.hypot(C[i][0]-C[i-1][0],C[i][1]-C[i-1][1]);
  FP.total=FP.cum[C.length-1]||1; FP.Lp=FP.data.seg_len_px;
  FP.s0=[]; FP.s0def=[]; FP.dir=[]; FP.origY=[];
  FP.data.segments.forEach((seg,i)=>{
    const a=seg.reduce((m,p)=>p[1]>m[1]?p:m, seg[0]);            // нижняя точка (подмышка)
    const sa=fpProj(a[0],a[1]);
    FP.dir[i]=(fpAt(sa+6)[1] < fpAt(sa-6)[1]) ? 1 : -1;          // направление «вверх» по дуге (меньший y)
    FP.s0[i]=sa;
    const hd=(S.arms[i]&&S.arms[i].holeDy)||0;
    FP.origY[i]=a[1]-hd/FP.data.mm_per_px;                       // дефолтный y якоря
    FP.s0def[i]=fpProj(a[0],FP.origY[i]);                        // дефолтная позиция (для сброса)
  });
  fpRebuild();
}
function fpAt(t){ const C=FP.data.contour,cum=FP.cum; t=((t%FP.total)+FP.total)%FP.total;   // заворот (кольцо)
  let i=1; while(i<cum.length && cum[i]<t) i++; const i0=Math.max(0,i-1),i1=Math.min(i,C.length-1);
  const sl=(cum[i1]-cum[i0])||1, f=(t-cum[i0])/sl;
  return [C[i0][0]+(C[i1][0]-C[i0][0])*f, C[i0][1]+(C[i1][1]-C[i0][1])*f]; }
function fpProj(x,y){ const C=FP.data.contour,cum=FP.cum; let bt=0,bd=1e18;  // проекция точки на контур -> дуга
  for(let i=0;i<C.length-1;i++){ const ax=C[i][0],ay=C[i][1],dx=C[i+1][0]-ax,dy=C[i+1][1]-ay,L2=(dx*dx+dy*dy)||1;
    let u=((x-ax)*dx+(y-ay)*dy)/L2; u=Math.max(0,Math.min(1,u)); const px=ax+dx*u,py=ay+dy*u,dd=(px-x)**2+(py-y)**2;
    if(dd<bd){bd=dd; bt=cum[i]+Math.sqrt(L2)*u;} } return bt; }
function fpSub(s0,dir){ const N=Math.max(8, Math.ceil(FP.Lp/6)); const pts=[];  // ФИКС. длина Lp, равномерная выборка (гладко, с заворотом)
  for(let k=0;k<=N;k++) pts.push(fpAt(s0 + dir*FP.Lp*k/N)); return pts; }
function fpRebuild(){ FP.segs=FP.s0.map((s0,i)=>fpSub(s0,FP.dir[i])); fpDraw(); }
function fpDraw(){ const c=FP.ctx, s=FP.scale; if(!FP.img) return;
  c.clearRect(0,0,FP.cv.width,FP.cv.height); c.drawImage(FP.img,0,0,FP.cv.width,FP.cv.height);
  FP.segs.forEach((seg,i)=>{ c.strokeStyle=FP.cols[i%4]; c.lineWidth=10; c.lineCap="round"; c.lineJoin="round";
    c.beginPath(); seg.forEach((p,k)=>{ const x=p[0]*s,y=p[1]*s; k?c.lineTo(x,y):c.moveTo(x,y); }); c.stroke();
    const a=fpAt(FP.s0[i]); c.fillStyle="#000"; c.font="bold 13px sans-serif"; c.fillText("рука "+(i+1), a[0]*s+10, a[1]*s-8); });
}
function fpXY(e){ const r=FP.cv.getBoundingClientRect();
  return [ (e.clientX-r.left)*(FP.cv.width/r.width)/FP.scale, (e.clientY-r.top)*(FP.cv.height/r.height)/FP.scale ]; }
function fpDistToSeg(x,y,seg){ let bd=1e18;
  for(let i=0;i<seg.length-1;i++){ const ax=seg[i][0],ay=seg[i][1],dx=seg[i+1][0]-ax,dy=seg[i+1][1]-ay,L2=(dx*dx+dy*dy)||1;
    let u=((x-ax)*dx+(y-ay)*dy)/L2; u=Math.max(0,Math.min(1,u)); const px=ax+dx*u,py=ay+dy*u;
    bd=Math.min(bd,(px-x)**2+(py-y)**2); } return Math.sqrt(bd); }
FP.cv.addEventListener("pointerdown", e=>{ if(!FP.editing) return; const [x,y]=fpXY(e);   // хват за ЛЮБУЮ часть отрезка
  let bi=-1,bd=1e18; FP.segs.forEach((seg,i)=>{ const dd=fpDistToSeg(x,y,seg); if(dd<bd){bd=dd;bi=i;} });
  if(bi>=0 && bd < 26/FP.scale){ FP.drag=bi; FP.grabArc=fpProj(x,y); FP.grabS0=FP.s0[bi]; FP.cv.setPointerCapture(e.pointerId); } });
FP.cv.addEventListener("pointermove", e=>{ if(FP.drag<0) return; const [x,y]=fpXY(e);
  let d=fpProj(x,y)-FP.grabArc; d=((d%FP.total)+FP.total)%FP.total; if(d>FP.total/2) d-=FP.total;  // кратчайший сдвиг по кольцу
  const s0=FP.grabS0+d;
  FP.s0[FP.drag]=s0; FP.segs[FP.drag]=fpSub(s0,FP.dir[FP.drag]); fpDraw(); });
FP.cv.addEventListener("pointerup", ()=>{ FP.drag=-1; });
FP.cv.addEventListener("pointercancel", ()=>{ FP.drag=-1; });
$("#fpEdit").onclick=()=>{ FP.editing=true; $("#fpApply").style.display="inline-flex";
  $("#fpReset").style.display="inline-flex"; $("#fpHint").style.display="block"; FP.cv.style.cursor="grab"; };
$("#fpReset").onclick=()=>{ FP.s0=FP.s0def.slice(); S.arms.forEach(a=>a.holeDy=0); fpRebuild(); };
$("#fpApply").onclick=()=>{
  FP.s0.forEach((s0,i)=>{ const dy=(fpAt(s0)[1]-FP.origY[i])*FP.data.mm_per_px; if(S.arms[i]) S.arms[i].holeDy=dy; });
  FP.editing=false; FP.cv.style.cursor="default"; $("#fpHint").style.display="none";
  const ds=S.arms.map(a=>Math.round(a.holeDy||0));
  msg("#frontPrevMsg",`сдвиг отверстия применён: ${ds.map((v,i)=>`рука ${i+1} ${v>0?"+":""}${v}мм`).join(", ")} — учтётся при сборке`,"ok");
};
$("#armNext4").onclick=()=>{ const a=S.arms[S.cur];
  if(a.wrist.length<2||a.elbow.length<2){ msg("#s4msg","нужны по 2 точки на запястье и локоть (или «пропустить»)","err"); return; }
  busy($("#armNext4"),true,"готовлю"); s4done(); busy($("#armNext4"),false); };
$("#skip4").onclick=()=>{ const a=S.arms[S.cur]; a.wrist=[]; a.elbow=[];   // авто-детект запястья/локтя
  busy($("#skip4"),true,"готовлю"); s4done(); busy($("#skip4"),false); };

// ---------- ШАГ 5: сборка ----------
$("#btnBuild").onclick=async()=>{
  $("#dl").style.display="none"; $("#previewWrap").innerHTML="";
  busy($("#btnBuild"),true,"собираю"); msg("#buildMsg","запускаю сборку (2–5 мин)…","spin");
  try{
    const body={sid:S.sid,size:S.size,n_arms:S.n,arms_identical:S.ident,svg:true,
      spare:$("#spare").checked, back_copy:$("#backCopy").checked,
      arms:S.arms.map(armPayload)};
    const r=await fetch("/api/build",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json(); if(!r.ok){ msg("#buildMsg",j.error||"ошибка","err"); busy($("#btnBuild"),false); return; }
    poll();
  }catch(e){ msg("#buildMsg","ошибка запуска сборки","err"); busy($("#btnBuild"),false); }
};
async function poll(){
  const r=await fetch("/api/status/"+S.sid); const j=await r.json();
  const done=(j.status==="done"||j.status==="error");
  msg("#buildMsg", j.msg || j.status, j.status==="error"?"err":(done?"ok":"spin"));
  if(j.status==="done"){
    busy($("#btnBuild"),false);
    $("#dl").style.display="block";
    $("#dlPdf").href=imgURL(j.pdf); $("#dlSvg").style.display = j.svg?"inline-block":"none"; if(j.svg) $("#dlSvg").href=imgURL(j.svg);
    if(j.png) makeZoomable($("#previewWrap"), imgURL(j.png));
    return;
  }
  if(j.status==="error"){ busy($("#btnBuild"),false); return; }
  setTimeout(poll, 3000);
}

// ---------- утилиты ----------
// рука -> payload для бэкенда: f=подмышка, blade=хорда (если выбран верх плеча), tip досчитает сервер
function armPayload(a){ const c0=cand(a.armpit); const o={f:[c0.x,c0.y]};
  if(a.shoulder!=null){ const c1=cand(a.shoulder); if(c1) o.blade=[[c0.x,c0.y],[c1.x,c1.y]]; }
  if(a.wrist&&a.wrist.length===2) o.wrist=a.wrist; if(a.elbow&&a.elbow.length===2) o.elbow=a.elbow;
  if(a.holeDy) o.hole_dy=a.holeDy; return o; }
function renderList(sel, slots, lab, filled){ const ol=$(sel); ol.innerHTML="";
  slots.forEach(s=>{ const li=document.createElement("li"); li.textContent=lab(s); li.dataset.slot=s;
    if(filled(s)) li.classList.add("filled"); ol.appendChild(li); }); }
function markActive(sel, slots, filled, next){ $(sel).querySelectorAll("li").forEach(li=>{
    li.classList.toggle("filled", filled(li.dataset.slot));
    li.classList.toggle("active", li.dataset.slot===next); }); }
function msg(sel,t,cls){ const e=$(sel); e.textContent=t; e.className="msg "+(cls||""); }
// кнопка: показать «нажата/загрузка» (другой цвет + спиннер) и заблокировать
function busy(btn,on,label){ if(!btn) return;
  if(on){ if(btn.dataset.t===undefined) btn.dataset.t=btn.textContent; btn.classList.add("busy"); btn.disabled=true; if(label) btn.textContent=label; }
  else { btn.classList.remove("busy"); btn.disabled=false; if(btn.dataset.t!==undefined){ btn.textContent=btn.dataset.t; delete btn.dataset.t; } } }

// ---------- Зумируемое превью раскладки ----------
function makeZoomable(container, url){
  container.innerHTML =
    `<div class="zoomctl"><button data-z="in">＋ приблизить</button><button data-z="out">－ отдалить</button>`+
    `<button data-z="reset" class="back">сброс</button><span class="zhint">колесо/пинч — зум, тащить — двигать</span></div>`+
    `<div class="zviewport"><img class="zimg" draggable="false"></div>`;
  const vp=container.querySelector(".zviewport"), img=container.querySelector(".zimg");
  let fit=1, scale=1, tx=0, ty=0, natW=0, natH=0;
  const apply=()=>{ img.style.transform=`translate(${tx}px,${ty}px) scale(${scale})`; };
  function center(){ const r=vp.getBoundingClientRect();
    if(!r.width || !natW){ requestAnimationFrame(center); return; }  // окно ещё не разложено — подождать
    fit=Math.min(r.width/natW, r.height/natH); scale=fit;           // ВПИСАТЬ фигуру в окно 1:1 (вся видна, по максимуму)
    tx=(r.width-natW*fit)/2; ty=(r.height-natH*fit)/2; apply(); }
  function zoomAt(cx,cy,f){ const r=vp.getBoundingClientRect(); const x=cx-r.left, y=cy-r.top;
    const ns=Math.min(fit*3, Math.max(fit, scale*f));               // приближение максимум 3×, отдалить не ниже 100%
    tx=x-(x-tx)*(ns/scale); ty=y-(y-ty)*(ns/scale); scale=ns; apply(); }
  img.onload=()=>{ natW=img.naturalWidth; natH=img.naturalHeight; center(); };
  img.src=url;
  vp.addEventListener("wheel",e=>{ e.preventDefault(); zoomAt(e.clientX,e.clientY, e.deltaY<0?1.2:1/1.2); },{passive:false});
  // перетаскивание + пинч
  const pts=new Map(); let pd=0;
  vp.addEventListener("pointerdown",e=>{ pts.set(e.pointerId,[e.clientX,e.clientY]); vp.setPointerCapture(e.pointerId); vp.style.cursor="grabbing"; });
  vp.addEventListener("pointermove",e=>{ if(!pts.has(e.pointerId)) return;
    const prev=pts.get(e.pointerId); pts.set(e.pointerId,[e.clientX,e.clientY]);
    if(pts.size===2){ const [a,b]=[...pts.values()]; const nd=Math.hypot(a[0]-b[0],a[1]-b[1]);
      if(pd){ zoomAt((a[0]+b[0])/2,(a[1]+b[1])/2, nd/pd); } pd=nd; }
    else { tx+=e.clientX-prev[0]; ty+=e.clientY-prev[1]; apply(); } });
  const up=e=>{ pts.delete(e.pointerId); if(pts.size<2) pd=0; if(!pts.size) vp.style.cursor="grab"; };
  vp.addEventListener("pointerup",up); vp.addEventListener("pointercancel",up);
  vp.addEventListener("dblclick",center);
  container.querySelector('[data-z=in]').onclick=()=>{const r=vp.getBoundingClientRect();zoomAt(r.left+r.width/2,r.top+r.height/2,1.5);};
  container.querySelector('[data-z=out]').onclick=()=>{const r=vp.getBoundingClientRect();zoomAt(r.left+r.width/2,r.top+r.height/2,1/1.5);};
  container.querySelector('[data-z=reset]').onclick=center;
  window.addEventListener("resize",center);
}

// ---------- База макетов ----------
$("#btnLib").onclick = async () => {
  const w=$("#libWrap");
  if(w.style.display==="none"){ w.style.display="block"; await loadLib(); } else { w.style.display="none"; }
};
async function loadLib(){
  const g=$("#libGrid"); g.innerHTML="<p class='hint'>загрузка…</p>";
  try{ const r=await fetch("/api/library"); const j=await r.json();
    g.innerHTML="";
    if(!j.items||!j.items.length){ g.innerHTML="<p class='hint'>база пуста — загрузите первый макет.</p>"; return; }
    j.items.forEach(it=>{ const c=document.createElement("div"); c.className="libcard";
      const im=document.createElement("img"); im.src=imgURL(it.thumb); im.loading="lazy";
      const nm=document.createElement("div"); nm.className="nm"; nm.textContent=it.name;
      c.appendChild(im); c.appendChild(nm); c.onclick=()=>useLib(it.id); g.appendChild(c); });
  }catch(e){ g.innerHTML="<p class='msg err'>не удалось загрузить базу</p>"; }
}
async function useLib(id){
  msg("#upMsg","открываю макет из базы…","spin");
  try{ const r=await fetch("/api/library/use",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
    const j=await r.json(); if(!r.ok){ msg("#upMsg",j.error||"ошибка","err"); return; }
    S.sid=j.sid; S.meta=j; (function(){var _m=$("#s2macet"); if(_m) _m.src=imgURL(j.img);})(); msg("#upMsg","макет «"+(j.name||"")+"» загружен ✓","ok");
    $("#libWrap").style.display="none"; show(2);
  }catch(e){ msg("#upMsg","ошибка открытия макета","err"); }
}

// ---------- Редактирование собранного макета ----------
$("#editArm").onclick = () => {                 // заново задетектить/переназначить рез руки
  S.arms.forEach(a=>{ a.armpit=null; a.shoulder=null; });
  busy($("#editArm"),true,"открываю"); enterStep3($("#editArm"));
};
$("#editHand").onclick = async () => {          // заново задетектить кисть/предплечье (переразметка)
  S.cur=0; busy($("#editHand"),true,"готовлю"); msg("#buildMsg","готовлю руку для переразметки…","spin");
  try{ const r=await fetch("/api/arm_preview",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size,arms:S.arms.map(armPayload)})});
    const j=await r.json(); if(!r.ok){ msg("#buildMsg",j.error,"err"); return; }
    S.armCrops=j.arms; S.arms.forEach(a=>{ a.wrist=[]; a.elbow=[]; }); msg("#buildMsg","",""); startHand();
  }catch(e){ msg("#buildMsg","ошибка подготовки руки","err"); }
  finally{ busy($("#editHand"),false); }
};
