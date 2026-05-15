# LucaWriter 使用说明

## 简介

LucaWriter 是一款面向小说作者的长文本创作辅助工具。它内置 AI 写作搭档（Luca），支持多章节管理、AI 对话、全书摘要、大纲整理、伏笔追踪等功能。

## 快速开始

1. **启动软件**：运行 `python3 backend/main.py`，浏览器自动打开 `http://127.0.0.1:10000`。
2. **首次使用**：注册管理员账号（仅首次），随后登录进入工作台。
3. **新建书本**：点击书架上的「+ 新建书本」，选择「新建空白书本」或「导入现有小说」。每本书独立保存章节、大纲、笔记和 AI 对话记录。

## 开发与测试约定

- **本地功能测试统一使用 10000 端口**：浏览器验证优先打开 `http://127.0.0.1:10000`，不要再用临时 `20000` 端口测试 LucaWriter 主界面。
- **旧知识库兼容优先**：已经通读生成过的 `kb.db` 必须能继续使用。通读成本很高，后续开发不得要求用户为了新功能重通读；新增表、字段和索引应通过惰性迁移 / `CREATE TABLE IF NOT EXISTS` / 兼容查询接入旧数据。只有在用户明确选择“重通读”时，才清空或重建已有知识库。

## 核心功能

### 1. 章节管理
- **左侧边栏**：显示当前书本的所有章节，可拖拽排序。
- **新建/删除**：随时新增章节；删除的章节进入「回收站」，可恢复。
- **自动保存**：编辑内容时自动保存到本地，无需手动按保存键。
- **改书名**：在书架界面点击书本卡片右上角的「⋯」菜单，选择「改书名」。

### 2. AI 对话（右侧面板）
- 随时向 Luca 提问：
  - 询问剧情设定、人物关系
  - 请求剧情建议、卡文破局
  - 让 Luca 帮你回顾前文（需先完成「摘要」）
- **标注功能**：Luca 可以在正文中为你添加荧光笔标注（黄/绿/粉/蓝），指出重点或批注。
- **多轮对话**：支持多轮上下文，Luca 会基于当前书本的上下文回答。
- **角色设定**：Luca 是你的写作搭档 / 助理，面向用户本人服务，称呼用户为「你」。

### 3. 摘要全书（核心功能）
- 点击顶部「摘要」按钮，Luca 会逐章生成章节摘要，并将它们组合成全书摘要：
  - 为每章生成详细章节摘要（用自己的语言复述，不复制原文）
  - 提取人物、事件、设定、数值、伏笔
  - 自动合并成一份 `source.md` 全书摘要笔记
  - 自动提炼 `outline.md` 故事大纲
- **摘要后能力**：完成摘要后，Luca 对全书的掌握度大幅提升，能准确回答「前面发生了什么」「某某是谁」「这个伏笔是什么」等问题。

### 4. 本章写完（单章摘要）
- **按钮触发**：编辑器顶部「✓ 本章写完」按钮，点击后 AI 会为当前章节单独生成摘要，并增量更新到 `source.md`。
- **AI 自动触发（隐藏功能）**：在 AI 聊天中明确告诉 Luca「这章写好了」，Luca 判断后会自动调用本章摘要工具，无需手动点击按钮。
- **结果保存**：单章摘要会写入章节数据，同时追加到全书摘要笔记中。

### 5. 大纲与记忆
- **大纲面板**：维护世界观、人物、时间线、关键事件、规则等。
- **AI 建议**：让 Luca 基于现有内容生成大纲建议，作者可一键采纳或修改。
- **记忆更新**：Luca 会根据最新写作内容自动更新「核心记忆」，帮助追踪长篇连载的设定。

### 6. 生成工具
- **章节摘要**：为单章生成结构化摘要（出场角色、核心事件、伏笔等）。
- **读者预言**：基于摘要笔记，模拟资深读者写长评，预测剧情走向。
- **时间线**：从全书笔记中梳理故事内时间线节点，支持子时间线展开。

### 7. 导入与导出
- **支持导入**：`.txt`、`.md`、`.docx`、`.pdf`、`.epub`
  - 自动识别章节标题并拆分章节。
  - EPUB 会提取书名、章节名，过滤封面/目录等非内容页。
- **支持导出**：
  - `.zip`：导出全书所有章节的 JSON 源文件。
  - `.md`：导出为合并的 Markdown 文稿。
  - `.txt`：导出为纯文本文稿。
  - `.epub`：导出为 EPUB 电子书（可在书架界面为每本书填写书名、作者、简介和封面）。
  - 导出入口：书架界面的书本卡片菜单（⋯）。

### 8. 设置（⚙️）
- **AI 提供商**：支持配置多个 API 预设（LMStudio / DeepSeek / MiniMax 等），随时切换。
- **参数调整**：模型、温度、最大 Token、自动评论开关、系统提示词等。
- **模型列表**：点击「获取模型列表」可自动拉取远端可用模型。
- **本地模型（可选）**：如果你自行放置了 llama-server 和模型文件到 `local_llm/` 目录，可使用「本地 Llama.cpp」预设完全离线运行。

## 界面可读性（重要）

- **以用户眼睛为先**：界面配色、字号、行距和背景对比直接影响长时间阅读与写作的舒适度，开发者应优先保证高对比及可切换的深/浅模式。
- **可切换浅/深主题**：当用户设置的主题色（accent）偏暗时，应提醒并建议切换到浅色界面以提升文字可读性，尤其编辑器区域应使用不透明的面板色以保证文字对比。
- **字体与排版**：统一正文主色，适当增加行高并使用易读字体，保证选区、光标与高亮具有良好可见性。

## 界面说明

- **顶部栏**：书本标题、摘要按钮、设置按钮、退出登录、任务队列（显示后台 AI 任务状态）。
- **左侧栏**：章节列表，点击切换章节；支持拖拽排序。
- **中间编辑区**：纯文本编辑器，专注写作。顶部有「本章写完」按钮。
- **右侧面板**：AI 对话区域。
- **底部面板**：时间线、读者预言、大纲、摘要笔记等 Tab。

## 使用建议

- **先生成摘要再深聊**：如果书稿较长，建议先运行「摘要全书」，这样 Luca 能基于完整资料给出高质量回答。
- **定期整理大纲**：随着剧情推进，定期让 Luca 更新大纲和记忆，防止长篇连载设定崩塌。
- **善用标注**：对话中可要求 Luca 在原文中标注重点，方便后续修改。
- **单章写完及时生成摘要**：每完成一章后点击「本章写完」，Luca 会立即为该章生成摘要并更新全书笔记，比攒到后面一次性生成全书摘要更省心。
- **本地模型**：如果担心隐私或网络，可切换到「本地 Llama.cpp」预设，完全离线运行（需自行配置模型文件）。

## 常见问题

**Q: Luca 回答的内容不符合前文？**
A: 请确认是否已完成「摘要全书」。未生成摘要时，Luca 只掌握最近几章摘要，不了解全书细节。

**Q: 摘要过程中可以关闭页面吗？**
A: 可以。摘要在后台运行，但关闭后端程序会停止摘要。停止后再次点击「开始摘要」会从头重新开始。

**Q: 导入的文件章节错乱？**
A: LucaWriter 会按常见章节标题格式自动拆分。如果原文格式特殊，可手动调整章节顺序。

**Q: AI 对话有字数限制吗？**
A: 取决于所选模型的上下文窗口。在线模型通常支持 8K-128K token；本地模型的上下文长度取决于你部署的模型配置。

**Q: 弹窗里的输入框选中文本时，鼠标滑到窗口外面松开，窗口会关掉？**
A: 该问题已在最新版本修复。现在只有真正点击黑色背景区域时才会关闭弹窗，选中文字再拖到外面松开不会误关闭。

## 对话摘要 (2026-05-13)

### 本次会话完成的工作

**UI 大改造：Luca 聊天框从右侧移到左侧**
- 整体布局：trigger(18px) → leftStack(chat + 章节列表overlay) → resizeHandle → editor-area
- 章节列表以 absolute overlay 方式覆盖在聊天框上方（z-index:5），展开时遮住聊天框
- 底栏：AI 输入框在左边（与聊天框宽度同步 via ResizeObserver），Tab 栏（时间线/预言/大纲/摘要）在右边
- sendAI() 按 Enter 后自动收起章节列表

**底栏溢出修复（核心架构变更）**
- `.app` 从 `display:flex;flex-direction:column` 改为 `display:grid;grid-template-rows:38px 1fr auto`
- Grid 引擎物理约束行高，彻底解决底栏展开时超出屏幕的问题

**其他交互优化**
- 底栏点击可靠性：引入 4px 拖拽阈值区分点击/拖拽，移除 grip handler
- 章节列表交互：移除展开/收起延迟和 opacity 动画，鼠标移入秒开、移出秒关
- 鼠标移到窗口最左边缘不再误触发章节列表收起（relatedTarget === null 时返回 false）
- 思考文字旋转速度从 2000ms 改为 3000ms
- 删除 syncAIPadding() 功能（不再随底栏抬升聊天消息）
- 删除 sidebar-header（返回按钮、章节标签），回收站按钮移到 footer 与"新建章节"并排
- 底栏展开时隐藏 AI 输入框（纯 CSS：`.bottom-panel:not(.collapsed) #aiInputZone{display:none}`）

## 对话摘要 (2026-05-14)

### 本次会话完成的工作

**知识库核对功能（AI 设定纠错）**
- 从 Claude Code 历史对话中提取并续写未完成的功能（Claude Code 额度耗尽停在了 CSS 阶段）
- 后端（main.py）：在 AI chat 系统提示词中注入 `[CITE]` 和 `[PROPOSE_KB_EDIT]` 工具说明，AI 可以在聊天中引用知识库出处、提议修改设定
- 后端（main.py）：解析 AI 回复中的 `[CITE]` 标签 → 查询 kb_storage 获取章节/摘要信息，生成 citation 卡片数据
- 后端（main.py）：解析 AI 回复中的 `[PROPOSE_KB_EDIT]` 标签 → 自动创建 proposal 记录，返回 proposal 卡片数据（含 old_value/new_value diff）
- 后端（main.py）：清理 AI 回复中的 CITE/PROPOSE_KB_EDIT 标签（不暴露给用户），通过 task status 的 `kb_citations`/`kb_proposals` 字段传给前端
- 前端（index.html）：`_doSendAI` 轮询回调中保存 `kb_citations`/`kb_proposals` 到消息对象
- 前端（index.html）：`renderAIMessages` 中渲染 citation 卡片（含跳转按钮）和 proposal 卡片（含确认/拒绝按钮 + diff 显示）
- 前端（index.html）：`kbJumpToChapter()` 跳转到引用所在章节并闪烁高光
- 前端（index.html）：`kbConfirmProposal()` / `kbRejectProposal()` 调用后端 API 确认或拒绝修改提议
- Claude Code 已完成的部分（无需修改）：kb_storage.py 新表（kb_proposals/kb_edit_log）+ DAO 函数 + 5 个 API endpoint + CSS 样式

**知识库核对功能优化——主动提议 + 弹窗**
- 后端（main.py）：增强系统提示词，添加【主动提议规则】，要求 AI 在以下情况必须主动使用 `[PROPOSE_KB_EDIT]`：用户说的和知识库矛盾、用户明确纠正、AI 自己发现信息可能过时、用户提到的设定和掌握的不一致
- 前端（index.html）：proposal 从内联卡片改为弹窗形式，弹窗定位在聊天框内部（`position:absolute` 相对于 `right-sidebar-section`），不影响写作区域
- 前端（index.html）：`kbShowProposalModal()` 自动弹出修改提议弹窗，支持多个 proposal 逐个处理
- 前端（index.html）：`kbModalConfirm()` / `kbModalReject()` 处理弹窗按钮，确认/拒绝后自动跳到下一个或关闭弹窗

## 对话摘要 (2026-05-14 #2)

### 本次会话完成的工作

**禁止浏览器自动填充 AI 输入框**
- 为 `#aiInput`（问 Luca 输入框）和 `#seriesAIInput`（系列规划输入框）添加 `autocomplete="off"` 和 `data-form-type="other"` 属性，防止 Edge 浏览器弹出自动填充建议

**审查并确认 Codex 完成的大规模重构（2303 行新增）**
- 后端 kb_storage.py（+659 行）：新增 kb_proposals/kb_edit_log 表、consistency_alerts 表、timeline_event_meta/timeline_relations 表、embedding chunks 管理、RT 状态管理、ChromaDB wrapper 等
- 后端 kb_pipeline.py（+714 行）：新增 chapter_outline()、timeline_map()、consistency_check()、reread_passages()、generate_short_prediction()、qa_context() 等核心函数
- 后端 main.py（+512 行）：新增 5 个 API 端点（chapter-kb、timeline-map、prediction-current、consistency-check、kb-reread）、_do_kb_reread_task、时间线编排调度等
- 前端 index.html（+516/-129 行）：大纲面板改为"吃书雷达+本章有用信息"双栏、时间线改为 timeline-map 可视化、预言改为自动更新、新增 consistency radar 定时检测、KB proposal 弹窗等
- 所有代码编译通过，服务器正常启动

## 对话摘要 (2026-05-14 #3)

### 本次会话完成的工作

**编辑器内搜索与替换功能**
- 前端（index.html CSS）：新增搜索栏样式（`.search-bar`、`.search-row`、`.search-input`、`.search-count`、`.search-btn`、`.search-replace-row`、`.search-close`、`.kb-find-btn`），搜索匹配高亮样式（`.search-match`、`.search-current`）含深/浅主题适配
- 前端（index.html HTML）：在 `.editor-body` 内添加浮动搜索栏（`#searchBar`），包含搜索输入框、匹配计数、上/下导航按钮、替换切换按钮、替换输入框和替换/全部替换按钮
- 前端（index.html JS）：新增搜索核心逻辑——`openSearch()`、`closeSearch()`、`doSearch()`/`_doSearch()`、`_refreshSearchMatches()`、`searchNav()`、`_scrollToMatch()`、`searchReplaceOne()`、`searchReplaceAll()`、`toggleSearchReplace()`、`findEntity()`
- 前端（index.html JS）：修改 `syncHighlights()` 以支持搜索匹配高亮与标注高亮共存——搜索匹配作为 `sstart`/`send` 事件类型参与事件排序，当前匹配项用 `search-current` 类，其他匹配用 `search-match` 类
- 前端（index.html JS）：`onContentChange()` 和 `loadChapterData()` 中搜索打开时自动刷新匹配
- 快捷键：Ctrl+F 打开搜索（覆盖浏览器默认）、Ctrl+H 打开搜索+替换、Escape 关闭搜索、F3/Ctrl+G 导航、Shift+F3/Ctrl+Shift+G 反向导航
- 键盘控制模式：搜索输入框中按 Enter 进入导航模式，方向键前后移动匹配项；从实体名点击进入时直接进入导航模式（焦点在编辑器），编辑器中方向键/Enter 也可导航
- 大纲面板：实体（entities）、设定（rules）、事件（events who 字段）的 `kb-item-head` 中添加🔍查找按钮，点击调用 `findEntity(name)` 填入搜索词并直接进入方向键导航状态

## 对话摘要 (2026-05-14 #4)

### 本次会话完成的工作

**后端移除所有时间线（timeline）相关代码**
- 后端（main.py）：从 `DEFAULT_OUTLINE` 中移除 `timeline`、`timeline_nodes` 字段
- 后端（main.py）：移除 `/api/book/:id/timeline-map` API 端点
- 后端（main.py）：移除文件类型 `timeline` 的读取分支
- 后端（main.py）：移除 3 处 `_schedule_timeline_after_kb_edit()` 调用（kb-proposal-confirm、kb-edit-apply、kb-edit-undo）
- 后端（main.py）：从 outline-save 的字段列表中移除 `timeline` 和 `timeline_nodes`
- 后端（main.py）：移除 `timeline-generate` action 整个代码块（含 `do_timeline_task` 函数）
- 后端（main.py）：移除 `timeline-detail` action 整个代码块（含 `find_node` 嵌套函数）
- 后端（main.py）：从 generate-stream 中移除 timeline 类型（type_map、prompt 生成、结果解析三个块）
- 后端（main.py）：从 `_looks_like_kb_correction` 正则中移除 `时间线`
- 后端（main.py）：移除 `save_timeline_md()` 和 `get_timeline_md()` 函数
- 后端（main.py）：移除 timeline.md chunking（embedding 索引）
- 后端（main.py）：移除 `_extract_timeline_from_notes()` 函数
- 后端（main.py）：从 smart context 中移除 timeline.md 读取
- 后端（main.py）：系统提示词中 `时间线关系` → `事件关系`、`时间线问题` → `连贯性问题`、`按时间线排列` → `按故事内时间排列`、移除记忆更新模板中的 `## 时间线` 行
- 后端（main.py）：移除 `_run_timeline_arrange_task()` 函数
- 后端（main.py）：移除 `_TIMELINE_EDIT_FIELDS`、`_schedule_timeline_arrange()`、`_schedule_timeline_after_kb_edit()` 三个定义
- 后端（main.py）：从 `_schedule_kb_after_write_jobs()` 中移除 timeline 编排调度
- 后端（main.py）：从大纲生成 prompt 和结果解析中移除 `timeline` 字段

## 对话摘要 (2026-05-14 #5)

### 本次会话完成的工作

**AI 活跃指示器：从轮询改为 SSE 推送**
- 后端（main.py）：新增 `import queue`、`_ai_sse_clients = []`、`_ai_sse_lock = threading.Lock()`
- 后端（main.py）：新增 `_notify_sse_clients()` 函数——使用 `queue.Queue` 向所有 SSE 客户端推送 `{"count": N}` 消息，死连接自动清理
- 后端（main.py）：在 `register_ai_connection()` 和 `unregister_ai_connection()` 末尾调用 `_notify_sse_clients()`
- 后端（main.py）：新增 `GET /api/ai-activity` SSE 端点——每个连接创建独立 `queue.Queue`，handler 线程从 queue 阻塞读取消息写入 response，30 秒无消息发送 keepalive，客户端断开时从列表移除
- 前端（index.html）：删除 `_activeConnections`、`_connPollTimer`、`_startConnPoll()`、`_stopConnPoll()` 轮询代码
- 前端（index.html）：新增 `_aiActivityES`（EventSource 实例）、`_activeAICount`、`_startAIActivity()`、`_stopAIActivity()`
- 前端（index.html）：`_startAIActivity()` 创建 EventSource 连接 `/api/ai-activity`，`onmessage` 解析 count 并调用 `_renderAIDots()`，`onerror` 关闭后 3 秒自动重连
- 前端（index.html）：`init()` 中 `_startConnPoll()` → `_startAIActivity()`
- 保留旧 `/api/active-connections` 端点（向后兼容）

## 对话摘要 (2026-05-14 #6)

### 本次会话完成的工作

**砍掉时间线前端入口**
- 前端（index.html CSS）：删除 `.timeline-pane`、`.timeline-toolbar`、`.timeline-scroll`、`.timeline-track`、`.tl-node`、`.tl-dot`、`.tl-line`、`.tl-label`、`.tl-hint`、`.tl-children`、`.tl-child`、`.tl-empty` 等旧版时间线样式；删除 `@property --tl-zoom` 和 `.timeline-map*`、`.timeline-seg*`、`.timeline-line`、`.timeline-event*`、`.timeline-dot`、`.timeline-card*`、`.timeline-uncertain` 等新版时间线可视化样式
- 前端（index.html CSS）：`.timeline-pane,.prediction-pane,.outline-pane` → `.prediction-pane,.outline-pane`
- 前端（index.html HTML）：删除底栏「时间线」Tab 按钮；删除 `#timelinePane` 整个面板（含 `#timelineMeta`、`.timeline-hint`、`#timelineResult`）
- 前端（index.html HTML）：通读提示文案从「时间线和本章大纲」改为「本章大纲」
- 前端（index.html JS）：删除 `timelineZoom`、`TL_ZOOM_MIN/MAX/STEP` 变量；删除 `loadTimeline()`、`_applyTimelineZoom()`、`_setTimelineZoom()`、`_renderTimelineEmpty()`、`_onTimelineWheel()`、`_onTimelineClick()`、`setupTimelineInteractions()`、`renderTimelineMap()`、`generateTimeline()`、`expandTimelineNode()` 函数
- 前端（index.html JS）：`switchBottomTab()` 移除 timeline 分支；`openBottomPanel()` 移除 timeline 分支；`init()` 移除 `loadTimeline()` 调用；kb-proposal-confirm 回调中 `loadTimeline()` → `loadPredictionCurrent()`；本章写完回调中移除 `loadTimeline()`
- 前端（index.html JS）：`refreshChapterKnowledge()` 移除 `loadTimeline()` 调用
- Landing 页面：删除「时间线」相关文案
- **后端代码全部保留**：数据库建表（`timeline_event_meta`、`timeline_relations`）、DAO 函数、API 端点（`/timeline-map`、`timeline-generate`、`timeline-detail`）、调度函数（`_schedule_timeline_arrange`、`_schedule_timeline_after_kb_edit`）、`save_timeline_md`/`get_timeline_md`、`_extract_timeline_from_notes`、`arrange_timeline_ai`、`timeline_map` 等全部保留，为后续重做时间线功能留底

## 对话摘要 (2026-05-15)

### 本次会话完成的工作

**后端时间线代码恢复验证与修复**
- 逐文件对比 `git diff HEAD`，确认三个后端文件的时间线/kb 代码恢复状态
- **kb_storage.py**：零差异，完全匹配 git HEAD ✅
- **kb_pipeline.py**：修复 `format_events()` 中 `## 事件` → `## 时间线`（2 处），其余代码完整 ✅
- **main.py**：所有时间线代码已恢复完整（`timeline-generate`、`timeline-detail`、`_schedule_timeline_arrange`、`_schedule_timeline_after_kb_edit`、`_TIMELINE_EDIT_FIELDS`、`save_timeline_md`、`get_timeline_md`、`_extract_timeline_from_notes`、generate-stream timeline 处理、向量索引 timeline.md chunking、系统提示词时间线文本等）✅
- main.py 与 git HEAD 的差异全部来自另一个 AI 的改动（SSE `/api/ai-activity` 端点、prediction-current `kb_modified`/`stale` 字段、系统提示词增强规则 5/6、设定关注规则等），非时间线删除遗留
- 三个后端文件均通过 `py_compile` 编译验证
