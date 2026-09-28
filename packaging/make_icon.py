"""生成应用图标（朱红印章 +「王」字）。需要 Pillow 和一款中文字体：

    python packaging/make_icon.py /path/to/cjk-font.ttc
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEAL = (176, 58, 46)
SIZE = 1024


def render(font_path, char="王"):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 56
    draw.rounded_rectangle((pad, pad, SIZE - pad, SIZE - pad), radius=200, fill=SEAL)
    inset = pad + 58
    draw.rounded_rectangle((inset, inset, SIZE - inset, SIZE - inset), radius=140,
                           outline=(255, 244, 236), width=22)
    font = ImageFont.truetype(font_path, 600)
    box = draw.textbbox((0, 0), char, font=font)
    x = (SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (SIZE - (box[3] - box[1])) / 2 - box[1]
    # 字体笔画较细，加描边加粗，缩小到 16px 时仍清晰
    draw.text((x, y), char, font=font, fill=(255, 250, 245), stroke_width=30, stroke_fill=(255, 250, 245))
    return img


def main(font_path):
    img = render(font_path)
    assets = os.path.join(ROOT, "packaging", "assets")
    os.makedirs(assets, exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(os.path.join(assets, "icon.png"))
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.join(ROOT, "wenzhen", "static", "icon.png"))
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(os.path.join(assets, "icon.ico"), sizes=ico_sizes)
    # 桌面版窗口图标：Windows 只接受 .ico；用 BMP 帧，兼容各版本 .NET
    img.save(os.path.join(ROOT, "wenzhen", "static", "icon.ico"), sizes=ico_sizes[:5], bitmap_format="bmp")
    img.save(os.path.join(assets, "icon.icns"))
    print("已生成图标：", assets)


if __name__ == "__main__":
    main(sys.argv[1])
