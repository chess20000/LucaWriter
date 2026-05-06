from PIL import Image, ImageDraw
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(ROOT_DIR)
ROOT_ICON = os.path.join(PROJECT_DIR, 'icon.png')

SIZES_ICO = [16, 24, 32, 48, 64, 128, 256]
RADIUS_RATIO = 0.18


def make_rounded(src_path, dst_path, size=None):
    img = Image.open(src_path).convert('RGBA')
    w, h = img.size
    radius = int(w * RADIUS_RATIO)

    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)

    white_bg = Image.new('RGBA', (w, h), (255, 255, 255, 255))
    result = Image.composite(img, white_bg, mask)

    if size is not None:
        result = result.resize((size, size), Image.LANCZOS)
    result.save(dst_path, format='PNG')
    print(f'  {os.path.basename(dst_path)} ({result.size[0]}x{result.size[1]})')
    return result


def make_ico(rounded_img, dst_path):
    imgs = [rounded_img.resize((s, s), Image.LANCZOS) for s in SIZES_ICO]
    imgs[-1].save(dst_path, format='ICO', sizes=[(s, s) for s in SIZES_ICO], append_images=imgs[:-1])
    print(f'  ICO saved: {os.path.basename(dst_path)} ({os.path.getsize(dst_path)} bytes)')


def make_icon_set(name, target_dir):
    print(f'[{name}]')
    os.makedirs(target_dir, exist_ok=True)

    png_path = os.path.join(target_dir, 'icon.png')
    rounded = make_rounded(ROOT_ICON, png_path)

    ico_path = os.path.join(target_dir, 'icon.ico')
    make_ico(rounded, ico_path)


def main():
    print('LucaWriter Icon Generator (transparent rounded corners)')
    print(f'Source: {ROOT_ICON}')
    print(f'Radius ratio: {RADIUS_RATIO}')
    print()

    # 1. Root icon (rounded source)
    print('[Root]')
    root_rounded = make_rounded(ROOT_ICON, ROOT_ICON)
    print()

    # 2. Electron icons
    electron_dir = os.path.join(ROOT_DIR)
    img = Image.open(ROOT_ICON).convert('RGBA')
    w, h = img.size
    radius = int(w * RADIUS_RATIO)

    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)

    white_bg = Image.new('RGBA', (w, h), (255, 255, 255, 255))
    result_256 = Image.composite(img, white_bg, mask)

    png_path = os.path.join(electron_dir, 'icon.png')
    result_256.save(png_path, format='PNG')
    print(f'[Electron] icon.png (256x256)')

    ico_path = os.path.join(electron_dir, 'icon.ico')
    make_ico(result_256, ico_path)

    tray_path = os.path.join(electron_dir, 'tray-icon.png')
    make_rounded(ROOT_ICON, tray_path, 64)

    loading_path = os.path.join(electron_dir, 'loading_icon.png')
    make_rounded(ROOT_ICON, loading_path, 64)

    icon_loading_path = os.path.join(electron_dir, 'icon-loading.png')
    make_rounded(ROOT_ICON, icon_loading_path, 64)
    print()

    # 3. Frontend icons
    frontend_dir = os.path.join(PROJECT_DIR, 'frontend')
    make_icon_set('Frontend', frontend_dir)
    print()

    # 4. Landing icons
    landing_dir = os.path.join(PROJECT_DIR, 'landing')
    make_icon_set('Landing', landing_dir)
    print()

    print('All icons generated with transparent rounded corners!')


if __name__ == '__main__':
    main()
