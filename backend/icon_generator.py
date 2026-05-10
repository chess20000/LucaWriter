"""
LucaWriter 主题色图标生成器
根据用户设置的主题色动态生成应用图标
"""

import os
import io
import base64
from PIL import Image, ImageDraw
import numpy as np

# 模板图标路径
TEMPLATE_ICON_PATH = os.path.join(os.path.dirname(__file__), '..', 'icon_template.png')
GOLD_ICON_PATH = os.path.join(os.path.dirname(__file__), '..', 'icon_gold.png')

# 缓存生成的图标
_icon_cache = {}


def hex_to_rgb(hex_color):
    """将十六进制颜色转换为 RGB 元组"""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join([c*2 for c in hex_color])
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def is_light_color(rgb):
    """判断颜色是否偏亮（浅色）
    使用亮度公式: Y = 0.299*R + 0.587*G + 0.114*B
    """
    r, g, b = rgb[:3]
    brightness = 0.299 * r + 0.587 * g + 0.114 * b
    return brightness > 128


def generate_themed_icon(theme_color_hex, size=None):
    """
    根据主题色生成图标
    
    逻辑:
    - 如果主题色偏白(浅色): 黑色像素保持黑色，灰色像素替换为主题色
    - 如果主题色偏黑(深色): 黑色像素先替换成白色，然后灰色像素替换为主题色
    
    Args:
        theme_color_hex: 主题色十六进制字符串 (如 '#e8c46c')
        size: 输出尺寸，None 表示保持原尺寸
    
    Returns:
        PIL.Image 对象
    """
    cache_key = f"{theme_color_hex}_{size}"
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]
    
    # 读取模板
    if not os.path.exists(TEMPLATE_ICON_PATH):
        # 如果模板不存在，使用金色图标作为回退
        if os.path.exists(GOLD_ICON_PATH):
            img = Image.open(GOLD_ICON_PATH).convert('RGBA')
        else:
            raise FileNotFoundError(f"Template icon not found: {TEMPLATE_ICON_PATH}")
    else:
        img = Image.open(TEMPLATE_ICON_PATH).convert('RGBA')
    
    if size:
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    
    data = np.array(img).astype(np.int32)
    theme_rgb = hex_to_rgb(theme_color_hex)
    theme_color = list(theme_rgb) + [255]
    
    # 判断主题色深浅
    is_light = is_light_color(theme_rgb)
    
    # 定义颜色范围
    # 黑色: RGB < 50
    # 灰色: 50 <= RGB < 200 (模板中的中性灰)
    # 其他: 透明或背景
    
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            r, g, b, a = data[i, j]
            
            if a < 100:  # 透明像素，跳过
                continue
            
            avg = (int(r) + int(g) + int(b)) / 3
            
            if avg < 50:
                # 黑色像素
                if is_light:
                    # 浅色主题: 保持黑色
                    pass
                else:
                    # 深色主题: 替换成白色
                    data[i, j] = [255, 255, 255, 255]
            elif 50 <= avg < 200:
                # 灰色像素 (模板中的中性灰) -> 替换为主题色
                data[i, j] = theme_color
            # 其他颜色保持不变
    
    result = Image.fromarray(data.astype(np.uint8))
    _icon_cache[cache_key] = result
    return result


def get_icon_base64(theme_color_hex=None, size=None):
    """
    获取图标的 base64 编码字符串
    
    Args:
        theme_color_hex: 主题色，None 表示使用金色默认图标
        size: 输出尺寸
    
    Returns:
        base64 字符串 (包含 data:image/png;base64, 前缀)
    """
    if theme_color_hex:
        img = generate_themed_icon(theme_color_hex, size)
    else:
        # 使用金色图标
        if os.path.exists(GOLD_ICON_PATH):
            img = Image.open(GOLD_ICON_PATH).convert('RGBA')
            if size:
                img = img.resize((size, size), Image.Resampling.LANCZOS)
        else:
            return None
    
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
    return f"data:image/png;base64,{img_base64}"


def get_icon_bytes(theme_color_hex=None, size=None):
    """
    获取图标的字节数据
    
    Args:
        theme_color_hex: 主题色，None 表示使用金色默认图标
        size: 输出尺寸
    
    Returns:
        bytes 对象
    """
    if theme_color_hex:
        img = generate_themed_icon(theme_color_hex, size)
    else:
        if os.path.exists(GOLD_ICON_PATH):
            img = Image.open(GOLD_ICON_PATH).convert('RGBA')
            if size:
                img = img.resize((size, size), Image.Resampling.LANCZOS)
        else:
            return None
    
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer.read()


def clear_cache():
    """清除图标缓存"""
    _icon_cache.clear()


# 预生成常用尺寸的缓存
def warmup_cache(theme_color_hex):
    """预热缓存，生成常用尺寸"""
    sizes = [16, 24, 32, 48, 64, 128, 256, 512]
    for size in sizes:
        generate_themed_icon(theme_color_hex, size)
