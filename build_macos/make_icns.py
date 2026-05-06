import os
import sys
import subprocess
import tempfile
import shutil
from PIL import Image

ICON_SIZES = [16, 32, 64, 128, 256, 512, 1024]
RADIUS_RATIO = 0.18


def make_rounded(src_path, size=None):
    img = Image.open(src_path).convert('RGBA')
    w, h = img.size
    radius = int(w * RADIUS_RATIO)
    mask = Image.new('L', (w, h), 0)
    from PIL import ImageDraw
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)
    white_bg = Image.new('RGBA', (w, h), (255, 255, 255, 255))
    result = Image.composite(img, white_bg, mask)
    if size is not None:
        result = result.resize((size, size), Image.LANCZOS)
    return result


def create_icns(src_path, dst_path):
    iconset_dir = dst_path.replace('.icns', '.iconset')
    os.makedirs(iconset_dir, exist_ok=True)

    for size in ICON_SIZES:
        img = make_rounded(src_path, size)
        if size == 1024:
            img.save(os.path.join(iconset_dir, 'icon_512x512@2x.png'), format='PNG')
        elif size == 512:
            img.save(os.path.join(iconset_dir, 'icon_512x512.png'), format='PNG')
        elif size == 256:
            img_1x = make_rounded(src_path, 128)
            img.save(os.path.join(iconset_dir, 'icon_256x256@2x.png'), format='PNG')
            img_1x.save(os.path.join(iconset_dir, 'icon_128x128@2x.png'), format='PNG')
        elif size == 128:
            img.save(os.path.join(iconset_dir, 'icon_128x128.png'), format='PNG')
        elif size == 64:
            img.save(os.path.join(iconset_dir, 'icon_32x32@2x.png'), format='PNG')
        elif size == 32:
            img.save(os.path.join(iconset_dir, 'icon_32x32.png'), format='PNG')
            img_16 = make_rounded(src_path, 16)
            img_16.save(os.path.join(iconset_dir, 'icon_16x16@2x.png'), format='PNG')
        elif size == 16:
            img.save(os.path.join(iconset_dir, 'icon_16x16.png'), format='PNG')

    result = subprocess.run(
        ['iconutil', '-c', 'icns', iconset_dir, '-o', dst_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f'Error creating icns: {result.stderr}', file=sys.stderr)
        shutil.rmtree(iconset_dir, ignore_errors=True)
        sys.exit(1)

    shutil.rmtree(iconset_dir, ignore_errors=True)
    print(f'  icon.icns created ({os.path.getsize(dst_path)} bytes)')


def main():
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_icon = os.path.join(project_dir, 'icon.png')
    if not os.path.exists(src_icon):
        print(f'Source icon not found: {src_icon}', file=sys.stderr)
        sys.exit(1)

    build_dir = os.path.dirname(os.path.abspath(__file__))
    dst_path = os.path.join(build_dir, 'icon.icns')

    print(f'Generating macOS .icns icon...')
    print(f'  Source: {src_icon}')
    create_icns(src_icon, dst_path)
    print('Done!')


if __name__ == '__main__':
    main()
