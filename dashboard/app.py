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
LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library")   # база прогнанных макетов
os.makedirs(SESS, exist_ok=True)
os.makedirs(LIB, exist_ok=True)
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


def _build_cfg(size, n_arms, arms_identical, arms, spare=None, back_copy=None):
    """Собрать cfg из выбора пользователя. arms = [{f:[x,y], tip:[x,y], blade?:[[..],[..]],
    wrist?:[[..],[..]], elbow?:[[..],[..]]}] в PT. spare — запасная рука (None=как в базе);
    back_copy — зад=копия переда (симметричная фигура; убирает лишние детали руки у 2-рукой)."""
    cfg = _base_cfg(size)
    if back_copy is not None:
        cfg["back_from_front"] = bool(back_copy)
    if int(n_arms) <= 0:
        cfg["make_appendages"] = False
        cfg["make_hand"] = False
        cfg["arm_cut"] = "none"
        cfg["expected_arms"] = 0
        cfg.pop("manual_arms", None)
        if spare is not None:
            cfg["make_spare_arm"] = bool(spare)
        return cfg
    # НЕ трогаем make_appendages/make_hand (в базовом cosmo-конфиге они false — рука/кисть строятся
    # из ВЫРЕЗАННОГО куска через arm_template+build_arm_from_poly, а не старым get_arm_contour).
    cfg["arm_cut"] = "armpit"
    cfg["expected_arms"] = int(n_arms)
    cfg.setdefault("arm_template", True)
    cfg["arms_identical"] = bool(arms_identical)
    if spare is not None:                                             # запаска — опция из UI
        cfg["make_spare_arm"] = bool(spare)
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
        if a.get("hole_dy"):
            m["hole_dy"] = float(a["hole_dy"])                        # ручной сдвиг отверстия руки (мм)
        man.append(m)
    cfg["manual_arms"] = man
    return cfg


def _save_library(sid, pdf_path, page_png, name):
    """Сохранить макет в БАЗУ (персистентно): PDF + превью-миниатюра + имя. id = sid."""
    try:
        from PIL import Image
        ld = os.path.join(LIB, sid); os.makedirs(ld, exist_ok=True)
        shutil.copy2(pdf_path, os.path.join(ld, "macet.pdf"))
        im = Image.open(page_png); im.thumbnail((360, 360)); im.save(os.path.join(ld, "thumb.png"))
        json.dump({"sid": sid, "name": name}, open(os.path.join(ld, "meta.json"), "w"))
    except Exception:
        pass


def _render_page_png(pdf_path, out_png, max_px=1600):
    page = pymupdf.open(pdf_path)[0]
    r = page.rect
    zoom = min(2.0, max_px / max(r.width, r.height))                   # кап по длинной стороне (лёгкая картинка для моб.)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    pix.save(out_png)
    return {"w": pix.width, "h": pix.height, "zoom": zoom,
            "ox": float(r.x0), "oy": float(r.y0)}                      # PT = px/zoom + ox/oy


def _root_candidates(poly_pt, N=12):
    """Нумерованные ВОГНУТЫЕ углы контура (подмышки/верх плеча/впадины) — как _dbg_cand.py.
    Растрируем полигон, approxPolyDP, берём вогнутые углы, NMS по 40pt, сортировка по остроте.
    Возвращает [(x,y,angle)...] в PT (индекс в списке = номер на картинке)."""
    import numpy as np, cv2, math
    from PIL import Image, ImageDraw
    b = poly_pt.bounds; Z = 2.0; pad = 8
    W = int((b[2] - b[0]) * Z) + 2 * pad; H = int((b[3] - b[1]) * Z) + 2 * pad
    im = Image.new("L", (W, H), 0); dr = ImageDraw.Draw(im)
    dr.polygon([((x - b[0]) * Z + pad, (y - b[1]) * Z + pad) for x, y in poly_pt.exterior.coords], fill=255)
    cnts, _ = cv2.findContours(np.array(im, np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return []
    c = cv2.approxPolyDP(max(cnts, key=cv2.contourArea), 2.0, True)
    P = np.array([((p[0][0] - pad) / Z + b[0], (p[0][1] - pad) / Z + b[1]) for p in c])
    n = len(P)
    if n < 3:
        return []
    area = sum(P[i][0] * P[(i + 1) % n][1] - P[(i + 1) % n][0] * P[i][1] for i in range(n))
    orient = 1 if area > 0 else -1
    L = 18.0

    def ptat(i, step):
        acc = 0.0; j = i
        while acc < L:
            nj = (j + step) % n; acc += float(np.hypot(*(P[nj] - P[j]))); j = nj
            if j == i:
                break
        return P[j]
    cand = []
    for i in range(n):
        pa = ptat(i, -1); pb = P[i]; pc = ptat(i, 1)
        e1 = pb - pa; e2 = pc - pb
        cr = e1[0] * e2[1] - e1[1] * e2[0]
        if cr * orient >= 0:                                          # выпуклый угол — пропускаем (нужны вогнутые)
            continue
        ang = math.degrees(math.atan2(abs(cr), float(np.dot(e1, e2))))
        cand.append((float(pb[0]), float(pb[1]), ang))
    cand.sort(key=lambda t: -t[2]); keep = []
    for x, y, a in cand:
        if all(np.hypot(x - kx, y - ky) > 40 for kx, ky, _ in keep):
            keep.append((x, y, a))
        if len(keep) >= N:
            break
    return keep


def _auto_tip(poly_pt, f):
    """Кончик кисти = самая ДАЛЬНЯЯ точка контура от подмышки f (в PT)."""
    import numpy as np
    exr = np.asarray(poly_pt.exterior.coords, float)
    d = np.hypot(exr[:, 0] - f[0], exr[:, 1] - f[1])
    p = exr[int(d.argmax())]
    return [float(p[0]), float(p[1])]


def _fill_tips(d, size, arms):
    """Досчитать tip тем рукам, у кого он не задан. tip = кончик кисти от БЛИЖАЙШЕЙ задетектированной
    руки (detect_armpits даёт корректный кончик); fallback — самая дальняя точка контура от f."""
    if all(a.get("tip") for a in arms):
        return arms
    import numpy as np
    from sides import get_front_figure
    from arm import detect_armpits
    cfg = _base_cfg(size); cfg["arm_cut"] = "armpit"; cfg["expected_arms"] = len(arms)
    page = pymupdf.open(os.path.join(d, "macet.pdf"))[0]; page._cfg = cfg
    fig = get_front_figure(page, cfg)
    try:
        det = detect_armpits(fig.contour_pt, cfg)
    except Exception:
        det = []
    for a in arms:
        if a.get("tip") or not a.get("f"):
            continue
        f = np.asarray(a["f"], float)
        if det:
            best = min(det, key=lambda da: float(np.hypot(da["f"][0] - f[0], da["f"][1] - f[1])))
            a["tip"] = [float(best["tip"][0]), float(best["tip"][1])]
        else:
            a["tip"] = _auto_tip(fig.contour_pt, a["f"])
    return arms


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
        meta = _render_page_png(pdf_path, os.path.join(d, "page.png"))
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        return jsonify(error=f"не удалось прочитать PDF: {e}"), 400
    meta["sid"] = sid
    meta["img"] = f"/api/img/{sid}/page.png"
    meta["name"] = f.filename
    json.dump(meta, open(os.path.join(d, "meta.json"), "w"))
    _save_library(sid, pdf_path, os.path.join(d, "page.png"), f.filename)   # в базу макетов
    return jsonify(**meta)


@app.route("/api/library")
def library():
    """Список макетов базы (прогнанных через инструмент): id, имя, миниатюра."""
    items = []
    for sid in os.listdir(LIB):
        md = os.path.join(LIB, sid, "meta.json")
        if not os.path.isfile(md):
            continue
        try:
            m = json.load(open(md))
            items.append({"id": sid, "name": m.get("name", sid),
                          "thumb": f"/api/libimg/{sid}", "t": os.path.getmtime(md)})
        except Exception:
            continue
    items.sort(key=lambda x: -x["t"])
    return jsonify(items=items)


@app.route("/api/libimg/<sid>")
def libimg(sid):
    p = os.path.join(LIB, os.path.basename(sid), "thumb.png")
    if not os.path.isfile(p):
        abort(404)
    return send_file(p)


@app.route("/api/library/use", methods=["POST"])
def library_use():
    """Открыть макет из базы: создать НОВУЮ сессию из его PDF -> вернуть как upload."""
    data = request.get_json(force=True)
    lid = os.path.basename(data.get("id", ""))
    src = os.path.join(LIB, lid, "macet.pdf")
    if not os.path.isfile(src):
        return jsonify(error="макет не найден в базе"), 404
    name = json.load(open(os.path.join(LIB, lid, "meta.json"))).get("name", lid) if os.path.isfile(os.path.join(LIB, lid, "meta.json")) else lid
    sid = uuid.uuid4().hex[:12]
    d = os.path.join(SESS, sid); os.makedirs(d, exist_ok=True)
    pdf_path = os.path.join(d, "macet.pdf"); shutil.copy2(src, pdf_path)
    meta = _render_page_png(pdf_path, os.path.join(d, "page.png"))
    meta["sid"] = sid; meta["img"] = f"/api/img/{sid}/page.png"; meta["name"] = name
    json.dump(meta, open(os.path.join(d, "meta.json"), "w"))
    return jsonify(**meta)


@app.route("/api/img/<sid>/<name>")
def img(sid, name):
    d = _sdir(sid)
    p = os.path.join(d, os.path.basename(name))
    if not os.path.isfile(p):
        abort(404)
    return send_file(p)


@app.route("/api/detect", methods=["POST"])
def detect():
    """Авто-детект машущих рук (подмышка): вернуть предложенные точки реза для каждой руки —
    f (корень), tip (кончик), blade (хорда реза). Пользователь потом правит/выбирает режим."""
    data = request.get_json(force=True)
    d = _sdir(data["sid"])
    size = data.get("size", "2700")
    n = int(data.get("n_arms", 1))
    try:
        import numpy as np
        from sides import get_front_figure
        from arm import detect_armpits, blade_perp
        from side_color import arm_fill_color
        cfg = _base_cfg(size)
        cfg["arm_cut"] = "armpit"
        cfg["expected_arms"] = n
        cfg.pop("arm_detect_v2", None)                                 # для подсказки используем контурный детект (даёт f/tip/blade)
        page = pymupdf.open(os.path.join(d, "macet.pdf"))[0]
        page._cfg = cfg
        fig = get_front_figure(page, cfg)
        arms = detect_armpits(fig.contour_pt, cfg)
        if n and len(arms) > n:                                        # оставить n самых «рукоподобных» (кожа/рукав: R>>B)
            def warmth(a):
                c = arm_fill_color(page, np.asarray(a["poly"].exterior.coords, float))
                return int(c[0]) - int(c[2])
            arms = sorted(arms, key=warmth, reverse=True)[:n]
    except Exception as e:
        return jsonify(error=f"авто-детект не удался: {e}", arms=[]), 200
    out = []
    for a in arms:
        o = {"f": [float(a["f"][0]), float(a["f"][1])],
             "tip": [float(a["tip"][0]), float(a["tip"][1])]}
        bl = a.get("blade")
        if bl is None:                                                # перпендикулярная хорда реза (как в авто-резе)
            try:
                bl = blade_perp(fig.contour_pt, a["f"], a["tip"], cfg)
            except Exception:
                bl = None
        if bl is not None:
            o["blade"] = [[float(bl[0][0]), float(bl[0][1])], [float(bl[1][0]), float(bl[1][1])]]
        out.append(o)
    return jsonify(arms=out, count=len(out))


@app.route("/api/candidates", methods=["POST"])
def candidates():
    """Нумерованные кандидатные точки (вогнутые углы) контура переда — для выбора подмышки/верха
    плеча по НОМЕРУ. Возвращает [{i,x,y}] в PT (как _dbg_cand.py)."""
    data = request.get_json(force=True)
    d = _sdir(data["sid"])
    size = data.get("size", "2700")
    try:
        from sides import get_front_figure
        cfg = _base_cfg(size); cfg["arm_cut"] = "none"
        page = pymupdf.open(os.path.join(d, "macet.pdf"))[0]; page._cfg = cfg
        fig = get_front_figure(page, cfg)
        keep = _root_candidates(fig.contour_pt, int(data.get("n", 12)))
    except Exception as e:
        return jsonify(error=f"кандидаты не найдены: {e}", pts=[]), 200
    pts = [{"i": i, "x": float(x), "y": float(y)} for i, (x, y, a) in enumerate(keep)]
    return jsonify(pts=pts, count=len(pts))


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
        arms = _fill_tips(d, size, arms)                              # авто-кончик, если не задан
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


@app.route("/api/front_preview", methods=["POST"])
def front_preview():
    """Превью ТОЛЬКО переда: контур + цветные отрезки, где ляжет рука по ДЛИНЕ отверстия руки
    (от подмышки вверх на диаметр отверстия). 1 рука — с одной стороны, 2 — с двух."""
    data = request.get_json(force=True)
    d = _sdir(data["sid"])
    size = data.get("size", "2700")
    arms = data.get("arms", [])
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
        from shapely.geometry import Point
        from shapely.ops import substring
        from sides import PT2MM, get_front_figure, contour_to_mm, smooth_front
        from arm import cut_waving_arms
        arms = _fill_tips(d, size, arms)
        cfg = _build_cfg(size, len(arms), data.get("arms_identical", False), arms,
                         spare=data.get("spare"), back_copy=data.get("back_copy"))
        page = pymupdf.open(os.path.join(d, "macet.pdf"))[0]; page._cfg = cfg
        fig = get_front_figure(page, cfg)                            # ЛЁГКИЙ путь: только контур переда (без боковин/днища/руки)
        fig.contour_pt, cut = cut_waving_arms(page, fig.contour_pt, cfg, strict=False)
        front = smooth_front(contour_to_mm(fig), cfg)
        ext = front.exterior; L = ext.length
        fmm = fig.s * PT2MM                                          # contour_to_mm = чистый масштаб ×fmm от origin
        D = float(cfg.get("arm_hole_diameter_mm", 250))
        bx0, by0, bx1, by1 = front.bounds; mg = 30.0
        sc = 1000.0 / max(bx1 - bx0, by1 - by0)
        W = int((bx1 - bx0) * sc + 2 * mg); H = int((by1 - by0) * sc + 2 * mg)
        T = lambda x, y: ((x - bx0) * sc + mg, (y - by0) * sc + mg)
        segs = []                                                    # отрезки в ПИКСЕЛЯХ (для интерактива)
        for m in cfg.get("manual_arms", []):
            pm = Point(m["f"][0] * fmm, m["f"][1] * fmm)             # подмышка в мм
            P0 = ext.interpolate(ext.project(pm))                   # якорь на контуре
            dy = float(m.get("hole_dy", 0.0))                       # применённый сдвиг отверстия (мм)
            s0 = ext.project(Point(P0.x, P0.y + dy))
            a = substring(ext, max(0.0, s0 - D), s0)
            b = substring(ext, s0, min(L, s0 + D))
            seg = a if a.centroid.y <= b.centroid.y else b          # вверх (меньший y)
            segs.append([list(T(x, y)) for x, y in seg.coords])
        # контур (упрощённый) в пикселях — для привязки перетаскивания к контуру
        cont_px = [list(T(x, y)) for x, y in front.exterior.simplify(1.5).coords]
        # ТЕКСТУРА переда из макета, обрезанная по контуру, + обводка (БЕЗ отрезков — их рисует клиент)
        im = Image.new("RGB", (W, H), (255, 255, 255)); dr = ImageDraw.Draw(im)
        import io
        from export_pdf_grouped import _bg_fill_colors, _pix_png_nobg
        Z = fmm * sc
        clip = pymupdf.Rect(bx0 / fmm, by0 / fmm, bx1 / fmm, by1 / fmm)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(Z, Z), clip=clip, alpha=True)
        png = _pix_png_nobg(pix, _bg_fill_colors(page, cfg))
        tex = Image.open(io.BytesIO(png)).convert("RGBA")
        mask = Image.new("L", tex.size, 0)
        ImageDraw.Draw(mask).polygon([((x - bx0) * sc, (y - by0) * sc) for x, y in ext.coords], fill=255)
        tex.putalpha(Image.composite(tex.getchannel("A"), Image.new("L", tex.size, 0), mask))
        im.paste(tex, (int(mg), int(mg)), tex)
        dr.polygon([T(x, y) for x, y in ext.coords], outline=(20, 24, 32), width=3)
        fn = "front_preview.png"; im.save(os.path.join(d, fn))
        return jsonify(img=f"/api/img/{data['sid']}/{fn}", arms=len(segs), hole_mm=D,
                       W=W, H=H, mm_per_px=1.0 / sc, seg_len_px=D * sc,
                       contour=cont_px, segments=segs)
    except Exception as e:
        return jsonify(error=f"превью переда не удалось: {e}"), 400


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
        import math as _m
        page = pymupdf.open(out_pdf)[0]
        rr = page.rect
        z = 4200.0 / max(rr.width, rr.height)                        # чётче (длинная сторона ~4200px)
        mp = (rr.width * z) * (rr.height * z)
        if mp > 60e6:                                                # кап по пикселям (огромные раскладки 4200)
            z *= _m.sqrt(60e6 / mp)
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
    d = _sdir(sid)
    size = data.get("size", "2700")
    arms = data.get("arms", [])
    if int(data.get("n_arms", 0)) > 0:
        arms = _fill_tips(d, size, arms)                              # авто-кончик, если не задан
    cfg = _build_cfg(size, data.get("n_arms", 0), data.get("arms_identical", False), arms,
                     spare=data.get("spare"), back_copy=data.get("back_copy"))
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
