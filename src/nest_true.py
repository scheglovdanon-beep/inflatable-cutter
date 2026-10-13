"""
True-Shape Nesting (NFP) — укладка деталей по РЕАЛЬНОМУ контуру, а не по bbox.

Кандидатные позиции берутся из вершин No-Fit Polygon (Minkowski-сумма через pyclipper):
деталь «обтекает» вогнутости уже уложенных → заметно плотнее, чем сетка (nest_shapes)
или bbox-полки (repack_layout) / rectpack (nest_roll).

Интерфейс СОВМЕСТИМ с pack.nest_shapes:
    nest_true_shape(shapes, roll_h, gap) -> ({id: affine6}, width)
где shapes = [(id, shapely.Polygon)], affine6 = (a,b,d,e,xoff,yoff) — shapely-аффин,
двигающий ИСХОДНЫЙ полигон детали на место (поворот вокруг 0,0 + сдвиг). Контуры/цвета/
подписи детали не трогаются — наружу отдаётся только преобразование (как у nest_shapes).

Адаптировано по идеям pyFit (github badass-data-science/pyFit-agentic-polygon-nesting):
packer.py (bottom-left по кандидатам-вершинам NFP), nfp.py (Minkowski через pyclipper).
"""
import pyclipper
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely import affinity

SCALE = 10 ** 5                                                   # 0.01 мм точность для pyclipper (int)
ROT = {0: (1.0, 0.0, 0.0, 1.0), 90: (0.0, -1.0, 1.0, 0.0),
       180: (-1.0, 0.0, 0.0, -1.0), 270: (0.0, 1.0, -1.0, 0.0)}


def _ext(poly):
    """Внешнее кольцо как список точек без замыкающей (крупнейший полигон из Multi)."""
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    return list(poly.exterior.coords)[:-1]


def _nfp_candidates(stationary, moving_pts):
    """Вершины NFP = позиции t, при которых moving(+t) КАСАЕТСЯ stationary (не налазит).
    NFP = stationary ⊕ (−moving) (Minkowski). moving_pts — точки детали (реф = 0,0)."""
    A = [(round(x * SCALE), round(y * SCALE)) for x, y in _ext(stationary)]
    negB = [(round(-x * SCALE), round(-y * SCALE)) for x, y in moving_pts]
    if len(A) < 3 or len(negB) < 3:
        return []
    try:
        sol = pyclipper.MinkowskiSum(negB, A, True)               # pattern=−B над путём A
    except Exception:
        return []
    out = []
    for cont in sol:
        for x, y in cont:
            out.append((x / SCALE, y / SCALE))
    return out


def _place_order(infl, roll_h, rotations, grid_step):
    """Один прогон укладки для заданного порядка infl=[(sid, poly_инфл)]. Bottom-left по
    вершинам NFP (+ пристеночные/сеточные кандидаты как подстраховка). Возвращает (res, width)."""
    placed = []                                                   # [(poly, bounds, prepared)] — bbox-прунинг
    cur_w = 0.0; res = {}
    eps = 1e-6; tol = max((grid_step * 0.02) ** 2, 1.0)           # допуск «касание, не наложение»

    def feasible(cand):
        """Нет наложения с уже уложенными (касание ок). Сначала bbox, потом prepared, потом площадь."""
        cb = cand.bounds
        for sp, sb, spp in placed:
            if cb[0] > sb[2] - eps or cb[2] < sb[0] + eps or cb[1] > sb[3] - eps or cb[3] < sb[1] + eps:
                continue                                          # bbox не пересекаются
            if spp.intersects(cand) and sp.intersection(cand).area > tol:
                return False
        return True

    for sid, ip in infl:
        best = None                                               # (key, M, cand)
        for ang in rotations:
            a, b, d, e = ROT[ang]
            rp = affinity.affine_transform(ip, (a, b, d, e, 0.0, 0.0))
            rpts = _ext(rp)
            rb = rp.bounds; w = rb[2] - rb[0]; h = rb[3] - rb[1]
            if h > roll_h + eps:
                continue
            # --- кандидатные сдвиги t: угол (0,0) + вершины NFP + пристеночные ---
            cands = [(-rb[0], -rb[1])]
            if placed:
                tx0 = -rb[0]; cands.append((tx0, -rb[1]))
                for sp, sb, _ in placed:
                    cands += _nfp_candidates(sp, rpts)            # касание впритык/в вогнутость
                    cands.append((tx0, sb[3] - rb[1]))            # стопка у левой стены
            # --- сорт по (x,y), берём ПЕРВЫЙ допустимый (bottom-left) ---
            cands.sort(key=lambda t: (rb[0] + t[0], rb[1] + t[1]))
            local = None
            for tx, ty in cands:
                nb0 = rb[0] + tx; nb1 = rb[1] + ty
                if nb0 < -eps or nb1 < -eps or rb[3] + ty > roll_h + eps:
                    continue
                cand = affinity.affine_transform(rp, (1, 0, 0, 1, tx, ty))
                if feasible(cand):
                    local = ((nb0, nb1), (a, b, d, e, tx, ty), cand); break
            # подстраховка сеткой, если NFP ничего не дал (редко)
            if local is None:
                x = 0.0
                while x <= cur_w + w + grid_step and local is None:
                    y = 0.0
                    while y + h <= roll_h + eps:
                        cand = affinity.affine_transform(rp, (1, 0, 0, 1, x - rb[0], y - rb[1]))
                        if feasible(cand):
                            local = ((x, y), (a, b, d, e, x - rb[0], y - rb[1]), cand); break
                        y += grid_step
                    x += grid_step
            if local is not None and (best is None or local[0] < best[0]):
                best = local
        if best is None:                                          # не влезло ни в один угол
            a, b, d, e = ROT[0]; rb = ip.bounds
            M = (a, b, d, e, cur_w - rb[0], -rb[1])
            cand = affinity.affine_transform(ip, M); best = ((cur_w, 0.0), M, cand)
        _, M, cand = best
        res[sid] = M; placed.append((cand, cand.bounds, prep(cand)))
        cur_w = max(cur_w, cand.bounds[2])
    return res, cur_w


def nest_true_shape(shapes, roll_h, gap, rotations=(0, 90, 180, 270), orders=None):
    """True-shape nesting в полосу высотой roll_h. Пробует несколько порядков укладки
    (по площади/высоте/ширине/периметру) и возвращает САМЫЙ ПЛОТНЫЙ (минимальная ширина).
    shapes=[(id, Polygon)] -> ({id: affine6}, width). Зазор gap — через buffer(gap/2)."""
    if not shapes:
        return {}, 0.0
    # Расчётный контур: припуск buffer(gap/2) + УПРОЩЕНИЕ (меньше вершин -> быстрый NFP).
    # Оригинал детали в макете НЕ меняется — наружу идёт только аффин.
    stol = max(gap * 0.5, 3.0)
    base = []
    for sid, poly in shapes:
        p = poly.buffer(gap / 2.0, join_style=2, quad_segs=2)
        if isinstance(p, MultiPolygon):
            p = max(p.geoms, key=lambda g: g.area)
        p = p.simplify(stol, preserve_topology=True)
        t = stol
        while len(p.exterior.coords) > 90 and t < 50:            # адаптивно режем вершины крупных панелей
            t *= 1.7; p = p.simplify(t, preserve_topology=True)
        base.append((sid, orient(p)))
    grid_step = max(gap * 0.8, 25.0)

    def bb(p):
        b = p.bounds; return (b[2] - b[0], b[3] - b[1])
    keys = {
        "area": lambda t: -t[1].area,
        "height": lambda t: -bb(t[1])[1],
        "width": lambda t: -bb(t[1])[0],
        "perim": lambda t: -t[1].length,
        "maxside": lambda t: -max(bb(t[1])),
    }
    if orders is None:
        orders = ["area", "height", "maxside"]                   # 3 порядка — баланс плотность/скорость
    best_res, best_w = None, float("inf")
    for name in orders:
        infl = sorted(base, key=keys[name])
        res, w = _place_order(infl, roll_h, rotations, grid_step)
        if w < best_w:
            best_res, best_w = res, w
    return best_res, best_w
