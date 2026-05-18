"""
LucaWriter 浏览器控制模块
提供轻量级浏览器自动化功能，让 AI 能够控制浏览器
"""

import os
import json
import base64
import threading
import subprocess
import urllib.request
from typing import Optional, Dict, Any, List, Callable
from urllib.parse import urljoin, urlparse

# 尝试导入 DrissionPage，如果未安装则提供安装提示
try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    HAS_DRISSION = True
except ImportError:
    HAS_DRISSION = False

# 全局浏览器实例
_browser_instance: Optional[Any] = None
_browser_lock = threading.Lock()
_browser_enabled = False
_browser_user_data_dir: Optional[str] = None
_IS_ELECTRON = bool(os.environ.get('BROWSER_DEBUG_PORT', ''))

# 浏览器操作回调（用于向前端发送操作日志）
_operation_callback: Optional[Callable[[str, Dict], None]] = None


def set_operation_callback(callback: Callable[[str, Dict], None]):
    """设置操作回调函数，用于向前端报告浏览器操作"""
    global _operation_callback
    _operation_callback = callback


def _notify_operation(action: str, data: Dict):
    """通知前端浏览器操作"""
    if _operation_callback:
        try:
            _operation_callback(action, data)
        except Exception:
            pass


def _which(name: str) -> Optional[str]:
    """查找可执行文件在 PATH 中的绝对路径。"""
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def is_browser_available() -> bool:
    """检查浏览器控制是否可用"""
    return HAS_DRISSION


def get_browser_status() -> Dict[str, Any]:
    """获取浏览器状态"""
    return {
        'available': HAS_DRISSION,
        'enabled': _browser_enabled,
        'running': _browser_instance is not None,
        'electron': _IS_ELECTRON,
        'library': 'DrissionPage' if HAS_DRISSION else None
    }


_CHROMIUM_CANDIDATES = [
    # Edge（Windows 自带）
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'msedge.exe',
    # Chrome
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'chrome.exe',
    # Brave
    r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
    r'brave.exe',
    # Chromium
    r'C:\Program Files\Chromium\Application\chrome.exe',
    r'chromium.exe',
    # Vivaldi
    r'C:\Program Files\Vivaldi\Application\vivaldi.exe',
    r'vivaldi.exe',
]


def _find_chromium_path() -> Optional[str]:
    """自动查找系统中可用的 Chromium 内核浏览器路径。"""
    for p in _CHROMIUM_CANDIDATES:
        norm = os.path.normpath(p)
        if os.path.isfile(norm):
            return norm
    for p in ['msedge.exe', 'chrome.exe', 'brave.exe', 'chromium.exe', 'vivaldi.exe']:
        found = _which(p)
        if found:
            return found
    return None


_ELECTRON_CTRL_PORT = int(os.environ.get('BROWSER_CTRL_PORT', '9224'))
_ELECTRON_CTRL_TOKEN = os.environ.get('BROWSER_CTRL_TOKEN', '')
_ELECTRON_TAB_ID = None  # 当前使用的 Electron 标签页 ID


def _find_electron_cdp_target() -> Optional[str]:
    """通过 Electron 内部浏览器控制 API 创建标签页，并返回其 CDP 目标 ID。"""
    global _ELECTRON_TAB_ID
    debug_port = os.environ.get('BROWSER_DEBUG_PORT', '')
    if not debug_port:
        return None
    try:
        # 创建新标签页
        ctrl_url = f'http://127.0.0.1:{_ELECTRON_CTRL_PORT}/tab/new'
        req = urllib.request.Request(ctrl_url)
        if _ELECTRON_CTRL_TOKEN:
            req.add_header('X-Ctrl-Token', _ELECTRON_CTRL_TOKEN)
        resp = urllib.request.urlopen(req, timeout=5)
        info = json.loads(resp.read().decode('utf-8'))
        if not info.get('ok'):
            return None
        _ELECTRON_TAB_ID = info.get('tabId', '')

        # 等待标签页出现在 CDP 列表中
        import time
        time.sleep(0.5)

        # 通过 CDP 查找新建的 about:blank 标签
        cdp_url = f'http://localhost:{debug_port}/json'
        req2 = urllib.request.Request(cdp_url)
        resp2 = urllib.request.urlopen(req2, timeout=3)
        targets = json.loads(resp2.read().decode('utf-8'))

        # 找 about:blank 页面（我们刚创建的标签）
        for t in targets:
            if t.get('type') == 'page' and t.get('url', '') == 'about:blank':
                return t.get('id')
        # 回退：找任意页面（排除 devtools）
        for t in targets:
            if t.get('type') == 'page' and 'devtools' not in t.get('url', ''):
                return t.get('id')
    except Exception:
        pass
    return None


def _default_browser_profile_dir() -> str:
    """源码启动模式下用的独立浏览器用户目录，避免与用户日常 Chrome 冲突。"""
    data_dir = os.environ.get('DATA_DIR')
    if not data_dir:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.normpath(os.path.join(script_dir, '..', 'usrdata'))
    p = os.path.join(data_dir, 'browser_profile')
    os.makedirs(p, exist_ok=True)
    return p


def _find_free_port() -> int:
    """随机分配一个空闲端口给 CDP 用，避免端口冲突。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _init_browser_standalone(user_data_dir: Optional[str] = None) -> tuple[bool, str]:
    """源码启动模式：用 DrissionPage 启动一个独立的本地 Chromium（headless 后台运行）。"""
    global _browser_instance, _browser_enabled, _browser_user_data_dir

    chrome_path = _find_chromium_path()
    if not chrome_path:
        return False, '未找到本地浏览器：请安装 Edge / Chrome / Brave 后重试'

    try:
        co = ChromiumOptions()
        co.set_browser_path(chrome_path)
        # 后台模式
        try:
            co.headless(True)
        except Exception:
            co.set_argument('--headless=new')
        # 降扰参数
        co.set_argument('--no-first-run')
        co.set_argument('--no-default-browser-check')
        co.set_argument('--disable-extensions')
        co.set_argument('--disable-blink-features=AutomationControlled')
        co.set_argument('--mute-audio')
        co.set_argument('--disable-gpu')
        # 独立用户数据目录，避免和用户日常 Chrome 冲突
        udd = user_data_dir or _default_browser_profile_dir()
        try:
            co.set_user_data_path(udd)
        except Exception:
            pass
        _browser_user_data_dir = udd
        # 独立 CDP 端口避免冲突
        try:
            co.set_local_port(_find_free_port())
        except Exception:
            pass

        _browser_instance = ChromiumPage(co)
        _browser_enabled = True
        _notify_operation('init', {'mode': 'standalone', 'success': True, 'headless': True})
        print(f'[browser] 源码模式：后台启动 {os.path.basename(chrome_path)}（headless）')
        return True, '浏览器后台启动成功'
    except Exception as e:
        _browser_instance = None
        _browser_enabled = False
        _notify_operation('init', {'mode': 'standalone', 'success': False, 'error': str(e)})
        return False, f'浏览器启动失败: {e}'


def init_browser(user_data_dir: Optional[str] = None) -> tuple[bool, str]:
    """初始化浏览器。
    - Electron 桌面版：通过 CDP 接管内置 Chromium 标签页
    - 源码启动：用 DrissionPage 启动独立本地 Chromium（headless 后台）
    """
    global _browser_instance, _browser_enabled, _browser_user_data_dir

    if not HAS_DRISSION:
        return False, '浏览器控制库未安装，请运行: pip install DrissionPage'

    with _browser_lock:
        if _browser_instance is not None:
            return True, '浏览器已初始化'

        if _IS_ELECTRON:
            electron_target_id = _find_electron_cdp_target()
            if electron_target_id:
                try:
                    debug_port = int(os.environ.get('BROWSER_DEBUG_PORT', '0'))
                    _browser_instance = ChromiumPage(debug_port, tab_id=electron_target_id)
                    _browser_enabled = True
                    _notify_operation('init', {'mode': 'electron_cdp', 'success': True})
                    return True, '浏览器初始化成功'
                except Exception as e:
                    print(f'[browser] Electron CDP 连接失败: {e}')
            return False, '无法连接浏览器，请重启 LucaWriter'

        # 源码启动：用 DrissionPage 启动独立 Chromium
        return _init_browser_standalone(user_data_dir)


def close_browser():
    """关闭浏览器"""
    global _browser_instance, _browser_enabled
    
    with _browser_lock:
        if _browser_instance:
            try:
                _browser_instance.quit()
            except Exception:
                pass
            _browser_instance = None
        _browser_enabled = False
    
    _notify_operation('close', {})


def ensure_browser() -> tuple[Any, Optional[str]]:
    """确保浏览器已启动，返回 (browser, error)"""
    global _browser_instance
    
    if not HAS_DRISSION:
        return None, '浏览器控制库未安装'
    
    if _browser_instance is None:
        success, msg = init_browser()
        if not success:
            return None, msg
    
    return _browser_instance, None


# ==================== 浏览器操作函数 ====================

def browser_navigate(url: str, wait_until: str = 'load', retry: int = 2) -> Dict[str, Any]:
    """导航到指定 URL。先尝试 tab.get()，失败则 ensure+retry。"""
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    for attempt in range(retry):
        browser, error = ensure_browser()
        if error:
            if attempt < retry - 1:
                close_browser()
                import time as _rt; _rt.sleep(1)
                continue
            return {'success': False, 'error': error}

        try:
            browser.get(url)
            if wait_until == 'networkidle':
                try:
                    browser.wait.load_start(timeout=15)
                except Exception:
                    pass
            result = {
                'success': True,
                'url': browser.url,
                'title': browser.title
            }
            _notify_operation('navigate', result)
            return result
        except Exception as e:
            if attempt < retry - 1:
                close_browser()
                import time as _rt2; _rt2.sleep(1)
                continue
            error_result = {'success': False, 'error': str(e)}
            _notify_operation('navigate', error_result)
            return error_result


def browser_get_text(selector: Optional[str] = None, max_length: int = 5000) -> Dict[str, Any]:
    """
    获取页面文本内容
    
    Args:
        selector: CSS 选择器（如果指定则获取该元素文本，否则获取整个页面）
        max_length: 最大返回字符数
    
    Returns:
        操作结果
    """
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        if selector:
            element = browser.ele(selector, timeout=5)
            if element:
                text = element.text
            else:
                return {'success': False, 'error': f'未找到元素: {selector}'}
        else:
            # 获取页面主要文本内容
            text = browser.ele('tag:body').text
        
        # 清理文本
        text = ' '.join(text.split())  # 去除多余空白
        
        # 截断
        truncated = len(text) > max_length
        display_text = text[:max_length] + ('...' if truncated else '')
        
        result = {
            'success': True,
            'text': display_text,
            'full_length': len(text),
            'truncated': truncated
        }
        _notify_operation('get_text', {'success': True, 'selector': selector, 'length': len(text)})
        return result
        
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('get_text', error_result)
        return error_result


def browser_click(selector: str, timeout: int = 5) -> Dict[str, Any]:
    """
    点击页面元素
    
    Args:
        selector: CSS 选择器
        timeout: 超时时间（秒）
    
    Returns:
        操作结果
    """
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        element = browser.ele(selector, timeout=timeout)
        if not element:
            return {'success': False, 'error': f'未找到元素: {selector}'}
        
        element.click()
        
        result = {'success': True, 'selector': selector}
        _notify_operation('click', result)
        return result
        
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('click', error_result)
        return error_result


def browser_fill(selector: str, value: str, timeout: int = 5) -> Dict[str, Any]:
    """
    填写输入框
    
    Args:
        selector: CSS 选择器
        value: 要填写的值
        timeout: 超时时间（秒）
    
    Returns:
        操作结果
    """
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        element = browser.ele(selector, timeout=timeout)
        if not element:
            return {'success': False, 'error': f'未找到元素: {selector}'}
        
        element.clear()
        element.input(value)
        
        result = {'success': True, 'selector': selector}
        _notify_operation('fill', result)
        return result
        
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('fill', error_result)
        return error_result


def browser_screenshot(full_page: bool = False) -> Dict[str, Any]:
    """
    截取浏览器截图
    
    Args:
        full_page: 是否截取整个页面
    
    Returns:
        操作结果（包含 base64 编码的图片）
    """
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        # 创建临时文件保存截图
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            temp_path = f.name
        
        if full_page:
            browser.get_screenshot(path=temp_path, full_page=True)
        else:
            browser.get_screenshot(path=temp_path)
        
        # 读取并编码为 base64
        with open(temp_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # 删除临时文件
        os.unlink(temp_path)
        
        result = {
            'success': True,
            'image_base64': image_data,
            'mime_type': 'image/png'
        }
        _notify_operation('screenshot', {'success': True, 'full_page': full_page})
        return result
        
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('screenshot', error_result)
        return error_result


def browser_scroll(direction: str = 'down', amount: int = 500) -> Dict[str, Any]:
    """
    滚动页面
    
    Args:
        direction: 滚动方向 ('up', 'down', 'left', 'right')
        amount: 滚动像素数
    
    Returns:
        操作结果
    """
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        if direction == 'down':
            browser.scroll.down(amount)
        elif direction == 'up':
            browser.scroll.up(amount)
        elif direction == 'left':
            browser.scroll.left(amount)
        elif direction == 'right':
            browser.scroll.right(amount)
        else:
            return {'success': False, 'error': f'未知方向: {direction}'}
        
        result = {'success': True, 'direction': direction, 'amount': amount}
        _notify_operation('scroll', result)
        return result
        
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('scroll', error_result)
        return error_result


def browser_find(keyword: str) -> Dict[str, Any]:
    """
    在页面中查找关键词
    
    Args:
        keyword: 要查找的关键词
    
    Returns:
        操作结果
    """
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        # 使用 JavaScript 查找
        script = f'''
        (function() {{
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            const results = [];
            let node;
            while (node = walker.nextNode()) {{
                if (node.textContent.toLowerCase().includes('{keyword.lower()}')) {{
                    const element = node.parentElement;
                    results.push({{
                        tag: element.tagName,
                        text: node.textContent.trim().substring(0, 200),
                        selector: element.id ? '#' + element.id : 
                                  element.className ? '.' + element.className.split(' ')[0] : 
                                  element.tagName.toLowerCase()
                    }});
                    if (results.length >= 5) break;
                }}
            }}
            return results;
        }})()
        '''
        
        results = browser.run_js(script)
        
        result = {
            'success': True,
            'keyword': keyword,
            'found_count': len(results),
            'matches': results
        }
        _notify_operation('find', result)
        return result
        
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('find', error_result)
        return error_result


def browser_get_links() -> Dict[str, Any]:
    """获取页面所有链接"""
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        links = []
        elements = browser.eles('tag:a')
        for ele in elements[:20]:  # 限制返回数量
            href = ele.attr('href')
            text = ele.text
            if href and text:
                links.append({
                    'text': text.strip()[:100],
                    'href': href
                })
        
        result = {
            'success': True,
            'count': len(links),
            'links': links
        }
        _notify_operation('get_links', result)
        return result
        
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('get_links', error_result)
        return error_result


def extract_bing_results() -> Dict[str, Any]:
    """从搜索结果页面提取结构化结果。用 DOM 结构而非类名，兼容 Bing 所有布局。"""
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}

    try:
        script = '''
        (function() {
            var results = [];
            var seen = {};

            function isResultLink(href, txt) {
                if (!href || !href.startsWith('http')) return false;
                if (href.includes('bing.com/ck/a') || href.includes('bing.com/search') || href.includes('microsoft.com/bing')) return false;
                if (href.includes('go.microsoft.com')) return false;
                if (txt.length < 6) return false;
                return true;
            }

            function addResult(url, title, snippet) {
                if (!url || seen[url]) return;
                seen[url] = true;
                results.push({index: results.length + 1, title: title.substring(0, 150), snippet: snippet.substring(0, 300), url: url});
            }

            // 方法1：找 h2 下的链接（Bing 最稳定的特征）
            var h2s = document.querySelectorAll('h2');
            for (var i = 0; i < h2s.length && results.length < 10; i++) {
                var a = h2s[i].querySelector('a[href^="http"]');
                if (a && isResultLink(a.href, (a.textContent||'').trim())) {
                    var parent = h2s[i].closest('li');
                    if (!parent) parent = h2s[i].parentElement;
                    while (parent && parent.children.length < 3) parent = parent.parentElement;
                    var snippet = '';
                    var snip = parent ? parent.querySelector('p') || parent.querySelector('.b_caption, .b_snippet, .b_paractl, .b_lineclamp2') : null;
                    if (snip) snippet = (snip.textContent||'').trim();
                    if (!snippet && parent) { var paras = parent.querySelectorAll('p'); for (var j=0;j<paras.length;j++) { if (paras[j].textContent.length > 20) { snippet = paras[j].textContent.trim(); break; } } }
                    addResult(a.href, (a.textContent||'').trim(), snippet);
                }
            }

            // 方法2：直接扫 #b_results 下的链接
            if (results.length === 0) {
                var container = document.querySelector('#b_results') || document.querySelector('ol') || document.body;
                var allLinks = container.querySelectorAll('a[href^="http"]');
                for (var j = 0; j < allLinks.length && results.length < 10; j++) {
                    var la = allLinks[j];
                    var lt = (la.textContent || '').trim();
                    if (isResultLink(la.href, lt)) {
                        addResult(la.href, lt, '');
                    }
                }
            }

            // 方法3：页面级别的 h2/a 匹配
            if (results.length === 0) {
                var raw = document.querySelectorAll('a[href^="http"]');
                for (var k = 0; k < raw.length && results.length < 10; k++) {
                    var ra = raw[k];
                    var rt = (ra.textContent || '').trim();
                    if (isResultLink(ra.href, rt) && rt.length > 8) {
                        addResult(ra.href, rt, '');
                    }
                }
            }

            return results;
        })()
        '''
        raw = browser.run_js(script)
        results = json.loads(json.dumps(raw)) if raw else []
        print(f'[extract_bing_results] found {len(results)} links')
        if results:
            for r in results[:5]:
                print(f'  [{r.get("index")}] {r.get("title","")[:60]}')
        _notify_operation('extract_results', {'count': len(results)})
        return {'success': True, 'results': results}
    except Exception as e:
        print(f'[extract_bing_results] ERROR: {e}')
        return {'success': False, 'error': str(e)}


def browser_click_search_result(index: int) -> Dict[str, Any]:
    """直接点击搜索结果的第 N 个 h2 链接（最可靠方式）。"""
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}

    try:
        script = f'''
        (function() {{
            var h2s = document.querySelectorAll('h2');
            var count = 0;
            for (var i = 0; i < h2s.length; i++) {{
                var a = h2s[i].querySelector('a[href^="http"]');
                if (a && !a.href.includes('bing.com/ck/a') && !a.href.includes('bing.com/search')) {{
                    count++;
                    if (count === {index}) {{
                        a.click();
                        return {{success: true, index: count, text: (a.textContent||'').trim().substring(0, 120), url: a.href}};
                    }}
                }}
            }}
            return {{success: false, error: '只有 ' + count + ' 个 h2 结果链接，找不到第 {index} 个'}};
        }})()
        '''
        result = browser.run_js(script)
        result = json.loads(json.dumps(result)) if result else {'success': False}
        _notify_operation('click_result', result)
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}


def browser_click_link_by_text(keyword: str) -> Dict[str, Any]:
    """在页面上查找包含关键词的链接，返回 URL（不执行点击，由调用方 navigate）。"""
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}

    try:
        k = json.dumps(keyword.lower())
        script = f'''
        (function() {{
            var kw = {k};
            var links = document.querySelectorAll('a[href^="http"]');
            var candidates = [];
            for (var i = 0; i < links.length; i++) {{
                var a = links[i];
                var txt = (a.textContent || '').toLowerCase();
                var href = a.href.toLowerCase();
                if (txt.includes(kw) || href.includes(kw.replace(/\\s+/g,''))) {{
                    if (!href.includes('bing.com') && !href.includes('microsoft.com/bing') && !href.includes('go.microsoft.com')) {{
                        candidates.push({{text: (a.textContent||'').trim().substring(0,120), url: a.href, score: (a.textContent||'').length}});
                    }}
                }}
            }}
            if (candidates.length === 0) return null;
            candidates.sort(function(a,b){{ return b.score - a.score; }});
            return candidates[0];
        }})()
        '''
        result = browser.run_js(script)
        result = json.loads(json.dumps(result)) if result else None
        if result and result.get('url'):
            _notify_operation('click_link', result)
            return {'success': True, 'text': result.get('text', ''), 'url': result['url']}
        return {'success': False, 'error': f'未找到包含关键词的链接: {keyword}'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def browser_go_back() -> Dict[str, Any]:
    """返回上一页"""
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        browser.back()
        result = {'success': True, 'url': browser.url}
        _notify_operation('go_back', result)
        return result
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('go_back', error_result)
        return error_result


def browser_refresh() -> Dict[str, Any]:
    """刷新页面"""
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        browser.refresh()
        result = {'success': True, 'url': browser.url}
        _notify_operation('refresh', result)
        return result
    except Exception as e:
        error_result = {'success': False, 'error': str(e)}
        _notify_operation('refresh', error_result)
        return error_result


def browser_evaluate(script: str) -> Dict[str, Any]:
    """
    在页面中执行 JavaScript
    
    Args:
        script: JavaScript 代码
    
    Returns:
        操作结果
    """
    browser, error = ensure_browser()
    if error:
        return {'success': False, 'error': error}
    
    try:
        result = browser.run_js(script)
        return {
            'success': True,
            'result': result
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ==================== AI Tools 定义 ====================

BROWSER_TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'browser_navigate',
            'description': '访问指定的网页 URL',
            'parameters': {
                'type': 'object',
                'properties': {
                    'url': {
                        'type': 'string',
                        'description': '要访问的网址，例如 https://www.example.com'
                    }
                },
                'required': ['url']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_get_text',
            'description': '获取当前页面的文本内容，用于阅读网页信息',
            'parameters': {
                'type': 'object',
                'properties': {
                    'selector': {
                        'type': 'string',
                        'description': '可选，CSS 选择器，用于指定获取特定元素的文本。如果不提供则获取整个页面的文本'
                    }
                }
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_click',
            'description': '点击页面上的元素，如按钮、链接等',
            'parameters': {
                'type': 'object',
                'properties': {
                    'selector': {
                        'type': 'string',
                        'description': 'CSS 选择器，例如 "#submit-button" 或 ".nav-link"'
                    }
                },
                'required': ['selector']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_fill',
            'description': '在输入框中填写文本，如搜索框、表单等',
            'parameters': {
                'type': 'object',
                'properties': {
                    'selector': {
                        'type': 'string',
                        'description': 'CSS 选择器，例如 "#search-input"'
                    },
                    'value': {
                        'type': 'string',
                        'description': '要填写的文本内容'
                    }
                },
                'required': ['selector', 'value']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_scroll',
            'description': '滚动页面以查看更多内容',
            'parameters': {
                'type': 'object',
                'properties': {
                    'direction': {
                        'type': 'string',
                        'enum': ['up', 'down', 'left', 'right'],
                        'description': '滚动方向，默认为 down'
                    },
                    'amount': {
                        'type': 'integer',
                        'description': '滚动的像素数，默认为 500'
                    }
                }
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_find',
            'description': '在页面中查找包含特定关键词的内容',
            'parameters': {
                'type': 'object',
                'properties': {
                    'keyword': {
                        'type': 'string',
                        'description': '要查找的关键词'
                    }
                },
                'required': ['keyword']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_get_links',
            'description': '获取当前页面上的所有链接，用于发现可点击的导航项'
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_go_back',
            'description': '返回上一页'
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'browser_refresh',
            'description': '刷新当前页面'
        }
    }
]


# ==================== 工具执行路由 ====================

def execute_browser_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行浏览器工具
    
    Args:
        tool_name: 工具名称
        arguments: 工具参数
    
    Returns:
        执行结果
    """
    tool_name = _normalize_tool_name(tool_name)
    
    # 参数名归一化
    args = dict(arguments)
    if 'q' in args and 'url' not in args and 'keyword' not in args:
        # web_search 风格：把 q 映射为合适参数
        if tool_name == 'browser_find':
            args['keyword'] = args.pop('q')
        elif tool_name == 'browser_navigate':
            q = args.pop('q')
            args['url'] = f'https://www.baidu.com/s?wd={q}'
    
    tools_map = {
        'browser_navigate': browser_navigate,
        'browser_get_text': browser_get_text,
        'browser_click': browser_click,
        'browser_fill': browser_fill,
        'browser_scroll': browser_scroll,
        'browser_find': browser_find,
        'browser_get_links': browser_get_links,
        'browser_go_back': browser_go_back,
        'browser_refresh': browser_refresh,
        'browser_evaluate': browser_evaluate,
    }
    
    if tool_name not in tools_map:
        return {'success': False, 'error': f'未知工具: {tool_name}'}
    
    if not _browser_enabled:
        return {'success': False, 'error': '浏览器控制未启用，用户需要先开启浏览器控制开关'}
    
    try:
        return tools_map[tool_name](**args)
    except Exception as e:
        return {'success': False, 'error': f'执行失败: {str(e)}'}


# ==================== 系统提示词扩展 ====================

BROWSER_SYSTEM_PROMPT_ADDITION = """

---

你可以为用户打开浏览器搜索网页。当用户询问需要查证的事实、专业术语、历史背景、技术细节、文化常识等内容时，主动输出：
[BROWSE]搜索关键词[/BROWSE]

选择搜索词时：
- 结合用户的小说时代背景选词。如：黑客题材搜"网络安全 钓鱼邮件"，工业革命背景搜"19世纪 邮政系统"
- 添加限定词，确保结果准确。如搜"计算机 邮件协议"而非单独搜"邮件"
- 用最容易找到准确答案的语言（中文或英文）

系统会在用户确认后打开浏览器，筛选有效信息后回报结果。

注意：只输出一个 [BROWSE] 标签，不要加其他格式字符。"""


def _normalize_tool_name(name: str) -> str:
    return name

def get_browser_enabled_prompt(base_prompt: str) -> str:
    """获取启用了浏览器功能的系统提示词"""
    if _browser_enabled:
        return base_prompt + BROWSER_SYSTEM_PROMPT_ADDITION
    return base_prompt
