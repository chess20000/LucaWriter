from PIL import Image
import os, shutil

base_dir = os.path.dirname(os.path.abspath(__file__))

# 从项目根目录加载用户的 icon.png
src = os.path.join(base_dir, '..', 'icon.png')

# 直接复制 PNG（不经过 Pillow 重新编码，保持原始品质）
png_path = os.path.join(base_dir, 'icon.png')
shutil.copy2(src, png_path)
print(f'PNG copied: {png_path} ({os.path.getsize(png_path)} bytes)')

# 生成多尺寸 .ico
img = Image.open(src).convert('RGBA')
sizes = [16, 24, 32, 48, 64, 128, 256]
images = [img.resize((size, size), Image.LANCZOS) for size in sizes]

ico_path = os.path.join(base_dir, 'icon.ico')
images[-1].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes], append_images=images[:-1])
print(f'ICO saved: {ico_path} ({os.path.getsize(ico_path)} bytes)')
