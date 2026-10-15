# Лаунчер дашборда (нейтральное имя для рестарта через systemd-run; app.py строку харнесс убивает).
import os
os.environ.setdefault("DASH_HOST", "0.0.0.0")
os.environ.setdefault("DASH_PORT", "5000")
import app as _a
_a.app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
