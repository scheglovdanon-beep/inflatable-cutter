"""Печатный лист: ГОТОВЫЙ ВАРИАНТ (сборка, repack) сверху + РАСКЛАДКА НА ПЕЧАТЬ
(рулонный nesting 1440) снизу — в одном PNG. Оба рендерятся через texture_view с одним
и тем же конфигом (для печати флаги repack/roll_pack переопределяются на лету).
Запуск: venv/bin/python print_sheet.py <pdf> [--config config_new.yaml] [-o out/print_sheet.png]"""
import sys, os, subprocess, argparse
import yaml
from PIL import Image, ImageDraw


def _render(pdf, cfg_path, out_png):
    subprocess.run([sys.executable, "texture_view.py", pdf, "--config", cfg_path, "-o", out_png], check=True)
    return Image.open(out_png).convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_pdf")
    ap.add_argument("--config", default="config_new.yaml")
    ap.add_argument("-o", "--out", default="out/print_sheet.png")
    args = ap.parse_args()
    os.makedirs("out", exist_ok=True)

    # 1) ГОТОВЫЙ ВАРИАНТ — как в конфиге (repack-сборка со всеми деталями/подписями)
    top = _render(args.input_pdf, args.config, "out/_ps_top.png")

    # 2) РАСКЛАДКА НА ПЕЧАТЬ — рулонный nesting (ширина roll_width_mm, повороты разрешены)
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    cfg["repack"] = False
    cfg["roll_pack"] = True
    roll_cfg = "out/_ps_roll.yaml"
    yaml.safe_dump(cfg, open(roll_cfg, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
    bot = _render(args.input_pdf, roll_cfg, "out/_ps_bot.png")

    # 3) склейка вертикально: готовый вариант сверху, печать — ниже
    W = max(top.width, bot.width); hdr = 34; gap = 60
    H = hdr + top.height + gap + hdr + bot.height + 16
    im = Image.new("RGB", (W, H), (255, 255, 255)); d = ImageDraw.Draw(im)
    y = 8
    d.text((16, y), "GOTOVYI VARIANT (sborka)", fill=(0, 0, 0)); y += hdr - 6
    im.paste(top, (0, y)); y += top.height + gap
    d.line([(0, y - gap // 2), (W, y - gap // 2)], fill=(180, 180, 180), width=2)
    d.text((16, y), f"RASKLADKA NA PECHAT (rulon {cfg.get('roll_width_mm', 1440):.0f} mm)", fill=(0, 0, 0)); y += hdr - 6
    im.paste(bot, (0, y))
    im.save(args.out)
    print(f"saved {args.out} ({im.width}x{im.height})")


if __name__ == "__main__":
    main()
