# LucaWriter 项目全景（概览 for AI agents）

> ⚠️ **每次修改代码后，必须同步更新本文件**，确保所有 AI agent 拿到的是最新可信的代码地图。
>
> 本文件是元文档，不描述"应该怎样"，只描述"现在实际怎样"。

---

## 目录

1. [项目定位与核心概念](#1-项目定位与核心概念)
2. [目录结构](#2-目录结构)
3. [后端架构总览（main.py）](#3-后端架构总览mainpy)
4. [知识库系统（kb_storage.py）](#4-知识库系统kb_storagepy)
5. [知识库管道（kb_pipeline.py）](#5-知识库管道kb_pipelinepy)
6. [前端架构总览（index.html）](#6-前端架构总览indexhtml)
7. [前端 JS 函数分类清单](#7-前端-js-函数分类清单)
8. [嵌入系统（embeddings.py）](#8-嵌入系统embeddingspy)
9. [COO 格式与溯源](#9-coo-格式与溯源)
10. [浏览器控制模块](#10-浏览器控制模块)
11. [本地 LLM 服务器控制](#11-本地-llm-服务器控制)
12. [Electron 桌面端](#12-electron-桌面端)
13. [Landing Page](#13-landing-page)
14. [开发约定](#14-开发约定)

---

## 1. 项目定位与核心概念

LucaWriter 是一款面向小说作者的长文本创作辅助工具，内置 AI 写作搭档（Luca）。

**核心工作流**：
- 作者在**纯文本编辑器**中写章
- 通过**AI 对话**（Luca）获得剧情建议
- 运行**摘要全书（通读）**让 AI 逐章提取人物、事件、设定、伏笔，存入 SQLite 知识库
- 在**底栏面板**切换大纲/摘要/预言/时间线/吃书雷达
- 支持多本书组成**世界观（Work）**跨书通读
- 支持导出/导入 `.coo` 格式（带哈希链校验）

---

## 2. 目录结构

```
lucawriter/
├── backend/                  # Python 后端（单文件 HTTPServer）
│   ├── main.py               # ★ 主服务器 (10976行) — 路由、AI调用、任务调度、文件解析
│   ├── kb_storage.py          # ★ 知识库 SQLite ORM (1397行) — 15张表 + 向量存储
│   ├── kb_pipeline.py         # ★ AI通读管道 (2366行) — 结构化提取、一致性检查、时间线编排
│   ├── embeddings.py          # 嵌入后端 (209行) — SentenceTransformer / API / 哈希回退
│   ├── coo_provenance.py      # COO v2 溯源 (227行) — 哈希链校验
│   ├── browser_agent.py       # 浏览器控制 (1032行) — DrissionPage 封装
│   └── icon_generator.py      # 动态图标生成
├── frontend/                 # 纯前端 (单 HTML 文件 + 字体 + Logo)
│   ├── index.html             # ★ 主界面 (7058行) — 所有 CSS + JS + HTML 合一
│   ├── login.html             # 登录/注册页 (269行)
│   └── fonts/                 # Playfair Display 字体
├── landing/                  # 官网/Marketing 页面
│   └── index.html             # (429行) 深色/浅色切换
├── electron/                 # Electron 打包
│   ├── main.js
│   ├── preload.js
│   ├── browser-preload.js
│   ├── loading.html
│   ├── browser.html
│   ├── package.json           # electron-builder 配置
│   └── make_icon.py
├── build_macos/              # macOS 构建（icns、entitlements）
├── local_llm/                # 本地 Llama.cpp 运行时目录
├── models_cache/             # HuggingFace 模型缓存 (bge-small-zh-v1.5)
├── builtin/                  # 内置示例小说
│   └── LUCA_Legend.md
├── backend/usrdata/          # 运行时数据目录（自动创建）
│   ├── books/                # 每本书一个子目录
│   ├── works/                # COO v2 世界观级共享目录
│   ├── messages/             # 旧版按日期拆分的聊天记录（已弃用）
│   ├── chat_sessions/        # 新版聊天会话（cs_*.json）
│   ├── chat_history.json     # ★ 全局 Luca 对话历史
│   ├── settings.json         # 全局设置
│   ├── users.json            # 用户账号
│   ├── sessions.json         # 登录会话
│   └── local_strategy.json   # 本地 LLM 策略缓存
├── cooverter.py              # CLI 工具：任意格式转 .coo
├── requirements.txt          # Python 依赖
├── Dockerfile                # Docker 部署配置
├── .python-version           # Python 3.12
├── AGENTS.md                 # 对话摘要（项目历史快照）
├── CLAUDE.md                 # 项目规范（UI/后端/COO 开发约定）
├── COO.md                    # COO v2 格式规范（与 Coobox 共享契约）
└── overview.md               # ← 本文件
```

---

## 3. 后端架构总览（main.py）

### 3.1 技术选型

| 项目 | 选型 |
|------|------|
| 服务器 | 纯 `ThreadingHTTPServer`（无框架） |
| 端口 | 默认 `10000`（本地开发）/ `20000`（Docker/CI 分离环境） |
| 数据库 | SQLite via `kb_storage.py`（知识库）/ JSON 文件（设置/用户/聊天） |
| 密码 | PBKDF2-HMAC-SHA256 (200K iters) |
| 加密 | AES-256-CBC + HMAC-SHA256（API key 加密存储） |
| 速率限制 | 内存滑动窗口（每 IP） |
| 前端 | 纯静态文件服务（从 `FRONTEND_DIR` 分发） |

### 3.2 全局常量/路径

| 变量 | 值/用途 |
|------|---------|
| `DATA_DIR` | `env DATA_DIR \|\| usrdata/` |
| `BOOKS_DIR` | `DATA_DIR/books` — 每本书一个子目录 |
| `WORKS_DIR` | `DATA_DIR/works` — COO v2 世界观 |
| `GLOBAL_CHAT_HISTORY_FILE` | `DATA_DIR/chat_history.json` |
| `PORT` | `env LUCA_PORT \|\| 20000`（分离环境）或 `10000`（本地） |
| `FRONTEND_DIR` | `env FRONTEND_DIR \|\| ../frontend/` |
| `DEFAULT_SETTINGS` | `base_url, api_key, model, ai_frequency:500, ai_max_tokens:512, temperature:None, ai_auto_comment:True, theme_accent:'#E8CC7A', theme_mode:'dark', content_font_size:20, ...` |
| `DEFAULT_OUTLINE` | `{worldview, characters:[], key_events:[], rules:[], chapter_summaries:{}, ai_suggestions:{}}` |

### 3.3 书本数据存储

每本书 = `DATA_DIR/books/<book_id>/`
```
meta.json           — 元数据（title, chapter_order[], readthrough_at, ...）
outline.json        — 大纲（DEFAULT_OUTLINE 结构）
core_memory.md      — 核心记忆（Luca 维护的设定总结）
chapters/
  ch_<timestamp>.json   — {id, title, content, updated}
kb.db               — SQLite 知识库（见 §4）
ai/
  source.md             — 全书摘要笔记
  outline.md            — 自动提炼的故事大纲
  prediction.md         — 读者预言
  timeline.md           — 时间线
  entities/*.md         — 每个实体一个文件
  foreshadowing.md      — 伏笔线索
  trash/                — 删除的章节
  chapter_summaries/    — 每章的摘要文本
```

### 3.4 API 端点完整清单

#### GET 端点

| 路由 | 说明 |
|------|------|
| `/` / `/index.html` | 主页面（未登录 → 重定向至 `/login`） |
| `/login` | 登录页 |
| `/readthrough` | 通读进度页 |
| `/icon.png` / `/icon.ico` | 主题色动态图标 |
| 静态文件 | `*.png,.svg,.ico,.jpg,.css,.js,.woff2,.ttf,.otf` 从 FRONTEND_DIR 分发 |
| `/api/auth/status` | `{has_users, logged_in}` |
| `/api/settings` | 返回设置（未登录只返回主题/语言） |
| `/api/connected-clients` | HTTP 客户端列表 |
| `/api/active-connections` | 活跃 AI 连接列表 |
| `/api/ai-activity` | **SSE 端点** — 实时推送 AI 连接数变化 |
| `/api/editor-fonts` | 编辑器字体预设列表 |
| `/api/editor-fonts/{filename}` | 字体文件下载 |
| `/api/sessions` | 活跃会话列表 |
| `/api/works` | 所有世界观（Work） |
| `/api/work/{wid}` | Work 详情 + 子书 + lore + reading_order |
| `/api/work/{wid}/cover` | Work 封面 |
| `/api/work/{wid}/coo-remote` | COO 远端服务器配置 |
| `/api/work/{wid}/readthrough/status` | Work 级通读状态 |
| `/api/work/{wid}/readthrough/file?type=` | Work 级 source/outline MD |
| `/api/books` | 所有书本（可按 work_id 过滤） |
| `/api/import-book-status?task_id=` | 异步导入状态 |
| `/api/book/{bid}/chapters` | 章节目录 |
| `/api/book/{bid}/chapter/{cid}` | 单章正文 |
| `/api/book/{bid}/outline` | 大纲 + 核心记忆 |
| `/api/book/{bid}/kb-archives` | KB 存档列表 |
| `/api/book/{bid}/chapter-kb?chapter_id=` | 单章知识（实体+事件+规则） |
| `/api/book/{bid}/prediction-current` | 当前预言 MD |
| `/api/book/{bid}/trash` | 回收站章节 |
| `/api/book/{bid}/export?format=zip\|md\|txt` | 导出下载 |
| `/api/book/{bid}/messages` | 聊天历史 |
| `/api/book/{bid}/inspirations` | 灵感清单 |
| `/api/book/{bid}/annotations` | 标注列表 |
| `/api/book/{bid}/cover` | 封面 |
| `/api/book/{bid}/readthrough/config` | 通读配置 |
| `/api/book/{bid}/readthrough/status` | 通读状态 |
| `/api/book/{bid}/readthrough/file?type=` | source/outline/timeline/prediction |
| `/api/book/{bid}/reread-status` | 增量重读状态 |
| `/api/book/{bid}/task/status?task_id=\|type=` | 任务状态 |
| `/api/book/{bid}/task/list` | 所有任务 |
| `/api/icon?size=` | 主题色图标 base64 |
| `/api/theme-is-light` | 判断主题色深浅 |
| `/api/local-llm/status` | 本地 LLM 运行状态 |
| `/api/local-llm/progress` | 加载进度 |
| `/api/local-llm/speed` | 推理速度快照 |
| `/api/local-llm/detected-model` | 检测到的模型 |
| `/api/local-llm/preset-models` | 预设模型列表 |
| `/api/local-llm/download-progress` | 下载进度 |
| `/api/local-llm/hardware-check` | 硬件检测+策略 |
| `/api/local-llm/models-dir` | 模型目录路径 |
| `/api/browser/status` | 浏览器代理状态 |
| `/api/chat-sessions` | 聊天会话列表 |
| `/api/chat-session/{sid}/messages` | 会话消息 |

#### POST 端点

| 路由 | 说明 |
|------|------|
| `/api/auth/setup` | 首次创建管理员 |
| `/api/auth/login` | 登录（含速率限制+账号锁定） |
| `/api/auth/logout` | 登出 |
| `/api/auth/reset-password` | 重置密码 |
| `/api/editor-fonts` | 上传字体 |
| `/api/editor-fonts/delete` | 删除字体 |
| `/api/sessions/revoke` | 吊销会话 |
| `/api/sessions/revoke-all` | 吊销其他所有会话 |
| `/api/auth/set-device-name` | 设置设备名 |
| `/api/works/create` | 创建 Work + 首本书 |
| `/api/work/{wid}/update` | 更新 Work 元数据 |
| `/api/work/{wid}/add-book` | 向 Work 添加子书 |
| `/api/work/{wid}/book-order` | 子书排序 |
| `/api/work/{wid}/reading-order` | 设置阅读线 |
| `/api/work/{wid}/lore-create` | 创建 lore 条目 |
| `/api/work/{wid}/lore-update\|delete\|place\|unplace` | Lore 管理 |
| `/api/work/{wid}/upload-cover` | 上传封面 |
| `/api/work/{wid}/delete` | 删除 Work |
| `/api/work/{wid}/export-coo` | 导出 .coo |
| `/api/work/{wid}/coo-remote` | 保存远端 COO 配置 |
| `/api/work/{wid}/coo-push` | 推送到远端 Coobox |
| `/api/work/{wid}/merge-coo` | 合并 .coo 到 Work |
| `/api/work/{wid}/readthrough/start` | Work 级通读开始 |
| `/api/work/{wid}/readthrough/pause\|stop` | 暂停/停止 |
| `/api/books/create` | 新建书（可选在 Work 内） |
| `/api/books/import` | 同步导入文件 |
| `/api/books/import-coo` | 导入 .coo |
| `/api/books/check-coo` | 检查 .coo 有效性 |
| `/api/books/rename` | 改书名 |
| `/api/books/delete` | 删除书 |
| `/api/book/{bid}/import-verify` | 导入验证任务 |
| `/api/book/{bid}/consistency-check` | 吃书雷达检查 |
| `/api/book/{bid}/consistency-alert` | 更新提醒状态 |
| `/api/book/{bid}/consistency-deep-check` | 深入分析提醒 |
| `/api/book/{bid}/kb-reread` | 局部重读 |
| `/api/book/{bid}/timeline-arrange` | 时间线编排 |
| `/api/book/{bid}/timeline-reorder` | 重排事件 |
| `/api/book/{bid}/timeline-generate` | 生成时间线 |
| `/api/book/{bid}/timeline-detail` | 事件详情 |
| `/api/book/{bid}/chapter` | 保存章节（`action=chapter` + id） |
| `/api/book/{bid}/set-current-chapter` | 设当前章节 |
| `/api/book/{bid}/reorder` | 重排章节 |
| `/api/book/{bid}/delete` | 删章（进回收站） |
| `/api/book/{bid}/restore` | 从回收站恢复 |
| `/api/book/{bid}/rename` | 重命名章节 |
| `/api/book/{bid}/export-epub` | 导出 EPUB |
| `/api/book/{bid}/comment` | AI 自动评论/启动聊天 |
| `/api/book/{bid}/annotations` | 增/删/清标注 |
| `/api/book/{bid}/kb-proposal-list` | 列出 KB 修改提议 |
| `/api/book/{bid}/kb-proposal-confirm` | 确认提议 |
| `/api/book/{bid}/kb-proposal-reject` | 拒绝提议 |
| `/api/book/{bid}/kb-edit-apply` | 直接应用 KB 编辑 |
| `/api/book/{bid}/kb-edit-undo` | 撤销 KB 编辑 |
| `/api/book/{bid}/outline-update` | AI 大纲建议 |
| `/api/book/{bid}/outline-check` | 检查内容与大结对矛盾 |
| `/api/book/{bid}/outline-save` | 保存大纲 |
| `/api/book/{bid}/memory-update` | AI 更新核心记忆 |
| `/api/book/{bid}/chapter-summary` | 单章摘要 |
| `/api/book/{bid}/chapter-complete` | 标记本章写完 |
| `/api/book/{bid}/reader-prediction` | 生成读者预言 |
| `/api/book/{bid}/import` | 导入章节到现有书 |
| `/api/book/{bid}/update-source` | 更新 source.md |
| `/api/book/{bid}/upload-cover` | 上传封面 |
| `/api/book/{bid}/inspirations` | 灵感管理 |
| `/api/book/{bid}/clear-chat` | 清空聊天 |
| `/api/book/{bid}/kb-archives/restore` | 恢复 KB 存档 |
| `/api/book/{bid}/browser-confirm\|reject` | 浏览器搜索结果确认/拒绝 |
| `/api/book/{bid}/readthrough/start\|pause\|stop\|resume\|reset\|redo\|config\|source\|generate-outline\|embedding/rebuild` | 通读操作 |
| `/api/book/{bid}/generate-stream` | AI 流式生成（时间线/大纲/预言） |
| `/api/import-book` | 异步导入 |
| `/api/settings` | 保存设置 |
| `/api/local-llm/start\|stop\|download\|download-cancel\|open-models-dir` | LLM 控制 |
| `/api/fetch-models` | 拉取远端模型列表 |
| `/api/context-estimate` | 估算上下文用量 |
| `/api/stop-all-ai` | 停止所有 AI 连接 |
| `/api/browser/init\|close\|action` | 浏览器控制 |
| `/api/chat-session/create` | 创建聊天会话 |
| `/api/chat-session/{sid}/messages` | 保存会话消息 |
| `/api/restart-server` | 重启服务器 |

### 3.5 AI 调用基础设施

**core functions**（行 ~9307-9755）:
- `call_ai_stream(settings, messages, ...)` — 流式调用，返回 `(full_text, error)`，支持 `think` 标签和 `reasoning_content`
- `call_ai_full(settings, messages, ...)` — 非流式，返回 `(content, reasoning, error)`
- `call_ai(settings, messages, ...)` — 旧封装，返回 `(content, error)`
- `call_ai_with_tools(settings, messages, tools, tool_choice, ...)` — 工具调用

**协议适配**: 兼容 OpenAI API 格式。支持自定义 JSON 模板（`use_custom_json`/`custom_json`）。支持 `reasoning_effort` 参数。

**Chat 上下文中的 Luca 工具标签**（AI 回复中解析）:
- `[READ_CHAPTER chapter_id=N]` — 读取章节正文
- `[ANNOTATE_ADD id=N color=...]` — 添加标注
- `[ANNOTATE_REMOVE id=N]` — 删除标注
- `[COMPLETE_CHAPTER]` — 标记本章写完
- `[SUGGEST_READTHROUGH]` — 建议运行通读
- `[CITE entity=N/meta=N]` — 引用知识库出处
- `[PROPOSE_KB_EDIT table=... record=... field=... value=...]` — 提议修改知识库
- `[REREAD_KB focus=...]` — 触发局部重读
- `[ADD_INSPIRATION text=...]` — 添加灵感条目

### 3.6 聊天系统（"一个 Luca"）

**所有书本共享同一个聊天历史**（`DATA_DIR/chat_history.json`）。

- `_load_chat_history(entity_id)` / `_save_chat_history(entity_id, messages, merge=True)` — 合并模式支持多窗口安全
- `_migrate_global_chat_history_locked()` — 首次启动时合并旧 per-book 历史
- 消息类型：`user` / `ai` / `system/compressed_summary` / `system/focus_notice`
- `_schedule_idle_compress(entity_id)` — 聊天结束后 30 秒，用 AI 摘要压缩旧消息（保留最近 12 条）
- 新版聊天会话：`POST /api/chat-session/create` → `cs_*.json` 存在 `CHAT_SESSIONS_DIR`

**系统提示词改造**（2026-05-15 #15）：
- Luca 不会自动看到章节正文，需要手动调用 `[READ_CHAPTER]` 工具
- 提示词注入浏览位置（系列 > 书 > 章），不注入正文
- 提示词附带章节列表供工具使用

### 3.7 任务系统

```python
_bg_tasks = {}  # task_id -> {type, book_id, name, status, progress, result, error, created_at, thread, stop_flag}
```

- `bg_task_start(type, book_id, name)` -> `task_id`
- `bg_task_update(task_id, **kwargs)` / `bg_task_done(task_id, error)`
- `bg_task_get_running_luca_chat()` — 获取正在运行的 Luca 聊天任务（全局限制一个）
- 任务类型：`import-book`, `import-verify`, `kb-reread`, `timeline`, `chat`, `chapter-complete`, `prediction`, `outline`, `source-update`, `reread-incremental`
- cleanup: 24 小时后自动移除

### 3.8 文件导入导出

**导入解析器**（`IMPORT_PARSERS` 字典，行 ~3446）：

| 格式 | 解析函数 | 备注 |
|------|---------|------|
| `.txt` | `parse_txt()` | 按中/英章节标题拆分 |
| `.md` | `parse_md()` | 按 `##` 拆分，`#` 提取书名 |
| `.docx` | `parse_docx_bytes()` | python-docx 或直接 XML |
| `.pdf` | `parse_pdf_bytes()` | pypdf，最大 500 页 |
| `.epub` | `parse_epub_bytes()` | 全面解析 OPF/NCX/Spine |

**安全限制**：ZIP_MAX_ENTRIES=5000，ZIP_MAX_TOTAL_BYTES=500MB，ZIP_MAX_ENTRY_BYTES=100MB

**导出**：zip（JSON）/ md / txt / epub / coo

### 3.9 SSE 系统

`GET /api/ai-activity` — 每个客户端分配 `queue.Queue`，`_notify_sse_clients()` 在 `register_ai_connection()` / `unregister_ai_connection()` 时推送 `{"count": N}`。30 秒 keepalive，断连自动清理。

---

## 4. 知识库系统（kb_storage.py）

### 4.1 数据库 Schema（15 张表）

| 表名 | 行数参考 | 用途 |
|------|---------|------|
| `chapters` | ~N | 通读进度与状态（pending/processing/done/failed/skipped） |
| `entities` | ~N*10 | 实体（人物/地点/物品/概念…），`(book_id, canonical_name)` UNIQUE |
| `mentions` | ~N*30 | 实体在每章的事实陈述，FK→entities CASCADE |
| `events` | ~N*5 | 事件（who/what/where/why/consequence） |
| `foreshadowing` | ~N*3 | 伏笔（open/resolved） |
| `rules` | ~N*2 | 设定规则，(book_id, name) UNIQUE |
| `rule_mentions` | ~N*2 | 规则提及，FK→rules CASCADE |
| `timeline_event_meta` | ~N*5 | 时间线元数据（story_order, lane, importance, zoom_level, confidence, evidence） |
| `timeline_relations` | ~N*3 | 事件关系（before/same_time/flashback/uncertain） |
| `consistency_alerts` | ~N*2 | 吃书雷达提醒（kind: timeline/character/rule/object/continuity） |
| `rt_state` | 1 | 通读状态机（running/paused/done/error） |
| `rt_logs` | ~500 max | 通读日志 |
| `embedding_chunks` | ~N*5 | 嵌入块索引（source_type: chapter_summary/entity/event/foreshadowing/rule） |
| `vector_entries` | ~N*5 | 向量存储（归一化 float32 余弦搜索） |
| `kb_proposals` | ~N | AI 修改知识库提议（pending/confirmed/rejected） |
| `kb_edit_log` | ~N | 修改历史（可撤销） |

### 4.2 关键 DAO 函数

**章节**: `upsert_chapter`, `get_chapter`, `list_chapters_db`, `delete_chapter_artifacts`
**实体**: `upsert_entity`（别名合并去重）, `get_entity_by_name`, `list_entities`, `match_entities_by_name`
**提及**: `add_mention`, `get_mentions_for_entity`, `get_mentions_by_chapter`, `get_entity_recent_mentions_before`
**事件**: `add_event`, `list_events`, `get_events_by_chapter`
**伏笔**: `add_foreshadowing`, `resolve_foreshadowing`, `list_foreshadowing`
**规则**: `upsert_rule`（去重）, `add_rule_mention`（去重）, `get_rule_mentions_by_chapter`, `list_rules`
**时间线**: `upsert_timeline_event_meta`, `add_timeline_relation`, `clear_ai_timeline_relations`, `list_timeline_events`, `list_timeline_relations`
**一致性**: `save_consistency_alerts`（去重）, `list_consistency_alerts`, `update_consistency_alert_status`
**批量删除**: `delete_kb_records(delete_map)` — 级联删除 + 清理孤立实体
**通读状态**: `set_rt_state`, `get_rt_state`, `get_pause_requested`, `set_pause_requested`, `append_stream`
**日志**: `rt_log`, `get_rt_logs`（写入 DB + readthrough.log 文件）

### 4.3 向量存储

- `embed_upsert_many(book_id, ids, docs, embeddings, metadatas)` — 批量 `INSERT OR REPLACE`
- `embed_upsert(book_id, chunk_id, text, backend, source_type, source_id)` — 便捷封装
- `embed_query(book_id, query_text, backend, top_k, where)` — **暴力余弦搜索**（无 ANN 索引），支持 `$eq/$ne/$in/$nin/$and/$or` 过滤
- `prune_vector_entries(book_id, expected_ids)` — 清理不在预期集合中的向量
- `embed_clear(book_id)` — 清空向量+块+`.vector_db` 目录

### 4.4 KB 提议与编辑日志

`_EDITABLE_FIELDS` 白名单控制可修改的表/字段。
- `create_proposal` → `confirm_proposal` / `reject_proposal` — AI 提议→确认/拒绝
- `apply_kb_edit` — 直接编辑
- `undo_edit` — 撤销（恢复旧值）

### 4.5 锁与事务

- `_get_lock(book_id)` — 每书一个 `threading.RLock()`（惰性创建）
- `db_transaction(book_id)` — 上下文管理器：`lock → conn (WAL+5000ms timeout) → yield → commit/rollback → close → unlock`
- 读操作：每次新建连接，try/finally 关闭

---

## 5. 知识库管道（kb_pipeline.py）

### 5.1 通读编排器 `do_readthrough(book_id, settings, config, resume)`

核心流程：
1. 读取 `chapter_order`（meta.json）
2. 初始化 rt_state（`status=running, total=N`）
3. 加载每个章节内容
4. **批量 vs 单章策略**：
   - 如果上下文窗口 > 16K tokens → 批量模式（`max_batch = (ctx*0.9 - 4000) / 7500`）
   - 如果 `read_mode == 'chapter'` → 强制单章
   - 批量失败自动降级（batch ÷ 2 → 单章）
5. 遍历章节：
   - `_unchanged_done()`: 内容哈希一致 → 跳过
   - `_is_content_empty()`: 无正文 → 标记 done（摘要=跳过）
   - **单章**: `ai_read_chapter_structured()` → `apply_structured_result()` → `upsert_chapter(done)`
   - **批量**: `ai_read_chapters_batch_structured()`（JSON 数组输出）→ 逐一 `apply_structured_result`
   - **重读纠错**: 已有 `done` 记录的章节走二周目（`prior_records` 注入旧笔记，AI 纠错）
6. 完成后: `incremental_embed()` → `render_markdown_views()`

### 5.2 世界观级通读 `do_readthrough_work(work_id, books_meta, settings, reading_order, work_title)`

- 解析 `reading_order`（支持 chapter / lore / volume_boundary 三种条目）
- 跨书时自动/手动插入 `volume_boundary` 提示
- lore 条目作为章节处理（存储 ID = `lore::<ref>`）
- 所有子书共用 work_id 的知识库

### 5.3 AI 结构化提取 Prompt

```json
{
  "summary": "连贯叙述摘要（200-3000字）",
  "entities": [{"canonical_name", "type", "aliases_in_chapter", "facts": [{"fact", "snippet"}]}],
  "events": [{"story_time", "who", "what", "where", "why", "consequence", "snippet"}],
  "foreshadowing_new": [{"hint", "snippet"}],
  "foreshadowing_resolved": [{"earlier_hint", "resolution", "snippet"}],
  "rules": [{"name", "body"}]
}
```

温度 0.3，最多重试 3 次。snippet 必须是原文实际存在的片段。

### 5.4 一致性检查（吃书雷达）

`consistency_check(book_id, chapter_id, text, settings)`:
1. 取最新 2200 字符正文
2. 模糊匹配实体名 → 加载相关详情
3. 发送给 AI，输出最多 3 条 alerts：`{kind, severity, message, evidence, suggestion, highlight_text}`
4. 通过 `source_hash` 去重持久化

`consistency_deep_check(book_id, alert_id, settings)`:
- "你去确认"功能
- 加载提醒详情 + 相关章节正文（最多 6000 字）+ 上下文（最多 14000 字）
- AI 深入分析，要求引用原文出处

### 5.5 其他管道函数

- `chapter_outline(book_id, chapter_id)` — 单章知识面板（实体按拼音首字母排序）
- `timeline_map(book_id, focus_chapter_id, zoom)` — 时间线可视化数据
- `arrange_timeline_ai(book_id, settings)` — AI 编排时间线（尊重 `status=user` 事件）
- `generate_short_prediction(book_id, settings)` — 读者预言（根据最新 6 章摘要+未解伏笔）
- `reread_passages(book_id, chapter_ids, correction, focus_texts, settings)` — 重读修正
- `incremental_embed(book_id, settings)` — 增量构建向量索引
- `render_markdown_views(book_id)` — 生成 source.md + 实体文件 + timeline.md + foreshadowing.md

### 5.6 隐私函数

- `_lazy_main()` / `_main()` — 惰性导入 `main.py` 避免循环依赖
- `_pinyin_initial(text)` — GBK 编码映射汉字到拼音首字母 A-Z
- `_build_prev_context(book_id, max_chars)` — 前情摘要（最近 8 章摘要 + 前 40 实体）

---

## 6. 前端架构总览（index.html）

**单文件架构**（7058 行）：CSS（~2500 行）+ HTML（~1000 行）+ JS（~3500 行）全部在 `index.html`。

### 6.1 CSS 架构

**CSS 变量主题系统**:
- `:root` — 深色主题默认
- `[data-theme-mode="light"]` — 浅色主题完整重写
- `body.electron` / `body.electron.electron-platform-win32` / `body.electron.electron-platform-darwin` — Electron 特殊样式
- `@media(max-width:1040px)` / `media(max-width:860px)` — 响应式断点

**核心 CSS 变量**:
- `--bg`, `--surface`~`--surface4`（背景色阶）
- `--t1`/`--t2`/`--t3`（文字色阶）
- `--accent`/`--accent2` + `--accent-a03`~`--accent-a90`（主题色透明序列）
- `--editor-font-size`（编辑器独立字号，默认 20px）
- `--content-font-size`, `--content-line-height`
- `--font-serif`, `--font-sans`, `--font-mono`, `--font-longform`, `--font-reading`, `--font-ui`
- 圆角 `--r1`~`--r4`，边框 `--border`, `--border2`

**布局**:
```
.app                          grid-template-rows: 38px 1fr auto
├── .topbar                   38px 固定
│   ├── .topbar-location      面包屑+章节标题输入
│   ├── .topbar-center-logo   Logo（绝对定位居中）
│   ├── .topbar-status        字数+保存指示器
│   └── .topbar-actions       摘要/设置/深色模式/完成按钮
├── .main-row                 flex: 1fr auto
│   ├── .trigger              18px 触发区（hover 展开 leftStack）
│   ├── #leftStack            chat + 章节列表 overlay
│   │   ├── .chat-section     Luca 对话
│   │   └── .chapter-list-overlay  章节列表（absolute, z-index:5）
│   ├── .resize-handle        可拖拽分隔条
│   └── .editor-area          编辑器
│       ├── .editor-header    章节标题/字数（已移到 topbar）
│       └── .editor-body      textarea + 搜索栏 overlay
└── .bottom-panel             flex: auto（可展开/折叠）
    ├── #aiInputZone          AI 输入框（折叠时显示）
    ├── .bottom-tabs          Tab 按钮（预言/大纲/摘要/吃书雷达）
    └── .bottom-panes         各面板内容
```

### 6.2 UI 规范（从 CLAUDE.md 提取）

- **禁止 `box-shadow`**：`--panel-shadow` 和 `--glass-shadow` 已全局为 `none`
- **`backdrop-filter` 仅限一处**：`.left-sidebar::before`（章节栏毛玻璃）
- **装饰动画仅允许 opacity 变化**：不写 `translateY/scaleX` 等位移/缩放动画
- **Hover 规范**：`transition: border-color .15s ease`（具体属性），禁 `all`，禁 `transform/scale/translateY`，禁 `box-shadow`，禁 `backdrop-filter`
- **hover 改 border-color 不改 border-width**（防抖动）
- **禁用 `100vh`**：用 `html,body{height:100%}` + `.app{height:100%;overflow:hidden}` 链条
- **编辑器区域** padding=0，textarea 无圆角无阴影

### 6.3 HTML 关键元素 ID / Class

| ID / Class | 用途 |
|-----------|------|
| `#bookshelfView` | 书架视图（卡片网格） |
| `#seriesView` | 系列视图（已弃用，走 Work） |
| `#editorArea` | 编辑器区域 |
| `#chapterList` | 章节侧栏列表 |
| `#aiMessages` | 聊天消息容器 |
| `#aiInput` | AI 输入框 |
| `#searchBar` | 编辑器搜索栏 |
| `#outlinePane` | 大纲面板（吃书雷达 + 本章有用信息双栏） |
| `#summaryPane` | 摘要笔记面板 |
| `#predictionPane` | 读者预言面板 |
| `.bottom-tabs` | 底栏 Tab 切换 |
| `#taskQueueWrap` | 任务队列显示 |
| `#settingsModal` | 设置弹窗 |
| `#radarDeepModal` | 吃书雷达深度分析弹窗 |
| `#kbProposalModal` | KB 修改提议弹窗 |

---

## 7. 前端 JS 函数分类清单

### 7.1 初始化与核心状态

| 函数 | 位置（约行） | 说明 |
|------|------------|------|
| `init()` | 4660 | 入口：加载设置→主题→书架/会话恢复 |
| `_appHeight()` | 5380 | 计算容器高度（非 window.innerHeight） |
| 全局变量 | ~4640 | `_currentBookId, _currentSeriesId, _chapters, _currentChapter, _aiMessages, _currentMode (shelf/book/series), _currentFontSize, _muteSave` 等 |

### 7.2 书架/系列

| 函数 | 说明 |
|------|------|
| `loadBookshelf()` | 加载书架 JSON → 渲染卡片 |
| `loadSeriesView(sid)` | 系列视图 |
| `createBook()`, `renameBook()`, `deleteBook()` | CRUD |
| `importBook()`, `uploadBookCover()`, `exportBook(format)` | 导入导出 |
| `checkImportStatus(taskId)` | 异步导入轮询 |

### 7.3 章节管理

| 函数 | 说明 |
|------|------|
| `loadChapterList()` | 加载章节列表 |
| `selectChapter(id)` | 切换章节，更新 topbar |
| `createChapter()`, `deleteChapter()`, `restoreChapter()` | CRUD |
| `reorderChapters()` | 拖拽排序 |
| `renameChapter(id)` | 重命名 |
| `onContentChange()` | 正文变更→自动保存 |
| `saveChapter()` | 保存章节省 |
| `completeChapter()` | "本章写完" → 触发 AI 摘要 |

### 7.4 编辑器

| 函数 | 说明 |
|------|------|
| `openSearch()`, `closeSearch()`, `doSearch()` | Ctrl+F 搜索 |
| `searchNav()` | 导航匹配项（Enter/F3/Ctrl+G） |
| `searchReplaceOne()`, `searchReplaceAll()` | 替换 |
| `findEntity(name)` | 大纲实体查找按钮→搜索 |
| `syncHighlights()` | 搜索高亮 + 标注高亮共存 render |

### 7.5 AI 聊天

| 函数 | 说明 |
|------|------|
| `sendAI()` | 发送消息（收起章节列表） |
| `_doSendAI()` | 核心：构建 history → POST → 轮询结果 |
| `loadAIMessages()`, `saveAIMessages()` | 加载/保存聊天历史（无日期参数） |
| `renderAIMessages()` | 全量渲染消息 |
| `_updateLiveAIMessage(idx)` | 流式更新单条消息（不重建全部 DOM） |
| `_buildAIMsgEl(m, idx)`, `_appendAIMsgCards()` | 消息 DOM 构建 |
| `clearAIChat()` | 清空聊天（API 调用） |
| `kbShowProposalModal()`, `kbModalConfirm()`, `kbModalReject()` | KB 提议弹窗 |
| `kbJumpToChapter(chapterId, text)` | 跳转到引用章节+高亮 |

### 7.6 底栏面板

| 函数 | 说明 |
|------|------|
| `switchBottomTab(tab)` | Tab 切换 |
| `openBottomPanel()`, `closeBottomPanel()` | 展开/折叠 |
| `loadSummaryNotes()`, `loadPredictionCurrent()` | 摘要/预言加载 |
| `refreshPrediction()` | 刷新预言 |
| `loadChapterKnowledge(chapterId)` | 加载本章知识（实体+事件+规则） |
| `loadConsistencyAlerts()` | 加载吃书雷达提醒 |
| `radarDeepCheck(alertId)` | "你去确认"深度分析 |
| `loadTimelineRiver()` | 加载时间线可视化 |

### 7.7 设置与主题

| 函数 | 说明 |
|------|------|
| `openSettings()`, `saveSettings()` | 设置弹窗读/写 |
| `applySettings()` | 应用设置（主题、字号、提供者） |
| `toggleTheme()` | 深色/浅色切换 |
| `applyFontSize(size)` | 编辑器独立字号 |
| `updateThemeToggleIcon()` | 同步书架/系列视图主题按钮 |

### 7.8 任务队列

| 函数 | 说明 |
|------|------|
| `loadTaskQueue()` | 轮询任务状态 |
| `doAction(action, body)` | 执行动作（通读/摘要/预言等） |
| `doActionStreaming(action)` | 流式动作（generate-stream） |
| `_startAIActivity()`, `_stopAIActivity()` | SSE 活跃连接监听 |

### 7.9 Electron 特有

| 函数 | 说明 |
|------|------|
| `windowControls(act)` | 最小化/最大化/关闭 |
| `isElectron` 检测 | `navigator.userAgent` 或 `window.chrome` |

### 7.10 快捷键

| 按键 | 动作 |
|------|------|
| Ctrl+F | 打开搜索 |
| Ctrl+H | 打开搜索+替换 |
| Escape | 关闭搜索/弹窗 |
| F3 / Ctrl+G | 搜索下一个 |
| Shift+F3 / Ctrl+Shift+G | 搜索上一个 |

---

## 8. 嵌入系统（embeddings.py）

```python
class EmbeddingBackend(ABC)      # 抽象基类: embed(texts) -> list[list[float]]
class LocalEmbedding             # SentenceTransformer (bge-small-zh-v1.5, 384d)
class HashEmbedding              # 降级回退（哈希 n-gram, 384d）
class APIEmbedding               # OpenAI 兼容 API (text-embedding-3-small, 1536d)
```

`get_embedding_backend(settings)` — 进程级缓存（相同配置返回同一实例）。
`_pick_embedding_device()` — Tier A (9B) + CUDA + VRAM ≤8.5GB 时强制 CPU（省显存给 LLM）。

---

## 9. COO 格式与溯源

### 9.1 COO 文件格式（COO.md）

`.coo` = 带 `.coo` 扩展名的 ZIP，描述一个 IP（世界观）：

```
三体.coo/
├── manifest.json          # IP 信息 + 书目 + lore + reading_order
├── META-INF/coo-history.jsonl   # 哈希链（留名+防篡改，无签名）
├── books/NN_标题/
│   ├── manifest.json      # 子书元数据
│   └── chapters/ch_N.json # 纯文本章节
├── lore/*.md              # 设定条目（无序便签）
├── shared/ai/             # AI 资产（角色/设定/时间线/kb.db）
└── assets/cover.webp
```

关键字段：
- `format_version: 2`（v1 不兼容）
- `reading_order`: `chapter` / `lore` / `volume_boundary` 三种条目类型
- `volume_boundary` 跨书提示：`新一卷《{book_title}》开始了。故事延续自世界观「{work_title}」。`
- `work_uid`: `coo_` 前缀 + ≥64 位随机十六进制

### 9.2 溯源（coo_provenance.py）

v2 只用哈希链（已删除 Ed25519 签名）。

校验规则（COO.md §8.3）：
1. manifest 可解析、version=2
2. coo-history.jsonl 至少一条事件
3. 每条事件的 `event_hash` = canonical JSON 的 sha256（去掉 event_hash 字段后）
4. `previous_event_hash` 链不断
5. 每条事件 `author` 非空
6. **最后一条**的 `changed_files` 与当前包内实际文件完全一致

控制路径（不计入校验）：`META-INF/coo-history.jsonl` 自身。
规范 JSON：`json.dumps(ensure_ascii=False, sort_keys=True, separators=(",",":"))`

### 9.3 cooverter.py

CLI 工具：将 TXT/MD/DOCX/PDF/EPUB 转为 `.coo`。
- `cooverter <path>` — 转换
- `cooverter expose <path.coo>` — 验签
- 身份文件：`~/.cooverter/identity.json`

---

## 10. 浏览器控制模块（browser_agent.py）

基于 DrissionPage（ChromiumPage）的浏览器自动化：
- `init_browser()` / `close_browser()` — 启动/关闭浏览器
- `navigate(url)` — 导航
- `click(selector)` / `input_text(selector, text)` / `scroll(delta)` — 基本操作
- `execute_js(script)` — 执行 JS
- `get_page_text()` — 获取页面文本
- `screenshot()` — 截图（base64）
- 支持 Electron 内置浏览器模式（`environ BROWSER_DEBUG_PORT`）

---

## 11. 本地 LLM 服务器控制（main.py 中）

全部通过 `main.py` 中的 `_start_llama_server()`, `_stop_llama_server()`, `_download_model_task()` 控制。

- 端口持久化到 `DATA_DIR/local_llm_port`
- 硬件自动检测（`_detect_hardware()`）→ 策略分级（Tier A/B/C）
- 预设模型配置：Qwen2.5-1.5B/3B/7B/14B（GGUF）+ DeepSeek/Coder 变体
- 下载语义：HuggingFace + ModelScope 双源回退

---

## 12. Electron 桌面端

**package.json**（`electron/`）:
- Electron 42.3.3, electron-builder 26.15.0
- Windows NSIS 安装器（输出 `LucaWriter-Setup.exe`）
- macOS DMG（`LucaWriter-mac-${arch}.dmg`）
- `extraResources`: dist-backend, frontend, dist-builtin, local_llm
- `asar: true`

---

## 13. Landing Page（`landing/index.html`）

429 行单页：深色/浅色主题 + 产品预览图 + 特性介绍。独立 CSS 变量系统，不依赖前端。

---

## 14. 开发约定

### 14.1 知识库向后兼容（最高优先级）

已有 `kb.db` 是用户花大量时间通读得到的核心资产。**任何时候新增表、字段、索引，必须用惰性迁移**（`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ADD COLUMN IF NOT EXISTS` / 兼容查询接入旧数据）。只有用户明确点击"重通读"或"清空知识库"时才允许重建。

### 14.2 UI 规范（详见 CLAUDE.md）

- 主题色 `--accent` 仅用于交互提示，**禁止**用于正文文字色
- 浅色模式文字必须是黑色/深灰色，不随主题色变化
- 全局禁止 `box-shadow`，`backdrop-filter` 仅限一处
- hover 只改 `border-color/background/color`，禁位移缩放的纯装饰动画
- 不使用 `100vh`（用 `clientHeight` 链条）
- editor-area padding=0，textarea 无圆角无阴影

### 14.3 COO 格式对齐

`.coo` 格式同时存在两份：Coobox `docs/coo-format.md` 与 LucaWriter `COO.md`，正文必须**逐字一致**。改格式必须三处同步：`main.py`（`_build_coo_zip`/`_import_coo_zip`）、`cooverter.py`、以及 Coobox 的解析逻辑。

### 14.4 行为准则

- 简洁优先（200 行能写成 50 行就重写）
- 只改相关的，不"顺带优化"相邻代码
- 不重构没坏的东西，只清理自己的改动产生的死代码
- 多步骤任务先列简要计划 + 验证步骤
- 先想再写，不确定就问，有更简单的方案就说

### 14.5 Git 分支

当前活跃分支：`dev/ui-refresh-2026`。最新提交 `ccc4c4d`。

### 14.6 端口约定

本地功能测试统一使用 `10000` 端口（`http://127.0.0.1:10000`）。`20000` 端口仅在 Docker/CI 分离环境使用。
