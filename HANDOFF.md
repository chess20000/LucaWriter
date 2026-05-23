# 本地模型功能交接

## 已完成

- **硬件检测 + 决策树**：`_detect_hardware()` / `_decide_local_strategy()` / `_apply_bundle_limit()` / `_build_llm_args()`。9 条规则把 (OS, GPU 厂家, 显存, 内存, Apple Silicon) → (tier, binary, model, offload_mode)。
- **接口**：`GET /api/local-llm/hardware-check`，结果缓存到 `usrdata/local_strategy.json`。
- **启动参数**：`_start_local_llm()` 改用动态参数（`-c 65536 -fa auto -ctk q8_0 -ctv q8_0 -np 1 -t<=8`），按 mode 追加 `-ngl 99` / `-ot MoE expert 钉 CPU` / `-ngl 0`；**仅 Tier A + CUDA 加 MTP**（`--spec-type draft-mtp --spec-draft-n-max 1`）。
- **模型预设**：Qwen 3.5 9B **Q4_K_M**（5.87GB，对应 DS 报告原始版本）+ Qwen 3.6 35B A3B APEX **I-Mini**（13.3GB）。
- **设置面板**：原来的两个静态下载按钮（Gemma / Qwen）已删，换成硬件摘要 + 单个智能推荐按钮；红灯走 DeepSeek 引导 → `platform.deepseek.com`。
- **「切换本地模型」按钮**：默认 `display:none`，仅当模型已下载时露出。
- **嵌入模型设备路由**：`embeddings._pick_embedding_device()` 按策略决定 BAAI/bge-small 放 CPU 还是 GPU；仅 Tier A + CUDA + 显存 ≤8.5GB 强制 CPU。
- **启动竞争修复**：`run()` 启动时调一次 `_load_local_strategy()`，确保 embedding warmup 线程读策略缓存时已就位。
- **打包**：`local_llm/llama-server.exe` + 7 个 DLL（CUDA + MTP build, commit `1d7ab2b`，~45MB），electron-builder `extraResources` 已配置一并打包。
- **测试用模型已就位**：`local_llm/models/Qwen3.5-9B-Q4_K_M.gguf`（从 D:\AI相关 拷的，5.5GB，gitignore 不进版本）。
- **设计文档**：`LOCAL_MODEL_DESIGN.md`。

## 未完成

### 1. Tier B + GPU 用户给"也下载 9B"备选按钮（小工作量）

**位置**：`frontend/index.html` 的 `refreshHardwareCheck()` 函数，约 3543 行起的动作区分支。

**逻辑**：当 `strat.tier === 'B' && !modelDetected` 时，主按钮下面加一行更小的次级按钮 / 文字链，调用 `downloadModel('qwen3.5-9b')`。文案类似"想要更小的？下载 Qwen 3.5 9B Q4_K_M (~5.9GB)"。

### 2. 顶栏 Luca 呼吸灯 → prefill/gen 速度显示（中等工作量，有坑）

**位置**：
- HTML：`frontend/index.html:937` `<div class="ai-active-dots" id="aiActiveDots">`
- JS 渲染：`frontend/index.html:1559-1564`，循环画 1-5 个 `<div class="ai-active-dot">`
- CSS：`frontend/index.html:447-457` `aiDotBreathe` 动画

**预期行为**：
- AI 工作时，呼吸点替换为文字：prefill 阶段显示 `prefill 273 t/s`，生成阶段显示 `gen 56 t/s`
- 空闲时隐藏（跟现在一样）

**坑**：用户自建的 llama.cpp (commit `1d7ab2b`) 的 `/slots` 返回的字段是**精简版**，只有 `id, id_task, is_processing, n_ctx, next_token[{has_next_token, has_new_line, n_remain, n_decoded}], params, speculative`，**没有 `tokens_predicted_per_second` 这种现成的速度字段**。三种可行思路：

   - **(A) 后端轮询 /slots，自己算速度**：每 500ms curl `/slots`，记录 `next_token[0].n_decoded` 和时间戳，diff 出 tok/s。phase 判定：`n_decoded` 在涨 → 生成；前一次 prompt 处理时 `is_processing=true` 但 `n_decoded` 还是 0 / 没变化 → prefill。需要新加一个 `/api/local-llm/speed` 接口。
   - **(B) 解析 llama-server 自己输出的日志**：log 里有 `prompt eval time` 和 `eval time` 两行，但是异步的、要在请求结束后才有。
   - **(C) 把 `--props` 或新版 llama.cpp 的 `timings_per_token` 流响应字段打开**：在 `_build_llm_args` 里加 `--props`。但用户的 build 是 1d7ab2b，不确定是否支持新字段，要先 `--help | grep timings`。

   **推荐 (A)**，最稳，跟前端配合简单。

### 3. （可选优化）Tier 升级提示
当前没有"我换了显卡 / 加了内存"的重新检测入口。如果用户硬件变了，要手动 `del usrdata/local_strategy.json` 才会重新检测。可以在设置里加一个"重新检测硬件"按钮，调 `/api/local-llm/hardware-check?force=1` 重写缓存。

## 重要上下文 / 不要踩的坑

- **`usrdata/` 是主项目 usrdata 的 Windows junction**（我用 `mklink /J` 建的）。worktree 和主项目共享同一份用户数据，所以书、对话、本地策略都通的。**不要 `rm -rf usrdata/`**——会触发删主项目数据。删的话用 `rmdir usrdata`（Windows）只删 junction，不动 target。
- **`local_llm/` 在 `.gitignore` 里**——binary 和模型都不会进 git。新克隆需手动拷 binary + 模型。打包时 electron-builder 从 filesystem 读，会一并打包（除了 `models/**` 和 `*.md`，filter 已配）。
- **DS 报告**（在 `D:\AI相关\llama.cpp 部署报告.md`）里关键事实：
  - 用户的 binary 是从 `ggml-org/llama.cpp` commit `1d7ab2b` 自建（CMake + CUDA + Build UI Off）
  - 验证过的参数：`-c 65536 -ctk q8_0 -ctv q8_0 -fa auto --spec-type draft-mtp --spec-draft-n-max 1 -ngl 99 -t 8`
  - 实测：**56 t/s gen, 273 t/s prefill, VRAM 7.9/8 GB**（顶到边）
  - 用户原本手动改了 `embeddings.py` 让 device=cpu 是为了让出那 300MB。现在已经做成自动判定。
- **`_apply_bundle_limit` 是关键安全网**：v1 只打了 CUDA build，AMD/Mac/纯 CPU 用户硬件即使理论上能跑也会被降级到 API 兜底，避免启动失败的糟糕体验。后续补齐其它 build 时把字符串加进 `_BUNDLED_BINARIES` 集合即可，决策树代码不变。
- **MTP 默认仅 Tier A + CUDA 启用**：因为 35B A3B 在消费级 GPU 实测净亏 3-12%（见 [llama.cpp issue #22320](https://github.com/ggml-org/llama.cpp/issues/22320)）。换模型时这条约束要重测。
- **模型换 GGUF 文件**只需改 `_PRESET_MODELS` 里的 `file` 和 `size_gb`，**决策树和启动参数不动**——这是设计原则（"档位驱动，不锁模型"，见 `LOCAL_MODEL_DESIGN.md`）。
- **重启 backend 的方法**：`taskkill /F /PID <旧 PID>` 用 PowerShell 的 `Stop-Process -Id <pid> -Force`（bash 里 `taskkill /F` 会被当成路径，要用 `//F` 转义）。
- **Electron 前端**不会自动重载新 HTML，改了 `frontend/index.html` 后需要在 app 里 **Ctrl+R 刷新**（或 Ctrl+Shift+R 忽略缓存）。
