#!/bin/bash
# build_figure.sh <pdf> <config> <name>
# Полный пайплайн «2D лекала -> 3D надувная фигура» (см. TZ_3D_PIPELINE.md).
# Этапы: 1) панели+текстуры  2) триангуляция кисти  3) тело (надув)  4) рука + сварка.
set -e
PDF="$1"; CFG="$2"; NAME="$3"
if [ -z "$PDF" ] || [ -z "$CFG" ] || [ -z "$NAME" ]; then
  echo "usage: build_figure.sh <macet.pdf> <config_*.yaml> <name>"; exit 1
fi
CUT=/opt/inflatable-cutter; PY=$CUT/venv/bin/python; BL=/opt/blender/blender
cd /opt/inflatable-3d
[ -f "$CFG" ] || CFG="$CUT/$CFG"                 # конфиг лежит в cutter
PDF="$(readlink -f "$PDF")"                       # абсолютный путь к макету (export делает chdir в cutter)

echo "=== [1/4] 2D панели + текстуры: $NAME ==="
$PY scripts/export_panels.py "$PDF" --config "$CFG" --name "$NAME"

echo "=== [2/4] триангуляция кисти ==="
$PY scripts/handbuild.py "$NAME"

echo "=== [3/4] тело (герметичный надутый баллон) ==="
NO_SPINE=1 NO_HOLE=1 MATERIAL=nostretch TENSION=120 SHEAR=90 BEND=0 PRESSURE=6 GRAVITY=0 \
  $BL -b --python blender/generate.py -- output/$NAME/panels.json

echo "=== [4/4] рука (цельная) + сварка с телом у подмышки ==="
FIG=$NAME $BL -b --python scripts/weld_figure.py

echo "=== ГОТОВО: output/${NAME}_figure/model.glb | смотреть /3d/${NAME}_figure ==="
