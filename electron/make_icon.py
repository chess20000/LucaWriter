from PIL import Image, ImageDraw, ImageFont
import os

sizes = [16, 24, 32, 48, 64, 128, 256]
images = []

for size in sizes:
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    r = max(2, size // 8)
    draw.rounded_rectangle([0, 0, size-1, size-1], radius=r, fill='#bca55d')

    font_size = int(size * 0.6)
    try:
        font = ImageFont.truetype('arial.ttf', font_size)
    except:
        font = ImageFont.load_default()

    text = 'L'
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) / 2
    y = (size - th) / 2 - bbox[1]
    draw.text((x, y), text, fill='#111111', font=font)

    images.append(img)

base_dir = os.path.dirname(os.path.abspath(__file__))

ico_path = os.path.join(base_dir, 'icon.ico')
big_img = images[-1]
big_img.save(ico_path, format='ICO', sizes=[(s, s) for s in sizes])
print(f'ICO saved: {ico_path} ({os.path.getsize(ico_path)} bytes)')

png_path = os.path.join(base_dir, 'icon.png')
big_img.save(png_path, format='PNG')
print(f'PNG saved: {png_path} ({os.path.getsize(png_path)} bytes)')
