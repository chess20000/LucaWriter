from PIL import Image
import os

base_dir = os.path.dirname(os.path.abspath(__file__))

# 从项目根目录加载用户的 icon.png
src = os.path.join(base_dir, '..', 'icon.png')
img = Image.open(src).convert('RGBA')

sizes = [16, 24, 32, 48, 64, 128, 256]
images = []
for size in sizes:
    images.append(img.resize((size, size), Image.LANCZOS))

# 输出 .ico（多尺寸）
ico_path = os.path.join(base_dir, 'icon.ico')
images[0].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes], append_images=images[1:])
print(f'ICO saved: {ico_path} ({os.path.getsize(ico_path)} bytes)')

# 输出 .png（256x256）
png_path = os.path.join(base_dir, 'icon.png')
images[-1].save(png_path, format='PNG')
print(f'PNG saved: {png_path} ({os.path.getsize(png_path)} bytes)')
