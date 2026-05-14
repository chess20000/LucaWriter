# LucaWriter 知识库重做设计文档

> 这份文档是给接手 AI 的完整设计书。读完后应当能直接开始实现，不需要再问问题。

## 0. 用户目标（原话）

> 用户携带已有的未完结作品，迁徙到我的软件，运行摘要功能通读全书初始化，然后 AI 可以问啥答啥，而且非常精确，能帮用户（作者）记住每一个他记不住的细节设定。而且，每写完一章，通过点击写完按钮，AI 可以自动将新的这一章的内容加入这个"本书的数据库"，以后可以精确查询。通读的功能对于用户需要：一个进度条，暂停功能，继续功能，而且暂停继续不应该出错。

拆解为可验证条目：

| # | 条目 | 验收标准 |
|---|------|---------|
| G1 | 通读完成后 AI 能精确回答全书事实 | 抽样 10 个具体问题（如"X 在第几章首次出现"、"Y 物品归谁所有"）回答正确且能引用章节号 |
| G2 | "本章写完"自动并入知识库 | 写完后立刻在 Q&A 中找得到该章新事实，无需重通读 |
| G3 | 通读 UI 有进度条 / 暂停 / 继续 | 暂停立即响应（≤2s 落盘），继续从下一未完成章开始，不重复跑、不丢已完成章 |
| G4 | 暂停继续不出错 | kill 进程 → 重启 → 继续按钮可用且工作正常；任何中断点恢复都不破坏数据库 |

---

## 1. 现状诊断（必读，避免重蹈覆辙）

### 1.1 现有管线
```
do_readthrough(bid, settings, config, resume)  main.py:7200
  ├─ 循环 chapter_order
  │   └─ _ai_read_chapter() → 输出 "## 剧情摘要 + ## 资料记录" markdown
  │       追加到 source.md
  │       save_json(readthrough_checkpoint.json, {notes, done, chapter_idx})
  ├─ _parse_entities_from_notes(): 正则从 markdown 抓 **Name**
  ├─ _save_parsed_entities_to_files(): 每实体一个 .md 写到 source/entities/
  ├─ _extract_timeline_from_notes() / _extract_foreshadowing_from_notes()
  ├─ _rebuild_vector_index(): chromadb 全量重建
  └─ _build_source_summary(): 顶层 8K 摘要回写 source.md
```

### 1.2 已定位的 7 个 bug

| # | 文件/行号 | 问题 | 后果 |
|---|----------|------|------|
| B1 | `main.py:6698` | `_assemble_smart_context_v2` 引用未定义的 `sd` | `get_smart_context` 在 timeline/foreshadowing 阶段抛 NameError，被上游 try/except 吞掉，AI **永远看不到** timeline 和 foreshadowing |
| B2 | `main.py:5158` | `_rebuild_set` 收到 `status='stopped'` 时强制 remap 为 `idle` 并 **删除 checkpoint** | 用户点暂停 → checkpoint 没了 → "继续"按钮实际从头开始 |
| B3 | `main.py:7229-7234` | `resume=True` 时无脑丢弃 `done_list` 最后一项和 `notes` 最后一条 | 每次暂停-继续都让最后一章被重做一次 |
| B4 | `main.py:6300` `_SimpleEmbedding` | 64 维字符频率哈希当嵌入 | 向量检索基本等于随机 |
| B5 | `main.py:6516-6519` | `_save_parsed_entities_to_files` 检查到 `### marker` 已存在就直接 skip | 角色在后续章节出现的新信息**写不进去** |
| B6 | `main.py:7182` chapter-complete | 每次都 `_rebuild_vector_index` 全量重建 | 章节多时阻塞；换真嵌入更糟 |
| B7 | `main.py:6467` `_parse_entities_from_notes` | 全靠 markdown 文本规则匹配 `**Name**` | AI 一旦换格式实体就漏 |

### 1.3 为什么必须重做不是修
- B1/B4 决定 AI 用不上检索；B2/B3 决定暂停继续不可信；B5/B7 决定后续章节信息会丢——这 4 条加起来意味着**没有任何输出可以信任**
- 实体存储用 markdown 文件，没有 alias 处理、没有结构化字段、无法做"列出全部开放伏笔"这种查询
- 单文件 `source.md` 当数据库用，AI 改写 prompt 哪天换格式就崩

---

## 2. 目标架构

### 2.1 分层

```
┌─────────────────────────────────────────────────────────┐
│                     前端 (index.html)                    │
│   通读 UI / 进度条 / 暂停-继续按钮 / 本章写完按钮 / AI 对话      │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP
┌──────────────────────▼──────────────────────────────────┐
│                    API 层 (main.py)                      │
│   /api/book/.../readthrough/{start,pause,resume,status}  │
│   /api/book/.../chapter-complete                         │
│   /api/book/.../qa-context (供 chat 调用)                │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              业务逻辑层 (kb_pipeline.py 新建)              │
│   do_readthrough() / do_chapter_complete() / qa_context()│
└────────┬──────────────────┬────────────────────┬────────┘
         │                  │                    │
┌────────▼─────┐   ┌────────▼────────┐   ┌──────▼───────┐
│ 存储层        │   │ 嵌入层           │   │ AI 调用层      │
│ kb_storage.py│   │ embeddings.py   │   │ (已存在)      │
│ SQLite + DAO │   │ Local + API     │   │ call_ai_stream│
│              │   │ ChromaDB writer │   │              │
└──────────────┘   └─────────────────┘   └──────────────┘
```

### 2.2 文件清单

**新建：**
- `backend/kb_storage.py` — SQLite schema、DAO、迁移
- `backend/embeddings.py` — 嵌入后端抽象 + Local/API 两实现
- `backend/kb_pipeline.py` — 通读编排器 + chapter-complete + Q&A 检索

**重写（覆盖现有函数）：**
- `main.py:7200 do_readthrough` → 调用 `kb_pipeline.do_readthrough`
- `main.py:7083 _do_chapter_complete` → 调用 `kb_pipeline.do_chapter_complete`
- `main.py:6640 get_smart_context` → 调用 `kb_pipeline.qa_context`

**删除：**
- `main.py:6300 _SimpleEmbedding`
- `main.py:6467 _parse_entities_from_notes`
- `main.py:6506 _save_parsed_entities_to_files`
- `main.py:6525 _extract_timeline_from_notes`
- `main.py:6550 _extract_foreshadowing_from_notes`
- `main.py:6431 _rebuild_vector_index`
- `main.py:6613 _build_source_summary`
- `main.py:5147-5173` 旧 `_rebuild_*` 状态管理（用 SQLite 替代）
- `readthrough_checkpoint.json` 不再使用（DB 取代）

**保留（向下兼容/作为人类可读视图）：**
- `source.md` 改为通读完成后从 DB 渲染的导出文件（只读）
- `source/entities/*.md` 同上，从 DB 渲染的导出

---

## 3. 数据模型（SQLite Schema）

每本书一个 `kb.db` 文件，放在 `usrdata/books/<book_id>/kb.db`。

```sql
-- 章节追踪
CREATE TABLE chapters (
  id              TEXT PRIMARY KEY,           -- 章节 ID（与文件系统一致）
  book_id         TEXT NOT NULL,
  idx             INTEGER NOT NULL,           -- 在 chapter_order 中的位置
  title           TEXT NOT NULL,
  content_hash    TEXT,                       -- 原文 sha256，用于跳过未变章节
  summary         TEXT,                       -- AI 生成的剧情摘要（自然语言段落）
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|done|failed|skipped
  error           TEXT,                       -- 失败原因
  tokens_used     INTEGER DEFAULT 0,
  updated_at      INTEGER NOT NULL
);
CREATE INDEX idx_chapters_book ON chapters(book_id, idx);
CREATE INDEX idx_chapters_status ON chapters(book_id, status);

-- 实体（人物/地点/物品/组织）
CREATE TABLE entities (
  id                TEXT PRIMARY KEY,         -- uuid
  book_id           TEXT NOT NULL,
  canonical_name    TEXT NOT NULL,            -- 标准名
  type              TEXT NOT NULL,            -- 人物|地点|物品|组织|概念
  aliases           TEXT NOT NULL DEFAULT '[]',  -- JSON 数组
  first_chapter_id  TEXT,
  updated_at        INTEGER NOT NULL,
  UNIQUE(book_id, canonical_name)
);
CREATE INDEX idx_entities_book ON entities(book_id);
CREATE INDEX idx_entities_type ON entities(book_id, type);

-- 实体在章节中的提及（每个事实一行）
CREATE TABLE mentions (
  id           TEXT PRIMARY KEY,              -- uuid
  entity_id    TEXT NOT NULL,
  chapter_id   TEXT NOT NULL,
  fact         TEXT NOT NULL,                 -- "李云获得断岳刀" 这种事实陈述
  snippet      TEXT,                          -- 原文片段（供 AI 验证用）
  created_at   INTEGER NOT NULL,
  FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
);
CREATE INDEX idx_mentions_entity ON mentions(entity_id);
CREATE INDEX idx_mentions_chapter ON mentions(chapter_id);

-- 事件（时间线）
CREATE TABLE events (
  id           TEXT PRIMARY KEY,
  book_id      TEXT NOT NULL,
  chapter_id   TEXT NOT NULL,
  story_time   TEXT,                          -- 故事内时间（"开篇之夜""三年后"），可选
  who          TEXT,                          -- 主角实体名（多人逗号分隔）
  what         TEXT NOT NULL,                 -- 发生了什么
  where_loc    TEXT,                          -- 地点
  why          TEXT,                          -- 原因/动机
  consequence  TEXT,                          -- 后果
  created_at   INTEGER NOT NULL
);
CREATE INDEX idx_events_book ON events(book_id, chapter_id);

-- 伏笔与悬念
CREATE TABLE foreshadowing (
  id                    TEXT PRIMARY KEY,
  book_id               TEXT NOT NULL,
  hint_chapter_id       TEXT NOT NULL,        -- 埋下伏笔的章
  hint                  TEXT NOT NULL,        -- 伏笔内容描述
  status                TEXT NOT NULL DEFAULT 'open',  -- open|resolved
  resolved_chapter_id   TEXT,
  resolution            TEXT,
  updated_at            INTEGER NOT NULL
);
CREATE INDEX idx_foreshadowing_book ON foreshadowing(book_id, status);

-- 世界观规则/设定
CREATE TABLE rules (
  id                TEXT PRIMARY KEY,
  book_id           TEXT NOT NULL,
  name              TEXT NOT NULL,            -- "灵气觉醒"
  body              TEXT NOT NULL,            -- 详细规则描述
  first_chapter_id  TEXT,
  updated_at        INTEGER NOT NULL,
  UNIQUE(book_id, name)
);

-- 通读运行时状态（替代 _rebuild_tasks + checkpoint.json）
CREATE TABLE rt_state (
  book_id          TEXT PRIMARY KEY,
  status           TEXT NOT NULL,             -- idle|running|paused|done|error
  current_idx      INTEGER DEFAULT -1,        -- 当前/上次处理到的章节索引
  total            INTEGER DEFAULT 0,
  phase            TEXT,                      -- 阶段描述（"读章节"|"提取实体"...）
  error            TEXT,
  pause_requested  INTEGER NOT NULL DEFAULT 0,  -- 0|1
  stream_buffer    TEXT DEFAULT '',           -- 当前 AI 输出 token 流
  updated_at       INTEGER NOT NULL
);

-- 通读日志（环形缓冲，只保留最近 N 条）
CREATE TABLE rt_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id     TEXT NOT NULL,
  ts          TEXT NOT NULL,                  -- HH:MM:SS
  msg         TEXT NOT NULL
);
CREATE INDEX idx_rt_logs_book ON rt_logs(book_id, id);

-- 嵌入元数据（追踪已嵌入的内容，支持增量更新）
CREATE TABLE embedding_chunks (
  id           TEXT PRIMARY KEY,              -- 与 chromadb 内 chunk_id 对应
  book_id      TEXT NOT NULL,
  source_type  TEXT NOT NULL,                 -- chapter_summary|entity|event|foreshadowing|rule
  source_id    TEXT NOT NULL,                 -- 源记录 id（chapter_id / entity_id ...）
  content_hash TEXT NOT NULL,                 -- 该 chunk 文本的 sha256
  backend_id   TEXT NOT NULL,                 -- "local:bge-small-zh-v1.5" / "api:openai/text-embedding-3-small"
  embedded_at  INTEGER NOT NULL
);
CREATE INDEX idx_embedding_chunks_source ON embedding_chunks(book_id, source_type, source_id);
```

### 3.1 设计要点
- **canonical_name + aliases 解决重名问题**：实体匹配先查 `canonical_name`，再查 JSON 数组 `aliases`
- **mentions.snippet 是质量保险**：检索时把原文片段一并返给 AI，AI 凭原文回答，避免凭印象编造
- **embedding_chunks.backend_id** 是嵌入后端切换的钥匙：换嵌入后端后此表 `backend_id` 与当前不匹配的全部重嵌
- **rt_state 单行表 + pause_requested 标志** 替代内存态 + 文件 checkpoint，原子且可恢复

---

## 4. AI 结构化输出 Schema

通读和 chapter-complete 都让 AI 返回**严格 JSON**，由后端校验。

### 4.1 Prompt 模板（核心要点）

```
你是资料整理员。读完下面这一章后，提取所有信息，输出严格 JSON。
JSON 必须能被 Python 的 json.loads 解析，不要包含任何多余文字。

【章节】{title}
【正文】
{content}

【前情索引（参考，不要复制）】
{prev_context}

【输出 JSON Schema】
{
  "summary": "本章剧情摘要的自然段落（200-3000 字，连贯叙述，不要 bullet，不要复制原文）",
  "entities": [
    {
      "canonical_name": "李云",
      "type": "人物",          // 人物|地点|物品|组织|概念
      "aliases_in_chapter": ["阿云", "李公子"],
      "facts": [
        {"fact": "本章首次出现，是北境国镇北将军之子", "snippet": "原文引用片段，用于核对"}
      ]
    }
  ],
  "events": [
    {
      "story_time": "开篇之夜",
      "who": "李云",
      "what": "在破庙获得断岳刀",
      "where": "城外破庙",
      "why": "老者临终所托",
      "consequence": "成为后续冲突的核心力量",
      "snippet": "原文引用"
    }
  ],
  "foreshadowing_new": [
    {"hint": "老者临终前的低语：黑铁会再开", "snippet": "原文引用"}
  ],
  "foreshadowing_resolved": [
    {"earlier_hint": "上一章提到的红衣女子身份", "resolution": "实为青城公主", "snippet": "原文引用"}
  ],
  "rules": [
    {"name": "灵气觉醒", "body": "16 岁前可觉醒，过期则废"}
  ]
}

【硬性要求】
1. 必须有 summary，其他数组可以为空
2. snippet 必须是原文中实际存在的片段（用来核对，不是你的转述）
3. 不要凭空编造任何内容
4. 只输出 JSON，前后不加任何文字、注释、代码块标记
```

### 4.2 容错策略
- 第一次失败：从 AI 输出中 `re.search(r'\{.*\}', raw, re.DOTALL)` 抓 JSON 段，再 parse
- 第二次失败：把上一次失败的输出连同一句"请只输出严格 JSON"重发
- 三次都失败：标记 `chapters.status = 'failed'`，记录 error，**继续下一章**（不要中断通读）

### 4.3 资源策略
- 单章模式：`_get_effective_context_length` < 32K 时用
- 批量模式：> 32K 时，把多章塞到一个 prompt 一次性出多章 JSON（数组形式）。批量解析失败 2 次自动降级回单章。

---

## 5. 嵌入抽象

### 5.1 接口

```python
# backend/embeddings.py
class EmbeddingBackend:
    backend_id: str   # 用于 embedding_chunks.backend_id 比对
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...

class LocalEmbedding(EmbeddingBackend):
    """sentence-transformers, 首次加载下载模型到 usrdata/models/"""
    def __init__(self, model_name='BAAI/bge-small-zh-v1.5'):
        self.backend_id = f'local:{model_name}'
        # 懒加载：第一次 embed 才 import + 下载
        self._model = None

class APIEmbedding(EmbeddingBackend):
    """复用 settings.base_url / api_key，走 OpenAI 兼容 /v1/embeddings"""
    def __init__(self, base_url, api_key, model='text-embedding-3-small'):
        self.backend_id = f'api:{model}'
```

### 5.2 选择逻辑

```python
def get_embedding_backend(settings):
    choice = settings.get('embedding_backend', 'local')  # 'local' | 'api'
    if choice == 'api':
        return APIEmbedding(settings['base_url'], settings['api_key'],
                            settings.get('embedding_model', 'text-embedding-3-small'))
    return LocalEmbedding(settings.get('local_embedding_model', 'BAAI/bge-small-zh-v1.5'))
```

### 5.3 默认模型
- 本地默认 `BAAI/bge-small-zh-v1.5` (~95MB，512 维，中文优化)
- API 默认 `text-embedding-3-small` (1536 维)
- 在 `settings.json` 增加字段 `embedding_backend`, `local_embedding_model`, `embedding_model`
- 前端在 settings 面板加一个 select，默认 local

### 5.4 切换时的行为
进入通读或 chapter-complete 时检查 `embedding_chunks.backend_id`：
- 表为空 → 用当前后端嵌入
- 任意 chunk 的 backend_id != 当前 → 提示用户"已切换嵌入模型，需要重新嵌入"，提供按钮触发全量重嵌（保留 SQL 数据，只刷向量层）

---

## 6. 控制流（通读 + 暂停 + 继续）

### 6.1 状态机

```
            start (有未完成章)
   idle ─────────────────────→ running
    ↑                            │
    │                       pause_requested=1
    │                            │  (checkpoint 在章末)
    │                            ▼
    │    resume                paused
    │  ←──────────────────────────│
    │                            │
    │      最后一章 done          │
    │  ←─────────────────────────┘
    │
    └── error (致命错误时)
```

### 6.2 通读主循环（伪代码）

```python
def do_readthrough(book_id, settings):
    set_rt_state(book_id, status='running', phase='准备中', pause_requested=0)
    
    chapters = list_chapters_ordered(book_id)   # 从文件系统读
    total = len(chapters)
    set_rt_state(book_id, total=total)
    
    # 关键：处理顺序按 idx，跳过已 done 的章节
    for ch_meta in chapters:
        if get_pause_requested(book_id):
            set_rt_state(book_id, status='paused', phase='已暂停')
            rt_log(book_id, '用户暂停，下次继续从此章')
            return
        
        # 查 DB 看是否已完成
        existing = get_chapter(book_id, ch_meta['id'])
        if existing and existing['status'] == 'done' and existing['content_hash'] == hash(ch_meta['content']):
            continue   # 已处理且原文未变 → 跳过
        
        # 处理本章
        set_rt_state(book_id, current_idx=ch_meta['idx'], phase=f"读: {ch_meta['title']}")
        upsert_chapter(book_id, ch_meta['id'], status='processing', ...)
        
        try:
            structured = ai_read_chapter_structured(settings, ch_meta, 
                                                    prev_context=build_prev_context(book_id),
                                                    should_stop=lambda: get_pause_requested(book_id),
                                                    on_token=lambda t: append_stream(book_id, t))
        except StoppedException:
            # 在 AI 流中检测到暂停
            upsert_chapter(book_id, ch_meta['id'], status='pending')  # 回滚到未处理
            set_rt_state(book_id, status='paused', phase='已暂停')
            rt_log(book_id, '用户暂停（章节中），下次重做此章')
            return
        except Exception as e:
            upsert_chapter(book_id, ch_meta['id'], status='failed', error=str(e))
            rt_log(book_id, f'失败: {ch_meta["title"]} ({e})')
            continue   # 不中断，下一章
        
        # 落库（一个事务）
        with db_transaction(book_id):
            apply_structured_result(book_id, ch_meta['id'], structured)
            upsert_chapter(book_id, ch_meta['id'], status='done', 
                          summary=structured['summary'], 
                          content_hash=hash(ch_meta['content']))
        
        # 增量嵌入（事务外，可恢复）
        incremental_embed(book_id, settings, sources=affected_sources(structured))
        
        rt_log(book_id, f'完成 ({done_count}/{total}) {ch_meta["title"]}')
    
    set_rt_state(book_id, status='done', phase='完成', current_idx=-1)
    rt_log(book_id, '通读完成')
    # 渲染 markdown 视图供人工查阅
    render_markdown_views(book_id)
```

### 6.3 暂停响应
- `/api/book/:id/readthrough/pause` → 单条 SQL：`UPDATE rt_state SET pause_requested=1 WHERE book_id=?`
- 通读主循环每章开始检查；AI 流式输出回调里也检查
- AI 流回调检测到暂停 → 抛 `StoppedException` → 主循环 catch → 把当前章 status 回滚到 `pending` → set rt_state status='paused' → return
- **关键**：当前章的 SQL 写入未提交（因为还没到事务那一步），所以数据库视图就是"这一章未处理"，下次 resume 自然重做这一章

### 6.4 继续
- `/api/book/:id/readthrough/resume` → 启动新线程跑 `do_readthrough` → 主循环按 status 筛选自动跳过已 done 章节
- 完全无副作用、可重入

### 6.5 进程崩溃恢复
- 进程被 kill：rt_state.status 停留在 'running'
- 重启后前端调 status 查询，检测到 running 但无对应线程 → API 自动改 rt_state.status='paused'（探活逻辑放在 status GET handler 里）
- 用户点继续，正常恢复

### 6.6 content_hash 跳过
- 已 done 章节若原文未变 → 直接跳
- 用户修改了某章原文后重通读 → hash 变了 → 重做该章

---

## 7. 增量 chapter-complete

`chapter-complete` 复用通读单章流程，但只跑一章：

```python
def do_chapter_complete(book_id, chapter_id, settings, text=None):
    ch = load_chapter(book_id, chapter_id, text)
    structured = ai_read_chapter_structured(settings, ch, prev_context=build_prev_context(book_id))
    with db_transaction(book_id):
        # 先清掉这一章的旧 mentions / events / foreshadowing
        delete_chapter_artifacts(book_id, chapter_id)
        # 重新写入
        apply_structured_result(book_id, chapter_id, structured)
        upsert_chapter(book_id, chapter_id, status='done', summary=structured['summary'], ...)
    incremental_embed(book_id, settings, sources=affected_sources(structured))
```

**关键差异**：
- 不重建整个向量索引，只 upsert 新章相关的 chunks
- 删除这一章的旧 artifacts 时不影响其他章节的同实体记录（mentions 表按 chapter_id 删，entity 主表不动）
- 完整一个事务内完成 SQL 写入

---

## 8. Q&A 检索路径（核心：能精确答）

替代当前 `get_smart_context()`。混合检索 + 结构化优先。

```python
def qa_context(book_id, user_query='', settings=None,
               chapter_tokens=0, history_tokens=0):
    """
    动态预算 + 优先级填充。
    上下文越大，能塞越多 KB；小上下文则靠精确匹配 + 向量筛选。
    """
    ctx_len = get_effective_context_length(settings)
    budget, need_compress = compute_smart_context_budget(
        ctx_len, chapter_tokens, history_tokens)
    # 不超过实际 KB 大小，避免给大模型留无意义的空白
    budget = min(budget, estimate_total_kb_size(book_id) + 1000)

    parts = []
    backend = get_embedding_backend(settings) if user_query else None

    # 1) 实体名直接匹配（最强信号）
    matched_entities = match_entities_by_name(book_id, user_query)
    # 检查 canonical_name 和 aliases 是否在 query 中出现

    # 2) 向量召回 top-K（K 随 ctx 缩放）
    vector_hits = []
    if user_query.strip() and backend:
        top_k = 8 if ctx_len < 32000 else 15 if ctx_len < 128000 else 30
        q_vec = backend.embed([user_query])[0]
        vector_hits = chromadb_query(book_id, q_vec, top_k=top_k)

    # 3) 时间/章节 query 识别
    if re.search(r'第\s*\d+\s*章|什么时候|时间线|顺序|经过', user_query):
        parts.append(format_events(book_id, filter=matched_entities))

    # 4) 伏笔 query 识别
    if re.search(r'伏笔|悬念|线索|未解|铺垫', user_query):
        parts.append(format_foreshadowing(book_id, status='open'))

    # 5) 按预算填充：直接匹配 > 向量命中 > 其他实体
    used = sum(len(p) for p in parts)
    remaining = budget - used

    # 大 ctx 下单实体块允许放大，小 ctx 强制精简
    per_entity_cap = 3000 if ctx_len < 64000 else 8000 if ctx_len < 256000 else 20000

    candidates = (matched_entities
                  + dedupe_by_entity(vector_hits)
                  + remaining_entities(book_id))
    for ent in candidates:
        if remaining <= 0:
            break
        block = render_entity_block(book_id, ent,
                                    max_chars=min(remaining, per_entity_cap))
        parts.append(block)
        remaining -= len(block)

    return '\n\n---\n\n'.join(parts), need_compress
```

**调用方约定**：拿到 `need_compress=True` 时，先把 messages 历史进一步压到 4K 以内再走 AI 调用。

**返回给 AI 的实体块示例**：

```markdown
## 李云 (人物)
别名：阿云、李公子
首次出现：第 1 章

### 第 1 章: 破庙夜雨
- 北境国镇北将军之子，年方十六
- 老者临终授予断岳刀
  > 原文："那老者……将刀柄塞入李云手中"

### 第 12 章: 城南血夜
- 误杀青城公主未遂
- 与红衣女子结下血仇
  > 原文："剑光乍起……"
```

每个 fact 都带 **章节引用** 和 **原文 snippet**。AI 回答时引用这些就不会编。

---

## 9. API surface（前端契约）

### 9.1 新增/修改

| Method | Path | Body | Response | 说明 |
|--------|------|------|----------|------|
| POST | `/api/book/:id/readthrough/start` | `{}` | `{status:'started'}` | 启动通读（已 paused 的也用它继续）|
| POST | `/api/book/:id/readthrough/pause` | `{}` | `{status:'pausing'}` | 请求暂停 |
| POST | `/api/book/:id/readthrough/resume` | `{}` | `{status:'started'}` | 从 paused 恢复（语义同 start，但 phase 显示"继续中"）|
| POST | `/api/book/:id/readthrough/reset` | `{}` | `{status:'ok'}` | 清空所有 KB 数据，重新通读 |
| GET | `/api/book/:id/readthrough/status` | - | `{status,phase,current_idx,total,stream_buffer,recent_logs[],error}` | 前端轮询 400ms |
| POST | `/api/book/:id/chapter/:cid/redo` | `{}` | `{status:'started'}` | 重做某章（用户修改原文后）|
| POST | `/api/book/:id/embedding/rebuild` | `{}` | `{status:'started'}` | 切换嵌入后端后调用 |

### 9.2 删除/废弃
- `/api/book/:id/readthrough/stop` → 改名 pause（保留旧 path 做 alias 一段时间）
- `/api/book/:id/readthrough/continue` → 改名 resume
- `/api/book/:id/readthrough/clear` → 改名 reset

---

## 10. 前端改造

### 10.1 状态机
```js
rtState = {
  status: 'idle' | 'running' | 'paused' | 'done' | 'error',
  current_idx: -1,  // -1 表示未开始或全部完成
  total: 0,
  phase: '',
  stream_buffer: '',
  recent_logs: [],
}
```

### 10.2 按钮规则
| status | 按钮显示 | 点击行为 |
|--------|---------|---------|
| idle, current_idx == -1 | "开始通读" | POST start |
| idle, current_idx >= 0 | "继续 (N/M)" + "重通读" | POST resume / POST reset |
| running | "暂停" + "停止" | POST pause / POST pause (双倍急停？) |
| paused | "继续 (N/M)" + "重通读" | POST resume / POST reset |
| done | "重通读" | POST reset |
| error | "继续" + "重通读" | POST resume / POST reset |

### 10.3 进度条
直接绑：`width = (current_idx / total) * 100`。
done 状态 = 100%。

### 10.4 polling
保持现有 400ms 轮询 `/status`。后端 `rt_state` 单行查询无压力。

---

## 11. 迁移策略

### 11.1 旧书识别
书目录下检测：
- 有 `source.md` 且大小 > 500 字节 → **旧版数据**
- 无 `kb.db` → **未迁移**

### 11.2 迁移流程
1. 第一次启动新版后端，扫描所有书
2. 对每本旧书：
   - 把 `source.md` 重命名为 `source_legacy_{YYYYMMDD}.md`
   - 把 `source/entities/` 目录改名为 `source/entities_legacy/`
   - 删除 `.vector_db/`（旧 chromadb，嵌入维度不对）
   - 删除 `readthrough_checkpoint.json`
   - 在书的 `meta.json` 加字段 `kb_status: 'needs_rebuild'`
3. 前端打开此书时检测到 `kb_status: 'needs_rebuild'`，在通读面板顶部显示提示：
   > 此书的阅读笔记需要重新生成才能用上新的精确问答功能。原数据已备份在 `source_legacy_*.md`。点击"开始通读"重新初始化。
4. 用户点开始通读 → 正常流程跑 → 完成后 `kb_status: 'ok'`

### 11.3 已发布版本兼容
若用户已经签了某些 commits 升上来，老 source.md 备份留在硬盘永远不删，用户可以从备份手抄关键信息。

---

## 12. 实现顺序与验收

### Phase 1: 存储 + 嵌入基础（独立可验证）
- **任务**
  - 写 `backend/kb_storage.py`：schema + init_db + 所有 DAO 函数
  - 写 `backend/embeddings.py`：抽象类 + LocalEmbedding + APIEmbedding
  - 写 chromadb wrapper：`kb_storage.embed_upsert / embed_query / embed_clear`
- **验收**
  - 在 REPL 中：建一本书 → init_db → upsert 一些 entities → 查询返回正确
  - 切换 LocalEmbedding/APIEmbedding → embed 同一文本，维度不同但功能正常
  - sqlite3 命令行查 `.schema` 看表都建对
- **不依赖任何其他改动**，可以单独跑测

### Phase 2: AI 结构化抽取
- **任务**
  - 写 `kb_pipeline.ai_read_chapter_structured(settings, ch, prev_context)`，返回 dict
  - 写 JSON 校验 + 重试逻辑
  - 写批量版本 `ai_read_chapters_batch_structured`
- **验收**
  - 喂一段已知章节文本，输出 JSON 满足 schema
  - 故意让 AI 输出垃圾，重试逻辑能恢复或正确报失败
  - 批量模式输出能 1:1 对应 N 个章节

### Phase 3: 通读编排器
- **任务**
  - 写 `kb_pipeline.do_readthrough(book_id, settings)` 主循环
  - 写 `apply_structured_result` 落库函数
  - 写 `incremental_embed`
- **验收**
  - 跑一本 5 章的测试书 → 完成后 DB 里有 5 个 chapters、若干 entities/events/foreshadowing
  - 跑到中途调 pause API → 落到 paused 状态，DB 一致
  - 调 resume → 接着跑完
  - kill 进程重启 → status 自动从 running 改 paused → resume 能继续

### Phase 4: chapter-complete
- **任务**
  - 写 `kb_pipeline.do_chapter_complete(book_id, chapter_id, settings)`
  - 替换 `main.py:7083` 的旧函数
- **验收**
  - 已通读的书，新加一章 → 点写完 → 该章数据进 DB 且 Q&A 能找到
  - 改某章原文 → 点写完 → 该章旧 mentions 被清掉、新的写入

### Phase 5: Q&A 检索
- **任务**
  - 写 `kb_pipeline.qa_context(book_id, user_query, budget_chars, settings)`
  - 替换 `main.py:6640` 的 `get_smart_context`
- **验收**
  - 问"李云在第几章首次出现" → 返回的上下文包含 "李云 ... 第 1 章" 且 AI 答得对
  - 问"全书有哪些未解伏笔" → 返回 foreshadowing 表中 status=open 的列表
  - 问"主角性格" → 退化到实体块拼接

### Phase 6: 前端
- **任务**
  - 修改 `index.html` 的通读 UI：替换按钮逻辑、添加 pause/resume 按钮
  - 修改 API 调用：start/pause/resume/reset 四个端点
- **验收**
  - 点暂停 ≤ 2s 反应、按钮变继续
  - 关闭软件再开 → 状态保留、继续按钮可用
  - 进度条按 current_idx/total 显示

### Phase 7: 迁移 + 清理
- **任务**
  - 启动时扫描旧书并标记 `kb_status: 'needs_rebuild'`
  - 前端检测此标识显示提示
  - 删除旧函数（B1-B7 那些）
- **验收**
  - 旧书数据保留备份、提示正确显示
  - 老 API 路径返回 410 或 redirect 到新路径
  - `grep _SimpleEmbedding main.py` 无结果

---

## 12.5 上下文预算（最低 64K 上下文模型的可行性）

**底线模型规格**：9B 参数级，64K 上下文，OpenAI 兼容协议（参考目标：qwenpaw-flash-9b 或同档位的 Qwen2.5-9B / GLM-4-9B-Chat / DeepSeek-Lite 等）。

### 12.5.1 通读阶段单章模式预算
单章模式下，每次 AI 调用：

| 组成 | tokens | 备注 |
|------|--------|------|
| System prompt | ~600 | 资料整理员人设 + 输出格式说明 |
| User prompt 模板 + JSON schema | ~1500 | schema 描述、硬性要求 |
| 前情索引 `prev_context` | ≤ 8000 | 受限填充 |
| 章节正文 | ≤ 8000 | 普通中文章节 3K-5K 字，5K tokens 上限够 |
| **输入合计** | **≤ 18100** | |
| 输出 reserve | ~4000 | 结构化 JSON 通常 1K-3K tokens |
| **总占用** | **~22K / 64K = 34%** | 充裕 |

### 12.5.2 通读阶段批量模式预算
批量模式（`context_window > 32K` 时启用）：

| 组成 | tokens |
|------|--------|
| System prompt + schema | ~2100 |
| 前情索引 | ≤ 6000 |
| 多章正文（N 章） | ≤ 38000 |
| 输出 reserve | ≤ 16000（N 章的 JSON 总和）|
| **总占用** | **≤ 62K / 64K = 97%** |

**安全配置**：批量模式下 `max_batch_content_tokens = ctx * 0.6`，输出 reserve `ctx * 0.25`，留 ~15% 缓冲。  
**单批章数估算**：中文长篇典型章节 3K-5K 字，64K 上下文一批 6-10 章。批量失败 2 次自动降级单章。

### 12.5.3 Q&A 阶段预算

**核心原则**：Smart context 预算不设硬上限。用减法算可用空间，再跟 KB 实际数据大小取小——既不浪费大上下文模型，也不让小模型爆。

```python
def compute_smart_context_budget(ctx_len, chapter_tokens, history_tokens):
    """
    返回 (smart_budget_tokens, need_compress_history)
    """
    sys_overhead   = 1500                        # system prompt + tools 描述
    user_msg       = 500                         # 用户当前消息
    output_reserve = max(2000, ctx_len // 16)    # 输出留 6-25%（ctx 大就多留）
    safety_margin  = ctx_len // 20               # 5% 缓冲，应对 tokenizer 误差

    used_by_others = (sys_overhead + chapter_tokens + history_tokens
                      + user_msg + output_reserve + safety_margin)
    smart_budget = ctx_len - used_by_others

    if smart_budget < 4000:
        # 太挤：告诉调用方先压缩历史
        return max(4000, smart_budget), True
    return smart_budget, False
```

**再叠一层"够用就停"**：
```python
total_kb_tokens = estimate_total_kb_size(book_id)   # 实体 + 时间线 + 伏笔 + 规则
final_budget = min(smart_budget, total_kb_tokens + 1000)  # 全部 KB 都塞得下就不留多余空间
```

**典型 ctx 下的实际预算**（假设当前章 6K，历史 10K，KB 总量 80K）：

| ctx_len | smart_budget 公式值 | 实际取值（min KB 总量+1K） | smart_context 充实度 |
|---------|-------------------|-----------------------|---------------------|
| 32K | ~10.5K | 10.5K | 局部召回，靠向量精挑 |
| 64K | ~39K | 39K | 大半 KB |
| 128K | ~96K | **81K**（KB 上限）| 全量 + 空闲 |
| 200K (Opus) | ~158K | **81K** | 全量 |
| 1M (Gemini) | ~870K | **81K** | 全量，剩 800K+ 空闲 |

**含义**：
- 小 ctx (≤32K)：必须靠向量检索 + 实体精确匹配筛选，只塞最相关的实体块
- 中 ctx (64K-128K)：可以塞绝大部分实体，AI 自己在长上下文里找
- 大 ctx (≥128K)：全 KB 直接 dump，AI 拥有完整知识图，最适合多跳推理

**历史对话压缩**：`_compress_messages_for_context` 已有，保留最近 6 轮 + 压缩旧轮。当 `need_compress_history=True` 时强制启用压缩并把 history_tokens 收到 4K 以内。

**估算 KB 总量**（粗略）：
```python
def estimate_total_kb_size(book_id):
    # 不需要精确，用字符数当 token 估值
    cnt = 0
    for ent in list_entities(book_id):
        cnt += render_entity_block_len(ent)  # 含全部 mentions
    cnt += sum_events_text_len(book_id)
    cnt += sum_foreshadowing_text_len(book_id)
    cnt += sum_rules_text_len(book_id)
    return cnt
```

### 12.5.4 嵌入维度无关
嵌入向量本身不进 LLM context，所以选 512 维（bge-small-zh）还是 1536 维（text-embedding-3-small）对 64K 模型没影响。

### 12.5.5 关键设计约束
1. `_get_effective_context_length(settings)` 必须返回真实数字，**完全以用户在设置里填的为准**。不要硬编码任何 ctx 上限——用户接 Opus (200K)、Gemini (1M、2M) 都应该能吃满
2. 没有"封顶 30K"或类似硬上限。所有预算用减法 + KB 实际大小取小
3. 单章正文若超过 `ctx * 0.3` tokens，需要先切片送 AI（罕见，长篇网文章节一般不会这么长）
4. prev_context 用 `_extract_context_summary(current_source)`（已存在）做摘要，预算 ≤ `ctx * 0.15`，不全量塞
5. 嵌入后端独立于 LLM 配置——切 LLM 不需要重嵌入；切嵌入后端要重嵌入

### 12.5.6 极端情况
- 章节正文极长（>15K tokens，如某些网文水章）→ 警告日志 + 切两半送两次合并结果
- 实体数量极多（>200）→ Q&A smart context 时只挑 top-K 与 query 相关的实体（向量检索筛）
- 多本系列书联合通读 → 系列总通读保持独立路径（series_readthrough），不与单本 KB 混淆

## 13. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| AI 不按 JSON 格式输出 | 中 | 2 次重试 + JSON 段抓取 + 失败标记不中断 |
| AI 漏掉重要细节 | 中 | mentions.snippet 存原文 + Q&A 把原文给 AI 让它"凭原文回答" + 允许用户重做单章 |
| 长上下文模型批量处理后 JSON 数组解析失败 | 中 | 失败 2 次自动降级单章 |
| sentence-transformers 首次下载失败（无网络） | 低 | 把模型预打包到 release，或弹错提示用户切 API |
| chromadb 索引文件膨胀 | 低 | 每 100 次 chapter-complete 做一次 compact |
| sqlite3 并发写（通读线程 + chat 线程同时改） | 中 | 一本书一个 sqlite 文件，开 WAL 模式；所有写经过 `with db_transaction(book_id)` 串行化 |
| 切换嵌入后端时部分 chunk 维度不一致 | 高 | embedding_chunks.backend_id 检查；不匹配时弹提示让用户主动 rebuild |
| 用户中途切换 AI 模型导致摘要风格变 | 低 | 不管，AI 提供商一致性是用户责任 |
| 旧用户数据被无意覆盖 | 高 | 迁移阶段所有旧文件 rename 而非 delete，且备份名带日期 |

---

## 14. 关于"能否完美达成目标"的诚实评估

### 14.1 架构 100% 支持目标
- G1（精确问答）：靠 mentions.snippet + 实体精确匹配 + 向量召回，比现状提升一个数量级
- G2（增量更新）：chapter-complete 走同一管线，DB 事务保证一致性
- G3（进度条/暂停/继续）：rt_state 表 + pause_requested 标志，从设计上消灭了"checkpoint 丢失"
- G4（不出错）：状态机闭合，事务原子，进程崩溃可探活恢复

### 14.2 架构无法兜底的部分
- **AI 提取一遍读漏**：架构允许用户单章重做，但不能保证 AI 第一次就把每个细节都抓全
- **跨章推理**：例如"第 100 章这个人的反应说明他知道第 5 章的秘密"，单章独立提取捕获不到。需要靠 prev_context 传递 + 通读完成后做一次"二轮关联通读"（可选 Phase 8）
- **冷僻语种或非常规文风**：sentence-transformers 中文模型效果取决于训练数据

### 14.3 验证 G1 的硬指标
通读完成后，跑一套预设问题集（在 `tests/qa_eval.json` 准备 30 题）：
- 10 题"X 在第几章首次出现"
- 10 题"X 物品现在归谁"
- 10 题"X 伏笔现在状态"

期望 ≥ 25/30 正确。低于此说明 AI 提取有问题，需要调 prompt。

---

## 15. 下一步（接手 AI 行动）

1. 先读这份文档一遍
2. 跑现有代码看通读功能是什么样：`python backend/main.py`，打开前端，导入一本测试书
3. 实现 Phase 1（kb_storage + embeddings），先单元跑通
4. 实现 Phase 2 / 3 / 4 / 5（按顺序）
5. Phase 6 前端
6. Phase 7 迁移
7. 最后跑预设问题集验证 G1

**实现风格遵循 `CLAUDE.md`**：
- 先想再写，不确定就问用户
- 简洁优先，不为未来需求加抽象
- 精准修改，不顺带优化邻居代码
- 多步骤先列计划再动手

---

## 16. 参考索引

| 现有功能 | 现有代码位置 | 重做后位置 |
|---------|-------------|-----------|
| 通读主循环 | `main.py:7200 do_readthrough` | `backend/kb_pipeline.py:do_readthrough` |
| 单章 AI 摘要 | `main.py:6150 _ai_read_chapter` | `backend/kb_pipeline.py:ai_read_chapter_structured` |
| 实体抽取 | `main.py:6467 _parse_entities_from_notes` | 由 AI 直接输出 JSON，删除 |
| 向量索引 | `main.py:6300 _SimpleEmbedding`, `main.py:6431 _rebuild_vector_index` | `backend/embeddings.py` + `kb_storage.embed_upsert` |
| 智能上下文 | `main.py:6640 get_smart_context` | `backend/kb_pipeline.py:qa_context` |
| chapter-complete | `main.py:7083 _do_chapter_complete` | `backend/kb_pipeline.py:do_chapter_complete` |
| 通读状态 | `main.py:5147 _rebuild_tasks` dict | `kb.db` 的 `rt_state` 表 |
| 通读 API | `main.py:4269 - 4329` | 同位置，POST handler 调用新 kb_pipeline |

---

文档结束。
