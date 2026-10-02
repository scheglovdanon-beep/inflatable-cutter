# PROJECT_MAP — карта проекта inflatable-cutter

> Справочник «что где лежит и что менять». Открывать ПЕРЕД любой правкой, затем менять код.
> Все лекала создаются ТОЛЬКО через единый пайплайн `export_svg.build_layout` (см. ниже).
> Геометрию правим — проверяем визуально (гонять отладчик, открыть PNG через Read).

---

## 1. Единый пайплайн — `build_layout(page, cfg)` в `export_svg.py:85`

Любой вход (2D-макет PDF) → лекала идёт через эту функцию. Шаги и КУДА лезть, чтобы поменять:

| # | Шаг | Функция (файл:строка) | Что меняем здесь |
|---|-----|------------------------|------------------|
| 1 | Силуэт переда | `get_front_figure` → `build_figure` (`sides.py:111`, `pieces.py:94`) | как читается контур, union заливок, закрытие щелей |
| — | **Высота переда/зада** | `front_target_height_mm` (`sides.py:104`) | = `target_height_mm − leg_height_mm` (общий рост ВКЛЮЧАЕТ ножку) |
| 2 | Рез руки от подмышки | `detect_armpits`, `cut_across_arm` (`arm.py:408, 335`) | детект машущей руки, линия реза; пороги `armpit_*` |
| 3a | Перёд → мм + сглаж. | `contour_to_mm`, `smooth_front` (`sides.py:271, 277`) | `front_smooth_mm` (морф. замыкание) |
| 3b | Боковины-гассет | `build_gusset`, `classify_sides` (`sides.py:310, 482`) | толщина (460→350), выступы, симметрия низа, рёбра |
| 3c | Раскладка + днище | `build_fitting_layout`, `build_base` (`sides.py:531`, `base.py:12`) | компоновка листа; днище Ø`fan`, ножка `leg_height_mm` |
| 4 | Отверстие/кисть (старый путь) | `place_arm_hole`, `detect_wrist`, `build_hand` (`sides.py:184`, `arm.py:497, 636`) | только при `make_appendages:true` |
| 5 | Зад | `add_back_panel` → `get_back_figure` (`sides.py:200, 138`) | реальная задняя фигура, ниже руки |
| 6 | Сборка отрезанных рук | `build_arm_from_poly`, `place_arm`, `place_hand` (`arm.py:951, 707, 751`) | рука по шаблону: база→плечо→предплечье→кисть; `place_armpit_hole` (`sides.py:249`) |
| 7 | Запасная рука | `add_spare_arm` (`arm.py:1049`) | при `make_spare_arm:true` |
| 8 | Цвета | `color_sides` (`side_color.py:154`) | акцентные цвета боковин/руки/днища |
| 9 | Nesting | `nest_roll` / `repack_layout` (`pack.py:135, 176`) | рулон 1440 (`roll_pack`) или без наложений (`repack`) |
| 10 | Припуски | `add_seams`, `seam_rings` (`seam.py:13, 62`) | `seam_allowance_mm` (10) |
| 11 | Экспорт | `export_svg.main` / `export_pdf_grouped.py` / `texture_view.py` | SVG / PDF-группы Corel / PNG-превью |

---

## 2. Модули `src/` — ответственность + ключевые функции

### `pieces.py` — чтение вектора → контур, масштаб
- `Figure` (кл., :42) — контур, `s` (масштаб pt→pt), `contour_pt` (PT, y вниз).
- `build_contour` (:74), `build_figure` (:94) — union заливок → закрытие щелей → контур → масштаб к высоте.
- `_poly_of` (:20), `_bezier` (:14) — безье→полигон.

### `sides.py` — перёд/зад/боковины/раскладка (ЦЕНТРАЛЬНЫЙ модуль)
- Чтение тел: `front_drawings` (:81), `back_drawings` (:116) — левое/правое из 2 крупнейших тел; `_drop_background` (:63).
- Фигуры: `get_front_figure` (:111), `get_back_figure` (:138), `front_target_height_mm` (:104), `contour_to_mm` (:271), `smooth_front` (:277).
- Классификация заливок front/back: по ближайшему телу + буфер `front.buffer(2)`; флаг `front_all_details` → полный nearest-body (ВСЕ детали: локоть, шланг, брызги в контуре). cfg прокинут в контур (`get_front/back_figure`) и в рендереры текстуры.
- Ложную «руку» (шланг/брызги — геометрия как у руки) отсекает ЦВЕТ: `build_layout` при кандидатов>expected_arms оставляет самые тёплые по `arm_fill_color` (R−B: кожа/рукав R≫B, вода/шланг B≫R).
- Боковины: `build_gusset` (:310), `classify_sides` (:482), `build_strips` (:468), `_hourglass` (:503), `detect_protrusions` (:23).
- Раскладка: `build_fitting_layout` (:531), `_pack_columns` (:492).
- Отверстия: `place_arm_hole` (:184), `place_arm_hole_xy` (:233), `place_armpit_hole` (:249), `detect_neck` (:654).
- Зад: `add_back_panel` (:200), `back_texmap` (:226).
- Рендеры-отладчики: `render_fitting` (:598), `render_neck` (:698), `render_sew_map` (:739), `render_debug` (:765).

### `arm.py` — РУКА (детект, рез, сборка по шаблону)
- Рез от подмышки: `detect_armpits` (:408), `_refine_armpit` (:360), `cut_across_arm` (:335), `blade_perp` (:315), `blade_pair` (:327).
- Детект кисти (запястье): `_wrist_by_width` (:200, профиль ширины), `_snap_wrist_to_notches` (:269), `_contour_notches` (:254), `detect_wrist` (:497), `detect_wrist_color` (:808), `_wrist_on_poly` (:901, выбор метода), `_wrist_score` (:877).
- Рез/разделение: `cut_arm` (:575), `_top_edge` (:600).
- Сборка по шаблону: `build_arm` (:67, эталон Ø250), `build_arm_from_poly` (:951), `build_arm_measured` (:994), `build_hand` (:636), `_develop_frustum` (:783), `measure_edges` (:36).
- Размещение: `place_arm` (:707), `place_hand` (:751), `place_arm_piece` (:473), `add_spare_arm` (:1049).
- Геометрия руки: `arm_shoulder_xy` (:860), `local_arm_thickness` (:295), `_perp_chord` (:184).
- Отладка: `render_hand` (:1082).

### `base.py` — днище + ножка
- `build_base` (:12) — днище (длина=нижняя кромка, ширина=`fan`), отверстие вентилятора Ø`fan`, ножка `leg_height_mm` × π·Ø. `render_base` (:28).

### `seam.py` — припуски
- `add_seams` (:13), `seam_rings` (:62), `_out` (:9, buffer наружу), `draw_dashed` (:46).

### `pack.py` — nesting
- `nest_roll` (:135, рулон), `repack_layout` (:176, без наложений), `_units` (:11).

### `side_color.py` — цвета
- `color_sides` (:154), `front_raster` (:71), `arm_fill_color` (:98, цвет рукава), `hand_fill_color` (цвет кисти → полоска-боковина, near-чёрный контур исключается), `base_fill_color`, `strip_band_quads`, `_accentize` (:41).
- Полоска-боковина кисти (`src=None`) красится `hand_fill_color` во ВСЕХ рендерерах: `texture_view.py`, `export_svg.py`, `export_pdf_grouped.py`.
- Цвет боковин: `_accentize` рубит малонасыщенное (<`accent_sat_min`) в белый; `_simplify_bands` (:17) сливает близкие оттенки; `_merge_small_bands` схлопывает скопления мелких зон (<`band_merge_small_frac` доли) в доминирующий цвет.

### Вспомогательные
- `geometry.py` — `scale_to_height` (:22), `smooth` (:41), `validate` (:56).
- `read_pdf.py` — `read_design` (:91), `split_figures` (:73), `_drawing_to_polygon` (:49).
- `export.py` — `save_pdf/svg/png`, `save_fitting_svg` (:162), `save_base_*`, `save_sides_*`.
- `layout.py` — пусто/утиль.

---

## 3. Раннеры и отладчики (превью PNG → открывать через Read)

| Скрипт | Что делает |
|--------|------------|
| `texture_view.py <pdf> --config <cfg> -o out/x.png` | ПОЛНАЯ раскладка (всё + припуски), единый вход `build_layout` |
| `export_svg.py` | вектор SVG (текстура через аффин контур→панель) |
| `export_pdf_grouped.py` | PDF с группами под CorelDRAW |
| `arm_separate.py` | перёд БЕЗ руки + отрезанная рука (отладка реза от подмышки) |
| `hand_detect.py` | рука + линия реза запястья (red=выбранный метод, green=по ширине) |
| `arm_template.py` | 4 детали руки (база/плечо/предплечье/кисть) + стыковка рёбер |
| `main.py` / `sides_stage.py` / `hand_stage.py` | ранние стадии |

Запуск: `cd /opt/inflatable-cutter && venv/bin/python <script> ...`
Тесты: `venv/bin/python -m pytest tests/ -q` (26 тестов; `test_clip.py`/`test_etap1.py` дёргают `build_figure`/`scale_to_height` напрямую с явным 2700 — высоту-через-cfg не трогают).

---

## 4. Конфиги `config_*.yaml` — ключевые флаги

**Макеты:** `config.yaml` (космонавт), `config_chiro.yaml` (chiro), `config_A..E.yaml` (5 эталонов), `config_new.yaml` (новый KOOL, 1 рука).

**Габариты:** `target_height_mm` (2700, ВКЛ. ножку), `leg_height_mm` (150), `fan_diameter_mm`/`base_diameter_mm` (460), `main_width_mm` (350), `tip_width_mm` (250), `arm_hole_diameter_mm` (250), `seam_allowance_mm` (10), `line_width_mm` (0.2), `front_smooth_mm`.

**Переключатели путей:** `arm_cut: "armpit"` (рез руки от подмышки), `arm_template: true` (рука по эталону), `arm_from_contour`, `expected_arms`/`max_arms`, `make_appendages`/`make_hand`/`make_spare_arm`/`make_back`/`make_base`, `wrist_by_color`, `arm_hole_at_shoulder`, `roll_pack`/`repack` (+`roll_width_mm` 1440), `mirror_back`.

**Пороги детекта:** `armpit_min_depth_frac`, `armpit_min_arm_frac`, `arm_max_frac`, `arm_min_aspect`, `blade_min_frac`, `armpit_center_frac`, `armpit_tip_free_frac`, `armpit_pair_local_frac`, `armpit_refine_aspect_relax`, `armpit_close_px`, `wrist_width_search_frac`, `wrist_notch_band_frac`, `wrist_notch_min_depth_frac`. Фон: `bg_page_frac`, `bg_soft_frac`.

---

## 5. Эталонные макеты (входы PDF)

`/root/.claude/uploads/2524ea48-e07f-4aeb-aa69-d2352fd257d9/`:
- A=`7dc36eab` (KOOL, ладонь), B=`8fd394b5` (космонавт, палец вверх), C=`7926cee4` (Охотники, 2 руки),
  D=`42d29c67` (Chiropractic 1900, перчатка), E=`48cc9f7c` (National Furniture),
  новый=`c3468b00` (KOOL, 1 рука → `config_new.yaml`).
- Локальные эталоны: `examples/arm_template.pdf` (шаблон руки Ø250), `examples/cosmonaut_2700.pdf`, `examples/chiro.pdf`.

---

## 6. Конвенции (из CLAUDE.md)

- Геометрию ПРОВЕРЯТЬ визуально — «зелёный» отчёт без картинки не принимать.
- Новое поведение — под флагом; старые пути (космонавт/chiro) не ломать (кроме явной общей правки).
- Коммитить прогресс по ходу (фоновые процессы рвутся при перезапуске).
- Рабочая ветка: `feature/arm-armpit-cut`.

## 7. Известные проблемы
- `build_layout` на пути БЕЗ `arm_cut:armpit` (космонавт `config.yaml`, `make_appendages:true`) падает
  `UnboundLocalError: place_arm` — из-за локального `from arm import ... place_arm` ниже по функции
  (`export_svg.py:139`), затеняющего обращение на `:116`. Предсуществующий баг, не связан с высотой.
