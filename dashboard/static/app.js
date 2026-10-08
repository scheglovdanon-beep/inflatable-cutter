"use strict";
// Состояние мастера. Все точки храним в PT страницы PDF (как в manual_arms).
const S = { sid:null, meta:null, size:"2700", n:1, ident:false,
            arms:[],            // [{f,tip,blade,wrist,elbow}] в PT
            armCrops:[],        // метаданные кропов рук из /api/arm_preview
            cur:0 };

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
    this.meta=meta; this.overlay=overlay; this.onClick=onClick;
    const im=new Image(); im.onload=()=>{ this.img=im;
      const maxw=880; this.scale=Math.min(1, maxw/im.naturalWidth);
      this.cv.width=Math.round(im.naturalWidth*this.scale);
      this.cv.height=Math.round(im.naturalHeight*this.scale);
      this.draw(); };
    im.src=url+"?t="+Date.now();
  }
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
$("#toS3").onclick = async () => {
  S.size=$("#size").value; S.n=parseInt($("#narms").value); S.ident=$("#ident").checked;
  S.arms=Array.from({length:S.n},()=>({armpit:null,shoulder:null,wrist:[],elbow:[]}));
  S.cur=0; S.cands=null;
  if(S.n===0){ show(5); return; }
  show(3); msg("#s3msg","ищу кандидатные точки…","");
  try {
    const r=await fetch("/api/candidates",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size})});
    const j=await r.json();
    S.cands = j.pts || [];
    if(S.cands.length){ msg("#s3msg",`найдено ${S.cands.length} точек. Выберите подмышку (и верх плеча для хорды).`,"ok"); }
    else { msg("#s3msg","кандидаты не найдены — попробуйте другой размер/макет.","err"); }
  } catch(e){ msg("#s3msg","не удалось получить кандидатов.","err"); S.cands=[]; }
  startCut();
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
function s4done(){ msg("#s4msg","",""); if(S.cur < S.n-1){ S.cur++; startHand(); } else show(5); }
$("#armNext4").onclick=()=>{ const a=S.arms[S.cur];
  if(a.wrist.length<2||a.elbow.length<2){ msg("#s4msg","нужны по 2 точки на запястье и локоть (или «пропустить»)","err"); return; }
  s4done(); };
$("#skip4").onclick=()=>{ const a=S.arms[S.cur]; a.wrist=[]; a.elbow=[]; s4done(); };   // авто-детект запястья/локтя

// ---------- ШАГ 5: сборка ----------
$("#btnBuild").onclick=async()=>{
  $("#dl").style.display="none"; $("#previewWrap").innerHTML="";
  msg("#buildMsg","запускаю сборку (2–5 мин)…","");
  const body={sid:S.sid,size:S.size,n_arms:S.n,arms_identical:S.ident,svg:true,
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
    $("#dlPdf").href=j.pdf; $("#dlSvg").style.display = j.svg?"inline-block":"none"; if(j.svg) $("#dlSvg").href=j.svg;
    if(j.png) $("#previewWrap").innerHTML=`<img src="${j.png}?t=${Date.now()}">`;
    return;
  }
  if(j.status==="error") return;
  setTimeout(poll, 3000);
}

// ---------- утилиты ----------
// рука -> payload для бэкенда: f=подмышка, blade=хорда (если выбран верх плеча), tip досчитает сервер
function armPayload(a){ const c0=cand(a.armpit); const o={f:[c0.x,c0.y]};
  if(a.shoulder!=null){ const c1=cand(a.shoulder); if(c1) o.blade=[[c0.x,c0.y],[c1.x,c1.y]]; }
  if(a.wrist&&a.wrist.length===2) o.wrist=a.wrist; if(a.elbow&&a.elbow.length===2) o.elbow=a.elbow; return o; }
function renderList(sel, slots, lab, filled){ const ol=$(sel); ol.innerHTML="";
  slots.forEach(s=>{ const li=document.createElement("li"); li.textContent=lab(s); li.dataset.slot=s;
    if(filled(s)) li.classList.add("filled"); ol.appendChild(li); }); }
function markActive(sel, slots, filled, next){ $(sel).querySelectorAll("li").forEach(li=>{
    li.classList.toggle("filled", filled(li.dataset.slot));
    li.classList.toggle("active", li.dataset.slot===next); }); }
function msg(sel,t,cls){ const e=$(sel); e.textContent=t; e.className="msg "+(cls||""); }
