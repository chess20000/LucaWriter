# LucaWriter 项目规范

## 主题与颜色

- **文字颜色**：始终使用 `--t1`（主文字）、`--t2`（次要文字）、`--t3`（辅助文字）变量。深色模式白色系，浅色模式黑色系。
- **主题色 `--accent`**：仅用于按钮状态、边框高亮、选中态、hover 背景等交互提示，**禁止**用于正文或标签的文字颜色。
- 文字颜色由 CSS 变量在 `:root` 和 `[data-theme-mode="light"]` 中定义，**禁止**在 JS 中用 `setProperty` 覆盖 `--t1/--t2/--t3`。
- 浅色模式下文字必须是黑色/深灰色（`#111` / `#333` / `#666`），不随主题色变化。
- **`--on-accent`**：主题色背景上的文字色（固定 `#1a1a1a` 深色），**禁止**在 `background:var(--accent)` 的元素上使用 `color:var(--bg)`。
- 浅色模式是深色模式的完整翻版：所有 `:root` 变量在 `[data-theme-mode="light"]` 中都有对应覆盖（border、scrollbar、accent-a* 透明序列等）。
- 浅色模式 `--panel-shadow: none`，书籍卡片不使用阴影。

## Hover / 交互动效

- **统一风格**：所有 hover 效果使用 `transition: all .15s ease`，仅改变 `border-color` + 轻微 `box-shadow`，**禁止** `transform: translateY` 上浮效果（按钮除外，允许 -1px）。
- **边框环**：不使用 `box-shadow: 0 0 0 Npx` 做 hover 边框环（会与正常阴影冲突），改用 `border-color: var(--accent-a25)` 直接变边框色。
- hover 阴影统一为 `0 2px 12px rgba(0,0,0,.15)` 量级，不夸张。
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
- **垂直时间线**：`.chapter-list::before` 画一条从顶到底的细线（`var(--border2)`，`opacity: .55`，两端各 8% 淡出）。
- **滚动条放左侧**：`.chapter-list` 用 `direction: rtl; scrollbar-gutter: stable`，子元素 `direction: ltr`。时间线 `left` 要 +5px 补偿 gutter，与 dot 居中对齐。
- **元素顺序**：dot（绝对定位，钉在时间线上）→ `.ch-meta`（wc/del 共享槽位，order 1）→ `.ch-title`（order 2）。所有元素靠左聚拢，不让 title `flex:1` 撑满。
- **wc/del 原地切换**：用同一个 `.ch-meta` 容器（固定 `width: 26px`），wc 和 del 都 `position: absolute; inset: 0`，hover 时 wc `opacity: 0` / del `opacity: 1`。**不能**用 `display:none ↔ flex` 切换，会让 title 左右抖。
- **搜索框**：紧凑型，`width: 66%; height: 24px; padding: 4px 10px; font-size: 12px`，左对齐，不撑满容器。

## 后端

- 密码使用 PBKDF2-HMAC-SHA256（200,000 迭代），格式 `pbkdf2:salt:hash`
- 账号锁定：5 次失败 → 15 分钟锁定
- CORS：仅对 localhost/127.0.0.1/::1 回显 Origin

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
