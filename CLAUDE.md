# LucaWriter 项目规范

## 主题与颜色

- **文字颜色**：始终使用 `--t1`（主文字）、`--t2`（次要文字）、`--t3`（辅助文字）变量。深色模式白色系，浅色模式黑色系。
- **主题色 `--accent`**：仅用于按钮状态、边框高亮、选中态、hover 背景等交互提示，**禁止**用于正文或标签的文字颜色。
- 文字颜色由 CSS 变量在 `:root` 和 `[data-theme-mode="light"]` 中定义，**禁止**在 JS 中用 `setProperty` 覆盖 `--t1/--t2/--t3`。
- 浅色模式下文字必须是黑色/深灰色（`#111` / `#333` / `#666`），不随主题色变化。
- **`--on-accent`**：主题色背景上的文字色（固定 `#1a1a1a` 深色），**禁止**在 `background:var(--accent)` 的元素上使用 `color:var(--bg)`。
- 浅色模式是深色模式的完整翻版：所有 `:root` 变量在 `[data-theme-mode="light"]` 中都有对应覆盖（border、scrollbar、accent-a* 透明序列等）。

## 阴影与玻璃

- **全局禁止 `box-shadow`**：`--panel-shadow` 和 `--glass-shadow` 已在 `:root` 和 `[data-theme-mode="light"]` 都设为 `none`。任何新代码不要写字面量 `box-shadow:0 N N rgba(...)`。需要层级区分时用 `background` / `border` / `border-color`。
- **`backdrop-filter` 仅允许一处**：`.left-sidebar::before` 章节栏毛玻璃（带 `mask-image` 右侧渐隐）。其它任何面板、modal、dropdown、hover 状态一律不要再加 `backdrop-filter` / `-webkit-backdrop-filter`，blur 是掉帧主因。
- **不要做装饰性动画**：`@keyframes` 不写 `transform: translateY/scaleX` 等位移缩放的纯装饰动画（已删除 `tl-flow`，简化 `rt-bounce` / `aiDotBreathe` / `dlPulse` 为 opacity-only）。状态/loading 指示器只用 opacity 脉动。

## Hover / 交互动效

- **统一风格**：所有 hover 效果使用 `transition: border-color .15s ease` 或类似具体属性（不用 `transition: all`），仅改变 `border-color` / `background` / `color`。**禁止** `transform`（包括 -1px 上浮）、`scale`、`translateY` 任何位移/缩放。UI 必须沉稳，不抖不浮。
- **禁止 hover 加任何 `box-shadow`、`backdrop-filter` 或 `filter: blur()`**。
- **不改 `border-width`**：hover 时只改 `border-color`，保持 1px。`1px → 2px` 即使 `box-sizing: border-box` 也会让内容内缩 1px 产生抖动。
- **边框环**：不使用 `box-shadow: 0 0 0 Npx` 做 hover 边框环，改用 `border-color: var(--accent-a25)` 直接变边框色。
- 列表项 hover 使用 `background: var(--surface2)`，不改变 transform。

## 布局

- 不使用 `100vh` 作为容器高度，改用 `html,body{height:100%}` + `.app{height:100%;overflow:hidden}` 链条。`vh` 单位在浏览器缩放时存在舍入偏差。
- 底部面板高度通过 `_appHeight()` 辅助函数基于容器 `clientHeight` 计算，而非 `window.innerHeight`。
- **编辑器区域**：`.editor-area`、`.editor-body` 的 padding 为 0，各模块紧密拼接。textarea 无圆角无阴影，与容器齐平。仅用 `background: var(--surface)` 区分区域。

## 章节侧栏（仅 `.left-sidebar` / `.chapter-list`）

> 这些规则只用于左侧章节栏，不外推到其他面板。

- **宽度**：`width: fit-content; min-width: 180px; max-width: 320px`。在能完整显示所有章节名前提下尽可能窄。
- **毛玻璃**：sidebar 自身 `background: transparent`，玻璃放到 `::before` 上并加横向 `mask-image: linear-gradient(to right, #000 0%, #000 75%, transparent 100%)`，右端 25% 渐隐。
- **右边界**：`::after` 一根 1px 渐变细线（`var(--border)`，`opacity: .25`），上下各 14px 淡出，不使用 `box-shadow`。
- **滚动条放左侧**：`.chapter-list` 用 `direction: rtl; scrollbar-gutter: stable`，子元素 `direction: ltr`。
- **元素顺序**：dot（绝对定位）→ `.ch-meta`（wc/del 共享槽位，order 1）→ `.ch-title`（order 2）。所有元素靠左聚拢，不让 title `flex:1` 撑满。
- **wc/del 原地切换**：用同一个 `.ch-meta` 容器（固定 `width: 26px`），wc 和 del 都 `position: absolute; inset: 0`，hover 时 wc `opacity: 0` / del `opacity: 1`。**不能**用 `display:none ↔ flex` 切换，会让 title 左右抖。
- **搜索框**：紧凑型，`width: 66%; height: 24px; padding: 4px 10px; font-size: 12px`，左对齐，不撑满容器。

## 后端

- 密码使用 PBKDF2-HMAC-SHA256（200,000 迭代），格式 `pbkdf2:salt:hash`
- 账号锁定：5 次失败 → 15 分钟锁定
- CORS：仅对 localhost/127.0.0.1/::1 回显 Origin
- 知识库必须向后兼容：已有 `kb.db` 是用户花大量时间通读得到的核心资产。新增知识库能力时只做增量迁移和兼容查询，不要求重通读，不自动清空旧表 / 旧索引 / 旧 `source.md`。只有用户明确点击重通读或清空知识库时，才允许重建。

## 预览

- 调用 preview 工具时，使用 `http://localhost:10000`（前端开发服务器固定端口），不要自己起 dev server。

## 行为准则

### 先想再写
- 动手前明确假设，不确定就问
- 存在多种解读时先列出来，不要默认选一种
- 有更简单的方案就说，有疑问就停

### 简洁优先
- 只写解决当前问题的最少代码
- 不为未来需求加功能、不为一次性代码做抽象
- 不处理不可能发生的错误场景
- 200 行能写成 50 行就重写

### 精准修改
- 只改相关的，不"顺带优化"相邻代码
- 不重构没坏的东西
- 匹配现有风格
- 只清理自己的改动产生的死代码，不主动删已有的

### 目标驱动
- 把模糊任务转成可验证目标
- 多步骤任务先列简要计划：
  1. [步骤] → 验证: [检查项]
  2. [步骤] → 验证: [检查项]
- 明确成功的定义，自主跑通再报告
