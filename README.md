# LucaWriter

**桌面版下载**

[Windows 下载](https://gh-proxy.com/github.com/chess20000/LucaWriter/releases/latest/download/LucaWriter-Setup.exe)
macOS 版需要从源码构建（见下方说明）

项目主页：
[lucawriter.fun](https://lucawriter.fun/)

LucaWriter 是一款面向长篇小说与网文作者的写作辅助工具，通过 AI 构建每本书独立的知识库，帮你记住每一个人物、地点、事件和设定。

---

## 快速开始

### 从源码运行

需要 Python 3.11+（推荐 3.12，项目使用 `.python-version` 锁定 3.12.9）

```bash
pip install -r requirements.txt
python backend/main.py
```

浏览器访问 `http://127.0.0.1:10000`

首次使用需注册管理员账号。

### Docker 运行

```bash
docker build -t lucawriter .
docker run -p 10000:10000 -v $(pwd)/usrdata:/app/usrdata lucawriter
```

### macOS 桌面版

macOS 桌面版未提供预编译安装包，需要从源码自行构建：

```bash
cd electron
npm install
npm run build:mac
```

构建产物在 `release/` 目录下。

---

## 核心功能

### 🧠 知识库（AI 自动提取）

你写正文，Luca 在后台用大模型从中提取人物、地点、事件、规则，写入每本书独立的 SQLite 数据库。不需要你手动录入，知识库伴随写作过程自动生长。

- 结构化存储：人物、地点、事件、规则、伏笔各为独立表
- 支持别名、登场章节、关联关系
- 增量更新：每章写完自动将新事实并入知识库，无需重通读

### 🔍 语义索引

正文和摘要笔记分块后做 Embedding 向量化，存入 ChromaDB。向 Luca 提问时，系统自动从知识库和语义索引中检索相关片段注入上下文——不依赖关键词匹配，意思相近就能搜到。

### ⚡ 矛盾检测（吃书雷达）

写完章节后自动扫描新内容与已有设定，发现前后矛盾时弹出「吃书雷达」提醒。支持逐条深度核查——点击「你去确认」，Luca 引用原文出处分析矛盾点。

### 📖 摘要全书

通读全书，AI 逐章生成结构化摘要，自动合并为全书摘要笔记 `source.md` 和故事大纲 `outline.md`。

- 支持进度条、暂停、继续（可中断恢复）
- 流式调用 API，实时反馈进度
- 自适应批次大小，全书千万字也能逐步消化

### ✅ 本章写完

每章完成后点击「✓ 本章写完」或告诉 Luca「这章写好了」，AI 自动为该章生成摘要，增量更新到全书笔记。Luca 对内容的掌握度即时提升。

### 🔮 读者预言

基于全书笔记，模拟资深读者写长评，预测剧情走向。知识库变动后自动标记为「已过期」，支持手动刷新。

### 📋 大纲面板

维护世界观、人物、时间线、关键事件。AI 可基于现有内容生成大纲建议，作者一键采纳或修改。支持实体查找（🔍按钮直接在编辑器中定位）。

### 💬 AI 对话

所有窗口共享对话历史，一个 Luca 记住所有上下文：

- **统一历史**：系列、书本、不同窗口共享同一份对话记录，除非手动清除
- **焦点公告**：切换系列/书本/章节时，Luca 知道你的浏览位置
- **[READ_CHAPTER]**：Luca 需要正文时自主调用工具读取，支持一次最多 3 个章节
- **自动上下文压缩**：对话结束后闲时压缩，AI 生成摘要替代简单截断，保留最近消息
- **128K 上下文**：视模型而定，长对话自动压缩

### 🏷️ 标注系统

对话中可要求 Luca 在正文中添加荧光笔标注（黄/绿/粉/蓝），指出重点或批注。

### 🔎 编辑器内搜索替换

- Ctrl+F 打开搜索，Ctrl+H 打开搜索+替换
- F3/Ctrl+G 导航匹配项，Shift+F3/Ctrl+Shift+G 反向导航
- 搜索高亮与标注高亮共存，互不干扰

### 📚 多书本管理

- 书架视图：新建空白书本或导入现有小说
- 系列管理：多本书归入同一系列，共享对话历史
- 拖拽排序、改书名、删除（回收站可恢复）

### 📥 导入

支持 EPUB / TXT / MD / DOCX / PDF 格式导入，自动识别章节标题并拆分。

### 📤 导出

支持 Markdown、TXT、ZIP（JSON 源文件）、EPUB 四种格式导出。EPUB 导出时可自定义书名、作者、简介和封面。

### ⏳ 时间线可视化

从知识库中提取事件，以时间长河形式可视化展示：

- 细线脊骨 + 流光动画 + 彩色左边框泡泡
- 类型着色：人物=青、地点=琥珀、规则=紫、物品=橙
- 点击泡泡跳转到对应章节并定位原文片段
- 鼠标滚轮水平滚动

### 🤖 本地模型（可选）

支持本地 llama.cpp 推理，完全离线运行。首次启动自动检测硬件，按内存档位自动选型：

- **Tier A** (~8GB)：Qwen3.5-9B Q4_K_M
- **Tier B** (~18GB)：APEX I-Mini (35B MoE)

硬件不达标自动降级为 API 模式。仅 NVIDIA 显卡（≥8GB 显存）启用本地模型。

### ☁️ AI 提供商

支持配置多个 API 预设（LMStudio / DeepSeek / MiniMax 等），随时切换。支持调整模型、温度、最大 Token、系统提示词等参数。

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12, Flask |
| 知识库 | SQLite（结构化存储）+ ChromaDB（语义索引） |
| AI 嵌入 | 本地嵌入 / API 嵌入 双模式 |
| 前端 | 原生 HTML + CSS + JavaScript（无框架） |
| 桌面壳 | Electron（Win 打包 / macOS 构建） |
| 本地推理 | llama.cpp（仅 NVIDIA ≥8GB VRAM） |
| 容器 | Docker（slim 镜像） |

## 项目结构

```
lucawriter/
├── backend/
│   ├── main.py             # Flask 后端服务（路由、AI 对话、通读调度）
│   ├── kb_storage.py       # 知识库存储层（SQLite DAO + ChromaDB 封装）
│   ├── kb_pipeline.py      # 知识库 AI 流水线（提取/摘要/检测/时间线）
│   ├── embeddings.py       # 嵌入层（本地 + API 双模式）
│   └── browser_agent.py    # 浏览器自动化代理
├── frontend/
│   ├── index.html          # 主编辑器（单页应用）
│   └── login.html          # 登录页
├── electron/               # Electron 桌面壳
│   ├── main.js             # 窗口管理、后端进程管理
│   ├── preload.js          # 安全预加载脚本
│   ├── package.json        # 打包配置
│   └── build-venv/         # 构建用 Python 虚拟环境
├── landing/                # 项目主页（可静态托管至 Cloudflare Pages）
│   └── index.html          # 响应式 landing page
├── local_llm/              # 本地模型（可选，需自行放置 llama-server 和 GGUF）
├── builtin/                # 内置资源
├── usrdata/                # 用户数据（运行时自动生成）
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 构建
├── build.bat               # Windows 构建脚本
├── build_macos/            # macOS 构建辅助
└── CHANGELOG.md            # 版本日志
```

## 数据存储

所有用户数据存放在 `usrdata/` 目录下：

- `users.json` — 用户账户（PBKDF2-HMAC-SHA256 加密，5 次失败锁定 15 分钟）
- `settings.json` — 全局设置
- `books/` — 每本书独立目录（章节、知识库 `kb.db`、摘要笔记）
- `chat_history.json` — 统一 AI 对话历史（所有窗口共享）
- `chat_sessions/` — 会话管理
- `logs/` — 运行日志

### 初始化（清空所有数据）

```bash
rm -rf usrdata/*
```

### 重置密码

```bash
# 停止服务器后执行
rm usrdata/users.json
```

重置密码不影响书籍数据。LucaWriter 是本地单机应用，对数据文件的物理访问即代表最高权限。

## 开发

### 端口约定

- 开发测试：`http://127.0.0.1:10000`
- 构建版 Electron：`http://127.0.0.1:20000`

### 本地模型部署

1. 将 `llama-server` 可执行文件放入 `local_llm/` 目录
2. 将 GGUF 模型文件放入 `local_llm/models/` 目录
3. 启动 LucaWriter 后，在设置中选择「本地 Llama.cpp」预设

详细配置参考 `LOCAL_MODEL_DESIGN.md`。

或直接使用ollama/LMStudio等工具。

### 知识库兼容性

已有 `kb.db` 是用户花大量时间通读得到的核心资产。新增知识库功能时：
- 只做增量迁移和兼容查询
- 不要求重通读，不自动清空旧数据
- 只有用户明确点击重通读时才重建

详细设计参考 `KB_REWRITE_DESIGN.md`。

## 版本

当前版本：**v1.2.2**（查看 [CHANGELOG.md](CHANGELOG.md) 获取完整版本历史）

## 许可证

[Apache-2.0](LICENSE)

## 相关链接

- [项目主页](https://lucawriter.fun/)
- [GitHub 仓库](https://github.com/chess20000/LucaWriter)
- [GitHub Releases](https://github.com/chess20000/LucaWriter/releases/latest)
