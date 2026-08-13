#!/usr/bin/env python3
"""Local-only image gallery. Bind 127.0.0.1. Do not enable Tailscale Funnel."""

from __future__ import annotations

import hashlib
import html
import io
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import segno

try:
    from PIL import Image
except ImportError:
    Image = None

HOST = "127.0.0.1"
PORT = 8787
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
OUTPUT_ROOT = HERE
CACHE_ROOT = Path(__file__).resolve().parent / "thumb-cache"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PAGE = 80
QUALITY = {
    "low": {"t": 320, "tq": 65, "v": 1280, "vq": 72},
    "std": {"t": 400, "tq": 70, "v": 2048, "vq": 80},
    "high": {"t": 480, "tq": 75, "v": 0, "vq": 0},
}
DERIVE_SEM = threading.Semaphore(3)
_DERIVE_LOCKS: dict[str, threading.Lock] = {}
_DERIVE_LOCKS_GUARD = threading.Lock()
CACHE_IMG = "private, max-age=604800"
TAILSCALE_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Tailscale" / "tailscale.exe",
)
TS_URL_RE = re.compile(r"https://[a-zA-Z0-9._-]+\.ts\.net", re.I)
SERVE_URL = ""

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#000000">
<title>Gallery</title>
<style>
:root{color-scheme:dark;}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{background:#000;color:#eee;font:15px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;
  min-height:100%;}
html.viewing header,html.viewing #grid,html.viewing #more,html.viewing #pull,html.viewing #offline{visibility:hidden;}
header{position:sticky;top:0;z-index:2;background:#0a0a0acc;backdrop-filter:blur(12px);
  padding:12px 14px;padding-top:calc(12px + env(safe-area-inset-top));display:flex;align-items:center;gap:8px;}
#back{display:none;border:0;background:transparent;color:#fff;font-size:32px;line-height:1;
  width:36px;height:36px;padding:0;flex-shrink:0;}
#back.show{display:grid;place-items:center;}
h1{font-size:17px;font-weight:650;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
#count{opacity:.55;font-size:13px;flex-shrink:0;}
#grid{display:grid;grid-template-columns:repeat(3,1fr);gap:2px;background:#000;}
#grid button{border:0;padding:0;background:#111;aspect-ratio:1;overflow:hidden;cursor:pointer;
  content-visibility:auto;contain-intrinsic-size:120px 120px;}
#grid img{width:100%;height:100%;object-fit:cover;display:block;}
#grid button.album{position:relative;background:#1c1c1e;}
#grid button.album .label{position:absolute;left:0;right:0;bottom:0;z-index:1;
  padding:22px 8px 8px;background:linear-gradient(transparent,#000d);
  font-size:12px;font-weight:600;text-align:left;color:#fff;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
#grid button.album .star{position:absolute;top:0;right:0;z-index:2;width:44px;height:44px;
  display:grid;place-items:center;font-size:18px;color:#ffffff88;text-shadow:0 1px 3px #000a;
  line-height:1;}
#grid button.album .star.on{color:#ffd60a;}
#fav{display:none;border:0;background:transparent;color:#ffffff66;font-size:22px;
  width:36px;height:36px;padding:0;flex-shrink:0;line-height:1;}
#fav.show{display:grid;place-items:center;}
#fav.on{color:#ffd60a;}
#reload{border:0;background:transparent;color:#fff;opacity:.7;font-size:20px;
  width:36px;height:36px;padding:0;flex-shrink:0;line-height:1;}
#reload.spin{animation:spin .7s linear infinite;}
@keyframes spin{to{transform:rotate(360deg)}}
#pull{height:0;overflow:hidden;display:flex;align-items:flex-end;justify-content:center;
  color:#aaa;font-size:13px;padding-bottom:4px;}
#offline{display:none;padding:64px 24px 32px;text-align:center;max-width:22em;margin:0 auto;}
#offline.on{display:block;}
#offline p{margin:0 0 10px;opacity:.85;}
#offline button{margin-top:12px;padding:12px 22px;border:0;border-radius:12px;
  background:#1c1c1e;color:#fff;font-size:16px;}
.seg{display:flex;background:#2c2c2e;border-radius:10px;padding:2px;flex-shrink:0;}
.seg button{border:0;background:transparent;color:#fff;padding:7px 12px;border-radius:8px;font-size:13px;}
.seg button.on{background:#636366;}
#settings .row.stack{flex-direction:column;align-items:stretch;gap:10px;}
#more{display:none;width:100%;margin:16px 0 32px;padding:12px;border:0;border-radius:12px;
  background:#1c1c1e;color:#fff;font-size:15px;}
#viewer{display:none;position:fixed;inset:0;background:#000;z-index:10;touch-action:none;}
#viewer.on{display:flex;align-items:center;justify-content:center;}
#viewer img{max-width:100%;max-height:100%;object-fit:contain;transform-origin:center center;
  will-change:transform;-webkit-user-drag:none;user-select:none;}
#chrome{position:absolute;top:calc(8px + env(safe-area-inset-top));right:6px;z-index:2;
  display:flex;align-items:center;gap:2px;}
#chrome button{border:0;background:transparent;color:#fff;opacity:.75;padding:8px;
  width:44px;height:44px;display:grid;place-items:center;}
#chrome svg{width:22px;height:22px;display:block;}
#close{font-size:28px;line-height:1;font-weight:300;}
#settings{display:none;position:fixed;inset:0;z-index:20;background:#000000cc;
  align-items:flex-end;justify-content:center;}
#settings.on{display:flex;}
#settings .sheet{width:100%;max-width:480px;background:#1c1c1e;border-radius:16px 16px 0 0;
  padding:18px 18px calc(18px + env(safe-area-inset-bottom));color:#fff;}
#settings h2{font-size:17px;font-weight:650;margin:0 0 14px;}
#settings .row{display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:12px 0;border-top:1px solid #ffffff14;}
#settings .row span{display:flex;flex-direction:column;gap:4px;}
#settings .row small{opacity:.55;font-size:12px;line-height:1.35;}
#settings .hint{margin:8px 0 16px;opacity:.7;font-size:13px;}
#invert{appearance:none;width:51px;height:31px;border-radius:16px;background:#39393d;
  position:relative;flex-shrink:0;border:0;}
#invert:checked{background:#34c759;}
#invert::after{content:"";position:absolute;top:2px;left:2px;width:27px;height:27px;
  border-radius:50%;background:#fff;transition:transform .2s;}
#invert:checked::after{transform:translateX(20px);}
#settings-done{width:100%;padding:12px;border:0;border-radius:12px;background:#2c2c2e;
  color:#fff;font-size:16px;margin-top:8px;}
.stepper{display:flex;align-items:center;gap:8px;flex-shrink:0;}
.stepper button{width:36px;height:36px;border:0;border-radius:10px;background:#2c2c2e;
  color:#fff;font-size:20px;line-height:1;}
.stepper b{min-width:1.6em;text-align:center;font-variant-numeric:tabular-nums;}
</style>
</head>
<body>
<div id="pull">更新</div>
<header>
  <button id="back" type="button" aria-label="back">&#8249;</button>
  <h1 id="title">Gallery</h1>
  <button id="reload" type="button" aria-label="refresh">↻</button>
  <button id="fav" type="button" aria-label="favorite">★</button>
  <span id="count"></span>
</header>
<div id="offline">
  <p>PCのギャラリーに繋がりません。</p>
  <p>start_gallery.bat を起動してから再試行してください。</p>
  <button id="retry" type="button">再試行</button>
</div>
<div id="grid"></div>
<button id="more" type="button">さらに読み込む</button>
<div id="viewer">
  <div id="chrome">
    <button id="gear" type="button" aria-label="settings">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"></path>
      </svg>
    </button>
    <button id="close" type="button" aria-label="close">&times;</button>
  </div>
  <img id="full" alt="">
</div>
<div id="settings">
  <div class="sheet">
    <h2>設定</h2>
    <p class="hint" id="nav-hint"></p>
    <label class="row">
      <span>左右を反転<small>タップとスワイプの向きを入れ替える</small></span>
      <input id="invert" type="checkbox">
    </label>
    <div class="row">
      <span>事前ロード<small>今見ている画像の前後を先読み。0でオフ</small></span>
      <div class="stepper">
        <button id="pre-minus" type="button" aria-label="減らす">&minus;</button>
        <b id="pre-val">5</b>
        <button id="pre-plus" type="button" aria-label="増やす">+</button>
      </div>
    </div>
    <div class="row stack">
      <span>画質<small>表示画像。標準が今までどおり。この端末に保存されます</small></span>
      <div class="seg" id="qseg">
        <button type="button" data-q="low">低</button>
        <button type="button" data-q="std" class="on">標準</button>
        <button type="button" data-q="high">原寸</button>
      </div>
    </div>
    <button id="settings-done" type="button">閉じる</button>
  </div>
</div>
<script>
const PRE_MIN=0, PRE_MAX=15, PRE_DEFAULT=5;
const MAX_INFLIGHT=2;
const items=[];
let offset=0, done=false, loading=false, idx=-1, viewToken=0, inflight=0, pre=PRE_DEFAULT;
let dir="", parent=null, q=[];
const have=new Set();
const grid=document.getElementById("grid");
const more=document.getElementById("more");
const viewer=document.getElementById("viewer");
const full=document.getElementById("full");
const count=document.getElementById("count");
const title=document.getElementById("title");
const back=document.getElementById("back");
const favBtn=document.getElementById("fav");
const reloadBtn=document.getElementById("reload");
const offlineEl=document.getElementById("offline");
const pullEl=document.getElementById("pull");
const Q_OPTS=["low","std","high"];
let quality="std";
try{
  const s=localStorage.getItem("gallery-quality")||"";
  if(Q_OPTS.indexOf(s)>=0) quality=s;
}catch(e){}
const KEY_FAV="gallery-favs";
const favs=new Set();
try{ JSON.parse(localStorage.getItem(KEY_FAV)||"[]").forEach(id=>favs.add(id)); }catch(e){}
let netAbort=new AbortController();

function saveFavs(){ try{ localStorage.setItem(KEY_FAV, JSON.stringify([...favs])); }catch(e){} }
function canFav(id){ return !!id && id!=="__all__"; }
function sortFolders(folders){
  return folders.slice().sort((a,b)=>{
    if(a.id==="__all__") return -1;
    if(b.id==="__all__") return 1;
    const fa=favs.has(a.id)?1:0, fb=favs.has(b.id)?1:0;
    if(fa!==fb) return fb-fa;
    return (b.m||0)-(a.m||0);
  });
}
function syncStars(){
  grid.querySelectorAll("button.album").forEach(b=>{
    const st=b.querySelector(".star");
    if(st) st.classList.toggle("on", favs.has(b.dataset.id));
  });
  const ok=canFav(dir);
  favBtn.classList.toggle("show", ok);
  favBtn.classList.toggle("on", ok && favs.has(dir));
}
function toggleFav(id, ev){
  if(ev){ ev.preventDefault(); ev.stopPropagation(); }
  if(!canFav(id)) return;
  if(favs.has(id)) favs.delete(id); else favs.add(id);
  saveFavs();
  const albums=[...grid.querySelectorAll("button.album")];
  if(albums.length>1){
    const ordered=sortFolders(albums.map(b=>({
      id:b.dataset.id, m:Number(b.dataset.m||0), el:b
    })));
    const firstImg=[...grid.children].find(el=>el.className!=="album");
    ordered.forEach(x=>grid.insertBefore(x.el, firstImg||null));
  }
  syncStars();
}
function freezeNet(){
  try{ window.stop(); }catch(e){}
  try{ netAbort.abort(); }catch(e){}
  netAbort=new AbortController();
  q=[]; inflight=0; loading=false;
}

function tsrc(it){return "/t?p="+encodeURIComponent(it.id)+"&m="+it.m+"&q="+quality;}
function vsrc(it){return "/v?p="+encodeURIComponent(it.id)+"&m="+it.m+"&q="+quality;}
function osrc(it){return "/i?p="+encodeURIComponent(it.id);}
function dirFromHash(){
  let h=location.hash||"";
  if(h.charAt(0)==="#") h=h.slice(1);
  if(!h) return "";
  try{ return decodeURIComponent(h); }catch(e){ return h; }
}
function renderFolders(folders){
  for(const f of sortFolders(folders)){
    const b=document.createElement("button");
    b.type="button";
    b.className="album";
    b.dataset.id=f.id;
    b.dataset.m=String(f.m||0);
    if(f.cover){
      b.dataset.cover=f.cover;
      const im=document.createElement("img");
      im.loading="lazy"; im.decoding="async"; im.alt="";
      im.src=tsrc({id:f.cover, m:f.m});
      b.appendChild(im);
    }
    if(canFav(f.id)){
      const st=document.createElement("span");
      st.className="star"+(favs.has(f.id)?" on":"");
      st.textContent="★";
      st.addEventListener("click",e=>toggleFav(f.id,e));
      b.appendChild(st);
    }
    const lab=document.createElement("span");
    lab.className="label";
    lab.textContent=f.n? f.name+" · "+f.n : f.name;
    b.appendChild(lab);
    b.addEventListener("click",e=>{
      if(e.target.closest(".star")) return;
      enter(f.id,true);
    });
    grid.appendChild(b);
  }
}
function render(batch){
  for(const it of batch){
    const i=items.length; items.push(it);
    const b=document.createElement("button");
    b.type="button";
    b.dataset.id=it.id;
    b.dataset.m=String(it.m);
    const im=document.createElement("img");
    im.loading="lazy"; im.decoding="async"; im.alt=""; im.src=tsrc(it);
    b.appendChild(im);
    b.addEventListener("click",()=>openAt(i));
    grid.appendChild(b);
  }
}
function hideOffline(){ offlineEl.classList.remove("on"); }
function showOffline(){
  if(viewer.classList.contains("on")) return;
  offlineEl.classList.add("on");
}
async function load(fresh){
  if(done||loading) return;
  loading=true;
  const local=new AbortController();
  const timer=setTimeout(()=>local.abort(),8000);
  const onParent=()=>local.abort();
  netAbort.signal.addEventListener("abort", onParent);
  try{
    const url="/api/browse?dir="+encodeURIComponent(dir)+"&offset="+offset+(fresh&&offset===0?"&fresh=1":"");
    const r=await fetch(url,{signal:local.signal, cache:"no-store"});
    if(!r.ok){ showOffline(); return; }
    const data=await r.json();
    hideOffline();
    if(offset===0){
      title.textContent=data.name||"Gallery";
      parent=data.parent;
      back.classList.toggle("show", parent!==null);
      renderFolders(data.folders||[]);
      const fc=data.folderCount||0, n=data.imageCount||0;
      count.textContent=(fc?fc+" フォルダ":"")+(fc&&n?" · ":"")+(n?n+" 枚":"");
      syncStars();
    }
    render(data.items||[]);
    offset=data.next;
    done=!data.more;
    more.style.display=done?"none":"block";
  } catch(e){
    if(netAbort.signal.aborted) return;
    showOffline();
  } finally {
    clearTimeout(timer);
    netAbort.signal.removeEventListener("abort", onParent);
    loading=false;
    reloadBtn.classList.remove("spin");
  }
}
function refresh(){
  if(viewer.classList.contains("on")) return;
  try{ netAbort.abort(); }catch(e){}
  netAbort=new AbortController();
  loading=false; done=false; offset=0;
  items.length=0; grid.innerHTML="";
  more.style.display="none";
  hideOffline();
  reloadBtn.classList.add("spin");
  load(true);
}
function enter(next, setHash){
  if(viewer.classList.contains("on")) close();
  dir=next||"";
  offset=0; done=false;
  items.length=0;
  grid.innerHTML="";
  more.style.display="none";
  if(setHash){
    const h="#"+encodeURIComponent(dir);
    if((location.hash||"#")!==h && location.hash!==(dir?"#"+dir:"#")){
      location.hash=dir?encodeURIComponent(dir):"";
    }
  }
  load();
}
function pump(){
  while(inflight<MAX_INFLIGHT && q.length){
    const u=q.shift();
    if(have.has(u)) continue;
    have.add(u);
    inflight++;
    fetch(u,{credentials:"same-origin",signal:netAbort.signal,cache:"no-store"}).catch(()=>have.delete(u)).finally(()=>{
      inflight--; pump();
    });
  }
}
function warmAround(i){
  const want=[];
  for(let k=i-pre;k<=i+pre;k++){
    if(k<0||k>=items.length||k===i) continue;
    const u=vsrc(items[k]);
    if(!have.has(u)) want.push(u);
  }
  q=want;
  pump();
  if(!done && i>=items.length-Math.max(8, pre+3)) load();
}
function openAt(i){
  if(i<0||i>=items.length) return;
  idx=i;
  const token=++viewToken;
  const it=items[i];
  viewer.classList.add("on");
  document.documentElement.classList.add("viewing");
  document.body.classList.add("viewing");
  if(!full.getAttribute("src")) full.src=tsrc(it);
  const view=vsrc(it);
  const hi=new Image();
  hi.decoding="async";
  hi.fetchPriority="high";
  hi.onload=()=>{ if(token===viewToken) full.src=view; };
  hi.onerror=()=>{ if(token===viewToken) full.src=osrc(it); };
  hi.src=view;
  warmAround(i);
  resetZoom();
}
function close(){
  closeSettings();
  viewer.classList.remove("on");
  document.documentElement.classList.remove("viewing");
  document.body.classList.remove("viewing");
  full.removeAttribute("src");
  idx=-1; q=[];
  resetZoom();
}
const KEY="gallery-invert-nav";
const KEY_PRE="gallery-preload";
let invert=false;
try{ invert=localStorage.getItem(KEY)==="1"; }catch(e){}
try{
  const n=parseInt(localStorage.getItem(KEY_PRE)||"",10);
  if(Number.isFinite(n)) pre=Math.min(PRE_MAX, Math.max(PRE_MIN, n));
}catch(e){}
const settings=document.getElementById("settings");
const invertEl=document.getElementById("invert");
const navHint=document.getElementById("nav-hint");
const preVal=document.getElementById("pre-val");
invertEl.checked=invert;
preVal.textContent=String(pre);
function hintText(){
  return invert
    ? "いま: 左タップ／左→右スワイプで次へ"
    : "いま: 右タップ／右→左スワイプで次へ";
}
function syncHint(){ navHint.textContent=hintText(); }
syncHint();
function setInvert(v){
  invert=!!v;
  invertEl.checked=invert;
  syncHint();
  try{ localStorage.setItem(KEY, invert?"1":"0"); }catch(e){}
}
function setPre(n){
  pre=Math.min(PRE_MAX, Math.max(PRE_MIN, n|0));
  preVal.textContent=String(pre);
  try{ localStorage.setItem(KEY_PRE, String(pre)); }catch(e){}
  if(idx>=0) warmAround(idx);
}
function syncQuality(){
  document.querySelectorAll("#qseg button").forEach(b=>{
    b.classList.toggle("on", b.getAttribute("data-q")===quality);
  });
}
function setQuality(v){
  if(Q_OPTS.indexOf(v)<0) return;
  quality=v;
  syncQuality();
  try{ localStorage.setItem("gallery-quality", quality); }catch(e){}
  have.clear();
  grid.querySelectorAll("button.album").forEach(b=>{
    const im=b.querySelector("img");
    if(im && b.dataset.cover) im.src=tsrc({id:b.dataset.cover, m:b.dataset.m});
  });
  grid.querySelectorAll("button:not(.album)").forEach(b=>{
    const im=b.querySelector("img");
    if(im && b.dataset.id) im.src=tsrc({id:b.dataset.id, m:b.dataset.m});
  });
  if(idx>=0){
    const it=items[idx];
    full.src=vsrc(it);
    warmAround(idx);
  }
}
syncQuality();
function openSettings(){ settings.classList.add("on"); }
function closeSettings(){ settings.classList.remove("on"); }
function goNext(){ openAt(idx+1); }
function goPrev(){ if(idx>0) openAt(idx-1); }
function wantNextByTap(clientX){
  const r=viewer.getBoundingClientRect();
  const right=clientX >= r.left + r.width/2;
  return invert ? !right : right;
}
function wantNextBySwipe(dx){
  const rtl=dx<0;
  return invert ? !rtl : rtl;
}
function onViewerTap(clientX){
  if(wantNextByTap(clientX)) goNext(); else goPrev();
}
const MAX_Z=4;
let scale=1, tx=0, ty=0;
let startX=0, startY=0, startTx=0, startTy=0, pinch0=1, pinchS=1;
let moved=false, pinching=false, gestured=false, fromTouch=false, dragging=false;
function applyZoom(){
  full.style.transform="translate("+tx+"px,"+ty+"px) scale("+scale+")";
}
function resetZoom(){
  scale=1; tx=0; ty=0;
  full.style.transition="transform .18s ease-out";
  applyZoom();
}
function clampPan(){
  if(scale<=1.001){ tx=0; ty=0; return; }
  const maxX=(full.offsetWidth*scale)/2, maxY=(full.offsetHeight*scale)/2;
  tx=Math.min(maxX, Math.max(-maxX, tx));
  ty=Math.min(maxY, Math.max(-maxY, ty));
}
function zoomAt(cx, cy, newScale){
  const wr=viewer.getBoundingClientRect();
  const vx=cx-wr.left-wr.width/2, vy=cy-wr.top-wr.height/2;
  const prev=scale||1;
  const next=Math.min(MAX_Z, Math.max(1, newScale));
  tx=vx-(vx-tx)*(next/prev);
  ty=vy-(vy-ty)*(next/prev);
  scale=next;
  clampPan();
  applyZoom();
}
function distTouch(a,b){
  return Math.hypot(a.clientX-b.clientX, a.clientY-b.clientY);
}
function chromeClick(e){ e.stopPropagation(); }
document.getElementById("chrome").addEventListener("click",chromeClick);
document.getElementById("close").addEventListener("click",e=>{
  e.stopPropagation();
  close();
});
document.getElementById("gear").addEventListener("click",e=>{
  e.stopPropagation();
  openSettings();
});
invertEl.addEventListener("change",()=>setInvert(invertEl.checked));
document.getElementById("pre-minus").addEventListener("click",()=>setPre(pre-1));
document.getElementById("pre-plus").addEventListener("click",()=>setPre(pre+1));
document.getElementById("qseg").addEventListener("click",e=>{
  const b=e.target.closest("button[data-q]");
  if(b) setQuality(b.getAttribute("data-q"));
});
document.getElementById("retry").addEventListener("click", refresh);
reloadBtn.addEventListener("click", refresh);
document.getElementById("settings-done").addEventListener("click",closeSettings);
settings.addEventListener("click",e=>{ if(e.target===settings) closeSettings(); });
more.onclick=load;
viewer.addEventListener("touchstart",e=>{
  fromTouch=true; gestured=false; moved=false;
  full.style.transition="none";
  if(settings.classList.contains("on")) return;
  if(e.target.closest && e.target.closest("#chrome")) return;
  if(e.touches.length>=2){
    pinching=true;
    pinch0=distTouch(e.touches[0], e.touches[1])||1;
    pinchS=scale;
    return;
  }
  pinching=false;
  const t=e.changedTouches[0];
  startX=t.clientX; startY=t.clientY;
  startTx=tx; startTy=ty;
},{passive:true});
viewer.addEventListener("touchmove",e=>{
  if(!viewer.classList.contains("on")||settings.classList.contains("on")) return;
  if(e.target.closest && e.target.closest("#chrome")) return;
  if(e.touches.length>=2){
    e.preventDefault();
    pinching=true; gestured=true;
    const d=distTouch(e.touches[0], e.touches[1])||1;
    const midX=(e.touches[0].clientX+e.touches[1].clientX)/2;
    const midY=(e.touches[0].clientY+e.touches[1].clientY)/2;
    zoomAt(midX, midY, pinchS*d/pinch0);
    return;
  }
  if(pinching) return;
  const t=e.touches[0];
  const dx=t.clientX-startX, dy=t.clientY-startY;
  if(Math.hypot(dx,dy)>8) moved=true;
  if(scale>1.02){
    e.preventDefault();
    gestured=true;
    tx=startTx+dx; ty=startTy+dy;
    clampPan(); applyZoom();
  }
},{passive:false});
viewer.addEventListener("touchend",e=>{
  if(settings.classList.contains("on")) return;
  if(e.target.closest && e.target.closest("#chrome")) return;
  if(e.touches.length>0){
    if(pinching){
      startX=e.touches[0].clientX; startY=e.touches[0].clientY;
      startTx=tx; startTy=ty;
    }
    return;
  }
  if(pinching){
    pinching=false;
    if(scale<1.08) resetZoom();
    else { clampPan(); applyZoom(); }
    gestured=true;
    return;
  }
  const t=e.changedTouches[0];
  const dx=t.clientX-startX, dy=t.clientY-startY;
  if(scale<=1.02 && Math.abs(dx)>=50 && Math.abs(dx)>Math.abs(dy)){
    gestured=true;
    if(wantNextBySwipe(dx)) goNext();
    else if(idx===0) close();
    else goPrev();
    return;
  }
  if(!moved && !gestured && scale<=1.02) onViewerTap(t.clientX);
},{passive:true});
viewer.addEventListener("click",e=>{
  if(settings.classList.contains("on")) return;
  if(e.target.closest("#chrome")) return;
  if(fromTouch){ fromTouch=false; return; }
  if(gestured){ gestured=false; return; }
  if(scale>1.02) return;
  onViewerTap(e.clientX);
});
viewer.addEventListener("wheel",e=>{
  if(!viewer.classList.contains("on")||settings.classList.contains("on")) return;
  e.preventDefault();
  full.style.transition="none";
  zoomAt(e.clientX, e.clientY, scale*(e.deltaY<0?1.12:1/1.12));
},{passive:false});
viewer.addEventListener("mousedown",e=>{
  if(e.button!==0||scale<=1.02) return;
  if(e.target.closest("#chrome")) return;
  dragging=true; gestured=true;
  startX=e.clientX; startY=e.clientY; startTx=tx; startTy=ty;
});
window.addEventListener("mousemove",e=>{
  if(!dragging) return;
  tx=startTx+(e.clientX-startX); ty=startTy+(e.clientY-startY);
  clampPan(); applyZoom();
});
window.addEventListener("mouseup",()=>{ dragging=false; });
document.addEventListener("keydown",e=>{
  if(settings.classList.contains("on")){
    if(e.key==="Escape") closeSettings();
    return;
  }
  if(!viewer.classList.contains("on")) return;
  if(e.key==="Escape") close();
  if(e.key==="ArrowLeft") onViewerTap(0);
  if(e.key==="ArrowRight") onViewerTap(99999);
});
if("IntersectionObserver" in window){
  new IntersectionObserver(es=>{
    if(es.some(e=>e.isIntersecting)) load();
  },{rootMargin:"1200px 0px"}).observe(more);
}
back.addEventListener("click",()=>{ if(parent!==null) enter(parent, true); });
favBtn.addEventListener("click",e=>toggleFav(dir,e));
let hiddenAt=0, pullY=0, pulling=false, pullStart=0;
document.addEventListener("visibilitychange",()=>{
  if(document.hidden){
    freezeNet();
    hiddenAt=Date.now();
    return;
  }
  if(Date.now()-hiddenAt>2500 && !viewer.classList.contains("on")) refresh();
  else if(!grid.children.length) load();
  else {
    grid.querySelectorAll("img").forEach(im=>{
      if(!im.complete && im.src){ const s=im.src; im.removeAttribute("src"); im.src=s; }
    });
  }
});
window.addEventListener("touchstart",e=>{
  if(viewer.classList.contains("on")||settings.classList.contains("on")) return;
  if(window.scrollY>4) return;
  pulling=true; pullStart=e.touches[0].clientY; pullY=0;
},{passive:true});
window.addEventListener("touchmove",e=>{
  if(!pulling) return;
  pullY=Math.max(0, e.touches[0].clientY-pullStart);
  if(window.scrollY<=0 && pullY>12){
    pullEl.style.height=Math.min(72, 8+pullY*0.35)+"px";
    pullEl.textContent=pullY>72?"離して更新":"下に引いて更新";
  }
},{passive:true});
window.addEventListener("touchend",()=>{
  if(!pulling) return;
  const go=window.scrollY<=0 && pullY>72;
  pulling=false; pullY=0; pullEl.style.height="0";
  if(go) refresh();
},{passive:true});
window.addEventListener("pagehide", freezeNet);
window.addEventListener("hashchange",()=>{
  const d=dirFromHash();
  if(d===dir) return;
  enter(d, false);
});
enter(dirFromHash(), false);
</script>
</body>
</html>
"""

SETUP_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gallery QR</title>
<style>
html,body{margin:0;background:#111;color:#eee;font:18px/1.45 -apple-system,sans-serif;
  min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:24px;text-align:center;}
.box{background:#fff;padding:28px;border-radius:16px;line-height:0;}
.box img{width:min(80vw,520px);height:auto;display:block;image-rendering:pixelated;}
p{margin:18px 0 0;max-width:28em;opacity:.9;}
code{font-size:13px;word-break:break-all;opacity:.55;}
</style>
</head>
<body>
<div class="box"><img src="/setup/qr.png" alt=""></div>
<p>iPhone のカメラでこの QR を読んでください。</p>
<p><code>__URL__</code></p>
</body>
</html>
"""

def qr_png(text: str) -> bytes:
    buf = io.BytesIO()
    segno.make(text, error='h').save(buf, kind='png', scale=14, border=6)
    return buf.getvalue()


def parse_serve_url(status: str) -> str:
    found = TS_URL_RE.search(status or "")
    return found.group(0).rstrip("/") if found else ""


def is_loopback(ip: str) -> bool:
    return ip in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def phone_url() -> str:
    base = (SERVE_URL or "").rstrip("/")
    if not base:
        return ""
    return "%s/?b=%s" % (base, os.getpid())


def setup_page() -> bytes:
    url = phone_url()
    if not url:
        body = (
            "<!DOCTYPE html><meta charset=utf-8><body style='font-family:sans-serif;padding:2rem'>"
            "<p>Tailscale の URL がまだありません。ログインして bat をやり直してください。</p>"
        )
        return body.encode("utf-8")
    page = SETUP_HTML.replace("__URL__", html.escape(url))
    return page.encode("utf-8")


def find_tailscale() -> Path | None:
    for raw in TAILSCALE_CANDIDATES:
        if raw and raw.is_file():
            return raw
    from shutil import which

    found = which("tailscale")
    return Path(found) if found else None


def run_ts(ts: Path, args: list[str], timeout: float = 8) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        [str(ts), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(
            proc.args, -1, stdout or "", stderr or "timed out"
        )
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout or "", stderr or "")


def funnel_is_on(ts: Path) -> bool:
    r = run_ts(ts, ["funnel", "status"], timeout=8)
    text = f"{r.stdout}\n{r.stderr}".lower()
    return "funnel on" in text


def serve_status_text(ts: Path) -> str:
    r = run_ts(ts, ["serve", "status"], timeout=8)
    return (r.stdout or r.stderr or "").strip()


def enable_serve(ts: Path) -> str:
    r = run_ts(ts, ["serve", "--bg", "--yes", str(PORT)], timeout=8)
    combined = f"{r.stdout}\n{r.stderr}".strip()
    if combined.lower().endswith("timed out"):
        combined = combined[: -len("timed out")].strip()
    if "not enabled" in combined.lower():
        return combined
    status = serve_status_text(ts)
    return status or combined


def to_rgb(im: "Image.Image") -> "Image.Image":
    if im.mode == "RGB":
        return im
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (0, 0, 0))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def parse_quality(raw: str) -> str:
    key = (raw or "std").lower()
    return key if key in QUALITY else "std"


def derived_path(src: Path, kind: str, qkey: str) -> Path:
    rel = src.resolve().relative_to(OUTPUT_ROOT).as_posix()
    spec = QUALITY[qkey]
    digest = hashlib.sha1(
        f"{kind}:{qkey}:{spec['t']}:{spec['v']}:{rel}".encode("utf-8")
    ).hexdigest()
    return CACHE_ROOT / kind / qkey / digest[:2] / f"{digest}.webp"


def lock_for(key: str) -> threading.Lock:
    with _DERIVE_LOCKS_GUARD:
        lock = _DERIVE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _DERIVE_LOCKS[key] = lock
        return lock


def build_derived(src: Path, dest: Path, kind: str, qkey: str) -> None:
    spec = QUALITY[qkey]
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        with Image.open(src) as im:
            im = to_rgb(im)
            if kind == "t":
                width, height = im.size
                side = min(width, height)
                if side <= 0:
                    raise ValueError("empty image")
                left = (width - side) // 2
                top = (height - side) // 2
                im = im.crop((left, top, left + side, top + side))
                im = im.resize((spec["t"], spec["t"]), Image.Resampling.LANCZOS)
                quality = spec["tq"]
            else:
                im.thumbnail((spec["v"], spec["v"]), Image.Resampling.LANCZOS)
                quality = spec["vq"]
            im.save(tmp, format="WEBP", quality=quality, method=4)
        os.replace(tmp, dest)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def ensure_derived(src: Path, kind: str, qkey: str) -> Path | None:
    if kind == "v" and QUALITY[qkey]["v"] == 0:
        return None
    dest = derived_path(src, kind, qkey)
    try:
        if dest.is_file() and dest.stat().st_mtime_ns >= src.stat().st_mtime_ns:
            return dest
    except OSError:
        return None
    if Image is None:
        return None
    key = str(dest)
    with lock_for(key):
        try:
            if dest.is_file() and dest.stat().st_mtime_ns >= src.stat().st_mtime_ns:
                return dest
        except OSError:
            return None
        with DERIVE_SEM:
            try:
                build_derived(src, dest, kind, qkey)
            except Exception:
                return None
        return dest


SKIP_DIRS = {".git", "__pycache__"}
ALL_ALBUM = "__all__"
INDEX_TTL = 2.0
_index_guard = threading.Lock()
_index: "Album | None" = None
_index_at = 0.0


class Album:
    __slots__ = ("name", "rel", "folders", "images", "count", "cover")

    def __init__(self, name: str, rel: str) -> None:
        self.name = name
        self.rel = rel
        self.folders: dict[str, Album] = {}
        self.images: list[tuple[float, str, str]] = []
        self.count = 0
        self.cover: tuple[float, str, str] | None = None

    def child(self, name: str) -> Album:
        node = self.folders.get(name)
        if node is None:
            rel = f"{self.rel}/{name}" if self.rel else name
            node = Album(name, rel)
            self.folders[name] = node
        return node


def build_index() -> Album:
    root = Album("Gallery", "")
    if not OUTPUT_ROOT.is_dir():
        return root
    for dirpath, dirnames, filenames in os.walk(OUTPUT_ROOT, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        base = Path(dirpath)
        try:
            resolved = base.resolve()
            resolved.relative_to(OUTPUT_ROOT)
        except ValueError:
            dirnames[:] = []
            continue
        rel_dir = resolved.relative_to(OUTPUT_ROOT).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        node = root
        chain = [root]
        if rel_dir:
            for part in rel_dir.split("/"):
                node = node.child(part)
                chain.append(node)
        for name in filenames:
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED_EXT:
                continue
            path = base / name
            try:
                st = path.stat()
                rel = path.resolve().relative_to(OUTPUT_ROOT).as_posix()
            except (OSError, ValueError):
                continue
            node.images.append((st.st_mtime, rel, name))
            rec = (st.st_mtime, rel, name)
            for ancestor in chain:
                ancestor.count += 1
                if ancestor.cover is None or rec[0] >= ancestor.cover[0]:
                    ancestor.cover = rec

    def sort_tree(album: Album) -> None:
        album.images.sort(key=lambda x: x[0], reverse=True)
        for child in album.folders.values():
            sort_tree(child)

    sort_tree(root)
    return root


def get_root(force: bool = False) -> Album:
    global _index, _index_at
    now = time.monotonic()
    with _index_guard:
        if not force and _index is not None and now - _index_at < INDEX_TTL:
            return _index
        _index = build_index()
        _index_at = now
        return _index


def find_album(root: Album, rel: str) -> Album | None:
    if not rel:
        return root
    node = root
    for part in rel.split("/"):
        node = node.folders.get(part)
        if node is None:
            return None
    return node


def flatten_images(album: Album) -> list[tuple[float, str, str]]:
    rows: list[tuple[float, str, str]] = []

    def walk(node: Album) -> None:
        rows.extend(node.images)
        for child in node.folders.values():
            walk(child)

    walk(album)
    rows.sort(key=lambda x: x[0], reverse=True)
    return rows


def folder_payload(album: Album) -> dict:
    cover = album.cover
    return {
        "id": album.rel,
        "name": album.name,
        "cover": cover[1] if cover else None,
        "m": int(cover[0]) if cover else 0,
        "n": album.count,
    }


def jail_dir(rel: str) -> str | None:
    if rel == ALL_ALBUM:
        return ALL_ALBUM
    if not rel:
        return ""
    if "\x00" in rel:
        return None
    decoded = rel.replace("\\", "/").lstrip("/")
    if decoded.startswith("/") or re.match(r"^[a-zA-Z]:", decoded):
        return None
    if ".." in Path(decoded).parts:
        return None
    try:
        candidate = (OUTPUT_ROOT / decoded).resolve()
        candidate.relative_to(OUTPUT_ROOT)
    except (OSError, ValueError):
        return None
    if not candidate.is_dir() or candidate.is_symlink():
        return None
    rel_out = candidate.relative_to(OUTPUT_ROOT).as_posix()
    return "" if rel_out == "." else rel_out


def browse_album(rel: str, offset: int, fresh: bool = False) -> dict:
    root = get_root(force=bool(fresh and offset == 0))
    if rel == ALL_ALBUM:
        rows = flatten_images(root)
        chunk = rows[offset : offset + MAX_PAGE]
        folders = [] if offset else []
        return {
            "dir": ALL_ALBUM,
            "name": "すべて",
            "parent": "",
            "folders": folders,
            "folderCount": 0,
            "imageCount": len(rows),
            "items": [{"id": r, "name": n, "m": int(m)} for m, r, n in chunk],
            "next": offset + len(chunk),
            "more": offset + len(chunk) < len(rows),
        }
    album = find_album(root, rel)
    if album is None:
        return {
            "dir": rel,
            "name": "Gallery",
            "parent": None,
            "folders": [],
            "folderCount": 0,
            "imageCount": 0,
            "items": [],
            "next": 0,
            "more": False,
        }
    folders = []
    if offset == 0:
        folders = [
            folder_payload(child)
            for child in sorted(
                album.folders.values(),
                key=lambda a: -(a.cover[0] if a.cover else 0),
            )
        ]
        if rel == "":
            include_all = root.count > 0
            if include_all:
                cover = root.cover
                folders.insert(
                    0,
                    {
                        "id": ALL_ALBUM,
                        "name": "すべて",
                        "cover": cover[1] if cover else None,
                        "m": int(cover[0]) if cover else 0,
                        "n": root.count,
                    },
                )
    rows = album.images
    chunk = rows[offset : offset + MAX_PAGE]
    parent: str | None
    if rel == "":
        parent = None
    else:
        parent_path = str(Path(rel).parent.as_posix())
        parent = "" if parent_path == "." else parent_path
    folder_count = len(album.folders) + (1 if rel == "" and root.count > 0 else 0)
    return {
        "dir": album.rel,
        "name": album.name,
        "parent": parent,
        "folders": folders,
        "folderCount": folder_count,
        "imageCount": len(rows),
        "items": [{"id": r, "name": n, "m": int(m)} for m, r, n in chunk],
        "next": offset + len(chunk),
        "more": offset + len(chunk) < len(rows),
    }


def jail(rel: str) -> Path | None:
    if not rel or "\x00" in rel:
        return None
    decoded = unquote(rel)
    if decoded != rel and ("\x00" in decoded):
        return None
    decoded = decoded.replace("\\", "/").lstrip("/")
    if decoded.startswith("/") or re.match(r"^[a-zA-Z]:", decoded):
        return None
    if ".." in Path(decoded).parts:
        return None
    try:
        candidate = (OUTPUT_ROOT / decoded).resolve()
        candidate.relative_to(OUTPUT_ROOT)
    except (OSError, ValueError):
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    if candidate.suffix.lower() not in ALLOWED_EXT:
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "Gallery"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s %s\n" % (self.command, getattr(self, "path", "")))

    def _headers_common(
        self, content_type: str, cache: str, length: int, etag: str | None = None
    ) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
            self.send_header("Vary", "Accept")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'",
        )
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("Connection", "close")

    def _send(
        self,
        code: int,
        body: bytes,
        content_type: str,
        cache: str,
        etag: str | None = None,
    ) -> None:
        self.close_connection = True
        self.send_response(code)
        self._headers_common(content_type, cache, len(body), etag)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _etag(self, path: Path, kind: str) -> str:
        st = path.stat()
        return f'"{kind}-{st.st_mtime_ns}-{st.st_size}"'

    def _send_304(self, cache: str, etag: str) -> None:
        self.close_connection = True
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send_file(self, path: Path, content_type: str, cache: str, kind: str) -> None:
        etag = self._etag(path, kind)
        if self.headers.get("If-None-Match") == etag:
            self._send_304(cache, etag)
            return
        try:
            data = path.read_bytes()
        except OSError:
            self._deny(404, "Not found")
            return
        self._send(200, data, content_type, cache, etag)

    def _send_image(self, rel: str, kind: str, qkey: str) -> None:
        path = jail(rel)
        if path is None:
            self._deny(404, "Not found")
            return
        if kind in {"t", "v"}:
            derived = ensure_derived(path, kind, qkey)
            if derived is not None:
                self._send_file(derived, "image/webp", CACHE_IMG, kind)
                return
        ctype = mimetypes.types_map.get(path.suffix.lower(), "application/octet-stream")
        if ctype not in {"image/png", "image/jpeg", "image/webp"}:
            self._deny(404, "Not found")
            return
        self._send_file(path, ctype, CACHE_IMG if kind != "i" else "private, max-age=120", kind)

    def _deny(self, code: int, msg: str) -> None:
        self._send(code, msg.encode("utf-8"), "text/plain; charset=utf-8", "no-store")

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._deny(405, "Method not allowed")

    def do_PUT(self) -> None:
        self._deny(405, "Method not allowed")

    def do_DELETE(self) -> None:
        self._deny(405, "Method not allowed")

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/setup":
            if not is_loopback(self.client_address[0]):
                self._deny(404, "Not found")
                return
            self._send(200, setup_page(), "text/html; charset=utf-8", "no-store")
            return
        if route == "/setup/qr.png":
            if not is_loopback(self.client_address[0]) or not SERVE_URL:
                self._deny(404, "Not found")
                return
            self._send(200, qr_png(phone_url()), "image/png", "no-store")
            return
        if route == "/":
            data = PAGE_HTML.encode("utf-8")
            self._send(200, data, "text/html; charset=utf-8", "no-store")
            return
        if route in {"/api/list", "/api/browse"}:
            qs = parse_qs(parsed.query)
            try:
                offset = max(0, int(qs.get("offset", ["0"])[0]))
            except ValueError:
                offset = 0
            raw_dir = qs.get("dir", [""])[0]
            jailed = jail_dir(raw_dir)
            if jailed is None:
                self._deny(404, "Not found")
                return
            fresh = qs.get("fresh", ["0"])[0] in {"1", "true", "yes"}
            payload = browse_album(jailed, offset, fresh=fresh)
            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self._send(200, data, "application/json; charset=utf-8", "no-store")
            return
        if route in {"/t", "/v", "/i"}:
            qs = parse_qs(parsed.query)
            rel = qs.get("p", [""])[0]
            kind = route.lstrip("/")
            qkey = parse_quality(qs.get("q", ["std"])[0])
            self._send_image(rel, kind, qkey)
            return
        self._deny(404, "Not found")


_CREATE_NO_WINDOW = 0x08000000
LOCK_PATH = Path(__file__).resolve().parent / "gallery.lock"
SCRIPT_PATH = Path(__file__).resolve()
BAT_PATH = SCRIPT_PATH.parent / "start_gallery.bat"


def _run_hidden(args: list[str], timeout: float = 12) -> subprocess.CompletedProcess[str]:
    kw: dict = {
        "args": args,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if sys.platform == "win32":
        kw["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(**kw)


def _protected_pids() -> set[int]:
    return {0, 4, os.getpid(), os.getppid()}


def _kill_pid(pid: int) -> bool:
    if pid in _protected_pids():
        return False
    try:
        r = _run_hidden(["taskkill", "/F", "/PID", str(pid)], timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _gallery_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    script = str(SCRIPT_PATH).replace("'", "''")
    bat = str(BAT_PATH).replace("'", "''")
    ps = (
        "$ids = New-Object System.Collections.Generic.List[int]; "
        "Get-CimInstance Win32_Process | ForEach-Object { "
        "  $c = $_.CommandLine; if (-not $c) { return }; "
        f"  if (($c -like '*{script}*') -or ($c -like '*{bat}*')) {{ "
        "    $ids.Add([int]$_.ProcessId) "
        "  } "
        "}; $ids -join ','"
    )
    try:
        r = _run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    skip = _protected_pids()
    pids: list[int] = []
    for part in (r.stdout or "").strip().split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pid = int(part)
        except ValueError:
            continue
        if pid not in skip:
            pids.append(pid)
    return pids


def _pids_on_port(port: int) -> list[int]:
    try:
        r = _run_hidden(["netstat", "-ano", "-p", "tcp"], timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return []
    skip = _protected_pids()
    found: list[int] = []
    token = f":{port}"
    for line in (r.stdout or "").splitlines():
        if "LISTENING" not in line.upper() or token not in line:
            continue
        if not re.search(rf"(?:127\.0\.0\.1|0\.0\.0\.0|\[::1\]):{port}\s", line):
            continue
        parts = line.split()
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid not in skip:
            found.append(pid)
    return found


def _acquire_takeover_lock():
    fh = open(LOCK_PATH, "a+b")
    if fh.tell() == 0:
        fh.write(b"\0")
        fh.flush()
    if sys.platform == "win32":
        import msvcrt

        deadline = time.time() + 8
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() > deadline:
                    break
                time.sleep(0.05)
    return fh


def _release_takeover_lock(fh) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass


def take_over_previous() -> None:
    killed: list[int] = []
    for pid in _gallery_pids():
        if _kill_pid(pid):
            killed.append(pid)
    for pid in _pids_on_port(PORT):
        if pid not in killed and _kill_pid(pid):
            killed.append(pid)
    if killed:
        print("Stopped previous gallery: %s" % ", ".join(str(p) for p in killed))
        time.sleep(0.35)


def bind_http() -> ThreadingHTTPServer:
    last_err: OSError | None = None
    ThreadingHTTPServer.allow_reuse_address = True
    for _ in range(20):
        try:
            return ThreadingHTTPServer((HOST, PORT), Handler)
        except OSError as exc:
            last_err = exc
            for pid in _pids_on_port(PORT):
                _kill_pid(pid)
            time.sleep(0.2)
    print("Could not bind 127.0.0.1:%s: %s" % (PORT, last_err))
    sys.exit(1)


def detect_output_candidates() -> list[Path]:
    found: list[Path] = []
    cur = HERE
    for _ in range(8):
        sibling = cur / "output"
        if sibling.is_dir():
            found.append(sibling)
        cur = cur.parent
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
    found.append(home / "Documents" / "ComfyUI" / "output")
    return found


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_config_root() -> Path | None:
    raw = load_config().get("output_root")
    if not raw:
        return None
    return Path(str(raw)).expanduser()


def save_config_root(root: Path) -> None:
    data = load_config()
    data["output_root"] = str(root)
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def cli_root() -> Path | None:
    argv = sys.argv[1:]
    i = 0
    found: Path | None = None
    while i < len(argv):
        arg = argv[i]
        if arg in {"-h", "--help"}:
            print("Usage: gallery.py [--root FOLDER]")
            print("  --root   Image folder. Saved to config.json.")
            print("  Or drag a folder onto start_gallery.bat")
            sys.exit(0)
        if arg in {"--root", "-r"} and i + 1 < len(argv):
            found = Path(argv[i + 1])
            i += 2
            continue
        if not arg.startswith("-") and found is None:
            found = Path(arg)
        i += 1
    env = os.environ.get("GALLERY_ROOT")
    if found is None and env:
        found = Path(env)
    return found


def resolve_output_root() -> Path:
    picked = cli_root()
    if picked is not None:
        root = picked.expanduser().resolve()
        if not root.is_dir():
            print("Not a folder: %s" % root)
            sys.exit(1)
        save_config_root(root)
        return root
    cfg = load_config_root()
    if cfg is not None:
        root = cfg.resolve()
        if not root.is_dir():
            print("config.json output_root is not a folder: %s" % root)
            print("Fix config.json or drag the folder onto start_gallery.bat")
            sys.exit(1)
        return root
    for cand in detect_output_candidates():
        try:
            if cand.is_dir():
                root = cand.resolve()
                save_config_root(root)
                print("Saved detected folder to config.json")
                return root
        except OSError:
            continue
    print("Set the image folder first.")
    print("  Drag the folder onto start_gallery.bat")
    print("  Or copy config.example.json to config.json and edit output_root")
    sys.exit(1)


def main() -> None:
    global SERVE_URL, OUTPUT_ROOT
    if HOST != "127.0.0.1":
        print("Refusing to start: bind host is not loopback.")
        sys.exit(1)
    OUTPUT_ROOT = resolve_output_root()
    print("Python: %s" % sys.executable)
    print("Images: %s" % OUTPUT_ROOT)
    ts = find_tailscale()
    if ts is not None and funnel_is_on(ts):
        print("Refusing to start: Tailscale Funnel is ON (public internet).")
        print("Turn Funnel off, then run this again. Do not use: tailscale funnel")
        sys.exit(1)

    lock_fh = _acquire_takeover_lock()
    try:
        take_over_previous()
        httpd = bind_http()
    finally:
        _release_takeover_lock(lock_fh)

    print("Gallery listening on http://127.0.0.1:%s (loopback only)" % PORT)
    if ts is None:
        print("Tailscale was not found. Install it, then run start_gallery.bat again.")
    else:
        status = enable_serve(ts)
        print("--- Tailscale Serve ---")
        print(status or "(no status yet; is Tailscale logged in?)")
        print("-----------------------")
        if "funnel on" in status.lower():
            print("Refusing to continue: Serve reported Funnel ON.")
            httpd.server_close()
            sys.exit(1)
        if "not enabled" in status.lower():
            m = re.search(r"https://login\.tailscale\.com/\S+", status)
            print()
            print("Serve is not enabled on this Tailscale account yet (one-time).")
            print("A browser will open. Enable Serve, then run start_gallery.bat again.")
            if m:
                print(m.group(0))
                webbrowser.open(m.group(0))
            httpd.server_close()
            sys.exit(0)
        SERVE_URL = parse_serve_url(status)
        if SERVE_URL:
            print("Phone URL: %s" % phone_url())
            print("A QR window will open. Scan it with the iPhone camera.")
            threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%s/setup" % PORT)).start()
        else:
            print("Could not parse Tailscale URL. Is Tailscale logged in?")
    print("Keep this window open. Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
