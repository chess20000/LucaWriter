# LucaWriter
**桌面版下载**
[Windows下载](https://gh-proxy.com/github.com/chess20000/LucaWriter/releases/latest/download/LucaWriter-Setup.exe) · [macOS下载](https://gh-proxy.com/github.com/chess20000/LucaWriter/releases/latest/download/LucaWriter-mac-arm64.dmg)

项目主页：[lucawriter.fun](https://lucawriter.fun/)

## 使用源码

### 本地运行

```bash
pip install -r requirements.txt
python backend/main.py
```

浏览器访问 `http://127.0.0.1:10000`

> 需要 Python 3.11+（<https://www.python.org/downloads/>）

### Docker 运行

```bash
docker build -t lucawriter .
docker run -p 10000:10000 -v $(pwd)/usrdata:/app/usrdata lucawriter
```

## 核心功能

- **知识库** — AI 从正文中自动提取人物、地点、事件、规则，存入每本书独立的 SQLite 数据库，无需手动录入
- **语义索引** — 正文和笔记分块向量化，支持语义搜索，意思相近就能搜到
- **矛盾检测（吃书雷达）** — 写完章节自动扫描新内容与已有设定，发现前后矛盾时弹窗提醒，支持逐条深度核查
- **摘要全书** — AI 逐章生成摘要，自动合并为全书摘要笔记 `source.md` 和故事大纲 `outline.md`
- **本章写完** — 每章完成后生成单章摘要，增量更新到全书笔记，支持 AI 主动触发
- **读者预言** — 基于全书笔记模拟资深读者写长评，预测剧情走向
- **大纲面板** — 维护世界观、人物、时间线、关键事件，AI 可一键采纳或修改建议
- **AI 对话** — 统一对话历史（一个 Luca，所有窗口共享），支持 128K 上下文和自动压缩
- **标注系统** — AI 可在正文中添加荧光笔批注（黄/绿/粉/蓝）
- **编辑器内搜索替换** — Ctrl+F 搜索，Ctrl+H 替换，F3 导航
- **多书本管理** — 书架视图，支持系列、改书名、拖拽排序
- **导入** — EPUB / TXT / MD / DOCX / PDF
- **导出** — Markdown / TXT / ZIP / EPUB

## 数据存储

所有用户数据（书本、账户、配置）存放在 `usrdata/` 目录下。

**初始化（清空所有数据）：**
```bash
rm -rf usrdata/*
```

**重置密码：**
```bash
# 停止服务器后执行
rm usrdata/users.json
```

重置密码不影响书籍数据。LucaWriter 是本地单机应用，对数据文件的物理访问即代表最高权限。

## 项目结构

```
lucawriter/
├── backend/
│   ├── main.py             # 后端服务
│   ├── kb_storage.py       # 知识库存储（SQLite + ChromaDB）
│   └── kb_pipeline.py      # 知识库 AI 流水线（提取/摘要/检测）
├── frontend/
│   ├── index.html          # 桌面端编辑器
│   └── login.html          # 登录页
├── electron/               # Electron 桌面壳
│   ├── main.js             # 窗口管理
│   └── package.json        # 打包配置
├── landing/                # 项目主页（可静态托管）
├── local_llm/              # 本地模型（可选，需自行放置 llama-server 和模型）
├── usrdata/                # 用户数据（运行时自动生成）
├── requirements.txt        # Python 依赖
└── Dockerfile              # Docker 构建文件
```
