from PIL import Image, ImageChops, ImageDraw
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_project_dir():
    candidates = []
    for base in (os.path.dirname(SCRIPT_DIR), os.getcwd(), os.environ.get('INIT_CWD', '')):
        if not base:
            continue
        base = os.path.abspath(base)
        if os.path.basename(base).lower() == 'electron':
            candidates.append(os.path.dirname(base))
        candidates.append(base)

    for candidate in candidates:
        if os.path.exists(os.path.join(candidate, 'icons', 'icon_gold.png')):
            return candidate
    return os.path.dirname(SCRIPT_DIR)


PROJECT_DIR = find_project_dir()
ROOT_DIR = os.path.join(PROJECT_DIR, 'electron')
if not os.path.isdir(ROOT_DIR):
    ROOT_DIR = SCRIPT_DIR
SOURCE_ICON = os.path.join(PROJECT_DIR, 'icons', 'icon_gold.png')
ROOT_ICON = os.path.join(PROJECT_DIR, 'icons', 'icon.png')
ICON_SCOPE = os.environ.get('LUCA_ICON_SCOPE', 'all').strip().lower()

SIZES_ICO = [16, 24, 32, 48, 64, 128, 256]
SIZES_PNG = [16, 32, 64, 128, 256, 512]
RADIUS_RATIO = 0.18
RESAMPLE = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS


def load_source(size=None):
    if not os.path.exists(SOURCE_ICON):
        raise FileNotFoundError(f'Source icon not found: {SOURCE_ICON}')

    img = Image.open(SOURCE_ICON).convert('RGBA')
    if img.width != img.height:
        side = min(img.width, img.height)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))

    if size is not None and img.size != (size, size):
        img = img.resize((size, size), RESAMPLE)
    return img


def apply_rounded_mask(img):
    w, h = img.size
    radius = int(min(w, h) * RADIUS_RATIO)
    mask = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)

    result = img.copy()
    result.putalpha(ImageChops.multiply(result.getchannel('A'), mask))
    return result


def make_rounded(dst_path, size=256):
    result = apply_rounded_mask(load_source(size))
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    result.save(dst_path, format='PNG')
    print(f'  {os.path.basename(dst_path)} ({result.size[0]}x{result.size[1]})')
    return result


def make_ico(rounded_img, dst_path):
    imgs = [apply_rounded_mask(load_source(s)) for s in SIZES_ICO]
    imgs[-1].save(dst_path, format='ICO', sizes=[(s, s) for s in SIZES_ICO], append_images=imgs[:-1])
    print(f'  ICO saved: {os.path.basename(dst_path)} ({os.path.getsize(dst_path)} bytes)')


def make_icon_set(name, target_dir, include_tray=False):
    print(f'[{name}]')
    os.makedirs(target_dir, exist_ok=True)

    png_path = os.path.join(target_dir, 'icon.png')
    rounded = make_rounded(png_path, 256)

    ico_path = os.path.join(target_dir, 'icon.ico')
    make_ico(rounded, ico_path)

    if include_tray:
        make_rounded(os.path.join(target_dir, 'tray-icon.png'), 64)


def main():
    print('LucaWriter Icon Generator (gold, transparent rounded corners)')
    print(f'Source: {SOURCE_ICON}')
    print(f'Scope: {ICON_SCOPE}')
    print(f'Radius ratio: {RADIUS_RATIO}')
    print()

    # 1. Root icon. Keep icons/icon_gold.png immutable as the canonical source.
    if ICON_SCOPE == 'all':
        print('[Root]')
        root_rounded = make_rounded(ROOT_ICON, 256)
        print()

    # 2. Electron icons
    electron_dir = os.path.join(ROOT_DIR)
    result_256 = apply_rounded_mask(load_source(256))

    png_path = os.path.join(electron_dir, 'icon.png')
    result_256.save(png_path, format='PNG')
    print(f'[Electron] icon.png (256x256)')

    ico_path = os.path.join(electron_dir, 'icon.ico')
    make_ico(result_256, ico_path)

    for size in SIZES_PNG:
        make_rounded(os.path.join(electron_dir, f'icon_{size}.png'), size)

    tray_path = os.path.join(electron_dir, 'tray-icon.png')
    make_rounded(tray_path, 64)

    loading_path = os.path.join(electron_dir, 'loading_icon.png')
    make_rounded(loading_path, 64)

    icon_loading_path = os.path.join(electron_dir, 'icon-loading.png')
    make_rounded(icon_loading_path, 64)
    print()

    # 3. Frontend icons
    if ICON_SCOPE == 'all':
        frontend_dir = os.path.join(PROJECT_DIR, 'frontend')
        make_icon_set('Frontend', frontend_dir, include_tray=True)
        print()

    # 4. Landing icons
    if ICON_SCOPE == 'all':
        landing_dir = os.path.join(PROJECT_DIR, 'landing')
        make_icon_set('Landing', landing_dir)
        print()

    print('All icons generated with transparent rounded corners!')


if __name__ == '__main__':
    main()
