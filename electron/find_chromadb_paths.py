"""PyInstaller 构建辅助：输出 chromadb 和 chromadb_rust_bindings 的安装路径。
在 build.bat 中用 for /f 调用此脚本，避免引号嵌套问题。"""
import importlib, os, sys

for mod_name in ('chromadb', 'chromadb_rust_bindings'):
    try:
        mod = importlib.import_module(mod_name)
        pkg_dir = os.path.dirname(mod.__file__)
        print(f'{mod_name}={pkg_dir}')
    except Exception as e:
        print(f'{mod_name}=', file=sys.stderr)
        print(f'ERROR: 找不到 {mod_name}: {e}', file=sys.stderr)
        sys.exit(1)
