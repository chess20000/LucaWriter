# 本地模型可用性自动检测与选型设计

## 目标

让非技术用户（"文科生"）开箱即用 AI 写作功能，不需要理解显存 / 量化 / 推理后端。第一次启动时自动检测硬件，决定走本地模型还是云端 API 引导，不出错就是默认状态。

## 总体原则

1. **不暴露调参面板**。所有 llama.cpp 参数（ctx、KV 量化、Flash Attention、采样参数）按档位定死。要调参的人自己装 LM Studio。
2. **检测一次，记住选择**。首次启动决定的路径写入用户配置，后续不重复检测。"我换模型了" 通过设置菜单进入。
3. **本地模型按"内存档位"分类，不锁死具体模型名**。新模型只要内存占用落进现有档位、写作质量验证通过，就可原地替换。当前两档：
   - **Tier A**：~8GB 总占用 → 现行 `Qwen3.5-9B-DeepSeek-V4-Flash-MTP Q4_K_M`
   - **Tier B**：~18GB 总占用 → 现行 `Qwen3.6-35B-A3B-APEX-GGUF (Mini)`
4. **硬件不达标 → API**，不偷偷给劣化体验。4B 以下、3-bit 以下的模型写小说会失忆、串人物，对写作软件等于劣品，不留分支。

## 硬件检测

### 检测项（首次启动）

| 项 | Windows / Linux | macOS |
|---|---|---|
| 操作系统 | `platform.system()` | 同 |
| 系统 RAM | `psutil.virtual_memory().total` | 同（unified memory） |
| 独显厂家 + 显存 | `nvidia-smi`（N 卡）/ `wmic path Win32_VideoController`（A 卡） | 不区分（统一内存） |
| Apple Silicon | — | `platform.processor() == 'arm'` |

**判定原则保守**。检测失败的项按"无独显 / 较小内存"处理，宁可降级到 API 也不要假阳性导致 OOM。

### Mac 特殊约束

- macOS 默认 Metal wired memory 上限 = unified memory × 0.75，超过会强制 swap。这是"能不能跑"的硬约束。
- 16GB Mac 跑 Tier A 临界（合计 13-15GB），UI 在该档显示轻提示"建议关闭其他大型应用"。

## 内存档位

### Tier A：~8GB 总占用

| 项 | 占用 |
|---|---|
| 模型权重 (Qwen3.5-9B DeepSeek V4 Flash MTP Q4_K_M) | ~5.8 GB |
| 65536 ctx KV cache (Q8_0, hybrid arch) | ~1.0 GB |
| Gated DeltaNet recurrent state | ~0.5 GB |
| **小计** | **~7 GB** |

Qwen3.5-9B DeepSeek V4 Flash MTP 是 Qwen3.5-9B 的 DeepSeek V4 Flash 蒸馏 GGUF，保留 Qwen3.5 混合架构与 MTP 支持；Q4_K_M 权重约 5.8GB，仍落在 Tier A。

**替换规范**：新模型若 Q4 权重 + 65k ctx Q8_0 KV ≤ **8GB**，可入 Tier A。

### Tier B：~18GB 总占用

| 项 | 占用 |
|---|---|
| 模型权重 (APEX I-Mini) | 13.3 GB |
| 65536 ctx KV cache (Q8_0) | ~5 GB |
| **小计** | **~19 GB** |

APEX (Adaptive Precision for EXpert Models) 是 MoE 感知的混合精度量化，路由专家压得狠、边层和共享专家精度高，质量数据上 I-Balanced 的 KL max 比 Q8_0 还低。"I-" 前缀表示 importance-aware 变体。stock llama.cpp GGUF，无需 fork。仓库里还有 Compact (16GB) / Quality (21GB) / Balanced (24GB) 等更大档可在替换时考虑。

**替换规范**：新模型若权重 + 65k ctx Q8_0 KV ≤ **20GB**，可入 Tier B。

## 完整决策树

```
Windows / Linux:
├ N 卡 ≥8GB VRAM, RAM ≥32GB   → CUDA   binary + Tier B (hybrid: weights→RAM, KV→VRAM)
├ N 卡 ≥8GB VRAM, RAM <32GB   → CUDA   binary + Tier A (全 VRAM)
├ A 卡 ≥8GB VRAM, RAM ≥32GB   → Vulkan binary + Tier B (hybrid)
├ A 卡 ≥8GB VRAM, RAM <32GB   → Vulkan binary + Tier A (全 VRAM)
├ 无 ≥8GB 独显,  RAM ≥32GB    → CPU    binary + Tier B (纯 CPU 推理)
├ 无 ≥8GB 独显,  RAM 16-32GB  → CPU    binary + Tier A (纯 CPU 推理)
└ 都不满足                    → DeepSeek API 引导

macOS:
├ Apple Silicon ≥32GB unified → Metal  binary + Tier B
├ Apple Silicon 16-24GB       → Metal  binary + Tier A  (16GB 显示弱提示)
└ <16GB unified               → DeepSeek API 引导
```

**优先级**：Tier B > Tier A。能跑 35B 就跑 35B（更聪明），有独显在场时也用 Tier B 走 hybrid offload，不退回 Tier A。

## 启动参数

### 通用基础（所有本地分支）

```
--host 127.0.0.1 --port <usrdata/local_llm_port 中持久化的随机端口>
-c 65536
-fa auto
-ctk q8_0 -ctv turbo3     # TurboQuant+：K 保持 q8_0，V 用 turbo3 (~4.6× 压缩, <1.5% PPL)
-np 1
-t <min(cpu_threads, 8)>
--timeout 300
```

**TurboQuant+ KV 量化备注**：`turbo3` 是 TurboQuant+ fork（commit 2cbfdc6，单 exe ~48MB）新增的非对称 V cache 量化。README 明确警告 K 不能用 turbo（"never lead with a turbo K"），所以 K 永远是 `q8_0`/`f16`。可替代值：`turbo4` 更保守（~2× 压缩，最稳），`turbo2` 更激进（~9× 压缩，自动启用 Boundary V 保护）。`turbo3` 是 README 推荐的甜点档位。

### Tier A 分支增量

- 全 GPU（NVIDIA/AMD ≥8GB VRAM）：`-ngl 99`
- 纯 CPU：`-ngl 0`
- macOS Metal：`-ngl 99`（Metal 自动用 unified memory）
- **CUDA 路径额外开 MTP**：`--spec-type draft-mtp --spec-draft-n-max 1`
  - Qwen3.5-9B 原生带 MTP 头（`nextn_predict_layers=1`），不需要单独的 MTP 仓库
  - 用户 RTX 4070 Laptop 实测：从 ~30 t/s → **56 t/s**，几乎 2x
  - 仅在 CUDA 上启用，因为只在该 binary 上验证过；AMD Vulkan / 纯 CPU / Metal 默认不开

### Tier B 分支增量

- **混合 offload**（独显 ≥8GB + RAM ≥32GB）：
  ```
  -ngl 99
  -ot "blk\.\d+\.ffn_.*_exps=CPU"
  ```
  正则把 MoE expert 张量钉在 CPU，attention / 共享专家 / embed / norm / KV cache 全去 GPU。8GB 显存装得下非 expert 部分 + KV。
- **纯 CPU**（RAM ≥32GB，无合格独显）：`-ngl 0`
- **macOS Metal**（≥32GB unified）：`-ngl 99`，不需要 `-ot`

### 采样参数（统一定死，不暴露）

```
temperature        = 0.7
top_p              = 0.8
top_k              = 20
min_p              = 0.0
presence_penalty   = 1.5
repetition_penalty = 1.0
enable_thinking    = false
```

Qwen3.5-9B 和 Qwen3.6-35B-A3B 的官方非思考模式推荐采样恰好一致，写作场景默认禁用 thinking（不让 `<think>` 标签泄漏给用户）。

## binary 打包策略

**v1 仅打包 CUDA build**（约 +45MB），直接放进 `local_llm/` 由 electron-builder 的 `extraResources` 配置一并打包到安装包。原因：

1. 用户基数最大的是 N 卡用户
2. 维护一个 build 比四个稳
3. 非 CUDA 用户（AMD / Apple Silicon / 纯 CPU）由 `_apply_bundle_limit()` 自动降级为 DeepSeek API 路径，避免启动失败的糟糕体验

未来扩展到全 4 个 build 时，把对应字符串加入 `_BUNDLED_BINARIES` 集合即可，决策树代码不变。

| binary | 状态 | 体积 |
|---|---|---|
| **TurboQuant+ CUDA build (含 MTP + turbo KV, commit 2cbfdc6)** | **v1 已打包** | **48 MB（单 exe）** |
| Vulkan build | 待加 | ~80 MB |
| CPU-only build | 待加 | ~30 MB |
| Metal build (universal2) | 待加 | ~80 MB |

打包好的 binary 清单（放在 `local_llm/`，已被 `.gitignore` 排除，需 build 前手动放置）：
- `llama-server.exe` (48 MB，TurboQuant+ fork，所有依赖静态链接：CUDA backend + ggml + llama + common + mtmd)

**开发者环境准备**：从 `D:\AI相关\llama-cpp-turboquant\llama-server.exe`（或等效 TurboQuant+ build）拷贝该单 exe 到项目根 `local_llm/`。electron-builder 的 `extraResources` 配置（`electron/package.json`）会自动将 `local_llm/` 整目录复制进安装包，过滤掉 `models/**` 和 `*.md`。

**先前的拆分 DLL CUDA-MTP build**（commit 1d7ab2b，8 个 DLL+exe 共 ~45MB）已备份在 `local_llm.cuda_mtp_backup/`，需要回滚时整目录还原即可。

**版本要求**：llama.cpp ≥ **2026-05-16 主干**（PR #22673 之后）。早期版本不识别 Qwen3.5 hybrid 架构和 Qwen3.6 MoE，整张表的本地分支都不可用。当前打包的 TurboQuant+ commit `2cbfdc6` 含 MTP 支持以及 `turbo2/3/4` 系列非对称 V cache 量化。

## DeepSeek API 引导（兜底路径）

不达标 → 兜底页面，**不是错误页**，文案上不要让用户感到被淘汰。

文案要点：
- 标题："您的设备暂时不适合本地 AI，我们建议使用云端 API"
- 成本说明："约 ¥10 可写 30 万字（DeepSeek 当前价格）"
- 主按钮："前往 DeepSeek 开放平台申请 Key" → 浏览器打开 `https://platform.deepseek.com/`
- 三步说明文字："注册 → 控制台充值 → 创建 API Key 粘贴到本软件"
- 次按钮："我已经有 Key，直接填" → 跳到设置界面的 DeepSeek 预设

**用系统浏览器打开**，不要内嵌 webview——手机验证码、登录态在原生浏览器里顺很多。

## 首次启动 UI 流程

1. **欢迎屏**：一句话介绍 + "开始" 按钮
2. **检测中**：进度提示（实际 ~1 秒），留转场动画 1-2 秒避免突兀
3. **结果屏**（三种之一）：
   - **绿灯**："您的设备可流畅运行本地 AI"
     - 显示要下载的模型名 + 文件大小（Tier A ~5GB / Tier B ~14GB）
     - 主按钮："下载并启用"
     - 次按钮："改用云端 API"
   - **黄灯**（仅 16GB Mac / 16GB Windows 无独显）："您的设备可以运行本地 AI"
     - 文案补充："运行时建议关闭其他大型应用"
     - 选项同绿灯
   - **红灯**："您的设备暂时不适合本地 AI" → 见 DeepSeek 引导小节
4. **始终保留**菜单 "AI 设置"：用户随时切换本地 / API、重新检测硬件。

## 模型替换规范（未来扩展）

出新模型想替换 Tier A 或 Tier B 时，验证清单：

1. **内存占用**：Q4 权重 + 65k ctx Q8_0 KV ≤ Tier 上限（A: 8GB / B: 20GB）
2. **架构兼容**：当前打包的 llama.cpp binary 能加载（看是否要升 binary 版本）
3. **中文写作质量**：拿 4-5 万 token 旧稿从中间续写 500 字，验证不忘人设、不串人名、文风不漂移
4. **拒绝率**：测三个常见小说场景（死亡、暧昧、暴力冲突），拒绝或添加 AI 免责声明的比例 < 10%
5. **采样兼容**：上节统一采样参数对新模型不破坏输出

四关全过 → 直接换 GGUF 文件路径 + 模型名常量。决策树和启动参数不动。

## ChromaDB 嵌入模型的设备策略

`backend/embeddings.py` 的 `_pick_embedding_device()` 按本地策略决定嵌入模型（BAAI/bge-small-zh-v1.5，~300MB VRAM）放 CPU 还是 GPU：

| 场景 | 设备 | 原因 |
|---|---|---|
| **Tier A (9B) + CUDA + 显存 ≤8.5GB** | **CPU** | 9B 全模型 + KV + MTP 已经吃满 8GB（用户实测 7.9/8GB），嵌入再抢 300MB 必 OOM |
| Tier A + CUDA + 显存 ≥10GB | GPU（默认） | 余量充足，GPU embedding 比 CPU 快 3-5x |
| Tier B (35B hybrid) + 任意显存 | GPU（默认） | hybrid 模式只 KV 上 GPU（~5GB），8GB 卡上 300MB 余量充裕 |
| 纯 CPU / Vulkan / Metal | 默认 | SentenceTransformer auto-select |
| API 嵌入用户 | 不涉及 | 走云端 |

`backend/main.py` 的 `run()` 启动时调一次 `_load_local_strategy()`，确保 `local_strategy.json` 在 embedding warmup 线程加载前已写入磁盘，避免首次启动竞争（缓存还没建 → 嵌入按默认装到 GPU → 用户后开启动本地模型 → OOM）。

## 明确不做的事

- 自动 benchmark（实测 token/s）：增加首次启动等待 + 复杂度，价值低
- 中等档位（Tier 之间的 4B / 30B 等）：增加分支不增加体验
- 参数调整 UI：用户调坏了责任不清，要调请装 LM Studio
- MTP 投机解码默认开启：消费级 GPU 实测净亏 3-12%（[llama.cpp PR #19493 后基准](https://github.com/thc1006/qwen3.6-speculative-decoding-rtx3090)），留作高级隐藏选项
- TurboQuant+ KV / 自定义 llama.cpp fork：维护成本高，标准 Q8_0 KV 够用
- "去审查" 模型默认下发：品牌/法务风险，可做成"高级 → 切换非官方模型"的隐藏选项
- GPT-OSS / Carnice 等非中文创作向模型：测过，中文文学质量或拒绝率不达标

## 参考

- [Jackrong/Qwen3.5-9B-DeepSeek-V4-Flash-MTP-GGUF](https://www.modelscope.cn/models/Jackrong/Qwen3.5-9B-DeepSeek-V4-Flash-MTP-GGUF/summary) — Tier A 来源
- [mudler/Qwen3.6-35B-A3B-APEX-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-APEX-GGUF) — Tier B 来源
- [mudler/apex-quant](https://github.com/mudler/apex-quant) — APEX 量化原理
- [Unsloth Qwen3.5 Docs](https://unsloth.ai/docs/models/qwen3.5)
- [Unsloth Qwen3.6 Docs](https://unsloth.ai/docs/models/qwen3.6)
- [llama.cpp issue #22320](https://github.com/ggml-org/llama.cpp/issues/22320) — APEX I-Compact GPU 利用率已知问题
