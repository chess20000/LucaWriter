# COO 格式规范 v2 —— 一个 IP 的管理规范

> 2026-06-06 · 本文件是 Coobox 与 LucaWriter 共享的**数据格式契约**。
> 配套阅读：`docs/coo-v2-plan.md`（决定与待办）、`docs/coo-vision.md`（愿景与例子）。
>
> **对齐约定**：本规范同时存在两份——Coobox `docs/coo-format.md` 与 LucaWriter `COO.md`，
> 两份正文必须**逐字一致**。改格式 = 两端同步改。

---

## 0. 这是什么

一个 `.coo` 文件 = **一个 IP**（一个世界观 / 一个作品）的完整管理单元。

它不是「一本书的交换格式」（那是已废弃的 v1），而是一座可以拷来拷去、可追溯、谁改了都留名的
**IP 仓库**——参考 git 仓库，以及 SCP 基金会 / 后室（Backrooms）那种「很多人围绕一个共享世界观
各写各的条目」的众创模式。

`.coo` 在物理上是一个带 `.coo` 扩展名的 ZIP。它只存放这个 IP 的创作内容与 AI 资产，**不得**包含
聊天记录、账号数据、私人设置等作者个人数据。

一个 `.coo` 里有三样东西，外加一套留名/防篡改记账本：

| 名字 | 目录 | 性质 | 类比 |
|---|---|---|---|
| **正文** | `books/` | **有序**的叙事：作品 > 书 > 章 | 书架上按顺序摆好的一本本书 |
| **设定** | `lore/` | **无序**的便签：实体/地点/概念/收容档案 | SCP 一条条互不相邻的条目 |
| **阅读线** | manifest 里的 `reading_order` | 一条**有序**清单，定义 AI 通读路径 | 博物馆导览动线 |
| **记账本** | `META-INF/coo-history.jsonl` | 留名 + 防偷改的历史链 | git 的 commit 历史 |

> 关键洞察：**「设定在不在阅读线里」是唯一开关**。一个 lore 条目，加进 `reading_order` → AI 通读时会
> 读到它；不加 → 它安静待在 `lore/` 当背景资料，AI 需要时自己查。不必分两种条目类型。

---

## 1. 目录结构

```
三体.coo                          (= 一个 IP = 一个世界观)
├── manifest.json                # IP 信息 + 书目(含章节清单) + lore 注册表 + 阅读线
├── META-INF/
│   └── coo-history.jsonl        # 记账本：author(留名) + 哈希链(防偷改)，无签名
├── books/                       # 【正文 · 有序】
│   ├── 01_三体/
│   │   ├── chapters/
│   │   │   ├── ch_001.json
│   │   │   └── ch_002.json
│   │   ├── ai/                  #   可选：该书的大纲、卷摘要、章节摘要
│   │   └── cover.webp           #   可选：该书封面
│   ├── 02_黑暗森林/
│   │   ├── chapters/
│   │   └── ai/
│   └── 03_死神永生/
│       ├── chapters/
│       └── ai/
├── lore/                        # 【设定 · 无序 · 便签】
│   ├── 三体人.md
│   ├── 水滴.md
│   └── 黑暗森林法则.md
├── shared/                      # 世界观级共享资产
│   ├── ai/                      #   AI 知识：角色 / 设定 / 时间线 / 知识库
│   │   ├── characters.md
│   │   ├── world_settings.md
│   │   ├── timeline.md
│   │   ├── core_memory.md
│   │   └── kb.db
│   └── vector_db/               #   世界观级向量库
└── assets/
    └── cover.webp               # 封面（Coobox 导入时压成小 WebP）
```

### 路径与命名约定

- 所有 ZIP 内路径用正斜杠 `/`，相对包根。导入方按 `/` 归一化反斜杠。
- 忽略 `__MACOSX/` 及任何目录项。
- 子书目录名格式 `NN_标题`（`NN` 为两位序号，仅为人类可读；真正的顺序以 manifest 里的 `order` 为准）。
- 顶层 manifest 内的所有 `path` 均相对**包根**（如 `books/01_三体/chapters/ch_001.json`、`lore/水滴.md`）。

### 必需 / 推荐文件

**必需**：`manifest.json`（其 `books[]` 至少含一个条目且该条目至少含一章）、`META-INF/coo-history.jsonl`。

**推荐**：`assets/cover.*`、`lore/*.md`、`shared/ai/*`、`shared/vector_db/**`、各子书 `ai/chapter_summaries/*.md`。

> **书不是独立实体**：书只是比「章」高一级的目录容器。书不持有自己的 manifest、UID、作者、简介、语言等元数据——
> 一切管理在顶层 `manifest.json` 的 `books[]` 和 `work` 里。书的目录下只有 `chapters/`（必需）、`ai/`（可选）、封面文件（可选）。

---

## 2. 顶层 manifest.json

描述整个 IP：身份、书目、lore 注册表、阅读线、共享资产。

```json
{
  "format_name": "coo",
  "format_version": 2,

  "work_uid": "coo_64_or_more_random_hex_chars",
  "exported_at": 1780459638.234826,
  "producer": { "app_name": "LucaWriter", "app_version": "2.0.0" },

  "work": {
    "title": "三体",
    "author": "刘慈欣",
    "description": "地球文明与三体文明的两个半世纪。",
    "language": "zh-CN",
    "created": 1780141269.5681436,
    "updated": 1780459638.234826,
    "cover_file": "assets/cover.webp"
  },

  "books": [
    {
      "id": "01_三体",
      "title": "三体",
      "order": 1,
      "path": "books/01_三体/",
      "cover_file": "cover.webp",
      "chapters": [
        {
          "id": "ch_001",
          "title": "科学边界",
          "order": 1,
          "path": "books/01_三体/chapters/ch_001.json",
          "summary_path": "books/01_三体/ai/chapter_summaries/ch_001.md",
          "word_count": 3500,
          "updated": 1780459638.0
        }
      ],
      "ai": {
        "outline_path": "books/01_三体/ai/outline.md",
        "volume_summary_path": "books/01_三体/ai/volume_summary.md"
      }
    },
    {
      "id": "02_黑暗森林",
      "title": "黑暗森林",
      "order": 2,
      "path": "books/02_黑暗森林/",
      "cover_file": "",
      "chapters": [
        {
          "id": "ch_001",
          "title": "上部 面壁者",
          "order": 1,
          "path": "books/02_黑暗森林/chapters/ch_001.json",
          "summary_path": "",
          "word_count": 4200,
          "updated": 1780459638.0
        }
      ],
      "ai": {}
    }
  ],

  "lore": [
    { "id": "lore_sandiren", "title": "三体人", "kind": "entity",  "path": "lore/三体人.md",     "updated": 1780459000.0 },
    { "id": "lore_shuidi",   "title": "水滴",   "kind": "item",    "path": "lore/水滴.md",       "updated": 1780459100.0 },
    { "id": "lore_dff",      "title": "黑暗森林法则", "kind": "concept", "path": "lore/黑暗森林法则.md", "updated": 1780459200.0 }
  ],

  "reading_order": [
    { "type": "chapter", "book": "01_三体", "chapter": "ch_001" },
    { "type": "chapter", "book": "01_三体", "chapter": "ch_002" },
    { "type": "lore", "ref": "lore_shuidi", "note": "此处剧情首次提到探测器" },
    { "type": "volume_boundary", "book": "02_黑暗森林" },
    { "type": "chapter", "book": "02_黑暗森林", "chapter": "ch_001" }
  ],

  "shared": {
    "ai": {
      "characters_path": "shared/ai/characters.md",
      "world_settings_path": "shared/ai/world_settings.md",
      "timeline_path": "shared/ai/timeline.md",
      "core_memory_path": "shared/ai/core_memory.md",
      "kb_path": "shared/ai/kb.db",
      "vector_db_path": "shared/vector_db/"
    }
  },

  "contains": {
    "books": true,
    "lore": true,
    "reading_order": true,
    "summaries": true,
    "knowledge_db": true,
    "vector_db": true,
    "chat_history": false,
    "personal_settings": false
  },

  "provenance": {
    "history_path": "META-INF/coo-history.jsonl",
    "merge_sources_path": "META-INF/coo-merge-sources.json"
  }
}
```

### 字段说明

- `format_version`：整数 `2`。导入方必须拒绝其他版本（v1 不再受支持，见 §9）。
- `work_uid`：IP 的全局稳定标识，`coo_` 前缀 + ≥64 位随机十六进制。重新导出同一 IP 应保持不变。
- `work`：IP（= 作品 = 世界观）的元数据。`title` 必填非空。Coobox 用户主页的「x 个作品」数的就是它。
  `author` 是**整个 IP 的唯一规范作者**。书不持有独立作者。`cover_file` 可为空；为空时展示方必须回退到 `books[]` 按 `order` 排序后的第一本书的封面。
- `books[]`：**有序**书目。书不是独立实体——它只是比章高一级的目录容器，不持有自己的 manifest 或 UID。
  `id` = 子书目录基名，全包内唯一；`order` 为真正排序依据；`chapters[]` 直接内联在此（所有 path 相对包根）。
  `cover_file` 可选（相对该书目录）；`ai` 可选，记录该书的大纲/卷摘要/章节摘要路径（均相对包根）。
- `lore[]`：lore **注册表**（无序集合，仅登记，不排序）。`id` 供 `reading_order` 引用，全包内唯一。
  `kind` 可选，建议取值：`entity`(实体) / `location`(地点) / `concept`(概念) / `item`(物件) / `archive`(收容档案) / `event`(事件)。
- `reading_order`：阅读线，见 §5。可省略/为空（见缺省推导规则）。
- `shared.ai`：世界观级 AI 资产路径（角色、设定、时间线、核心记忆、知识库、向量库）。整段及其中任一字段都可缺省。
- `kb_path` 与 `vector_db_path` 是**可再生缓存**。导入方不得把不受信任 COO 中的数据库直接作为运行时数据库打开；
  应丢弃后在本地重新通读生成。Markdown 类 AI 资产可作为普通文本导入。
- `provenance.history_path`：固定 `META-INF/coo-history.jsonl`。**注意 v2 已删去 `keys_path` 与 `signature_alg`。**
- `provenance.merge_sources_path`：可选。合并过的包用它指向 `META-INF/coo-merge-sources.json`，
  记录被合并分支的最后事件哈希与作者名；该文件属于普通载荷，也必须进入 `changed_files` 校验。

---

## 3. 书目录与章节文件

### books/NN_xxx/ —— 书的目录

书**不持有自己的 manifest**。它只是一个目录容器，里面放：

- `chapters/`：章节 JSON 文件（必需）
- `ai/`：该书专属的 AI 资产——大纲（`outline.md`）、卷摘要（`volume_summary.md`）、章节摘要（`chapter_summaries/*.md`）——全部可选
- 封面文件：可选，文件名任意，扩展名决定 MIME

书的全部元数据（标题、章节清单、AI 资产路径等）都在顶层 `manifest.json` 的 `books[]` 里，
见 §2。书没有 `book_uid`、没有独立作者、没有简介/语言/时间戳——这些要么不存在（书不是独立实体），
要么在 `work` 里（作者、简介、语言）。

### 章节文件 books/NN_xxx/chapters/NNNNN_xxx.json

沿用纯文本章节形状（与 v1 一致，未变）：

```json
{
  "id": "ch_001",
  "title": "科学边界",
  "content": "纯文本正文……",
  "updated": 1780459638.0
}
```

Coobox 阅读时只渲染一个极小的 Markdown 子集：

- 空行 → 分段
- 单独一行的 `***` 或 `---` → 分隔线
- `***文字***`、`**文字**`、`*文字*`、`~~文字~~` → 强调

---

## 4. 设定 lore/

每个 lore 条目 = `lore/` 下的一个 Markdown 文件，并在顶层 manifest 的 `lore[]` 里登记一条。

- **无序**：条目之间没有先后，像一张张便签 / 一条条 SCP 档案。
- 文件内容是自由 Markdown（可写设定、可带一段剧情）。
- 是否被 AI 通读，**完全取决于它有没有出现在 `reading_order` 里**（见 §0 的「唯一开关」）。
- 不在阅读线里的 lore 仍随包分发，作为背景资料；AI 通读时可按需检索（配合 `shared/vector_db/`）。

---

## 5. 阅读线 reading_order（关键设计）

阅读线把「正文章节」和「需要被通读的设定」编织成**唯一一条有序的线**，定义 AI 通读的路径。

### 5.1 它排的是「读者的阅读顺序」，不是「故事内时间」

所以轮回、穿越、插叙、倒叙都不怕——故事内时间可以乱，但读者总归一页页往下读，永远存在唯一一条线。
任何片段都能在这条线上找到插入位置。

### 5.2 条目类型

`reading_order` 是一个有序数组，每个条目有 `type`：

| `type` | 含义 | 字段 |
|---|---|---|
| `chapter` | 读某书的某一章 | `book`(子书 id) + `chapter`(章 id)；可选 `note` |
| `lore` | 在此处读一条设定（把无序便签钉在这个阅读点） | `ref`(lore 注册表 id)；可选 `note` |
| `volume_boundary` | 跨书点：新一卷开始，触发提示注入 | `book`(新一卷的子书 id)；可选 `prompt_override` |

作者可以任意拖动 `chapter` 与 `lore` 条目来定义实际阅读顺序，不要求同一本书的章节连续出现。
当相邻两个 `chapter` 条目的 `book` 不同，通读方必须把它视为一个**隐式**
`volume_boundary` 并注入标准卷次提示。显式 `volume_boundary` 仍可写入，主要用于指定
`prompt_override`；若它与隐式边界重合，只注入一次。

示例：

```json
"reading_order": [
  { "type": "chapter", "book": "01_三体", "chapter": "ch_001" },
  { "type": "chapter", "book": "01_三体", "chapter": "ch_002" },
  { "type": "lore", "ref": "lore_shuidi", "note": "此处剧情首次提到探测器" },
  { "type": "volume_boundary", "book": "02_黑暗森林" },
  { "type": "chapter", "book": "02_黑暗森林", "chapter": "ch_001" }
]
```

引用完整性要求：

- `chapter` 条目的 `book` 必须存在于 `books[]`，`chapter` 必须存在于该子书的 `chapters[]`。
- `lore` 条目的 `ref` 必须存在于 `lore[]`。
- `volume_boundary` 的 `book` 必须存在于 `books[]`。

### 5.3 缺省推导（`reading_order` 省略或为空时）

阅读线**可以不写**。缺省时，导入/通读方按以下规则自动推导出等价路径：

1. 按 `books[].order` 升序遍历每本书；
2. 每本书内按 `chapters[].order` 升序产出 `chapter` 条目；
3. 从第二本书起，在该书首章前自动插入一个 `volume_boundary`（`book` = 该书 id）；
4. 不钉任何 `lore`（设定全部退居背景）。

因此**单书 IP**（只有一个 `books/01_*/`）缺省阅读线就是「按章顺读、无卷次提示」，等同旧单书行为。

### 5.4 卷次切换提示注入（`volume_boundary`）

通读跨书边界时，既不能完全沉默切换（AI 不知道换了书），也不能当全新故事清空记忆。
正确做法：**告知新一卷开始，但故事和世界观延续**。

在到达 `volume_boundary` 时，通读方在「上一本书最后一章」与「新一本书第一章」之间，给 AI 注入一句系统提示。
**标准文案（两端必须一致）**：

```
新一卷《{book_title}》开始了。故事延续自世界观「{work_title}」。
```

其中 `{book_title}` 取该 `book` 在 `books[]` 里的 `title`，`{work_title}` 取 `work.title`。
若条目带 `prompt_override`，则用它整体替换该句。

这一句只做一件事：告诉 AI 换卷了、世界观不变、继续读。**不附简介**（AI 还没读到，哪来简介）。
世界观记忆（角色、设定、历史、知识库、向量库）全部保留，只是标记此处有时间跳跃 / 视角切换，
不把新卷首章当上一卷的直接续写。

---

## 6. 世界观级 AI 资产 shared/

- `shared/ai/`：整个 IP 的角色、世界设定、时间线、核心记忆、知识库（`kb.db`）。
- `shared/vector_db/`：IP 级向量库，供 AI 按需检索（含未进阅读线的 lore）。
- 路径在顶层 manifest 的 `shared.ai` 里登记；整段可缺省。

---

## 7. 留名与防篡改：META-INF/coo-history.jsonl

走 **git 模型**：目标是 `.coo` 能像 git 仓库一样拷出去、脱离 Coobox 网站也认账。

- **留名** = 每条记录带 `author` 字段：**自己填、不验证、重名随意，但必填**（= git 的 commit author）。
- **防偷改** = 哈希链：每条记录含前一条的哈希（= git 的 commit hash 链）。
- **不做签名**：v2 **砍掉 Ed25519 签名**，删除 `META-INF/coo-keys.json`。签名是用来「防冒充某个人」的，
  本项目不在乎冒充（重名随意），留着纯添乱。

`coo-history.jsonl` 每行一个 JSON 事件（JSON Lines）。每次改动包后追加一条。事件形状：

```json
{
  "format_version": "coo-provenance-v2",
  "event_id": "evt_3f9a…",
  "event_type": "export",
  "author": "chen7",
  "client_name": "LucaWriter",
  "client_version": "2.0.0",
  "client_id": "lucawriter_…",
  "created_at": 1780460000.12,
  "changed_files": [
    { "path": "manifest.json", "sha256": "…", "size": 1024 },
    { "path": "books/01_三体/chapters/ch_001.json", "sha256": "…", "size": 8123 }
  ],
  "previous_event_hash": "",
  "event_hash": "…"
}
```

字段：

- `author`：**必填**，自由填写的留名字符串，不校验、可重名。
  此字段记录的是「谁执行了这次导出/编辑」（类似 git 的 committer），不是作品内容的作者（内容作者见 `work.author`）。
- `event_type`：`export` / `edit` / `merge` 等，描述这次事件。
- `changed_files[]`：本次事件覆盖的「当前完整载荷」清单——即除控制文件外、包内每个文件的
  `{path, sha256, size}`，按 `path` 升序。（与 v1 一致：记录的是改动后包的全量快照，便于「最后一条 = 当前文件」核对。）
  **如果本次导出时载荷与上一条事件完全一致，不得追加新事件**（避免无修改的重复导出产生冗余记录）。
- `previous_event_hash`：上一条事件的 `event_hash`；第一条为 `""`。
- `event_hash`：本事件的哈希，算法见 §8。
- **已移除字段**（相对 v1）：`signature_alg`、`public_key_id`、`signature`。

---

## 8. 篡改校验模型

### 8.1 规范化与哈希

- **Canonical JSON**：`json.dumps(event_without_event_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`，
  按 UTF-8 编码。即：去掉 `event_hash` 字段后，键名排序、无多余空格、不转义非 ASCII。
- **event_hash** = 该 canonical bytes 的 `sha256` 十六进制小写。
  （v1 是「去掉 `signature` 和 `event_hash` 再算」；v2 没有签名，故只去掉 `event_hash`。）
- 文件哈希 `sha256` 同为十六进制小写，对文件原始字节计算。

### 8.2 控制文件

载荷比对时排除的**控制路径**只有一个：

```
META-INF/coo-history.jsonl
```

（v1 还排除 `META-INF/coo-keys.json`，v2 已无此文件。）另外忽略目录项与 `__MACOSX/`。

### 8.3 校验通过条件

一个 `.coo` 通过篡改校验，当且仅当：

1. `manifest.json` 存在、可解析、`format_name == "coo"`、`format_version == 2`；
2. `coo-history.jsonl` 至少一条事件；
3. 每条事件的 `event_hash` 等于其 canonical bytes 的 sha256（§8.1）；
4. 每条事件的 `previous_event_hash` 等于上一条的 `event_hash`（首条为 `""`）——链不断；
5. 每条事件 `author` 非空；
6. **最后一条**事件的 `changed_files`（按 path 排序）逐项等于当前包的实际载荷
   `{path, sha256, size}`（排除 §8.2 控制路径），完全一致。

> **已知局限（可接受）**：纯哈希链挡得住「偷偷改一条还不被发现」，挡不住「把整条历史推倒重写」。
> git 也挡不住后者，靠的是「大家手里都有副本，你重写了跟别人对不上就露馅」。这种 git 式多副本兜底，对本项目够用。

### 8.4 一个最小例子（哈希为示意占位，非真实计算值）

```jsonl
{"author":"chen7","changed_files":[{"path":"manifest.json","sha256":"aaaa…","size":1024}],"client_id":"lucawriter_01","client_name":"LucaWriter","client_version":"2.0.0","created_at":1780460000.12,"event_id":"evt_001","event_type":"export","format_version":"coo-provenance-v2","previous_event_hash":"","event_hash":"H1"}
{"author":"someone_else","changed_files":[{"path":"manifest.json","sha256":"bbbb…","size":1100}],"client_id":"lucawriter_02","client_name":"LucaWriter","client_version":"2.0.0","created_at":1780470000.00,"event_id":"evt_002","event_type":"edit","format_version":"coo-provenance-v2","previous_event_hash":"H1","event_hash":"H2"}
```

第二条的 `previous_event_hash` = 第一条的 `event_hash`（`H1`），链相连；最后一条（`H2`）的
`changed_files` 必须与当前包文件逐字节对得上。

> 注：上面写成一行内含 `event_hash` 只为示意。实际计算 `event_hash` 时，是对**去掉 `event_hash` 字段后**
> 的 canonical JSON 求 sha256。

---

## 9. 与 v1 的关系：推倒重来，不兼容

- COO 仍在开发阶段，**没有真实用户**，无需数据迁移、无需向后兼容。
- v2 与 v1 **不兼容**：导入方遇到 `format_version != 2` 一律拒绝。
- 两端现有的 coo 处理代码全部重写：
  - Coobox：`import_coo()`（改为多书/lore/阅读线）、`coo_provenance.py`（保留 author+哈希链、删签名）、
    数据模型加「作品/世界观」父层、删 series、模板与主页改「x 个作品」。
  - LucaWriter：`cooverter` 系列、`backend/` 的 coo 处理、`local_llm/` 加卷次切换提示注入。

（v1 的「单书 = 顶层 manifest 直接挂 chapters[]」结构已废弃；v2 里「单本书的 IP」表现为只有一个
`books/01_*/` 的包，缺省阅读线即可。）

---

## 10. 对齐约定与待定项

**对齐**：本规范的两份副本（Coobox `docs/coo-format.md` 与 LucaWriter `COO.md`）正文必须逐字一致。任何字段改动两端同步。

**本版已拍板的命名选择**（如需改，改这一处并同步两端）：

- 顶层标识对象用 `work`（= 作品 = 世界观 = IP），uid 为 `work_uid`；网站「x 个作品」对应它。
- 阅读线字段名定为 `reading_order`；卷次切换条目类型名为 `volume_boundary`。
- `format_version` 用整数 `2`；记账本 `format_version` 用字符串 `"coo-provenance-v2"`。

**留给后续（本规范未深入，见 `coo-v2-plan.md` §7）**：

- 多作者并发的 history 由单链升级为 DAG（git merge 式）——当前线性链够用。
- 社区治理（评分 / 低质删除）——SCP 式众创规模化时再说。
