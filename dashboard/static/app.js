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
  if(!f){ $("#upMsg").textContent="выберите PDF"; $("#upMsg").className="msg err"; return; }
  $("#upMsg").textContent="загрузка…"; $("#upMsg").className="msg";
  const fd=new FormData(); fd.append("pdf", f);
  const r=await fetch("/api/upload",{method:"POST",body:fd}); const j=await r.json();
  if(!r.ok){ $("#upMsg").textContent=j.error||"ошибка"; $("#upMsg").className="msg err"; return; }
  S.sid=j.sid; S.meta=j;
  $("#upMsg").textContent="макет загружен ✓"; $("#upMsg").className="msg ok";
  show(2);
};

// ---------- ШАГ 2: параметры ----------
$("#narms").onchange = () => { $("#identWrap").style.display = $("#narms").value==="2" ? "inline-block":"none"; };
async function enterStep3(){                    // детект кандидатов + шаг 3 (вход и «переделать рез руки»)
  show(3); S.cur=0; msg("#s3msg","ищу кандидатные точки…","");
  try {
    const r=await fetch("/api/candidates",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size})});
    const j=await r.json(); S.cands = j.pts || [];
    if(S.cands.length){ msg("#s3msg",`найдено ${S.cands.length} точек. Выберите подмышку (и верх плеча для хорды).`,"ok"); }
    else { msg("#s3msg","кандидаты не найдены — попробуйте другой размер/макет.","err"); }
  } catch(e){ msg("#s3msg","не удалось получить кандидатов.","err"); S.cands=[]; }
  startCut();
}
$("#toS3").onclick = () => {
  S.size=$("#size").value; S.n=parseInt($("#narms").value); S.ident=$("#ident").checked;
  S.arms=Array.from({length:S.n},()=>({armpit:null,shoulder:null,wrist:[],elbow:[]}));
  S.cands=null;
  if(S.n===0){ goStep5(); return; }
  enterStep3();
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
  msg("#s3msg","проверяю рез…","");
  const r=await fetch("/api/arm_preview",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({sid:S.sid,size:S.size,arms:S.arms.map(armPayload)})});
  const j=await r.json();
  if(!r.ok){ msg("#s3msg",j.error,"err"); return; }
  S.armCrops=j.arms; S.cur=0; startHand();
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
  msg("#frontPrevMsg","строю превью переда…","");
  try{
    const r=await fetch("/api/front_preview",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size,arms_identical:S.ident,
        spare:$("#spare").checked,back_copy:$("#backCopy").checked,arms:S.arms.map(armPayload)})});
    const j=await r.json();
    if(!r.ok){ msg("#frontPrevMsg",j.error||"не удалось","err"); return; }
    msg("#frontPrevMsg",`цветом — где ляжет рука (${j.arms} шт., длина = Ø${j.hole_mm} мм отверстия)`,"ok");
    FP.data=j; FP.segs=j.segments.map(s=>s.slice());
    FP.orig=j.segments.map((s,i)=>{ const a=fpAnchor(s); const hd=(S.arms[i]&&S.arms[i].holeDy)||0;
      return [a[0], a[1]-hd/j.mm_per_px]; });        // дефолтный (несдвинутый) якорь для абсолютного сдвига
    const im=new Image();
    im.onload=()=>{ FP.img=im; FP.scale=Math.min(1,880/j.W);
      FP.cv.width=Math.round(j.W*FP.scale); FP.cv.height=Math.round(j.H*FP.scale);
      $("#fpWrap").style.display="block"; $("#fpBtns").style.display="block";
      $("#fpApply").style.display="none"; $("#fpReset").style.display="none"; $("#fpHint").style.display="none";
      FP.editing=false; fpDraw(); };
    im.src=imgURL(j.img);
  }catch(e){ msg("#frontPrevMsg","ошибка превью переда","err"); }
}
function fpAnchor(seg){ return seg.reduce((a,p)=>p[1]>a[1]?p:a, seg[0]); }   // нижняя точка отрезка (= подмышка)
function fpDraw(){ const c=FP.ctx, s=FP.scale; if(!FP.img) return;
  c.clearRect(0,0,FP.cv.width,FP.cv.height); c.drawImage(FP.img,0,0,FP.cv.width,FP.cv.height);
  FP.segs.forEach((seg,i)=>{ c.strokeStyle=FP.cols[i%4]; c.lineWidth=9; c.lineCap="round"; c.lineJoin="round";
    c.beginPath(); seg.forEach((p,k)=>{ const x=p[0]*s,y=p[1]*s; k?c.lineTo(x,y):c.moveTo(x,y); }); c.stroke();
    const a=fpAnchor(seg); c.fillStyle=FP.cols[i%4]; c.beginPath(); c.arc(a[0]*s,a[1]*s,7,0,7); c.fill();
    c.fillStyle="#000"; c.font="bold 13px sans-serif"; c.fillText("рука "+(i+1), a[0]*s+10, a[1]*s-10); });
}
// построить отрезок по контуру от якоря вверх на длину seg_len_px
function fpSegFromAnchor(ax,ay){ const C=FP.data.contour, n=C.length, Lp=FP.data.seg_len_px;
  let bi=0,bd=1e18; C.forEach((p,i)=>{const dd=(p[0]-ax)**2+(p[1]-ay)**2; if(dd<bd){bd=dd;bi=i;}});
  const up=(C[(bi+1)%n][1] < C[(bi-1+n)%n][1]) ? 1 : -1;      // направление к макушке (меньший y)
  let pts=[C[bi].slice()], acc=0, i=bi;
  while(acc<Lp){ const jn=(i+up+n)%n; acc+=Math.hypot(C[jn][0]-C[i][0],C[jn][1]-C[i][1]); pts.push(C[jn].slice()); i=jn; if(i===bi)break; }
  return pts;
}
function fpXY(e){ const r=FP.cv.getBoundingClientRect();
  return [ (e.clientX-r.left)*(FP.cv.width/r.width)/FP.scale, (e.clientY-r.top)*(FP.cv.height/r.height)/FP.scale ]; }
FP.cv.addEventListener("pointerdown", e=>{ if(!FP.editing) return; const [x,y]=fpXY(e);
  let bi=-1,bd=1e18; FP.segs.forEach((seg,i)=>{ const a=fpAnchor(seg); const dd=(a[0]-x)**2+(a[1]-y)**2; if(dd<bd){bd=dd;bi=i;} });
  if(bi>=0 && bd < (40/FP.scale)**2){ FP.drag=bi; FP.cv.setPointerCapture(e.pointerId); } });
FP.cv.addEventListener("pointermove", e=>{ if(FP.drag<0) return; const [x,y]=fpXY(e);
  FP.segs[FP.drag]=fpSegFromAnchor(x,y); fpDraw(); });
FP.cv.addEventListener("pointerup", ()=>{ FP.drag=-1; });
$("#fpEdit").onclick=()=>{ FP.editing=true; $("#fpApply").style.display="inline-flex";
  $("#fpReset").style.display="inline-flex"; $("#fpHint").style.display="block"; FP.cv.style.cursor="grab"; };
$("#fpReset").onclick=()=>{ FP.segs=FP.data.segments.map(s=>s.slice()); S.arms.forEach(a=>a.holeDy=0); fpDraw(); };
$("#fpApply").onclick=()=>{
  FP.segs.forEach((seg,i)=>{ const dy=(fpAnchor(seg)[1]-FP.orig[i][1])*FP.data.mm_per_px;
    if(S.arms[i]) S.arms[i].holeDy=dy; });
  FP.editing=false; FP.cv.style.cursor="default"; $("#fpHint").style.display="none";
  const ds=S.arms.map(a=>Math.round(a.holeDy||0));
  msg("#frontPrevMsg",`сдвиг отверстия применён: ${ds.map((v,i)=>`рука ${i+1} ${v>0?"+":""}${v}мм`).join(", ")} — учтётся при сборке`,"ok");
};
$("#armNext4").onclick=()=>{ const a=S.arms[S.cur];
  if(a.wrist.length<2||a.elbow.length<2){ msg("#s4msg","нужны по 2 точки на запястье и локоть (или «пропустить»)","err"); return; }
  s4done(); };
$("#skip4").onclick=()=>{ const a=S.arms[S.cur]; a.wrist=[]; a.elbow=[]; s4done(); };   // авто-детект запястья/локтя

// ---------- ШАГ 5: сборка ----------
$("#btnBuild").onclick=async()=>{
  $("#dl").style.display="none"; $("#previewWrap").innerHTML="";
  msg("#buildMsg","запускаю сборку (2–5 мин)…","");
  const body={sid:S.sid,size:S.size,n_arms:S.n,arms_identical:S.ident,svg:true,
    spare:$("#spare").checked, back_copy:$("#backCopy").checked,
    arms:S.arms.map(armPayload)};
  const r=await fetch("/api/build",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const j=await r.json(); if(!r.ok){ msg("#buildMsg",j.error||"ошибка","err"); return; }
  poll();
};
async function poll(){
  const r=await fetch("/api/status/"+S.sid); const j=await r.json();
  msg("#buildMsg", j.msg || j.status, j.status==="error"?"err":"");
  if(j.status==="done"){
    $("#dl").style.display="block";
    $("#dlPdf").href=imgURL(j.pdf); $("#dlSvg").style.display = j.svg?"inline-block":"none"; if(j.svg) $("#dlSvg").href=imgURL(j.svg);
    if(j.png) $("#previewWrap").innerHTML=`<img src="${imgURL(j.png)}">`;
    return;
  }
  if(j.status==="error") return;
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
  msg("#upMsg","открываю макет из базы…","");
  try{ const r=await fetch("/api/library/use",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
    const j=await r.json(); if(!r.ok){ msg("#upMsg",j.error||"ошибка","err"); return; }
    S.sid=j.sid; S.meta=j; msg("#upMsg","макет «"+(j.name||"")+"» загружен ✓","ok");
    $("#libWrap").style.display="none"; show(2);
  }catch(e){ msg("#upMsg","ошибка открытия макета","err"); }
}

// ---------- Редактирование собранного макета ----------
$("#editArm").onclick = () => {                 // заново задетектить/переназначить рез руки
  S.arms.forEach(a=>{ a.armpit=null; a.shoulder=null; });
  enterStep3();
};
$("#editHand").onclick = async () => {          // заново задетектить кисть/предплечье (переразметка)
  S.cur=0; msg("#buildMsg","готовлю руку для переразметки…","");
  try{ const r=await fetch("/api/arm_preview",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size,arms:S.arms.map(armPayload)})});
    const j=await r.json(); if(!r.ok){ msg("#buildMsg",j.error,"err"); return; }
    S.armCrops=j.arms; S.arms.forEach(a=>{ a.wrist=[]; a.elbow=[]; }); msg("#buildMsg","",""); startHand();
  }catch(e){ msg("#buildMsg","ошибка подготовки руки","err"); }
};
