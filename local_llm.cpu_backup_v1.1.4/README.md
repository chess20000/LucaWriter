# LucaWriter 本地 Llama.cpp 服务

本目录包含独立运行的 **llama.cpp server**，为 LucaWriter 提供完全离线的本地 AI 推理能力。

## 模型信息

- **模型**：NVIDIA Nemotron 3 Nano 4B (Q4_K_M)
- **路径**：`models/NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf`
- **量化**：Q4_K_M（平衡质量与速度）

## 启动方式

直接双击或命令行运行：

```batch
start_server.bat
```

服务器将启动在 `http://127.0.0.1:8080`，**仅监听本机地址**，不允许外部网络访问。

## 在 LucaWriter 中使用

1. 先运行 `start_server.bat` 启动本地服务器。
2. 打开 LucaWriter 设置（⚙️）。
3. 在「AI 提供商」中选择预设 **「本地 Llama.cpp」**。
4. 点击「获取模型列表」可自动拉取可用模型（通常显示为默认模型名）。
5. 保存后即可开始对话或通读。

## 停止服务

在 `start_server.bat` 窗口中按 `Ctrl+C` 即可停止。

## 安全说明

- `--host 127.0.0.1` 确保只有本机可以访问，局域网/公网无法连接。
- 如需更换模型，把新的 `.gguf` 文件放入 `models/` 文件夹，并修改 `start_server.bat` 里的 `MODEL` 文件名即可。

## 文件说明

| 文件 | 说明 |
|------|------|
| `llama-server.exe` | llama.cpp 推理服务器 |
| `start_server.bat` | 一键启动脚本（已绑定 127.0.0.1） |
| `models/` | 存放 GGUF 模型文件 |
| `*.dll` | 运行所需的依赖库 |

## 进阶参数

如需调整上下文长度、并发数等，编辑 `start_server.bat` 中的参数：

- `-c 131072`：上下文长度（token 数），默认 131072
- `-np 1`：并发槽位数
- `--timeout 300`：单次请求超时秒数

更多参数请参阅 [llama.cpp 官方文档](https://github.com/ggml-org/llama.cpp/blob/master/examples/server/README.md)。
