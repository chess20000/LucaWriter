# LucaWriter

AI 辅助长篇写作工具。

## 快速开始

### 本地直接运行

```bash
pip install -r requirements.txt
python backend/main.py
```

然后打开浏览器访问 `http://localhost:20000`

> 需要 Python 3.11+（https://www.python.org/downloads/）

### Docker 运行

```bash
docker build -t lucawriter .
docker run -p 20000:20000 -v $(pwd)/usrdata:/app/usrdata lucawriter
```

## 数据存储

所有用户数据（书本、账户、配置）都存放在项目目录下的 `usrdata/` 文件夹中。

**初始化（清空所有数据）：**
```bash
rm -rf usrdata/*
```

## 功能

- **AI 实时写作建议** — 输入时自动触发
- **AI 记忆大纲** — 自动更新全局记忆
- **通读全书** — AI 逐章阅读，生成 `source.md` 全书笔记和 `outline.md` 大纲
- **本章写完** — 单章完成后一键触发 AI 通读摘要，或让 AI 自动识别并调用
- **读者预言模式** — 基于全书笔记推测未来剧情
- **时间线生成** — 梳理故事内时间线节点
- **多书本管理 + 书架视图** — 支持改书名、导出、删除
- **导入** — EPUB / TXT / MD / DOCX / PDF
- **导出** — Markdown / TXT / ZIP / EPUB
- **标注系统** — AI 可在正文中添加荧光笔批注

## 项目结构

```
lucawriter/
├── backend/
│   └── main.py           # 后端服务
├── frontend/
│   ├── index.html        # 桌面端编辑器
│   └── login.html        # 登录页
├── local_llm/            # 本地模型（可选，需自行放置 llama-server 和模型）
├── usrdata/              # 用户数据（运行时自动生成）
├── requirements.txt      # Python 依赖
├── Dockerfile            # Docker 构建文件
└── README.md             # 本文件
```
