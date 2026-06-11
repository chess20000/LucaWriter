# LucaWriter × Coobox 在线写作（SaaS 模式）施工计划

> **给施工 session 的须知**：开工前先通读本文档；每完成一个阶段，更新底部「施工日志」；
> 施工中发现设计问题，**直接修改本文档对应章节**并在日志里说明原因。
> 本文档是唯一权威计划，聊天记录里的旧方案以此为准。

## 一、目标

把 LucaWriter 改造为可由 Coobox（`~/Documents/Coobox-prod`，部署在 AWS `ssh aws`，线上 coobox.space）
托管的多用户在线写作服务。**单机版行为完全不变**，SaaS 行为全部由 `LUCA_SAAS=1` 环境变量开启，
是 LucaWriter 主线特性——升级网站 = 替换服务器上的 lucawriter 核心目录 + 重启服务。

## 二、已拍板的决策

| 事项 | 决定 |
|---|---|
| 架构 | **单进程多租户**（一个 LucaWriter 进程服务所有用户，按请求切租户数据目录），不是每用户一进程 |
| 付费 | 余额制（分为单位存储）；**兑换码**先行，定位为测试功能；渠道后续再接 |
| AI 计费倍率 | DeepSeek 成本 × **1.25**（可配置） |
| 模型 | `deepseek-v4-flash`，站长 key 只存服务端，不下发 |
| 磁盘配额 | 每用户 **100MB**，需在界面显示剩余空间 |
| 功能范围 | **全部功能开放**（含知识库通读、嵌入、向量库），只把 AI 提供商换成云网关；重任务走全局队列限并发 |
| 施工方式 | 按阶段分多个 session 完成，每阶段独立可验证（用户额度有限，不一次写完） |

## 三、架构

```
浏览器 ──► Coobox(Flask, :8000)
            ├─ /me ────────── 「在线写作」入口（新开页 /write/）
            ├─ /write/** ──── 反向代理 ──► LucaWriter SaaS 进程（127.0.0.1:21000）
            │                  注入 X-Luca-User: <uid> + X-Luca-Sign: HMAC(secret, uid)
            ├─ /api/ai/v1/** ─ AI 计费网关 ──► DeepSeek（验余额→转发→按 usage 扣费×1.25）
            ├─ /internal/coo-upload ─ 内部直传（LucaWriter 服务端回环调用，零密码）
            ├─ /me/wallet ──── 钱包：余额 / 兑换码 / 用量明细
            └─ /admin/codes ── 管理员生成兑换码

LucaWriter SaaS 进程（LUCA_SAAS=1）
  └─ DATA_DIR/tenants/<uid>/ ── 每用户独立数据目录（books/works/settings/kb 全套）
```

通信全部走 127.0.0.1 回环，认证头用固定 HMAC（不出公网，无需时间戳）；
LucaWriter SaaS 进程只监听 127.0.0.1。Cloudflare 配置不用动。

## 四、施工阶段

> 每个阶段一节，含：改动文件、要点、验证方式。按顺序施工，阶段间无未完成依赖。

### 阶段 1：LucaWriter 多租户核心 ⬅ 最大的一块

**文件**：`backend/main.py`（可能涉及 `backend/kb_storage.py` / `kb_pipeline.py`）

1. 新增模块顶部基础设施：
   - `LUCA_SAAS = os.environ.get('LUCA_SAAS') == '1'`，`LUCA_SAAS_SECRET`
   - `_TENANT: contextvars.ContextVar`（存租户 uid 或 None）
   - `data_dir()`：单机模式返回原 DATA_DIR；SaaS 模式返回 `DATA_DIR/tenants/<uid>/`，无租户抛 401
   - 首次见到某租户时 `_ensure_tenant_dirs()`：建子目录、salt、默认 settings
2. **路径常量函数化**（机械改造，~15 个常量、main.py 内 34+ 处 DATA_DIR 引用）：
   `BOOKS_DIR / WORKS_DIR / LOG_DIR / MESSAGES_DIR / CHAT_SESSIONS_DIR / USER_FONTS_DIR /
   GLOBAL_CHAT_HISTORY_FILE / SALT_FILE / SETTINGS_FILE / USERS_FILE / SESSIONS_FILE /
   _LOCAL_LLM_PORT_FILE / _ENCRYPT_KEY_FILE / AI_PROVIDERS_FILE` → 同名小写函数。
   注意：模块加载期/启动期用到这些路径的代码（makedirs、salt 初始化等）要改为单机立即执行、SaaS 延迟到租户首次请求。
   ⚠️ 施工时先 `grep` 确认 `kb_storage.get_kb_path()` 的基路径来源（初查 kb_storage.py 无 DATA_DIR 引用，可能是 main 传入或自行拼接，需落实）。
3. **鉴权分支**：SaaS 模式跳过自带 session 体系，每个请求验 `X-Luca-User`+`X-Luca-Sign`
   （`hmac.compare_digest`），验过后 set 租户 contextvar。401 时返回 JSON 错误（前端由 Coobox 负责跳登录）。
4. **后台线程带租户**：新增 `spawn_thread(target,...)` helper（`contextvars.copy_context().run`），
   替换请求路径里所有 `threading.Thread(...)` 调用点（通读/导入/同步等）。
5. **缓存 re-key**：`_chapter_brief_cache` 等以 book_id 为 key 的缓存改为以完整路径为 key。
6. **全局任务队列**：SaaS 模式下重任务（通读/嵌入/导入校验）过全局 `Semaphore(1)`，排队不拒绝。

**验证**：`python3 -m py_compile`；单机模式（不设 LUCA_SAAS）用临时 DATA_DIR 冒烟（建书/写章/导出，
复用本仓库已有冒烟流程）确认零回归；SaaS 模式用 curl 带/不带签名头各打一遍，确认 401 与租户目录创建。

### 阶段 2：LucaWriter SaaS 行为层

**文件**：`backend/main.py`

1. **AI 提供商覆盖**：SaaS 模式 `get_settings()` / `get_ai_providers()` 出口处强制覆盖为云提供商：
   `base_url = LUCA_AI_GATEWAY`（默认 `http://127.0.0.1:8000/api/ai/v1`）、`model = LUCA_AI_MODEL`
   （deepseek-v4-flash）、`api_key = f"{LUCA_INTERNAL_SECRET}:{uid}"`。
   设置保存接口在 SaaS 模式忽略提供商相关字段。嵌入/本地 LLM 相关端点不动（功能全开）。
2. **`GET /api/saas-info`**：`{saas, model, quota_used, quota_limit, balance_cents, wallet_url}`；
   balance 从 Coobox 内部接口实时查（`/internal/balance?uid=`，带 internal secret）。单机模式返回 `{saas:false}`。
3. **磁盘配额**（100MB，`LUCA_TENANT_QUOTA_MB` 可配）：
   - `tenant_disk_usage()`：os.scandir 递归求和，每租户缓存 30s
   - 拦截点：文件导入、章节保存、封面/字体上传、merge-coo——超限返回明确错误「存储空间不足，已用 X / 100 MB」
4. **直传内部通道**：SaaS 模式下 `coo-push` 分支不再走 email+password 登录公网，改 POST
   `http://127.0.0.1:8000/internal/coo-upload`（头：internal secret + uid），响应格式与现有一致。

**验证**：SaaS 模式 curl saas-info / 模拟超配额导入被拒 / coo-push 打到 mock 端点；单机模式回归不受影响。

### 阶段 3：LucaWriter 前端适配

**文件**：`frontend/index.html`

1. **API 前缀**：定义 `var API_BASE=window.__LUCA_BASE||''`；`api()` helper 统一加前缀；
   排查零散直连点（导入/导出 XHR、EventSource SSE、cover URL 等 `'/api/` 字面量）全部走前缀。
   Coobox 反代时在返回的 index.html 里注入 `<script>window.__LUCA_BASE='/write'</script>`（由 Coobox 端做，见阶段 4）。
2. **SaaS UI**（启动时调 `/api/saas-info`，`saas:true` 才生效）：
   - 设置页提供商/模型/API key 大面板整体隐藏，原位替换为：
     `当前模型 deepseek-v4-flash（Coobox 云模型）｜余额 ￥xx.xx ［充值］［用量明细］`（跳 wallet_url 新标签）
   - **存储条**：`已用 23.4 / 100 MB` + 进度条（设置页顶部；保存章节返回超限错误时也要 toast 明示）
   - 隐藏单机概念入口：本地 LLM 管理、嵌入模型管理、数据目录相关（功能在但管理入口不给）
   - 导出弹窗：推送区隐藏 网址/邮箱/密码 三个输入框，按钮文案改「发布到 Coobox」
3. 登录界面：SaaS 模式不会出现（后端 401 由 Coobox 兜底跳登录），前端无需改登录逻辑。

**验证**：单机模式 UI 无变化（node --check + 浏览器冒烟）；SaaS 模式见阶段 6 联调。

### 阶段 4：Coobox 网关与入口

**文件**：`Coobox-prod/app.py`、`templates/me.html`（或对应「我的」模板）

1. **反向代理 `/write` `/write/<path:rest>`**（GET/POST 全方法）：
   - 要求 Coobox 登录，否则 302 到 /login
   - `http.client` 实现双向流式（请求体 passthrough、响应逐块 yield，SSE 不缓冲）
   - 注入 `X-Luca-User` / `X-Luca-Sign`，剥离 hop-by-hop 头
   - 对 `text/html` 响应注入 `window.__LUCA_BASE='/write'`（替换 `<head>` 后第一个位置）
   - ⚠️ gunicorn 线程要加大（SSE 长连接占线程）：部署时 `COOBOX_THREADS=16`
   - ⚠️ `/write` 不要进 page_views 埋点（ANALYTICS_SKIP_PREFIXES 加 `/write`）
2. **「我的」页入口**：在线写作按钮 → `/write/`（target=_blank）
3. **内部接口**（仅验 internal secret，403 其余）：
   - `POST /internal/coo-upload`：读 uid 头 → `import_coo(file, owner_id=uid)`
   - `GET /internal/balance`：返回该 uid 余额（给 saas-info 用）

**验证**：本地两个 Coobox 账号分别进 /write/，各自见到独立空白工作区；SSE（AI 对话流式）通过代理不断流。

### 阶段 5：Coobox 计费系统

**文件**：`Coobox-prod/app.py`、新模板 `wallet.html`、`admin_codes.html`

1. **表**（init_db 增量迁移，不动已有表）：
   - `wallets(user_id PK, balance_cents, updated_at)`
   - `wallet_tx(id, user_id, amount_cents, kind: redeem|ai|admin, meta, created_at)`
   - `ai_usage(id, user_id, model, in_tokens, out_tokens, cost_cents, created_at)`
   - `redeem_codes(code PK, amount_cents, created_at, redeemed_by, redeemed_at)`
2. **AI 网关 `POST /api/ai/v1/chat/completions`**：
   - 鉴权：Bearer = `internal_secret:uid`（来自 LucaWriter 服务端，回环）
   - 余额 ≤ 0 直接 402，返回 OpenAI 风格 error（message 写明「余额不足，请充值」，LucaWriter 前端会原样展示）
   - 转发 DeepSeek（`DEEPSEEK_API_KEY` env）：流式请求自动注入 `stream_options:{include_usage:true}`，
     从最后一个 chunk 取 usage；非流式直接取 usage
   - 计费：`cost = (in_tokens×单价in + out_tokens×单价out) × 1.25`，向上取整到分；
     单价 env：`COOBOX_DS_PRICE_IN` / `COOBOX_DS_PRICE_OUT`（元/百万 token，按 DeepSeek 官网填）
   - 扣余额 + 写 ai_usage + wallet_tx；允许最后一笔扣成小幅负数
3. **钱包页 `/me/wallet`**：余额大数字、兑换码输入框（成功 toast + 入账）、最近 50 条用量/流水
4. **管理 `/admin/codes`**：生成（面值 × 张数）、列表（未兑/已兑）；入口加到 /admin 导航；页面标注「测试功能」
5. 兑换码格式：`coo-` + 16 位随机 base32，防爆破限速复用现有 RATE_LIMIT

**验证**：本地 mock DeepSeek（一个返回固定 usage 的本地 stub，env 指过去）：兑换 → 调用 → 余额按 1.25 倍率精确减少、
usage 入表、余额耗尽返回 402；流式与非流式都验。

### 阶段 6：本地端到端联调

本地同时起 Coobox（临时数据目录）+ LucaWriter SaaS（LUCA_SAAS=1）+ mock DeepSeek：

- [ ] 两个用户数据完全隔离（互看不到书）
- [ ] 「我的」→ 在线写作 → 免登录直达工作区
- [ ] AI 对话流式输出，扣费正确，余额在写作界面实时可见
- [ ] 余额为 0：AI 调用收到明确「余额不足」提示，兑换后恢复
- [ ] 存储条显示正确；导入大文件超 100MB 被拒且提示清晰
- [ ] 「发布到 Coobox」一键成功，作品出现在该用户名下（更新路径也验）
- [ ] 知识库通读全流程跑通（走全局队列；嵌入子进程在本机验，服务器内存风险见「风险」）
- [ ] 单机模式（LUCA_SAAS 不设）全量回归冒烟

### 阶段 7：部署上线

1. 服务器布局：`~/lucawriter-core/`（rsync 本仓库，排除 data/.git）、`~/lucawriter-data/`（DATA_DIR）
2. venv：`~/lucawriter-core/.venv`，装导入解析依赖（python-docx/pypdf/ebooklib，以 IMPORT_PARSERS 实际需要为准）
   和嵌入 runtime（如需；模型文件按仓库「模型下载说明」放置）
3. systemd `lucawriter.service`：`LUCA_SAAS=1 LUCA_PORT=21000 DATA_DIR=... LUCA_SAAS_SECRET=...
   LUCA_AI_GATEWAY=http://127.0.0.1:8000/api/ai/v1 LUCA_INTERNAL_SECRET=...`，只听 127.0.0.1
4. Coobox `.env` 增加：`LUCA_UPSTREAM=127.0.0.1:21000`、两个 secret、`DEEPSEEK_API_KEY`（**站长手工填**）、
   DS 单价、`COOBOX_THREADS=16`
5. 生产冒烟（按阶段 6 清单抽核心项）；**Coobox 改动 md5 同步回本地仓库**
6. 升级 SOP 写进本文档：`rsync 新版 lucawriter → ~/lucawriter-core && sudo systemctl restart lucawriter`

## 五、关键技术决策备忘

- **为什么单进程多租户**：每用户一进程 ~100MB×N 不可承受；contextvar + 请求线程隔离，改造面集中在路径层
- **为什么固定 HMAC 不带时间戳**：仅在本机回环传输，不出公网；LucaWriter 只听 127.0.0.1
- **为什么 AI key 走服务端注入**：key/token 永不到浏览器；按 uid 记账省掉每用户发 token
- **为什么 /write 路径前缀而非子域名**：子域名 cookie 不共享、Cloudflare 要加配置；路径方案只需前端 API_BASE 一处收口
- **币值单位**：分（int），显示时除 100

## 六、风险与待确认

| 风险 | 预案 |
|---|---|
| 嵌入子进程在服务器 RAM 不够（模型数百 MB） | 功能照开 + 全局并发 1；部署后实测，OOM 就在 SaaS 模式把向量索引改为可选降级（需改 kb_pipeline，到时更新本文档） |
| ~~kb_storage 基路径来源未落实~~ | ✅ 已落实（2026-06-10）：`get_kb_path()` 走 `main.get_book_dir()`，租户化后自动跟随，kb_storage 无需改路径 |
| LucaWriter 前端 `/api/` 字面量遗漏 | 阶段 3 全文 grep `'/api/`、`"/api/`、`EventSource(`、`XMLHttpRequest` 逐个排查 |
| Coobox gthread 线程被 SSE 占满 | COOBOX_THREADS=16；不够再评估 gevent |
| deepseek-v4-flash 实际单价 | 部署时按官网填 env，文档不写死 |
| AWS 实例内存规格未确认 | 部署前 `free -h` 实测，决定嵌入是否可开 |

## 七、施工日志

> 每个 session 完成的内容、遇到的问题、对计划的修改，按时间倒序记在这里。

- 2026-06-11 **阶段 3 完成。** 注：主体改造由一个**额度耗尽未及记日志**的 session 完成（已留在工作区），本 session 审计补全 + 验证 + 补记。
  - 那次中断 session 已完成（`frontend/index.html`，经逐项审计确认完整）：
    - **API 前缀**：`var API_BASE=window.__LUCA_BASE||''`（script 顶部）；`api()` helper 的 `x.open` 统一加前缀；零散直连点全部收口——EventSource(`/api/ai-activity`)、4 处导出 XHR（export/export-epub/export-coo×2）、封面 img src（书架卡片/作品页/卷行/封面上传预览）、2 处 fetch（local-llm open-models-dir/stop）。审计结论：剩余 `'/api/` 字面量全部作为参数传入 `api()`，无遗漏（grep src=/href=/sendBeacon/iframe 均空）。
    - **SaaS UI**：`loadSaasInfo()`（init 时调 `/api/saas-info`，`saas:true` 则 body 加 `.saas` class + `SAAS_INFO` 全局）；设置页 `#saasProviderPanel`（模型名 + 余额 + 充值/用量明细链接，跳 wallet_url 新标签）与 `#saasStorageBar`（已用 X / N MB + 进度条，openSettings 时刷新）；CSS `body.saas` 隐藏 localLLMBox/providerSlider/providerApplyBtn/remoteApiForm；导出弹窗三个输入行（网址/邮箱/密码）隐藏、按钮文案改「发布到 Coobox」；`doPushCoo()` SaaS 分支只发 `{pen_name}`（走阶段 2 的内部直传）；章节保存 413 时 `quotaToast`（10s 限速，自动保存高频）。
    - 同一 diff 里混有更早性能 session 的改动（章节列表 word_count/preview 轻量化、syncHighlights 跳过重建等），与 SaaS 无关，提交时注意。
  - 本 session 补全：
    - 网络 tab 单机概念行 SaaS 隐藏：`#accessScopeRow`（访问范围）/`#accessScopeNote`/`#keepBackgroundRow`（留后台）/`#keepBackgroundNote` 加 id 并入 body.saas CSS；网络搜索保留（SaaS 功能全开）。
    - **阶段 2 漏网之鱼（backend/main.py）**：`import-volume`（导入文件作为新卷，可写入 150MB）没有配额拦截，已按统一模式补 `check_tenant_quota()` → 413。
  - 验证结果（全部通过）：
    - `python3 -m py_compile main.py kb_storage.py` ✓；前端整段 JS 抽出 `node --check` ✓
    - 单机（21733）：saas-info `{saas:false}`、GET / 302 login（无用户预期）✓
    - SaaS（21734）：无签名 401；签名后 saas-info 全字段；quota=0 时 import-volume 413 + 明确文案；quota=100 时 import-volume 200 导入成功 ✓
    - SaaS 引用的全部 19 个元素 id 经脚本核对均存在；renderSaasInfo 空值兜底（balance null→'—'、quota_limit 0 防除零）核对 ✓
    - 浏览器级 SaaS 冒烟未做（preview 工具读不到 launch.json）：写了一个验签注入代理脚本思路可复用（监听本地端口、转发时注入 X-Luca-User/X-Luca-Sign），但真实渲染验证按计划归入阶段 6 联调。
  - 备忘：`.claude/launch.json` 的 `lucawriter` 配置（DATA_DIR=/tmp/luca_p3_solo, 端口 10000）即那次 session 留下的单机冒烟入口。
  - 下一步：**阶段 4（Coobox 网关与入口）**，施工对象是 `~/Documents/Coobox-prod`（不在本仓库）。

- 2026-06-11 **阶段 2 完成。** AI 提供商覆盖、/api/saas-info、磁盘配额、coo-push 内部直传全部落地（均在 `backend/main.py`），单机回归 + SaaS curl 验证全过。
  - 新增环境变量（默认值见代码顶部 SaaS 块）：`LUCA_AI_GATEWAY`（默认 `http://127.0.0.1:8000/api/ai/v1`）、`LUCA_AI_MODEL`（deepseek-v4-flash）、`LUCA_INTERNAL_SECRET`、`LUCA_COOBOX_INTERNAL`（默认 `http://127.0.0.1:8000`，内部接口 base）、`LUCA_TENANT_QUOTA_MB`（100）、`LUCA_WALLET_URL`（`/me/wallet`）。
  - **AI 提供商覆盖**：`get_settings()` 出口（持久化之后）强制 `base_url=网关 / model=云模型 / api_key=f"{secret}:{uid}"`；`get_ai_providers()` SaaS 返回单条云提供商。设置保存接口按 `_SAAS_LOCKED_SETTINGS`（base_url/api_key/model/models/provider_presets/active_provider_idx）忽略提供商字段，其余设置（主题等）正常保存。
  - **⚠️ 密钥防泄漏（施工中发现的设计盲点）**：get_settings() 覆盖后 `api_key` 含内部密钥，而 GET/POST `/api/settings` 会把 settings 原样返回浏览器——两处响应前已加 `api_key=''` 遮蔽；持久化路径验证过不落盘（get_settings 的 save 在覆盖前、settings POST 的顶层字段保存前会被 preset 同步覆写回空值）。阶段 4 反代与阶段 3 前端无需再处理。内部调用头定为 `X-Internal-Secret`（阶段 4/5 Coobox 侧照此实现）。
  - **`GET /api/saas-info`**：单机 `{saas:false}`；SaaS 返回 `{saas,model,quota_used,quota_limit,balance_cents,wallet_url}`（quota 单位字节），balance 实时查 `GET {LUCA_COOBOX_INTERNAL}/internal/balance?uid=`（5s 超时，失败回 null 不阻塞页面）。挂在 is_authed 401 门之前（与 auth/status 同级，单机未登录也能拿到 saas:false）。
  - **磁盘配额**：`tenant_disk_usage()`（os.scandir 迭代式递归、不跟符号链接、每租户缓存 30s）+ `check_tenant_quota()`（单机恒 None）。拦截 8 处，超限 413 `{'error':'存储空间不足，已用 X / N MB'}`：章节保存、/api/import-book、/api/books/import、/api/books/import-coo、字体上传、book 封面、work 封面、merge-coo。读路径不拦。
  - **coo-push 内部直传**：work 级 `coo-push` 加 SaaS 分支——直接 `_build_coo_zip` 后 POST `{LUCA_COOBOX_INTERNAL}/internal/coo-upload`（头：`X-Internal-Secret` + `X-Luca-User` + `X-COO-Filename`），不再要求网址/邮箱/密码；响应格式与单机一致（ok/size/work_id/updated）；笔名记忆逻辑保留。
  - 验证结果（全部通过）：
    - 单机（21733）：saas-info `{saas:false}`、建书/写章、settings 保存 provider 字段正常生效，零回归 ✓
    - SaaS（21734 + mock Coobox 21800）：saas-info 全字段含 mock 余额 1234 ✓；settings GET `api_key=''`/`base_url=网关`/`model=云模型` ✓；POST 改 provider 字段被忽略、theme 正常保存 ✓；落盘 settings.json 无内部密钥 ✓
    - 配额（LUCA_TENANT_QUOTA_MB=0）：8 个拦截点全部 413 + 明确错误文案，读章节 200 不受影响；saas-info quota_used 反映真实字节数 ✓
    - coo-push：mock 收到 `uid=alice` + 1655 字节 .coo + URL 编码文件名，响应 `{ok:true,work_id,updated}` ✓；mock 关停后 balance 容错回 null ✓
  - 阶段 3（前端适配）未动，按用户要求分 session 施工。

- 2026-06-11 **阶段 1 完成。** 鉴权分支 + spawn_thread + 缓存 re-key + run()/调度器租户化全部落地，双模式冒烟验证通过。
  - 鉴权分支（`backend/main.py` Handler）：
    - `_saas_verify()`：每请求先 `_TENANT.set(None)`（防 keep-alive 残留），验 `X-Luca-User`（`is_valid_id`）+ `X-Luca-Sign`（HMAC-SHA256 `compare_digest`，secret 未配置一律拒），通过则 set 租户 + `_ensure_tenant_dirs`，失败 401 JSON。挂载顺序 `_track_me() → _saas_verify() → _check_access()`（do_GET/do_POST 各一处；`_track_me` 纯内存记录，验签前调用安全）。
    - `_check_access` SaaS 分支只验 127.0.0.1，不读 settings；`_check_csrf` SaaS 直接放行；`is_authed` SaaS 返回 `_TENANT.get() is not None`；`/api/auth/status` GET/POST 均返回 `{has_users:true,logged_in:true}`；do_POST 其余 `/api/auth/*` SaaS 一律 403。
  - `spawn_thread(target, args, kwargs, daemon, name, heavy)` helper（含全局 `Semaphore(LUCA_HEAVY_CONCURRENCY，默认1)`，heavy=True 且 SaaS 时排队）：替换了请求路径全部 24 处 `threading.Thread`。标记 heavy 的：readthrough（书/作品级全部入口）、work-sync-kb、import-verify、kb-reread（含聊天内触发+兜底）、chapter-complete、incremental_embed。未标记 heavy（但带租户）：chat/timeline/prediction/update-source/generate/browser-search/import-book/本地LLM warmup/timeline-arrange/prediction-update。保持 `threading.Thread` 的（机器级，无租户语义）：模型下载、`_restart`、`_monitor_local_llm`、启动期线程。
  - 缓存 re-key：`_chapter_brief_cache` 键 `(bid,cid)` → 章节文件完整路径；`kb_storage._get_lock` 键 book_id → `get_kb_path()` 路径（`init_db` 的 `_initialized_dbs` 本来就是 db_path 键，无需改）。
  - **计划外补充（同类隔离漏洞，已修）**：`_bg_tasks` 后台任务表按 book_id 扫描会跨租户（`bg_task_get_by_book_type` 去重会被别人的任务挡住、`bg_task_get_running_luca_chat` 会被其它租户运行中的 chat 卡住、任务列表/状态端点会泄露他人任务）。修法：`bg_task_start` 记录 `'tenant': _TENANT.get()`，新增 `_bg_task_visible()` 过滤（单机两边都是 None 恒真），应用于 `bg_task_get` / `bg_task_get_by_book_type` / `bg_task_get_running_luca_chat` 及 task status/list 两处内联扫描。
  - `run()` 租户化：`_migrate_old_books`/`_ensure_work_index`/bind_host 读 settings/嵌入 warmup 全部仅单机执行（SaaS 固定 bind 127.0.0.1）；`_load_local_strategy` 机器级保留双模式。启动脏队列复位与 `_auto_kb_scheduler_loop` SaaS 按 `_list_tenants()` 逐租户 set contextvar 扫描，`_AUTO_KB_RUNNING`/`_last_edit` 键加 `uid+':'` 前缀。
  - 验证结果（全部通过）：
    - `python3 -m py_compile main.py kb_storage.py` ✓
    - 单机模式（临时 DATA_DIR，端口 21733）：setup/login、CSRF 拦截、建书、写章、读章、书列表、未登录 401，零回归 ✓
    - SaaS 模式（LUCA_SAAS=1，端口 21734）：无签名/错签名 → 401；正确签名 → 200 且自动建 `tenants/<uid>/` 六子目录；根目录仅 machine 级文件（local_llm_port/local_strategy.json/logs/tenants）✓
    - 双租户隔离：alice 建书写章，bob 列表为空、按 alice 的 book_id 访问 → 404；settings.json 懒生成在各自租户目录 ✓
    - `/api/auth/status` 返回 `{has_users:true,logged_in:true}`，`/api/auth/login` → 403 ✓
  - 注意：curl 测试时 zsh 不对未加引号变量做 word-split，多个 `-H` 不能塞一个变量里。
  - 工作区仍含早前 session 的性能优化改动（未提交），本次未动；按要求未提交任何东西。

- 2026-06-10 **阶段 1 动工，完成第 1、2 步（基础设施 + 路径函数化），py_compile 与双模式导入验证通过。**
  - 已完成（`backend/main.py`）：
    - 顶部新增 SaaS 基础设施（紧跟 `DATA_DIR` 定义后）：`LUCA_SAAS` / `LUCA_SAAS_SECRET` / `_TENANT` contextvar / `TenantRequired` 异常 / `data_dir()` / `_ensure_tenant_dirs(uid)`（建 books/works/logs/messages/chat_sessions/fonts 六个子目录，`_tenants_ready` set 去重）/ `_list_tenants()`。
    - 12 个路径常量 → 同名小写函数：`books_dir/works_dir/log_dir/messages_dir/chat_sessions_dir/user_fonts_dir/global_chat_history_file/salt_file/settings_file/users_file/sessions_file/ai_providers_file`，全文 ~170 处引用已机械替换（BSD sed 注意：`\b` 无效，要用 `[[:<:]]`）。
    - 模块加载期改造：`ensure_dirs()` SaaS 分支只建 `DATA_DIR/tenants` + 根 `logs`；模块级 `_salt = get_salt()` 移除，旧版 SHA-256 校验改为懒调 `get_salt()`；`migrate_old_data()` / `_import_builtin_books()` 仅单机执行；`log_action()` 无租户上下文时回落根 `DATA_DIR/logs`（服务级日志）；merge 备份 mkdtemp 和 `legacy_group_archive` 改 `data_dir()`。
    - settings 默认值由 `get_settings()` 读时自动补全并保存，租户 settings 无需预创建，salt 同理懒创建——`_ensure_tenant_dirs` 不再负责 salt/settings（对计划第 1.4 条的简化）。
  - **设计偏差**（保持全局、不做租户化，理由记录）：
    - `_ENCRYPT_KEY_FILE`（`.enckey`）：服务器级 AES key，加密租户 settings 里的 API key 用同一把即可，按租户分 key 无安全增益；
    - `_LOCAL_LLM_PORT_FILE` / `LOCAL_STRATEGY_FILE`：llama.cpp 进程和硬件策略是机器级单例（embeddings.py 也直接从根 DATA_DIR 读 local_strategy.json）；
    - `coo_identity.json`：SaaS 下所有导出统一用服务身份（client `lucawriter`）签名；
    - `_V0_MIGRATION_MARKER`：仅单机有 v0 数据，保持根目录常量。
  - 验证结果：`py_compile` 通过；`DATA_DIR=/tmp/... python3 -c "import main"` 单机模式建出 books/works/logs/messages/fonts/chat_sessions，`LUCA_SAAS=1` 模式只建 tenants/+logs，均无异常。
  - **阶段 1 剩余**（下个 session 继续，设计已敲定）：
    1. 鉴权分支：Handler 加 `_saas_verify()`（每请求先 `_TENANT.set(None)` 防 keep-alive 连接残留租户，再验 `X-Luca-User` 须过 `is_valid_id` + HMAC-SHA256 `compare_digest`，通过则 set 租户 + `_ensure_tenant_dirs`，失败 401 JSON）；挂载顺序 `_track_me() → _saas_verify() → _check_access()`（do_GET/do_POST 各一处）；`_check_access` SaaS 分支只验 127.0.0.1 不读 settings；`_check_csrf` SaaS 直接放行（回环+HMAC 即信任边界）；`is_authed` SaaS 返回 `_TENANT.get() is not None`；`/api/auth/status`（GET/POST 两处）SaaS 返回 `{has_users:true,logged_in:true}`（否则前端会进 setup 界面）；do_POST 其余 `/api/auth/*` SaaS 一律 403。
    2. `spawn_thread()` helper（捕获当前租户 uid，子线程先 `_TENANT.set(uid)`）替换请求路径 ~30 处 `threading.Thread`；通读/嵌入/导入校验在 SaaS 过全局 `Semaphore(LUCA_HEAVY_CONCURRENCY，默认1)`。
    3. 缓存 re-key：`_chapter_brief_cache` 键 `(bid,cid)` → 章节文件路径（跨租户同 bid 会串数据，.coo 导入会保留原 bid，必改）；`kb_storage._get_lock` 键 book_id → db 路径。
    4. `run()` 租户化：`_migrate_old_books()`/`_ensure_work_index()` SaaS 跳过；嵌入 warmup SaaS 跳过（依赖租户 settings）；启动脏队列复位与 `_auto_kb_scheduler_loop` SaaS 按 `_list_tenants()` 逐租户 set contextvar 扫描（`_AUTO_KB_RUNNING`/`_last_edit` 键加租户前缀）。
    5. 验证：单机临时 DATA_DIR 冒烟（建书/写章/读章）+ SaaS curl 带/不带签名头验 401 与租户目录、双租户隔离。
  - 注意：本仓库工作区还有一批**与 SaaS 无关**的未提交改动（性能优化：章节列表轻量化/_chapter_brief/load_json_cached、笔名记忆、Cloudflare UA），来自更早的 session，本次施工建立在其上，提交时注意区分。

- 2026-06-10 计划制定，未动工。决策确认：兑换码（测试功能）、倍率 1.25、配额 100MB、功能全开。
