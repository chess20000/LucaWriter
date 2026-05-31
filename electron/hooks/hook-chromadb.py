"""PyInstaller hook for chromadb.

chromadb 大量使用动态导入（importlib.import_module + 配置系统按需加载），
PyInstaller 的静态分析无法完整检测其依赖树。
该 hook 使用 collect_all 枚举所有子模块确保打包完整。
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

# 收集 chromadb 所有子模块、数据文件和隐藏导入
datas, binaries, hiddenimports = collect_all('chromadb', include_py_files=True)

# 显式补充关键动态导入模块（确保即使配置系统换版本也不遗漏）
hiddenimports.extend([
    'chromadb.api.rust',
    'chromadb.api.segment',
    'chromadb.api.fastapi',
    'chromadb.api.async_fastapi',
])

# 移除可选的端到端测试模块和 notebook 集成（桌面版不需要）
_exclude = {
    'chromadb.test',
    'chromadb.notebook',
}
hiddenimports = [h for h in hiddenimports if not any(h.startswith(e) for e in _exclude)]
