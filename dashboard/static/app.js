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
  S.arms=Array.from({length:S.n},()=>({f:null,tip:null,blade:[],wrist:[],elbow:[],vertical:false}));
  S.cur=0;
  if(S.n===0){ show(5); return; }
  show(3); msg("#s3msg","определяю точки реза (авто)…","");
  try {
    const r=await fetch("/api/detect",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sid:S.sid,size:S.size,n_arms:S.n})});
    const j=await r.json();
    if(j.arms && j.arms.length){
      j.arms.forEach((a,i)=>{ if(i<S.n){ S.arms[i].f=a.f; S.arms[i].tip=a.tip; if(a.blade) S.arms[i].blade=a.blade.map(p=>p.slice()); }});
      msg("#s3msg",`предложено авто: ${j.arms.length} шт. Поправьте точки кликом или смените режим реза.`,"ok");
    } else { msg("#s3msg","авто-детект не нашёл руку — отметьте точки вручную.",""); }
  } catch(e){ msg("#s3msg","авто-детект недоступен — вручную.",""); }
  startCut();
};
document.querySelectorAll(".back").forEach(b=> b.onclick=()=>show(parseInt(b.dataset.to)));

// ---------- ШАГ 3: рез руки ----------
const pick3 = new Picker($("#cv3"));
let slots3=[];                                   // текущая последовательность слотов
function startCut(){
  show(3);
  $("#armLabel").textContent = S.n>1 ? `(рука ${S.cur+1} из ${S.n})` : "";
  // радио отражает режим текущей руки
  document.querySelector(`input[name=cutmode][value=${S.arms[S.cur].vertical?"vertical":"chord"}]`).checked=true;
  rebuildSlots3();
  pick3.load(S.meta.img, S.meta, ov3, onPick3);
}
function rebuildSlots3(){
  slots3 = S.arms[S.cur].vertical ? ["f","tip"] : ["f","tip","blade0","blade1"];
  renderList("#pickList", slots3, slotLabel, slotFilled3);
  updPrompt3();
}
function slotLabel(s){ return {f:"корень руки (у подмышки)", tip:"кончик кисти",
  blade0:"хорда реза — точка 1", blade1:"хорда реза — точка 2",
  wrist0:"запястье — точка 1", wrist1:"запястье — точка 2",
  elbow0:"локоть — точка 1", elbow1:"локоть — точка 2"}[s]; }
function slotFilled3(s){ const a=S.arms[S.cur];
  if(s==="f") return !!a.f; if(s==="tip") return !!a.tip;
  if(s==="blade0") return a.blade.length>=1; if(s==="blade1") return a.blade.length>=2; return false; }
function nextSlot3(){ return slots3.find(s=>!slotFilled3(s)); }
function updPrompt3(){ const n=nextSlot3();
  $("#prompt3").textContent = n ? "Кликните: "+slotLabel(n) : "Все точки заданы — подтвердите руку.";
  markActive("#pickList", slots3, slotFilled3, n); }
function onPick3(pt){ const a=S.arms[S.cur]; const n=nextSlot3(); if(!n) return;
  if(n==="f") a.f=pt; else if(n==="tip") a.tip=pt; else a.blade.push(pt);
  pick3.draw(); updPrompt3(); }
function ov3(p){ const a=S.arms[S.cur];
  if(a.f) p.dot(a.f,"#e4002b","корень");
  if(a.tip) p.dot(a.tip,"#1560ff","кончик");
  if(!a.vertical){
    if(a.blade[0]) p.dot(a.blade[0],"#ff8c00");
    if(a.blade[1]){ p.dot(a.blade[1],"#ff8c00"); p.line(a.blade[0],a.blade[1],"#ff8c00"); } } }
document.querySelectorAll("input[name=cutmode]").forEach(r=> r.onchange=()=>{ S.arms[S.cur].vertical=(r.value==="vertical"); rebuildSlots3(); pick3.draw(); });
$("#undo3").onclick=()=>{ const a=S.arms[S.cur];
  if(!a.vertical && a.blade.length){ a.blade.pop(); } else if(a.tip){ a.tip=null; } else if(a.f){ a.f=null; }
  pick3.draw(); updPrompt3(); };
$("#armNext").onclick=async()=>{ const a=S.arms[S.cur];
  if(!a.f||!a.tip){ msg("#s3msg","нужны корень и кончик","err"); return; }
  if(!a.vertical && a.blade.length<2){ msg("#s3msg","нужны 2 точки хорды","err"); return; }
  msg("#s3msg","","");
  if(S.cur < S.n-1){ S.cur++; startCut(); return; }
  // все руки размечены -> прогнать рез и перейти к запястью/локтю
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
$("#armNext4").onclick=()=>{ const a=S.arms[S.cur];
  if(a.wrist.length<2||a.elbow.length<2){ msg("#s4msg","нужны по 2 точки на запястье и локоть","err"); return; }
  msg("#s4msg","","");
  if(S.cur < S.n-1){ S.cur++; startHand(); return; }
  show(5);
};

// ---------- ШАГ 5: сборка ----------
$("#btnBuild").onclick=async()=>{
  $("#dl").style.display="none"; $("#previewWrap").innerHTML="";
  msg("#buildMsg","запускаю сборку (2–5 мин)…","");
  const body={sid:S.sid,size:S.size,n_arms:S.n,arms_identical:S.ident,svg:true,
    arms:S.arms.map(a=>({...armPayload(a),wrist:a.wrist.length===2?a.wrist:undefined,elbow:a.elbow.length===2?a.elbow:undefined}))};
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
function armPayload(a){ const o={f:a.f,tip:a.tip}; if(!a.vertical && a.blade&&a.blade.length===2) o.blade=a.blade;
  if(a.wrist&&a.wrist.length===2) o.wrist=a.wrist; if(a.elbow&&a.elbow.length===2) o.elbow=a.elbow; return o; }
function renderList(sel, slots, lab, filled){ const ol=$(sel); ol.innerHTML="";
  slots.forEach(s=>{ const li=document.createElement("li"); li.textContent=lab(s); li.dataset.slot=s;
    if(filled(s)) li.classList.add("filled"); ol.appendChild(li); }); }
function markActive(sel, slots, filled, next){ $(sel).querySelectorAll("li").forEach(li=>{
    li.classList.toggle("filled", filled(li.dataset.slot));
    li.classList.toggle("active", li.dataset.slot===next); }); }
function msg(sel,t,cls){ const e=$(sel); e.textContent=t; e.className="msg "+(cls||""); }
