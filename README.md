# LucaWriter

[Windows安装包下载](https://gh-proxy.com/github.com/chess20000/LucaWriter/releases/download/v0.7.0/LucaWriter-Setup-0.7.0.exe)

项目主页：[lucawriter.pages.dev](https://lucawriter.pages.dev/)

LucaWriter是一个ai辅助的小说写作专用软件，为每个想要写小说的人设计。\
LucaWriter的设计哲学是：艺术作品应该全部由人类完成。\
ai可以读取，给出建议，但不应对作品本身有任何的写入操作。\
基于这个哲学，本项目的ai功能将全部围绕“读”来展开。\
新手刚开始萌生写小说的念头，稍微深入思考，可能就会碰到一个问题——吃书怎么办。\
LucaWriter就是为了解决这个问题的。LucaWriter的终极目标是，充分利用未来长上下文模型的优势，让作者能够充分发挥天马行空的想象力，尽可能不需要在提出新设定的时候瞻前顾后，不需要思考何时更新大纲才不会打断创作心流的状态，只要大胆写就可以了。ai会在适时自动发消息警告作者吃书了，或者作者想不起来什么设定也可以立刻询问，最高效率地获得答案。\
LucaWriter本身不会占用很多系统性能。你可以选择使用云端模型，也可以选择本地模型。本地模型的话，就会对电脑性能提出要求。推荐Qwen3.6 27B或35B A3B，显存不够，一些9B级别的模型也可以用。但太小的模型不具备实用价值，可能看不懂你写的段子和伏笔之类的。

## 快速开始

### 本地直接运行

```bash
pip install -r requirements.txt
python backend/main.py
```

然后打开浏览器访问 `http://localhost:20000`

> 需要 Python 3.11+（<https://www.python.org/downloads/）>

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

