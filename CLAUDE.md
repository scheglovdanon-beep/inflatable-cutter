# Inflatable Cutter — раскрой плоских надувных рекламных фигур

Python-конвейер: вход — векторный PDF-макет надувной фигуры (перёд+зад, иногда с машущей
рукой), выход — раскрой-лекала (перёд, зад, боковины-гассет, днище+ножка, рука по шаблону)
в PNG-превью / SVG / PDF с группами для CorelDRAW. Linux, без GUI, без Corel/Blender.

## Стек
Python 3 (venv в `venv/`). Ключевое: **pymupdf** (чтение вектора `get_drawings`), **shapely**
(геометрия/affine/split), **opencv-headless** (distanceTransform, convexityDefects,
connectedComponents), **scikit-image** (skeletonize), **rectpack** (nesting в рулон),
**cairosvg** (SVG→PDF), **Pillow** (превью), **numpy/scipy**, **PyYAML** (конфиги).
Установка: `python -m venv venv && venv/bin/pip install -r requirements.txt`.

## Карта проекта
**Перед ЛЮБОЙ правкой открывай `PROJECT_MAP.md`** — там шаги пайплайна `build_layout` →
функции (файл:строка), модули, раннеры, флаги конфигов, «что где менять». Все лекала из
2D-макета создаются ТОЛЬКО через единый вход `export_svg.build_layout`.

## Архитектура (слои)
- `src/pieces.py` — `Figure`/`build_figure`/`build_contour`: union заливок → закрытие щелей
  (buffer±) → один контур; масштаб `s` к `target_height_mm`. `contour_pt` — контур в PT (y вниз).
- `src/sides.py` — перёд/зад (`front_drawings`/`back_drawings` = левое/правое из 2 крупнейших тел),
  `get_front_figure`, `contour_to_mm`, `smooth_front` (морф. замыкание `front_smooth_mm`),
  `build_gusset`/`classify_sides`/`build_fitting_layout` (боковины), `add_back_panel` (зад),
  `_drop_background` (фон = заливка во всю страницу), `place_armpit_hole` (Ø250 на боковине).
- `src/arm.py` — РУКА: детект машущей руки и рез ОТ ПОДМЫШКИ (`detect_armpits`/`cut_across_arm`/
  `_refine_armpit`), детект КИСТИ (`_wrist_by_width` профиль ширины + `_snap_wrist_to_notches`
  привязка к точкам перехода; выбор в `_wrist_on_poly`, приоритет width>color>convexity),
  рука по ЭТАЛОННОМУ шаблону (`build_arm` из `examples/arm_template.pdf`, Ø250) + стыковка
  кисти (`build_hand`, `opening == верхнее ребро предплечья`), сборка `build_arm_from_poly`.
- `src/base.py` — днище Ø `fan_diameter_mm` + ножка `leg_height_mm` (150). `src/seam.py` —
  припуск `seam_allowance_mm` (10). `src/pack.py` — `nest_roll` (рулон 1440) / `repack_layout`.
- `src/side_color.py` — цвета боковин/руки/днища по 2D-макету; `src/read_pdf.py`,
  `src/geometry.py`, `src/layout.py`, `src/export.py` — утилиты.

## Раннеры и отладчики (превью — PNG, открываются как изображения)
- `texture_view.py <pdf> --config <cfg> -o out/x.png` — ПОЛНАЯ раскладка (перёд/зад/боковины/
  днище/рука/кисть + припуски), единый вход через `export_svg.build_layout`.
- `export_svg.py` / `export_pdf_grouped.py` — вектор SVG / PDF с группами под Corel.
- `arm_separate.py` — изолированно: перёд БЕЗ руки + отрезанная рука (отладка реза от подмышки).
- `hand_detect.py` — изолированно: рука + линия реза ЗАПЯСТЬЯ (детект кисти; red=выбранный метод,
  green=по ширине) + кисть/предплечье.
- `arm_template.py` — 4 детали руки (база/плечо/предплечье/кисть) + числовая стыковка рёбер.
- `main.py` / `sides_stage.py` / `hand_stage.py` — ранние стадии.
Запуск: `cd /opt/inflatable-cutter && venv/bin/python <script> ...`.

## Ключевые флаги конфигов (config_*.yaml)
`target_height_mm` (2700; D=1900), `front_smooth_mm`, `arm_cut: "armpit"` (рез руки от подмышки),
`arm_template: true` (рука по эталону), `expected_arms`, `make_back`, `roll_pack`/`repack`,
`arm_hole_diameter_mm` (250), `seam_allowance_mm` (10), `line_width_mm` (0.2). Пороги детекта:
`armpit_min_depth_frac`, `arm_max_frac`, `arm_min_aspect`, `wrist_width_search_frac`,
`wrist_notch_band_frac`, `wrist_notch_min_depth_frac`.

## Модель детали (плоская «подушка»)
Фигура = перёд + зад (зеркало/реальная задняя) + боковина-гассет по периметру. Машущая рука
режется из силуэта ОТ ПОДМЫШКИ (перпендикулярно оси руки), выносится отдельно и строится по
ЭТАЛОННОМУ шаблону: база (переход Ø250) → плечо → предплечье → кисть (перёд|боковина|зад),
рёбра стыкуются (цепочка π·250 → … → верхнее ребро предплечья = opening кисти).

## 5 эталонных макетов + новый
`/root/.claude/uploads/2524ea48-e07f-4aeb-aa69-d2352fd257d9/`: A=7dc36eab (KOOL, ладонь),
B=8fd394b5 (космонавт, палец вверх), C=7926cee4 (Охотники, 2 руки), D=42d29c67 (Chiropractic
1900, перчатка), E=48cc9f7c (National Furniture). Новый: c3468b00 (KOOL, 1 рука) → `config_new.yaml`.

## Конвенции
- Геометрия критична — любую правку детекта/реза ПРОВЕРЯТЬ визуально: гонять отладчик,
  открывать PNG через Read, смотреть глазами. «Зелёный» отчёт без картинки не принимать.
- Новое поведение — под флагом в конфиге; старые пути (космонавт `config.yaml`, chiro
  `config_chiro.yaml`) не ломать.
- Коммитить прогресс по ходу (фоновые процессы в этой среде рвутся при перезапуске — всё,
  что не в гите, теряется).
- Ветка текущей работы — `feature/arm-armpit-cut`.

## Текущее состояние (на момент настройки проекта)
Готово и проверено визуально: рез руки от подмышки на всех 5; детект кисти по профилю ширины +
снэп к точкам перехода (рез точно по запястью) на новом макете и A; рука по эталонному шаблону
со стыковкой кисти (Δ0%); полная раскладка без рулона. НУЖНО добить: проверка детекта кисти на
B/D/E/C (другая форма кисти — палец вверх, перчатка, 2 руки) и подстройка порогов где плохо.
```
python -m pytest tests/ -q      # тесты
```

## Память / OOM (важно — чат раньше умирал от этого)
Обработка макетов 2048² с ~7700 заливок раньше раздувала python до ~6 ГБ → OOM → системное
давление роняло remote-control чат (уходил в архив). Исправлено оптимизациями памяти (пик теперь
~0.5 ГБ). Все снижения — через флаги конфигов с дефолтами = старое поведение:
- `free_poly_cache` (деф. true) — сброс кэша shapely-полигонов `_poly` после построения контуров.
- `union_batch_size` (деф. 0=выкл; тяжёлым ставить `300`) — батчевый `unary_union` (меньше пик).
- `raster_zoom` (деф. 2.0; тяжёлым `1.5`) — масштаб растра переда (`front_raster`).
- `arm_raster_z` (деф. 5.0; тяжёлым `3.0`) — масштаб растров рук/кисти.
Эти флаги уже прописаны в `config_new.yaml`/`config_A..E.yaml`; `config.yaml`/`config_chiro` — на дефолтах.
**Тяжёлые прогоны запускать через `./run_capped.sh`** (cgroup-лимит 3 ГБ) — тогда при пике OOM убьёт
только скрипт, а не чат. Своп на сервере увеличен до 12 ГБ (swappiness 30).
