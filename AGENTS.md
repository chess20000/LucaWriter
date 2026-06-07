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
- **遇到问题绝对禁止让用户"强制刷新/Ctrl+F5/清缓存"**。现代浏览器不需要这种操作。如果页面显示异常，排查方向应为：端口是否冲突、进程是否重复启动、前端代码是否有错误、后端是否崩溃抛异常。必须在工具内部定位并修复，而不是推给用户操作。
- **服务器 crash / 进程退出不用管**：用户会同时开多个 AI agent 并行开发，某个 agent 可能在调试时 kill 进程或导致服务器崩溃。看到 exit code 1 或连接拒绝时，直接重启服务器即可，不需要排查崩溃原因（大概率是别的 agent 干的）。

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

## 对话摘要 (2026-05-15 #2)

### 本次会话完成的工作

**编辑器独立字号调整功能**
- 后端（main.py）：`DEFAULT_SETTINGS` 新增 `content_font_size: 20` 字段；设置保存端点类型转换中添加 `content_font_size` → int
- 前端（index.html CSS）：新增 `--editor-font-size:20px` CSS 变量（深色/浅色模式各一），与 `--content-font-size` 分离
- 前端（index.html CSS）：`.editor-body textarea` 和 `.editor-highlight-content` 的 `font-size` 从 `--content-font-size` 改为 `--editor-font-size`，确保调整字号只影响编辑器区域，不影响 body 和其他 UI 元素
- 前端（index.html HTML）：外观 Tab 新增「编辑器字号」选择行（14/16/18/20/22/24/28 七档按钮），复用 `.zoom-row`/`.zoom-btn` 样式
- 前端（index.html JS）：新增 `_currentFontSize`、`_fontSizeSaveTimer`、`applyFontSize()` 函数——设置 `--editor-font-size` CSS 变量、更新按钮 active 状态、debounce 300ms 自动持久化到后端
- 前端（index.html JS）：`applySettings()` 中恢复字号——读取 `settings.content_font_size`，设置 `--editor-font-size` CSS 变量
- 前端（index.html JS）：`openSettings()` 中恢复字号按钮状态
- 前端（index.html JS）：`saveSettings()` 中包含 `content_font_size` 字段
- 向后兼容：`get_settings()` 自动补齐缺失的 `content_font_size` 字段，旧 `settings.json` 无需手动修改

## 对话摘要 (2026-05-15 #3)

### 本次会话完成的工作

**修复 Luca 聊天框流式回复时滚动抽搐问题**
- 问题根因：`renderAIMessages()` 每 600ms 轮询时用 `c.innerHTML=''` 清空整个聊天容器再重建所有消息 DOM，导致滚动位置被重置、布局重算，用户往上滑时画面抽搐
- 前端（index.html JS）：提取 `_buildAIMsgEl(m, idx)` 函数——构建单条 AI 消息 DOM 元素（从 renderAIMessages 中抽离渲染逻辑）
- 前端（index.html JS）：提取 `_appendAIMsgCards(c, m, d)` 函数——追加 needs_summary / kb_citations / kb_proposals 卡片
- 前端（index.html JS）：新增 `_updateLiveAIMessage(msgIdx)` 函数——流式更新时只替换正在输出的那条消息的 DOM 元素（通过 `data-idx` 定位旧元素，`replaceWith` 替换），不清空整个容器，不影响滚动位置；仅在用户原本在底部时才自动滚到底
- 前端（index.html JS）：`_doSendAI` 轮询回调 `running` 状态：`renderAIMessages()` → `_updateLiveAIMessage(msgIdx)`
- 前端（index.html JS）：`_startChatPoll` 轮询回调 `running` 状态：找到已有 pending 消息时调用 `_updateLiveAIMessage(i)`，未找到时仍用 `renderAIMessages()` 全量渲染
- 前端（index.html JS）：`renderAIMessages()` 滚动修复——保存 `savedScrollTop`，移除第二次错误的 `nearBottom` 检测，用户不在底部时恢复 `savedScrollTop`

## 对话摘要 (2026-05-15 #4)

### 本次会话完成的工作

**编辑器顶部栏整合：章节标题/字数/本章写完移到 topbar**
- 前端（index.html HTML）：删除 `.editor-top` 整行（章节标题输入框、字数统计、保存指示器、"本章写完"按钮）
- 前端（index.html HTML）：topbar 面包屑区域中 `#bcChapter` 之前插入 `#chTitleInput`（class=`topbar-ch-title`），有章节时显示 input 替代静态文本
- 前端（index.html HTML）：面包屑后新增 `#wordCount`（有章节时显示）和 `#saveIndicator`（始终存在）
- 前端（index.html HTML）：摘要按钮后新增 `#topbarCompleteBtn`（"✓ 本章写完"，class=`topbar-btn topbar-complete-btn`，有章节时显示）
- 前端（index.html CSS）：删除 `.editor-top`、`.editor-top input`、`.editor-top input::placeholder` 样式；从 glass-surface 列表移除 `.editor-top`
- 前端（index.html CSS）：新增 `.topbar-ch-title`（flex:1 透明输入框，13px 加粗）、`.topbar-ch-title::placeholder`、`.topbar-complete-btn` 样式
- 前端（index.html JS）：`updateBreadcrumb()` 重写——有章节时显示 `chTitleInput`+`wordCount`+`topbarCompleteBtn`，隐藏 `bcChapter`；无章节时反过来
- 前端（index.html JS）：`completeChapter()` 按钮选择器从 `document.querySelector('.editor-top .ai-send')` 改为 `$('topbarCompleteBtn')`
- 前端（index.html JS）：`selectChapter()` 中新增 `updateBreadcrumb()` 调用，确保切换章节时 topbar 元素立即显示

## 对话摘要 (2026-05-15 #5)

### 本次会话完成的工作

**Git 提交并推送到 GitHub**
- 提交 `306079c`，分支 `dev/ui-refresh-2026`
- 包含变更：时间线前端恢复（时间长河可视化）、编辑器独立字号调整、SSE 推送 AI 活跃指示器、流式回复滚动修复、编辑器 topbar 整合、后端系统提示词增强等

### 下一步计划

**1. 吃书雷达增加"你去确认"功能** ✅ 已完成
- 后端（kb_pipeline.py）：新增 `consistency_deep_check()` 函数——加载 alert 详情+章节原文+实体/规则/时间线上下文（最多 14000 字符），AI 深入分析矛盾点，要求引用原文出处
- 后端（main.py）：新增 `consistency-deep-check` action 端点
- 前端（index.html）：雷达条目新增"你去确认"按钮（`.radar-deep-btn`，主题色高亮）
- 前端（index.html）：新增 `radarDeepCheck()` 函数——调用 API 后在弹窗中展示 Markdown 格式的分析结果
- 前端（index.html）：新增 `#radarDeepModal` 弹窗（含 `.radar-deep-analysis` 样式，支持 blockquote 引用格式高亮）

**2. 时间线可视化优化** ✅ 已完成
- CSS 全面重写：
  - 时间线脊骨：粗渐变条 → 细线（3px）+ 流光动画（`tl-flow` keyframes）
  - 泡泡：统一大阴影 → 轻阴影 + 3px 左侧彩色边框（按类型着色：人物=青、地点=琥珀、规则=紫、物品=橙、其他=绿）
  - 新增事件点（`.timeline-dot`）：脊骨上的小圆点，hover 放大
  - 新增连接茎（`.timeline-stem`）：泡泡到脊骨的细线
  - 新增章节标记（`.timeline-chapter-mark`）：虚线竖线+章节名标签
  - 远景/中景/近景三档缩放各有独立样式
  - 中景下 meta 信息默认可见（不再隐藏）
- JS 渲染逻辑重写：
  - 新增 `_timelineKindClass()` 按事件属性分配颜色类
  - `renderTimelineRiver()` 新增章节边界计算和渲染
  - 新增事件点和连接茎的 DOM 输出
  - 泡泡内容重构：时间+章节号分列显示，meta 用 `·` 分隔
  - `timelineRiverClick` 选择器适配新 DOM 结构
- 布局算法优化：
  - 泡泡尺寸缩小（中景 160×56，远景 130×44，近景 210×80）
  - 行间距收紧（rowGap 从 bubbleH+18 → bubbleH+14）
  - 可用行数计算优化（viewH-60 代替 viewH-92）
  - 内边距增大（viewW*10%），最小间距收紧

## 对话摘要 (2026-05-15 #6)

### 本次会话完成的工作

**吃书雷达"你去确认"功能**
- 后端（kb_pipeline.py）：新增 `consistency_deep_check()` 函数——加载 alert 详情+章节原文（最多 6000 字）+实体/规则/时间线上下文（最多 14000 字），AI 深入分析矛盾点，要求引用原文出处（章节号+原文片段）
- 后端（main.py）：新增 `consistency-deep-check` action 端点
- 前端（index.html）：雷达条目新增"你去确认"按钮（`.radar-deep-btn`，主题色高亮）
- 前端（index.html）：新增 `radarDeepCheck()` 函数——调用 API 后在弹窗中展示 Markdown 格式的分析结果
- 前端（index.html）：新增 `#radarDeepModal` 弹窗（含 `.radar-deep-analysis` 样式，支持 blockquote 引用格式高亮）

**时间线可视化优化**
- CSS 全面重写：
  - 时间线脊骨：粗渐变条 → 细线（3px）+ 流光动画（`tl-flow` keyframes）
  - 泡泡：统一大阴影 → 轻阴影 + 3px 左侧彩色边框（按类型着色：人物=青、地点=琥珀、规则=紫、物品=橙、其他=绿）
  - 新增事件点（`.timeline-dot`）：脊骨上的小圆点，hover 放大
  - 新增连接茎（`.timeline-stem`）：泡泡到脊骨的细线
  - 新增章节标记（`.timeline-chapter-mark`）：虚线竖线+章节名标签
  - 远景/中景/近景三档缩放各有独立样式
  - 中景下 meta 信息默认可见（不再隐藏）
- JS 渲染逻辑重写：
  - 新增 `_timelineKindClass()` 按事件属性分配颜色类
  - `renderTimelineRiver()` 新增章节边界计算和渲染
  - 新增事件点和连接茎的 DOM 输出
  - 泡泡内容重构：时间+章节号分列显示，meta 用 `·` 分隔
- 布局算法优化：
  - 泡泡尺寸缩小（中景 160×56，远景 130×44，近景 210×80）
  - 行间距收紧（rowGap 从 bubbleH+18 → bubbleH+14）

**时间线交互简化**
- 移除工具栏所有按钮（远景/中景/近景缩放、刷新、整理），工具栏仅保留标题和事件计数
- 移除 `_timelineZoomLabel()`、`timelineZoomIn()`、`timelineZoomOut()`、`timelineLineClick()`、`arrangeTimelineRiver()` 函数
- 移除 `.timeline-actions`、`.timeline-action-btn`、`.timeline-zoom-badge` CSS 样式
- 缩放交互改为：单击泡泡/事件点 → 放大一级，单击空白区域 → 缩小一级
- 鼠标滚轮改为水平滚动（`timelineWheel()` 函数，支持 `deltaX` 触摸板横滑 + `deltaY` 普通滚轮 + Shift+滚轮）
- 清理 `timelineZoomBadge` 相关引用

**时间线缩放层级简化（3档→2档）**
- 移除远景（zoom=0），原中景变为 zoom=0（整体层级），原近景变为 zoom=1（详情层级）
- `timelineZoom` 默认值从 1 改为 0，`_timelineSetZoom` 限制范围 0-1
- zoom=0（整体）：带边框泡泡 + 彩色左边框 + 阴影
- zoom=1（详情）：纯文字无边框——`border:none;background:transparent;box-shadow:none`，hover 时仅显示极浅背景色
- 详情层级字号放大（title 12px、meta 10px），行数限制放宽（-webkit-line-clamp:3）
- 布局参数：zoom=0 泡泡 160×56，zoom=1 泡泡 200×72

**时间线缩放逻辑修正**
- zoom=0（远景/整体）：不传 `chapter_id`，显示整本书全局时间线
- zoom=1（近景/详情）：传 `chapter_id`，聚焦当前章节事件
- 修正前：两个层级都传 `chapter_id`，远景反而只看本章，近景看全书，逻辑反了

**时间线层级重新设计**
- zoom=0（远景/整体）：纯文字无边框（原近景样式升级为默认），整本书全局视图，200×72
  - 类型着色改为标题文字颜色（k-char=青、k-loc=琥珀、k-rule=紫、k-obj=橙、k-cont=绿）
  - hover 仅显示极浅背景色
- zoom=1（近景/章节详情）：带边框泡泡+彩色左边框+阴影，220×80
  - 点击远景中的事件 → 记录该事件的 `chapter_id` 到 `_timelineDrillChapter` → 放大进入该章节详细时间线
  - 点击空白区域 → 清空 `_timelineDrillChapter` → 缩回全局视图
- 删除旧的带边框远景样式，纯文字样式成为默认

## 对话摘要 (2026-05-15 #7)

### 本次会话完成的工作

**修复吃书雷达"你去确认"功能报错**
- 问题：`_extract_context_summary() takes 1 positional argument but 2 were given`
- 原因：`consistency_deep_check()` 中调用 `summary(book_id, cid)` 传了两个参数，但 `_extract_context_summary(source_text)` 只接受一个字符串参数
- 修复（kb_pipeline.py）：改为先通过 `get_source(book_id)` 获取 source.md 文本，再传给 `_extract_context_summary(source_text)`

## 对话摘要 (2026-05-15 #8)

### 本次会话完成的工作

**修复时间线点击逻辑：点击事件不放大，点空白反而有反应**
- 问题根因：`timelineBubblePointerDown` 中调用了 `e.preventDefault()`，这会阻止后续 `click` 事件触发，导致 `onclick="timelineBubbleClick(event)"` 永远不会执行
- 修复（index.html HTML）：移除泡泡上的 `onclick="timelineBubbleClick(event)"` 属性
- 修复（index.html JS）：删除 `timelineBubbleClick()` 函数
- 修复（index.html JS）：将点击放大逻辑移到 `timelineBubblePointerUp()` 中——当 `_dragMoved` 为 false 时（即点击而非拖拽），设置 `_timelineDrillChapter` 并调用 `_timelineSetZoom(timelineZoom+1)` 放大
- 修复（index.html JS）：移除 `setTimeout(function(){bubble._dragMoved=false},80)` 延迟重置（不再需要，因为点击判断已在 pointerup 中完成）

## 对话摘要 (2026-05-15 #9)

### 本次会话完成的工作

**优化通读面板"思考中"黄球浮动动画**
- 问题：原来用单个 `<span>` + `box-shadow` 伪造三个点，三个点同时上下移动，没有波浪错开效果，看起来生硬歪斜
- 修复（index.html HTML）：`<span></span>` → `<span></span><span></span><span></span>` 三个真实元素
- 修复（index.html CSS）：移除 `box-shadow` 伪点方案，改为三个独立 `<span>` 各自有 `animation-delay`（0s / 0.16s / 0.32s）的波浪弹跳
- 动画参数：`rt-bounce` 1.4s 周期，弹跳高度 5px，透明度 0.4→1 渐变，三个点依次起跳形成波浪

## 对话摘要 (2026-05-15 #10)

### 本次会话完成的工作

**底栏 Tab 切换按钮重新设计**
- 前端（index.html CSS）：`.bottom-tabs` 增加 `gap:4px` 间距 + `align-items:center` 垂直居中
- 前端（index.html CSS）：`.bottom-tab` 加背景 `var(--surface2)`、padding 改为 `3px 12px` 上下对称居中、`line-height:1.4`、圆角从 `var(--r1)` 改为 `var(--r2)`、字号从 12px 改为 11px
- 前端（index.html CSS）：`.bottom-tab:hover` 新增 `background:var(--accent-a8)` 浅色底
- 前端（index.html CSS）：`.bottom-tab.active` 文字色从 `var(--t1)` 改为 `var(--accent)` 主题色高亮，新增 `background:var(--accent-a12)` 浅色底

## 对话摘要 (2026-05-15 #11)

### 本次会话完成的工作

**吃书雷达"你去确认"展开卡片居中对齐**
- 前端（index.html CSS）：`.radar-deep-panel` 的 margin 从 `-2px 0 8px 8px`（左8右0，偏右）改为 `-2px 8px 8px 8px`（左右各8px，对称居中），使展开的确认小卡片相对于上面的雷达大卡片水平居中

## 对话摘要 (2026-05-15 #12)

### 本次会话完成的工作

**调取 Claude Code 最新对话，查找未完成任务**
- 读取 `/Users/lanwangqiu/.claude/projects/-Users-lanwangqiu-LucaWriter/` 下 14 个 JSONL 会话文件
- 发现两条因 API 额度耗尽而未完成的任务：
  1. **时间线总览/局部视图修复**（会话 `2b5a366f`）：计划已列出（toolbar 标题、交互分离、节点 UI 卡片化、遵循 CLAUDE.md hover 规范），但未实施代码
  2. **搜索功能高光修复**（会话 `b94148ee`）：代码已改完（`_scrollToMatch` 重写为非破坏性高光模式，不再调 `setSelectionRange`/`ta.focus()`），但未等用户验证
- 另发现未执行的计划文件：`/Users/lanwangqiu/.claude/plans/sunny-tumbling-whistle.md`（Landing Page 重设计 + 深色模式切换按钮）

## 对话摘要 (2026-05-15 #13)

**修复多轮对话时 Luca 不知当前章节内容的问题**
- 问题：用户打开某章后问 Luca"这一章写了什么"，Luca 误判为"后记"或其他章节
- 根因：后端 main.py 中 AI 对话 system prompt 构建（行 3352-3417），当前章节标题和正文**仅在首轮对话**注入（`is_first_round` 分支），多轮对话分支**缺少** `【章节名】` 和 `【现有正文】` 两个关键信息
- 修复（main.py）：在多轮对话的 system prompt 中补充注入当前章节标题和正文，与首轮对话保持一致

**续写 Claude Code 未完成的时间线总览/局部视图修复**
- 确认搜索功能高光修复已到位（`_scrollToMatch` 用 `offsetTop` 测量，`searchReplaceOne` 直接拼接 `ta.value`，`openSearch` 不再 `ta.focus()`）
- 时间线 toolbar 标题动态显示：总览时显示"总览"，局部时显示"局部 · 第 N 章 章节名"
- 时间线 hint 文案动态切换：总览→"点击节点进入局部"，局部→"拖拽调整位置，点空白返回总览"
- 交互分离：总览下 bubble 用 `onclick="timelineBubbleOverviewClick(event)"` 进入局部（cursor:pointer），局部下 bubble 用 `onpointerdown="timelineBubblePointerDown(event)"` 拖拽（cursor:grab）
- 新增 `timelineBubbleOverviewClick()` 函数——点击 bubble 获取 chapter_id 并放大进入局部
- `timelineBubblePointerUp()` 移除点击放大逻辑——局部下点击 bubble 不再放大，只有拖拽有效
- `timelineRiverClick()` 总览下点空白不做任何事（不再缩回）
- 总览下 lane 强制为 0——所有 bubble 排在脊骨线上，不上下错开
- 总览下不渲染 dot 和 stem——去掉杂乱的事件点和连接茎，只保留卡片式 bubble
- hover 已遵循 CLAUDE.md 规范（`transition: all .15s ease` + `border-color` + `box-shadow`，无 translateY）
- JS 语法验证通过（Node.js `new Function()` 检查），后端 `py_compile` 通过，服务器 10000 端口正常响应

**修复时间线 zoom 逻辑反转问题**
- 问题：总览只显示极少量事件（importance≥4），局部反而显示更多——与用户直觉完全相反
- 根因：后端 `kb_pipeline.timeline_map()` 的 zoom 参数含义是 zoom=0 最精简、zoom=2 最全，但前端直接把 timelineZoom=0 当总览传给后端，导致总览只看到 importance≥4 的极少数事件
- 修复（index.html JS）：`loadTimelineRiver()` 中添加 zoom 映射——前端 zoom=0（总览）→ 后端 apiZoom=2（全部事件），前端 zoom=1（局部）→ 后端 apiZoom=1（importance≥3 + chapter_id 过滤）

**砍掉时间线双视图，只保留单视图**
- 删除 `timelineZoom`、`_timelineDrillChapter` 变量
- 删除 `_timelineSetZoom()`、`timelineBubbleOverviewClick()`、`timelineRiverClick()` 函数
- `loadTimelineRiver()` 固定传 `zoom=2`（全部事件），不再传 chapter_id
- 统一使用 dot+stem+bubble+拖拽样式（原"局部"样式）：bubble 220×78、cursor:grab、touch-action:none、line-clamp:3
- CSS：`.timeline-bubble` 默认样式改为 220px/78px/grab，`.timeline-bubble.dragging` 从 `.zoom-1` 选择器移到默认选择器
- 删除 `.timeline-river.zoom-1`、`.timeline-hint` CSS
- HTML：删除 `#timelineViewName`、`#timelineHint`、`onclick="timelineRiverClick(event)"`
- toolbar 只保留事件计数，不再显示"总览"/"局部"标签

**时间线节点点击静默跳转到对应章节对应片段**
- bubble HTML 新增 `data-chapter-id`、`data-what`、`data-evidence` 属性
- `data-evidence` 来自 kb 的 `timeline_event_meta.evidence` 字段（AI 提取的原文引用片段）
- 新增 `_jumpToText(text)` 函数——在 textarea 中静默查找文本，精确匹配失败则尝试前 20 字符模糊匹配，定位后滚动到该位置并选中，不弹出搜索栏
- `timelineBubblePointerUp()` 非拖拽点击时：优先用 evidence（原文片段）定位，退而用 what（事件描述），切换章节后等加载完成再跳转

## 对话摘要 (2026-05-15 #14)

**AI 对话存储从按日期拆分改为统一存储，支持系列/书本对话合并**
- 后端（main.py）：新增 `_find_series_for_book(book_id)` 函数——遍历 books 目录查找书本所属的系列 ID
- 后端（main.py）：新增 `_get_chat_history_path(entity_id)` 函数——如果实体是系列直接返回系列路径，如果书本属于系列则返回系列路径，否则返回书本自身路径；统一文件名为 `chat_history.json`
- 后端（main.py）：新增 `_load_chat_history(entity_id)` / `_save_chat_history(entity_id, messages)` 函数——封装统一对话历史的读写
- 后端（main.py）：修改 GET `/api/book/{id}/messages`——移除日期参数和按日期拆分逻辑，改用 `_load_chat_history(bid)` 加载统一历史
- 后端（main.py）：修改 POST `/api/book/{id}/messages`——移除日期参数和按日期拆分逻辑，改用 `_save_chat_history(bid, messages)` 保存统一历史
- 后端（main.py）：修改 POST `/api/book/{id}/comment` 中的消息保存——从 `messages/{date}.json` 改为 `_load_chat_history` / `_save_chat_history`
- 后端（main.py）：修改 `_replace_pending_chat_msg` 函数——从按日期文件查找改为 `_load_chat_history` / `_save_chat_history`
- 后端（main.py）：修改 POST `/api/series/chat` 中的消息保存——从 `messages/{date}.json` 改为 `_load_chat_history` / `_save_chat_history`
- 后端（main.py）：新增 POST `/api/book/{id}/clear-chat` 端点——清空对话历史（`_save_chat_history(bid, [])`）
- 确认 `_do_series_chat` 中的 `_replace_pending_chat_msg` 调用已使用 `sid`，无需修改
- 语法验证通过（`py_compile`）

## 对话摘要 (2026-05-15 #15)

**统一 Luca 对话架构大改造**

**核心思想：一个 Luca**——除非用户手动清除对话记录，否则在任何地方、任何客户端访问都带有之前的对话记录。用户打开某章时，提示词只告知浏览位置（系列-书本-章节名），不注入正文。Luca 需要正文时自主调用 [READ_CHAPTER] 工具。

**1. 统一对话存储（替代按日期拆分）**
- 后端（main.py）：新增 `_find_series_for_book(book_id)` 函数——遍历 books 目录查找书本所属系列
- 后端（main.py）：新增 `_get_chat_history_path(entity_id)` 函数——系列→系列目录，书本属于系列→系列目录，独立书本→自身目录；统一文件名 `chat_history.json`
- 后端（main.py）：新增 `_load_chat_history(entity_id)` / `_save_chat_history(entity_id, messages)` 函数
- 后端（main.py）：修改 GET/POST `/api/book/{id}/messages`——移除日期参数，改用统一历史
- 后端（main.py）：修改 POST `/api/book/{id}/comment` 和 `_replace_pending_chat_msg` 和 POST `/api/series/chat`——统一使用新函数
- 后端（main.py）：新增 POST `/api/book/{id}/clear-chat` 端点

**2. 系列/书本对话合并**
- 书本属于系列时，`_get_chat_history_path` 返回系列的 `chat_history.json`，实现跨书本共享对话
- 前端无需改动——仍调用 `/api/book/{bookId}/messages`，后端自动重定向到系列对话

**3. 系统提示词改造（注入浏览位置，不注入正文）**
- 后端（main.py）：`do_chat_task` 中构建 `browse_ctx`（系列名 - 书名 - 章节名）替代章节正文注入
- 后端（main.py）：构建 `ch_list_ctx`（章节列表，含 id 和标题）供 [READ_CHAPTER] 工具使用
- 后端（main.py）：首轮/多轮系统提示词从"你已经在系统里看到了用户当前正在写的章节正文"改为"你无法直接看到章节正文。如果需要查看正文，请使用[READ_CHAPTER]工具读取"
- 后端（main.py）：系列对话 `_do_series_chat` 同步改造，新增章节列表和 [READ_CHAPTER] 工具说明

**4. [READ_CHAPTER] 工具实现**
- 后端（main.py）：`do_chat_task` 中新增 [READ_CHAPTER] 标签解析——提取 chapter_id，读取章节正文，注入下一轮对话
- 后端（main.py）：支持一次读取多个章节（最多 3 个），每个章节调用一次
- 后端（main.py）：读取后自动调用 AI 生成基于正文的回复，与原回复拼接
- 后端（main.py）：系列对话 `_do_series_chat` 中同步实现 [READ_CHAPTER] 处理（跨书本查找章节）

**5. 自动上下文压缩算法（闲时+发送前双保险）**
- 后端（main.py）：`_compress_messages_for_context` 升级——压缩比从 75% 降为 60%，新增 `_summarize_chunk_ai()` 函数使用 AI 生成摘要替代简单截断
- 后端（main.py）：新增 `_schedule_idle_compress(entity_id)`——对话结束后 30 秒检查是否需要压缩
- 后端（main.py）：新增 `_do_idle_compress(entity_id)`——闲时压缩：读取对话历史，超过阈值则用 AI 压缩旧消息，将压缩摘要存为 `type:'system', subtype:'compressed_summary'` 消息，裁剪旧消息保留最近 12 条
- 后端（main.py）：`do_chat_task` 和 `_do_series_chat` 完成后调用 `_schedule_idle_compress`

**6. 前端适配**
- 前端（index.html）：`loadAIMessages()` 移除日期参数
- 前端（index.html）：`saveAIMessages()` 移除日期参数
- 前端（index.html）：`_doSendAI()` 中 history 构建新增 `compressed_summary` 消息类型
- 前端（index.html）：`clearAIChat()` 改为调用 `/api/book/{id}/clear-chat` API
- 前端（index.html）：修复 `clearAIChat()` 缺少闭合括号 `}` 导致整个 JS 脚本块解析失败的 bug
- 前端（index.html）：`renderAIMessages()` 跳过 `compressed_summary` 消息的渲染
- 前端（index.html）：系列对话同步适配（`loadSeriesAIMessages`、`saveSeriesAIMessages`、`clearSeriesAIChat`、history 构建）

**未来计划：给 Luca 设计 OpenAI 兼容 API / 远程 MCP 接口**

## 对话摘要 (2026-05-16)

**修复预言手动更新返回 not found 的问题**
- 问题：知识库变动后，预言面板显示"知识库已变动，预言已经不是最新"，点击"手动更新"按钮报 not found
- 根因：前端 `refreshPrediction()` 调用 `POST /api/book/{id}/action`，把 action 放在 body 中（`{action:'reader-prediction'}`），但后端路由从 URL 路径 `parts[4]` 取 action，URL 中 `parts[4]` 为 `'action'` 而非 `'reader-prediction'`，导致不匹配任何分支
- 修复（index.html）：`refreshPrediction()` 的 API 调用从 `POST /api/book/{id}/action` + body `{action:'reader-prediction'}` 改为 `POST /api/book/{id}/reader-prediction` + body `{}`，与 `chapter-complete` 等其他 action 的调用方式一致

## 对话摘要 (2026-05-16 #2)

**移除时间线拖拽纠错和待确认前端显示**
- 前端（index.html JS）：删除 `_timelineDragging` 变量
- 前端（index.html JS）：删除 `timelineBubblePointerDown()`、`timelineBubblePointerMove()`、`timelineBubblePointerUp()` 三个拖拽函数
- 前端（index.html JS）：删除 `_timelineClamp()` 辅助函数
- 前端（index.html JS）：删除 `timelineSaveLayout()` 函数（拖拽后保存纠错到后端的逻辑）
- 前端（index.html JS）：新增 `timelineBubbleClick()` 函数——点击泡泡跳转到对应章节并定位原文片段（从原 pointerUp 的非拖拽分支提取）
- 前端（index.html JS）：泡泡 HTML 从 `onpointerdown="timelineBubblePointerDown(event)"` 改为 `onclick="timelineBubbleClick(event)"`
- 前端（index.html JS）：泡泡 HTML 移除 `data-line-y`、`data-lane`、`data-min-lane`、`data-max-lane` 拖拽相关属性
- 前端（index.html CSS）：`.timeline-bubble` 的 `cursor:grab;touch-action:none` 改为 `cursor:pointer`
- 前端（index.html CSS）：删除 `.timeline-bubble.dragging` 样式
- 前端（index.html CSS）：删除 `.timeline-dot.uncertain` 样式
- 前端（index.html CSS）：删除 `.timeline-bubble.uncertain` 样式
- 前端（index.html CSS）：删除 `.timeline-bubble.user-corrected::after` 样式

## 对话摘要 (2026-05-16 #3)

**书架和系列视图：设置和深色模式按钮移到右侧**
- 前端（index.html HTML）：书架 header 右侧新增深色模式切换按钮（`.shelf-theme-btn`）和设置按钮（`.shelf-settings-btn`），放在"+ 新建系列"按钮左边
- 前端（index.html HTML）：系列 header 右侧新增深色模式切换按钮和设置按钮，利用 h2 的 `flex:1` 自动推到右边
- 前端（index.html HTML）：topbar-actions 中设置按钮新增 `id="topbarSettingsBtn"`
- 前端（index.html JS）：`updateBreadcrumb()` 中新增 `themeEl`/`settEl` 变量，书架视图（无 bookId 无 seriesId）和系列视图时隐藏 topbar 中的深色模式和设置按钮，编辑器视图时显示
- 前端（index.html JS）：`updateThemeToggleIcon()` 新增同步书架/系列视图的深色模式图标状态（`.shelf-theme-btn .shelf-moon`/`.shelf-sun`）

## 对话摘要 (2026-05-16 #4)

**系列视图顶栏移除 › 分隔符**
- 前端（index.html JS）：`updateBreadcrumb()` 系列视图分支中 `sepEl.style.display='inline'` → `'none'`，面包屑从"系列 › 系列名"变为"系列 系列名"

## 对话摘要 (2026-05-16 #5)

**一个 Luca：所有窗口共享对话历史 + 当前焦点提示**
- 后端（main.py）：新增全局对话历史文件 `DATA_DIR/chat_history.json`，`_get_chat_history_path()` 统一返回全局路径，书本、系列、不同窗口不再各自隔离 Luca 对话
- 后端（main.py）：新增旧历史惰性迁移逻辑——首次读取全局历史时合并各书/系列目录下旧 `chat_history.json` 和旧 `messages/*.json`
- 后端（main.py）：修复 `_find_series_for_book()` 扫描路径，改为使用 `BOOKS_DIR`，确保系统提示词能识别书本所属系列
- 后端（main.py）：新增聊天历史合并保存，前端 POST `/messages` 不再整份覆盖全局历史，降低多窗口互相冲掉消息的风险
- 后端（main.py）：聊天任务发送前改为从服务端全局历史重建上下文，而不是完全信任当前窗口传来的局部 history
- 后端（main.py）：普通聊天和系列聊天共用一个运行中任务检查，避免多个窗口同时启动多个 Luca 对话任务
- 前端（index.html）：编辑器 Luca 聊天流和系列 Luca 聊天流新增无框公告行：`当前焦点 系列名 > 书名 > 章节`
- 前端（index.html JS）：新增 `system/focus_notice` 消息类型，切换系列、书本、章节和修改章节标题时把焦点公告追加到最新消息下方；该消息只用于界面显示，不进入 Luca 模型上下文
- 前端（index.html JS）：系列视图 topbar 中隐藏残留的「系列」二字
- 前端（index.html JS）：系列聊天渲染时跳过 `compressed_summary` 系统压缩摘要消息

## 对话摘要 (2026-05-16 #6)

**Git 提交并推送到 GitHub**
- 提交 `ccc4c4d`，分支 `dev/ui-refresh-2026`
- 包含变更：统一 Luca 对话架构（全局 chat_history.json + [READ_CHAPTER] 工具 + 自动上下文压缩）、吃书雷达"你去确认"深度分析、时间线可视化重构（单视图+点击跳转）、预言更新 API 修复、搜索高光修复、底栏 Tab 样式优化、书架/系列视图深色模式按钮、编辑器 topbar 整合等
- 4 个文件，+1486 / -368 行
