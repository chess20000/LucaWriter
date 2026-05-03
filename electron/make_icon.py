from PIL import Image, ImageDraw
import os, shutil

base_dir = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(base_dir, '..', 'icon.png')

img = Image.open(src).convert('RGBA')
w, h = img.size

radius = int(w * 0.18)
mask = Image.new('L', (w, h), 0)
draw = ImageDraw.Draw(mask)
draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)

r, g, b, a = img.split()
from PIL import Image as _Img
a_data = a.load()
m_data = mask.load()
for y in range(h):
    for x in range(w):
        if m_data[x, y] == 0:
            a_data[x, y] = 0
result = Image.merge('RGBA', (r, g, b, a))

sizes = [16, 24, 32, 48, 64, 128, 256]
imgs = [result.resize((s, s), Image.LANCZOS) for s in sizes]

ico_path = os.path.join(base_dir, 'icon.ico')
imgs[-1].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
print('ICO saved:', ico_path, os.path.getsize(ico_path), 'bytes')

png_path = os.path.join(base_dir, 'icon.png')
result.save(png_path, format='PNG')
print('PNG saved:', png_path, os.path.getsize(png_path), 'bytes')
