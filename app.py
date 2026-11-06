"""PDF Renamer - lokalna aplikacja do szybkiego nazywania i dzielenia skanów PDF.

Uruchomienie:  python app.py   (otwiera http://127.0.0.1:8765 w przeglądarce)
Budowanie exe: build.bat        (tworzy dist\PDF-Renamer.exe)
"""

import json
import os
import re
import shutil
import socket
import threading
import webbrowser
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

HOST, PORT = "127.0.0.1", 8765
STATE_FILE = ".pdf_renamer.json"  # w folderze źródłowym: przetworzone pliki i wycięte strony
DONE_DIR = "_przetworzone"        # tu trafiają oryginały, z których wycięto wszystkie strony
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME = 200

# PyMuPDF nie jest bezpieczny wielowątkowo, a FastAPI obsługuje żądania w puli wątków
PDF_LOCK = threading.Lock()
DIALOG_LOCK = threading.Lock()

app = FastAPI(title="PDF Renamer")


# ---------- pomocnicze ----------

def get_folder(folder: str) -> Path:
    p = Path(folder.strip().strip('"')).expanduser()
    if not p.is_dir():
        raise HTTPException(400, f"Folder nie istnieje: {p}")
    return p.resolve()


def get_target(source: Path, target: str) -> Path:
    """Folder docelowy; pusty = folder źródłowy."""
    return get_folder(target) if target.strip() else source


def get_pdf(folder: Path, name: str) -> Path:
    p = folder / name
    if p.parent != folder or not p.is_file():
        raise HTTPException(404, f"Nie znaleziono pliku: {name}")
    return p


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(a.resolve()) == os.path.normcase(b.resolve())


def clean_name(new: str) -> str:
    new = new.strip()
    if new.lower().endswith(".pdf"):
        new = new[:-4].rstrip()
    new = new.rstrip(". ")
    if not new:
        raise HTTPException(400, "Nowa nazwa jest pusta")
    if INVALID_CHARS.search(new):
        raise HTTPException(400, 'Niedozwolone znaki w nazwie: < > : " / \\ | ? *')
    if new.split(".")[0].strip().upper() in RESERVED:
        raise HTTPException(400, f"„{new}” to nazwa zarezerwowana przez Windows")
    if len(new) > MAX_NAME:
        raise HTTPException(400, f"Nazwa jest za długa ({len(new)} znaków, maks. {MAX_NAME})")
    return new


def open_pdf(path: Path) -> fitz.Document:
    """Otwiera PDF z czytelnym błędem dla plików uszkodzonych i zabezpieczonych hasłem.
    Wywoływać tylko pod PDF_LOCK."""
    try:
        doc = fitz.open(path)
    except Exception:
        raise HTTPException(422, f"Nie można otworzyć „{path.name}” – plik jest uszkodzony albo to nie jest PDF")
    if doc.needs_pass:
        doc.close()
        raise HTTPException(422, f"„{path.name}” jest zabezpieczony hasłem – nie da się go podejrzeć ani podzielić")
    if doc.page_count == 0:
        doc.close()
        raise HTTPException(422, f"„{path.name}” nie zawiera żadnych stron")
    return doc


def page_count(path: Path) -> int:
    with PDF_LOCK, open_pdf(path) as doc:
        return doc.page_count


def load_state(folder: Path) -> dict:
    try:
        data = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if isinstance(data, list):  # starszy format: sama lista przemianowanych plików
        data = {"done": data}
    return {
        "done": set(data.get("done", [])),
        "used": {k: set(v) for k, v in data.get("used", {}).items()},
    }


def save_state(folder: Path, st: dict) -> None:
    data = {
        "done": sorted(st["done"]),
        "used": {k: sorted(v) for k, v in st["used"].items() if v},
    }
    (folder / STATE_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def free_path(p: Path) -> Path:
    """p, a jeśli zajęta: 'nazwa (2).pdf', 'nazwa (3).pdf'..."""
    base, i = p, 2
    while p.exists():
        p = base.with_name(f"{base.stem} ({i}){base.suffix}")
        i += 1
    return p


def move(src: Path, dst: Path) -> None:
    try:
        shutil.move(src, dst)
    except OSError as e:
        raise HTTPException(500, f"Nie udało się przenieść pliku (otwarty w innym programie?): {e}")


def archive_original(folder: Path, src: Path, st: dict) -> str:
    """Przenosi w pełni pocięty oryginał do podfolderu _przetworzone."""
    dst = free_path(folder / DONE_DIR / src.name)
    dst.parent.mkdir(exist_ok=True)
    move(src, dst)
    st["used"].pop(src.name, None)
    st["done"].discard(src.name)
    return f"{DONE_DIR}/{dst.name}"


def write_pages(src: Path, dst: Path, pages: list[int]) -> None:
    """Zapisuje strony (numerowane od 1, kolejne) do nowego PDF."""
    with PDF_LOCK, open_pdf(src) as doc:
        try:
            with fitz.open() as out:
                out.insert_pdf(doc, from_page=pages[0] - 1, to_page=pages[-1] - 1)
                out.save(dst, garbage=3, deflate=True)
        except Exception as e:
            dst.unlink(missing_ok=True)  # nie zostawiaj niedokończonego pliku
            raise HTTPException(500, f"Nie udało się zapisać {dst.name}: {e}")


# ---------- API ----------

@app.get("/api/pick-folder")
def pick_folder():
    """Otwiera natywne okno Windows do wyboru folderu."""
    import tkinter as tk
    from tkinter import filedialog

    if not DIALOG_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Okno wyboru folderu jest już otwarte (sprawdź pasek zadań)")
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Wybierz folder", parent=root)
        root.destroy()
    finally:
        DIALOG_LOCK.release()
    return {"folder": str(Path(path)) if path else ""}


@app.get("/api/files")
def list_files(folder: str):
    f = get_folder(folder)
    done = load_state(f)["done"]
    pdfs = sorted((p for p in f.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
                  key=lambda p: p.name.lower())
    return {"folder": str(f), "files": [
        {"name": p.name, "done": p.name in done, "v": p.stat().st_mtime_ns} for p in pdfs
    ]}


@app.get("/api/info")
def info(folder: str, name: str):
    f = get_folder(folder)
    path = get_pdf(f, name)
    return {"pages": page_count(path), "used": sorted(load_state(f)["used"].get(name, []))}


@app.get("/api/preview")
def preview(folder: str, name: str, page: int = Query(0, ge=0),
            width: int = Query(1200, ge=100, le=3000)):
    path = get_pdf(get_folder(folder), name)
    with PDF_LOCK, open_pdf(path) as doc:
        if page >= doc.page_count:
            raise HTTPException(404, f"PDF ma tylko {doc.page_count} stron")
        try:
            p = doc[page]
            zoom = width / p.rect.width
            png = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes("png")
        except Exception as e:
            raise HTTPException(422, f"Nie można wyrenderować strony {page + 1}: {e}")
    # adres zawiera wersję pliku (mtime), więc można go cache'ować
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


class SaveRequest(BaseModel):
    folder: str
    target: str = ""
    name: str
    new: str
    start: int  # numer strony od 1
    end: int


@app.post("/api/save")
def save(req: SaveRequest):
    """Zapisuje strony start..end jako nowy plik.

    Gdy wybrano cały plik i nic z niego jeszcze nie wycięto, plik jest po prostu
    przemianowany / przeniesiony (bez ponownego zapisu PDF).
    """
    folder = get_folder(req.folder)
    src = get_pdf(folder, req.name)
    out_dir = get_target(folder, req.target)
    dst = out_dir / f"{clean_name(req.new)}.pdf"
    n = page_count(src)
    if not 1 <= req.start <= req.end <= n:
        raise HTTPException(400, f"Nieprawidłowy zakres stron: {req.start}-{req.end} (plik ma {n})")

    st = load_state(folder)
    used = st["used"].get(src.name, set())
    whole = req.start == 1 and req.end == n and not used
    if dst.exists() and not (whole and same_path(dst, src)):
        raise HTTPException(409, f"Plik już istnieje: {dst.name}")

    archived = None
    if whole:
        move(src, dst)
        st["done"].discard(src.name)
        finished = True
    else:
        write_pages(src, dst, list(range(req.start, req.end + 1)))
        used |= set(range(req.start, req.end + 1))
        st["used"][src.name] = used
        finished = len(used) >= n
        if finished:
            archived = archive_original(folder, src, st)
    if same_path(out_dir, folder):
        st["done"].add(dst.name)
    save_state(folder, st)
    return {"saved": [dst.name], "finished": finished, "archived": archived, "used": sorted(used)}


class SplitRequest(BaseModel):
    folder: str
    target: str = ""
    name: str
    base: str = ""  # pusty = nazwa oryginału


@app.post("/api/split")
def split(req: SplitRequest):
    """Dzieli wszystkie niewycięte jeszcze strony na pojedyncze pliki nazwa_01.pdf, nazwa_02.pdf..."""
    folder = get_folder(req.folder)
    src = get_pdf(folder, req.name)
    out_dir = get_target(folder, req.target)
    base = clean_name(req.base) if req.base.strip() else src.stem
    n = page_count(src)

    st = load_state(folder)
    used = st["used"].get(src.name, set())
    digits = max(2, len(str(n)))
    jobs = [(p, out_dir / f"{base}_{p:0{digits}d}.pdf") for p in range(1, n + 1) if p not in used]
    taken = [d.name for _, d in jobs if d.exists()]
    if taken:
        raise HTTPException(409, f"Pliki już istnieją: {', '.join(taken[:5])}")

    # postęp zapisujemy nawet przy błędzie w połowie, żeby ponowna próba nie trafiła na konflikty
    try:
        for p, dst in jobs:
            write_pages(src, dst, [p])
            used.add(p)
            st["used"][src.name] = used
            if same_path(out_dir, folder):
                st["done"].add(dst.name)
        archived = archive_original(folder, src, st)
    finally:
        save_state(folder, st)
    return {"saved": [d.name for _, d in jobs], "finished": True, "archived": archived}


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


# ---------- interfejs ----------

INDEX_HTML = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>PDF Renamer</title>
<link rel="icon" href="data:,">
<style>
  :root { --bg:#f4f5f7; --panel:#fff; --line:#dde0e5; --text:#1d2129; --muted:#6b7280;
          --accent:#2563eb; --ok:#16a34a; --err:#dc2626; --sel:#dbeafe; }
  * { box-sizing:border-box; }
  html, body { height:100%; margin:0; }
  body { font:14px/1.4 system-ui, "Segoe UI", sans-serif; background:var(--bg); color:var(--text);
         display:grid; grid-template-rows:auto 1fr; }
  header { display:grid; grid-template-columns:auto 1fr auto auto; gap:6px 8px; padding:10px 14px;
           background:var(--panel); border-bottom:1px solid var(--line); align-items:center; }
  header label { color:var(--muted); font-size:13px; }
  input[type=text], input[type=number] { font:inherit; padding:7px 10px; border:1px solid var(--line); border-radius:6px; }
  input:focus { outline:2px solid var(--accent); border-color:transparent; }
  input[type=number] { width:70px; }
  button { font:inherit; padding:7px 14px; border:0; border-radius:6px; background:var(--accent); color:#fff; cursor:pointer; white-space:nowrap; }
  button.secondary { background:#e5e7eb; color:var(--text); }
  button:disabled { opacity:.5; cursor:default; }
  main { display:grid; grid-template-columns:260px 160px 1fr; min-height:0; }
  aside { background:var(--panel); border-right:1px solid var(--line); display:flex; flex-direction:column; min-height:0; }
  .listhead { padding:8px 12px; border-bottom:1px solid var(--line); color:var(--muted); display:flex; justify-content:space-between; align-items:center; gap:6px; }
  #list { list-style:none; margin:0; padding:0; overflow:auto; flex:1; }
  #list li { padding:6px 12px; cursor:pointer; display:flex; gap:8px; align-items:center; border-bottom:1px solid #f0f1f3; word-break:break-all; }
  #list li:hover { background:#f3f4f6; }
  #list li.active { background:var(--sel); }
  #list li.done { color:var(--muted); }
  .dot { width:8px; height:8px; border-radius:50%; background:#d1d5db; flex:none; }
  li.done .dot { background:var(--ok); }
  #thumbs { overflow:auto; padding:10px; border-right:1px solid var(--line); background:#eceef1; display:flex; flex-direction:column; gap:10px; }
  .th { position:relative; cursor:pointer; border:3px solid transparent; border-radius:4px; background:#fff; flex:none; user-select:none; }
  .th img { display:block; width:100%; min-height:60px; }
  .th span { position:absolute; left:4px; bottom:4px; background:rgba(0,0,0,.6); color:#fff; font-size:11px; padding:0 5px; border-radius:3px; }
  .th.sel { border-color:var(--accent); }
  .th.used img { opacity:.35; }
  .th.used::after { content:"✓"; position:absolute; top:2px; right:6px; color:var(--ok); font-weight:700; font-size:18px; }
  section { display:grid; grid-template-rows:auto 1fr; min-height:0; }
  .editor { padding:10px 16px; background:var(--panel); border-bottom:1px solid var(--line); display:flex; flex-direction:column; gap:8px; }
  .oldname { color:var(--muted); word-break:break-all; }
  .oldname b { color:var(--text); }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  #newname { flex:1; min-width:200px; font-size:18px; }
  .ext { color:var(--muted); font-size:18px; }
  #msg { min-height:20px; font-size:13px; }
  #msg.err { color:var(--err); } #msg.ok { color:var(--ok); }
  .viewer { position:relative; overflow:auto; padding:16px; display:flex; flex-direction:column; align-items:center; gap:16px; container-type:size; }
  #pagesBox { display:contents; }
  .pg { max-width:100%; background:#fff; box-shadow:0 2px 12px rgba(0,0,0,.15); min-height:200px; flex:none; }
  .viewer.fit .pg { max-height:calc(100cqh - 32px); }
  .pager { color:var(--muted); font-size:13px; white-space:nowrap; margin-left:auto; }
  .empty { color:var(--muted); margin-top:40px; }
  kbd { font-size:11px; border:1px solid var(--line); border-radius:3px; padding:0 4px; background:#f9fafb; }
  .hint { color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <label for="folder">Źródło</label>
  <input id="folder" type="text" placeholder="Folder z plikami PDF, np. C:\Users\Ja\Documents\Skany">
  <button id="pick" class="secondary">Wybierz…</button>
  <button id="load">Wczytaj</button>
  <label for="target">Cel</label>
  <input id="target" type="text" placeholder="Folder docelowy dla nowych plików (puste = ten sam co źródło)">
  <button id="pickTarget" class="secondary">Wybierz…</button>
  <span></span>
</header>
<main>
  <aside>
    <div class="listhead">
      <span id="count">—</span>
      <label><input type="checkbox" id="onlyTodo" checked> tylko do zrobienia</label>
    </div>
    <ul id="list"></ul>
  </aside>
  <div id="thumbs"></div>
  <section>
    <div class="editor">
      <div class="oldname">Plik: <b id="old">—</b> <span id="pages"></span></div>
      <div class="row">
        Strony <input id="from" type="number" min="1" disabled> – <input id="to" type="number" min="1" disabled>
        <button id="all" class="secondary" disabled title="Zaznacz wszystkie niewycięte strony">Wszystkie</button>
        <button id="split" class="secondary" disabled title="Każda niewycięta strona jako osobny plik: nazwa_01.pdf, nazwa_02.pdf…">Podziel na pojedyncze strony</button>
        <button id="fit" class="secondary" title="Przełącz: cała strona / pełna szerokość">Cała strona</button>
        <span class="pager" id="pager"></span>
      </div>
      <div class="row">
        <input id="newname" type="text" placeholder="Nowa nazwa (bez .pdf)" disabled autocomplete="off">
        <span class="ext">.pdf</span>
        <button id="save" disabled>Zapisz i następny</button>
        <button id="skip" class="secondary" disabled>Pomiń</button>
      </div>
      <div id="msg"></div>
      <div class="hint">
        <kbd>Enter</kbd> zapisz · klik miniatury = jedna strona, <kbd>Shift</kbd>+klik = zakres ·
        w polu „do” <kbd>Enter</kbd> przechodzi do nazwy · <kbd>Ctrl</kbd>+<kbd>↓</kbd>/<kbd>↑</kbd> następny/poprzedni plik ·
        <kbd>PgDn</kbd>/<kbd>PgUp</kbd> przewiń podgląd · <kbd>Esc</kbd> przywróć nazwę
      </div>
    </div>
    <div class="viewer fit" id="viewer"><div class="empty" id="empty">Wskaż folder i kliknij „Wczytaj”.</div><div id="pagesBox"></div></div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
let folder = "", files = [], current = null, busy = false;
let info = null;   // {pages, used: Set} aktualnego pliku
let infoReady = Promise.resolve();  // kończy się, gdy info aktualnego pliku jest wczytane
let range = null;  // {from, to} zaznaczone strony (od 1)

const stem = n => n.replace(/\.pdf$/i, "");
const visible = () => $("onlyTodo").checked ? files.filter(f => !f.done) : files;
const q = name => `folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(name)}&v=${files.find(f => f.name === name)?.v ?? 0}`;
const pageUrl = (name, i, w = 1400) => `/api/preview?${q(name)}&page=${i}&width=${w}`;
const store = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
const recall = k => { try { return localStorage.getItem(k) || ""; } catch { return ""; } };

function msg(text, cls = "") { $("msg").textContent = text; $("msg").className = cls; }

async function api(url, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined;
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

// ---------- folder i lista plików ----------

async function fetchFiles(path = folder) {
  const data = await api(`/api/files?folder=${encodeURIComponent(path)}`);
  folder = data.folder; files = data.files;  // dopiero po udanym wczytaniu
}

async function loadFolder() {
  const path = $("folder").value.trim();
  if (!path) { msg("Wpisz ścieżkę do folderu albo kliknij „Wybierz…”.", "err"); $("folder").focus(); return; }
  msg("Wczytywanie…");
  try {
    await fetchFiles(path);
    $("folder").value = folder;
    store("pdfRenamerFolder", folder);
    select(visible()[0]?.name || null);
    msg(`Wczytano ${files.length} plików PDF.`, "ok");
  } catch (e) { msg(e.message, "err"); }
}

async function pickInto(input) {
  msg("Otwieram okno wyboru folderu… (może pojawić się za przeglądarką)");
  try {
    const { folder: picked } = await api("/api/pick-folder");
    if (!picked) { msg(""); return null; }
    $(input).value = picked;
    msg("");
    return picked;
  } catch (e) { msg(e.message, "err"); return null; }
}

function renderList() {
  const done = files.filter(f => f.done).length;
  $("count").textContent = `${done} / ${files.length} zrobione`;
  $("list").innerHTML = "";
  for (const f of visible()) {
    const li = document.createElement("li");
    li.className = (f.done ? "done " : "") + (f.name === current ? "active" : "");
    li.innerHTML = `<span class="dot"></span><span></span>`;
    li.lastChild.textContent = f.name;
    li.onclick = () => select(f.name);
    $("list").appendChild(li);
    if (f.name === current) li.scrollIntoView({ block: "nearest" });
  }
}

function neighbour(name, dir) {
  const list = visible();
  const i = list.findIndex(f => f.name === name);
  return list[i + dir]?.name || null;
}

// ---------- wybór pliku ----------

function setControls(on) {
  for (const id of ["newname", "save", "from", "to", "all", "split"]) $(id).disabled = !on;
}

function select(name) {
  current = name; info = null; range = null;
  const has = !!name;
  msg("");
  setControls(has);
  $("skip").disabled = !has;
  $("old").textContent = name || "—";
  $("pages").textContent = $("pager").textContent = "";
  $("pagesBox").innerHTML = $("thumbs").innerHTML = "";
  $("empty").hidden = has;
  $("newname").value = has ? stem(name) : "";
  $("from").value = $("to").value = "";
  if (!has) {
    $("empty").textContent = files.length ? "Wszystkie pliki przetworzone 🎉" : "Brak plików PDF w folderze.";
  } else {
    $("newname").focus(); $("newname").select();
    infoReady = loadInfo(name);
    const n = neighbour(name, 1);
    if (n) new Image().src = pageUrl(n, 0);  // wczytaj z wyprzedzeniem
  }
  renderList();
}

async function loadInfo(name) {
  try {
    const data = await api(`/api/info?${q(name)}`);
    if (current !== name) return;  // użytkownik już przeszedł dalej
    info = { pages: data.pages, used: new Set(data.used) };
    const p = info.pages;
    $("pages").textContent = `· ${p} ${p === 1 ? "strona" : (p % 10 >= 2 && p % 10 <= 4 && (p % 100 < 10 || p % 100 >= 20)) ? "strony" : "stron"}`
      + (info.used.size ? ` · wycięto już ${info.used.size}` : "");
    $("from").max = $("to").max = p;
    buildThumbs(name);
    setRange(nextRange());
  } catch (e) {
    if (current !== name) return;
    // plik uszkodzony / z hasłem: można go tylko pominąć
    setControls(false);
    $("empty").hidden = false;
    $("empty").textContent = e.message + " · Ctrl+↓ lub „Pomiń”, aby przejść dalej.";
    $("skip").focus();
  }
}

function buildThumbs(name) {
  const box = $("thumbs");
  box.innerHTML = "";
  for (let i = 1; i <= info.pages; i++) {
    const d = document.createElement("div");
    d.className = "th"; d.dataset.page = i;
    d.innerHTML = `<img loading="lazy" alt=""><span>${i}</span>`;
    d.firstChild.src = pageUrl(name, i - 1, 280);
    d.onclick = e => {
      if (e.shiftKey && range) setRange(range.from, i);
      else setRange(i, i);
      $("newname").focus();
    };
    box.appendChild(d);
  }
}

// pierwszy ciągły blok niewyciętych stron (dla nowego pliku: cały plik)
function nextRange() {
  const n = info.pages, used = info.used;
  let s = 1;
  while (s <= n && used.has(s)) s++;
  if (s > n) return null;
  let e = s;
  while (e < n && !used.has(e + 1)) e++;
  return { from: s, to: e };
}

function isWhole() {
  return range && info && range.from === 1 && range.to === info.pages && info.used.size === 0;
}

function setRange(from, to) {
  if (from && typeof from === "object") ({ from, to } = from);
  if (!info || !from) { range = null; return; }
  const n = info.pages;
  from = Math.min(Math.max(1, from | 0), n);
  to = Math.min(Math.max(1, (to ?? from) | 0), n);
  if (to < from) [from, to] = [to, from];
  if (range && range.from === from && range.to === to) return;  // bez zmian: nie przeładowuj podglądu
  range = { from, to };
  $("from").value = from; $("to").value = to;
  for (const th of $("thumbs").children) {
    const p = +th.dataset.page;
    th.classList.toggle("sel", p >= from && p <= to);
    th.classList.toggle("used", info.used.has(p));
  }
  $("thumbs").children[from - 1]?.scrollIntoView({ block: "nearest" });
  $("save").textContent = isWhole() ? "Zapisz i następny" : `Zapisz strony ${from}–${to}`;
  renderPreview();
}

// ---------- podgląd zaznaczonych stron ----------

function renderPreview() {
  const box = $("pagesBox");
  box.innerHTML = "";
  $("viewer").scrollTop = 0;
  if (!range) return;
  for (let i = range.from; i <= range.to; i++) {
    const img = document.createElement("img");
    img.className = "pg"; img.alt = `Strona ${i}`; img.dataset.page = i;
    if (i > range.from) img.loading = "lazy";
    img.src = pageUrl(current, i - 1);
    box.appendChild(img);
  }
  updatePager();
}

function updatePager() {
  if (!range) { $("pager").textContent = ""; return; }
  const v = $("viewer"), mid = v.scrollTop + v.clientHeight / 3;
  let cur = range.from;
  for (const img of $("pagesBox").children) if (img.offsetTop <= mid) cur = +img.dataset.page;
  $("pager").textContent = `str. ${cur} / ${info.pages}` + (range.from !== range.to ? ` (wycinek ${range.from}–${range.to})` : "");
}

// ---------- zapis ----------

async function afterSave(res, old) {
  const where = $("target").value.trim() ? " → folder docelowy" : "";
  const saved = res.saved.length > 3 ? `${res.saved.length} plików` : res.saved.join(", ");
  const text = `✓ Zapisano: ${saved}${where}` + (res.archived ? ` · oryginał przeniesiony do ${res.archived}` : "");
  if (res.finished) {
    const next = neighbour(old, 1) || neighbour(old, -1);
    await fetchFiles();
    select(files.some(f => f.name === next) ? next : (visible()[0]?.name || null));
    msg(text, "ok");
  } else {
    msg(text, "ok");
    // zostajemy przy tym pliku: zaznacz kolejny blok i czekaj na zakres + nazwę
    info.used = new Set(res.used);
    $("pages").textContent = $("pages").textContent.replace(/ · wycięto.*$/, "") + ` · wycięto już ${info.used.size}`;
    await fetchFiles();
    renderList();
    setRange(nextRange());
    $("newname").value = "";
    $("to").focus(); $("to").select();
  }
}

async function save() {
  if (!current || busy) return;
  const newName = $("newname").value.trim();
  if (!newName) { msg("Podaj nazwę.", "err"); $("newname").focus(); return; }
  busy = true;
  const old = current;
  try {
    await infoReady;  // Enter tuż po otwarciu pliku: poczekaj na liczbę stron zamiast ignorować
    if (current !== old || !range) return;
    const res = await api("/api/save", {
      folder, target: $("target").value.trim(), name: old, new: newName, start: range.from, end: range.to,
    });
    await afterSave(res, old);
  } catch (e) { msg(e.message, "err"); }
  finally { busy = false; }
}

async function splitAll() {
  if (!current || !info || busy) return;
  busy = true;
  const old = current;
  const base = $("newname").value.trim();
  try {
    msg("Dzielenie…");
    const res = await api("/api/split", { folder, target: $("target").value.trim(), name: old, base });
    await afterSave(res, old);
  } catch (e) { msg(e.message, "err"); }
  finally { busy = false; }
}

// ---------- zdarzenia ----------

$("load").onclick = loadFolder;
$("folder").addEventListener("keydown", e => { if (e.key === "Enter") loadFolder(); });
$("pick").onclick = async () => { if (await pickInto("folder")) loadFolder(); };
$("pickTarget").onclick = async () => { if (await pickInto("target")) store("pdfRenamerTarget", $("target").value); };
$("target").addEventListener("change", () => store("pdfRenamerTarget", $("target").value.trim()));
$("save").onclick = save;
$("split").onclick = splitAll;
$("skip").onclick = () => { const n = neighbour(current, 1); if (n) select(n); };
$("all").onclick = () => { if (info) setRange(nextRange()); };
$("fit").onclick = () => {
  const fit = $("viewer").classList.toggle("fit");
  $("fit").textContent = fit ? "Cała strona" : "Pełna szerokość";
};
$("onlyTodo").onchange = () => {
  if (current && !visible().some(f => f.name === current)) select(visible()[0]?.name || null);
  else renderList();
};
$("viewer").addEventListener("scroll", updatePager);

$("from").addEventListener("change", () => setRange(+$("from").value, Math.max(+$("from").value, range?.to || 0)));
$("to").addEventListener("change", () => setRange(range?.from || 1, +$("to").value));
$("from").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); $("from").blur(); $("to").focus(); $("to").select(); } });
$("to").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); setRange(range?.from || 1, +$("to").value); $("newname").focus(); $("newname").select(); }
});

$("newname").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); save(); }
  else if (e.key === "Escape") { $("newname").value = stem(current); $("newname").select(); }
});
document.addEventListener("keydown", e => {
  if (e.ctrlKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    const n = neighbour(current, e.key === "ArrowDown" ? 1 : -1);
    if (n) select(n);
  } else if (e.key === "PageDown" || e.key === "PageUp") {
    // przewijanie podglądu działa także gdy kursor jest w polu nazwy
    e.preventDefault();
    const v = $("viewer");
    v.scrollBy({ top: (e.key === "PageDown" ? 1 : -1) * v.clientHeight * 0.9 });
  }
});

$("target").value = recall("pdfRenamerTarget");
const last = recall("pdfRenamerFolder");
if (last) { $("folder").value = last; loadFolder(); }
</script>
</body>
</html>
"""


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, PORT)) == 0


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    if port_in_use():
        # aplikacja już działa (np. drugie kliknięcie w exe) – tylko otwórz przeglądarkę
        webbrowser.open(url)
        raise SystemExit
    print(f"PDF Renamer działa: {url}\nAby zakończyć, zamknij to okno.\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
