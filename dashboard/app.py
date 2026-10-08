"""
Веб-дашборд (админка) раскроя надувных фигур. Повторяет наш чат-воркфлоу:
  1) загрузка PDF-макета -> рендер страницы для выбора точек;
  2) выбор размера фигуры (1700/2700/3300/4200) и числа машущих рук (0/1/2);
  3) для каждой руки — точка КОРНЯ (f) + КОНЧИК (tip) + хорда реза (2 точки) ИЛИ вертикальный рез (0 точек);
  4) предпросмотр реза -> изолированная рука -> выбор среза ЗАПЯСТЬЯ (2 точки) и ЛОКТЯ (2 точки);
  5) сборка -> превью полной раскладки + скачать PDF/SVG.

ВСЕ точки — в координатах PT страницы PDF (как в manual_arms конфигов). Маппинг клика:
PT = пиксель/zoom + page.rect.x0/y0. Бэкенд рендерит страницу get_pixmap(Matrix(zoom,zoom)).

Запуск:  cd /opt/inflatable-cutter && venv/bin/python dashboard/app.py   (http://127.0.0.1:5000)
"""
import os
import sys
import json
import copy
import uuid
import shutil
import threading
import subprocess

import yaml
import pymupdf
from flask import Flask, request, jsonify, send_file, render_template, abort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # /opt/inflatable-cutter
SESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
os.makedirs(SESS, exist_ok=True)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024                    # до 200 МБ PDF

# ---------- токен-доступ (панель публичная -> защищаем ключом) ----------
# Ключ из env DASH_KEY, иначе из файла dashboard/.access_key (создаётся при первом запуске).
_KEYFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".access_key")
ACCESS_KEY = os.environ.get("DASH_KEY")
if not ACCESS_KEY:
    if os.path.isfile(_KEYFILE):
        ACCESS_KEY = open(_KEYFILE).read().strip()
    else:
        ACCESS_KEY = uuid.uuid4().hex
        open(_KEYFILE, "w").write(ACCESS_KEY)


@app.before_request
def _gate():
    if request.path.startswith("/static/"):
        return
    key = request.args.get("key") or request.cookies.get("dash_key") or request.headers.get("X-Key")
    if key != ACCESS_KEY:
        return ("Доступ запрещён. Откройте ссылку с ?key=КЛЮЧ.", 403)


@app.after_request
def _setcookie(resp):
    if request.args.get("key") == ACCESS_KEY:                          # запомнить ключ в cookie после входа по ссылке
        resp.set_cookie("dash_key", ACCESS_KEY, max_age=30 * 86400, samesite="Lax")
    return resp

# базовый конфиг на размер = проверенный cosmo-конфиг (универсальные дефолты + память-оптимизации)
SIZE_BASE = {
    "1700": "config_cosmo1700.yaml",
    "2700": "config_cosmo.yaml",
    "3300": "config_cosmo3300.yaml",
    "4200": "config_cosmo4200.yaml",
}
PICK_ZOOM = 2.0                                                        # пикс/PT для картинки выбора точек (стр. целиком)
ARM_ZOOM = 4.0                                                        # пикс/PT для кропа руки (точный выбор запястья/локтя)

JOBS = {}                                                             # job_id -> {"status","msg","pdf","svg","png"}


def _sdir(sid):
    d = os.path.join(SESS, sid)
    if not os.path.isdir(d):
        abort(404, "сессия не найдена")
    return d


def _base_cfg(size):
    path = os.path.join(ROOT, SIZE_BASE.get(size, SIZE_BASE["2700"]))
    cfg = yaml.safe_load(open(path, encoding="utf-8"))
    cfg.pop("manual_arms", None)                                       # точки зададим из UI
    return cfg


def _build_cfg(size, n_arms, arms_identical, arms):
    """Собрать cfg из выбора пользователя. arms = [{f:[x,y], tip:[x,y], blade?:[[..],[..]],
    wrist?:[[..],[..]], elbow?:[[..],[..]]}] в PT."""
    cfg = _base_cfg(size)
    if int(n_arms) <= 0:
        cfg["make_appendages"] = False
        cfg["make_hand"] = False
        cfg["arm_cut"] = "none"
        cfg["expected_arms"] = 0
        cfg.pop("manual_arms", None)
        return cfg
    cfg["arm_cut"] = "armpit"
    cfg["expected_arms"] = int(n_arms)
    cfg["make_appendages"] = True
    cfg["make_hand"] = True
    cfg["arm_template"] = True
    cfg["arms_identical"] = bool(arms_identical)
    man = []
    for a in arms:
        m = {"f": [float(a["f"][0]), float(a["f"][1])],
             "tip": [float(a["tip"][0]), float(a["tip"][1])]}
        if a.get("blade"):
            m["blade"] = [[float(a["blade"][0][0]), float(a["blade"][0][1])],
                          [float(a["blade"][1][0]), float(a["blade"][1][1])]]
        if a.get("wrist"):
            m["wrist"] = [[float(a["wrist"][0][0]), float(a["wrist"][0][1])],
                          [float(a["wrist"][1][0]), float(a["wrist"][1][1])]]
        if a.get("elbow"):
            m["elbow"] = [[float(a["elbow"][0][0]), float(a["elbow"][0][1])],
                          [float(a["elbow"][1][0]), float(a["elbow"][1][1])]]
        man.append(m)
    cfg["manual_arms"] = man
    return cfg


def _render_page_png(pdf_path, out_png, zoom):
    page = pymupdf.open(pdf_path)[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    pix.save(out_png)
    r = page.rect
    return {"w": pix.width, "h": pix.height, "zoom": zoom,
            "ox": float(r.x0), "oy": float(r.y0)}                      # PT = px/zoom + ox/oy


# ---------- маршруты ----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("pdf")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify(error="нужен PDF-файл"), 400
    sid = uuid.uuid4().hex[:12]
    d = os.path.join(SESS, sid)
    os.makedirs(d, exist_ok=True)
    pdf_path = os.path.join(d, "macet.pdf")
    f.save(pdf_path)
    try:
        meta = _render_page_png(pdf_path, os.path.join(d, "page.png"), PICK_ZOOM)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        return jsonify(error=f"не удалось прочитать PDF: {e}"), 400
    meta["sid"] = sid
    meta["img"] = f"/api/img/{sid}/page.png"
    json.dump(meta, open(os.path.join(d, "meta.json"), "w"))
    return jsonify(**meta)


@app.route("/api/img/<sid>/<name>")
def img(sid, name):
    d = _sdir(sid)
    p = os.path.join(d, os.path.basename(name))
    if not os.path.isfile(p):
        abort(404)
    return send_file(p)


@app.route("/api/arm_preview", methods=["POST"])
def arm_preview():
    """Прогнать рез рук (cut_waving_arms) по выбранным точкам. Вернуть bbox каждой руки +
    кроп-картинку руки (для выбора запястья/локтя). Валидирует рез — при ошибке вернёт её текст."""
    data = request.get_json(force=True)
    sid = data["sid"]
    d = _sdir(sid)
    size = data.get("size", "2700")
    arms = data.get("arms", [])
    try:
        from sides import get_front_figure
        from arm import cut_waving_arms
        cfg = _build_cfg(size, len(arms), False, arms)
        page = pymupdf.open(os.path.join(d, "macet.pdf"))[0]
        page._cfg = cfg
        fig = get_front_figure(page, cfg)
        body, cut = cut_waving_arms(page, fig.contour_pt, cfg, strict=True)
    except Exception as e:
        return jsonify(error=f"рез не удался: {e}"), 400
    out = []
    page2 = pymupdf.open(os.path.join(d, "macet.pdf"))[0]
    for i, ca in enumerate(cut):
        b = ca["poly_pt"].bounds                                      # PT bbox руки
        pad = 12.0
        clip = pymupdf.Rect(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
        pix = page2.get_pixmap(matrix=pymupdf.Matrix(ARM_ZOOM, ARM_ZOOM), clip=clip)
        fn = f"arm{i}.png"
        pix.save(os.path.join(d, fn))
        out.append({"idx": i, "img": f"/api/img/{sid}/{fn}", "w": pix.width, "h": pix.height,
                    "zoom": ARM_ZOOM, "ox": float(clip.x0), "oy": float(clip.y0),
                    "f": ca["f"], "tip": ca["tip"]})
    return jsonify(arms=out, count=len(cut))


def _run_build(sid, cfg, want_svg):
    """Фоновая сборка: PDF (+SVG опц.) + превью PNG."""
    job = JOBS[sid]
    d = os.path.join(SESS, sid)
    cfg_path = os.path.join(d, "cfg.yaml")
    yaml.safe_dump(cfg, open(cfg_path, "w", encoding="utf-8"), allow_unicode=True)
    pdf_in = os.path.join(d, "macet.pdf")
    out_pdf = os.path.join(d, "layout.pdf")
    runcap = os.path.join(ROOT, "run_capped.sh")
    env = dict(os.environ)
    try:
        job["status"] = "running"; job["msg"] = "строю PDF-раскладку…"
        cmd = [runcap, os.path.join(ROOT, "venv/bin/python"),
               os.path.join(ROOT, "export_pdf_grouped.py"), pdf_in, "--config", cfg_path, "-o", out_pdf]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=1200)
        if r.returncode != 0 or not os.path.isfile(out_pdf):
            job["status"] = "error"; job["msg"] = "ошибка сборки PDF:\n" + (r.stderr or r.stdout)[-1500:]
            return
        job["pdf"] = f"/api/img/{sid}/layout.pdf"
        job["msg"] = "рендерю превью…"
        page = pymupdf.open(out_pdf)[0]
        rr = page.rect
        z = min(1900.0 / rr.width, 1900.0 / rr.height)
        page.get_pixmap(matrix=pymupdf.Matrix(z, z)).save(os.path.join(d, "layout.png"))
        job["png"] = f"/api/img/{sid}/layout.png"
        if want_svg:
            job["msg"] = "строю SVG…"
            out_svg = os.path.join(d, "layout.svg")
            cmd[-1] = out_svg
            rs = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env, timeout=1200)
            if rs.returncode == 0 and os.path.isfile(out_svg):
                job["svg"] = f"/api/img/{sid}/layout.svg"
        job["status"] = "done"; job["msg"] = "готово"
    except subprocess.TimeoutExpired:
        job["status"] = "error"; job["msg"] = "таймаут сборки (>20 мин)"
    except Exception as e:
        job["status"] = "error"; job["msg"] = f"исключение: {e}"


@app.route("/api/build", methods=["POST"])
def build():
    data = request.get_json(force=True)
    sid = data["sid"]
    _sdir(sid)
    cfg = _build_cfg(data.get("size", "2700"), data.get("n_arms", 0),
                     data.get("arms_identical", False), data.get("arms", []))
    JOBS[sid] = {"status": "queued", "msg": "в очереди", "pdf": None, "svg": None, "png": None}
    threading.Thread(target=_run_build, args=(sid, cfg, bool(data.get("svg", True))), daemon=True).start()
    return jsonify(sid=sid, status="queued")


@app.route("/api/status/<sid>")
def status(sid):
    job = JOBS.get(sid)
    if not job:
        return jsonify(status="unknown"), 404
    return jsonify(**job)


if __name__ == "__main__":
    host = os.environ.get("DASH_HOST", "127.0.0.1")                    # 0.0.0.0 — слушать внешний интерфейс
    port = int(os.environ.get("DASH_PORT", "5000"))
    print(f"ACCESS KEY: {ACCESS_KEY}")
    print(f"URL: http://{host}:{port}/?key={ACCESS_KEY}")
    app.run(host=host, port=port, debug=False, threaded=True)
