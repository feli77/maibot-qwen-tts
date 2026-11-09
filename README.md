# MaiBot Qwen3-TTS QQ 语音插件

在本机运行 Qwen3-TTS，把合成结果发到当前 QQ 群聊或私聊。MaiBot 的官方 QQ 连接器将语音转换为 OneBot v11 `record` 消息段，再通过 SnowLuma 的 `send_msg` 发送。SnowLuma 会将 WAV 转码为 QQ 使用的 SILK 语音。

## 安装

1. 使用 MaiBot 1.3.18 或以上的 1.3.x 版本、插件 SDK 2.19.x–2.22.x 和 [官方 QQ 连接器 v1.1.13](https://github.com/Mai-with-u/MaiBot-SnowLuma-Adapter/releases/tag/v1.1.13)。在 SnowLuma 登录机器人 QQ 号并启用正向 WebSocket。按 [QQ 连接器文档](https://docs.maibot.chat/manual/adapters/qq-local-client) 配置适配器，在适配器的 `[client]` 下设置 `client_type = "snowluma"`，然后验证 QQ 文字收发。
2. 将本目录复制到 `MaiBot/plugins/maibot-qwen-tts/`，确保 `_manifest.json`、`plugin.py` 和 `tts.py` 都在插件目录根部。
3. 使用 Python 3.12 创建独立推理环境。下面的 Windows PowerShell 命令在插件目录中执行，使用 `uv`。

   ```powershell
   uv venv --python 3.12 .venv-tts
   $ttsPython = (Resolve-Path .\.venv-tts\Scripts\python.exe).Path

   # NVIDIA GPU 示例。根据驱动和 GPU 选择 PyTorch 的 CUDA 构建。
   uv pip install --python $ttsPython torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
   uv pip install --python $ttsPython -r requirements-tts.txt
   ```

   Windows 或 Linux 纯 CPU 环境可将安装 PyTorch 的索引改为 `https://download.pytorch.org/whl/cpu`。Linux/macOS 的虚拟环境解释器位于 `.venv-tts/bin/python`。其他平台按 [PyTorch 安装说明](https://pytorch.org/get-started/locally/) 选择匹配的 `torch` 和 `torchaudio`。如果 MaiBot 在容器中运行，`python` 和 `model_dir` 都应填写容器内可访问的路径。
4. 若已有完整 Qwen3-TTS 模型目录，可直接使用。也可以下载 0.6B CustomVoice 模型。

   ```powershell
   uvx --from huggingface-hub hf download Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice --local-dir ./models/Qwen3-TTS-12Hz-0.6B-CustomVoice
   ```

   `model_dir` 应指向包含 `config.json`、模型权重和 `speech_tokenizer/` 的目录。准备完模型后，推理进程以离线模式加载权重。

## 配置

在 WebUI 的插件设置中填写 `python` 和 `model_dir`，或编辑插件目录生成的 `config.toml` 后重载插件。以下是 Windows 示例。TOML 单引号可以直接容纳路径中的反斜杠。

```toml
[plugin]
enabled = true
config_version = "1.0.0"

[tts]
python = 'D:\MaiBot\plugins\maibot-qwen-tts\.venv-tts\Scripts\python.exe'
model_dir = 'D:\models\Qwen3-TTS-12Hz-0.6B-CustomVoice'
device = "auto"
language = "Auto"
speaker = "Vivian"
max_chars = 120
timeout_seconds = 120
```

`device = "auto"` 优先使用 CUDA，否则使用 CPU。也可以填 `cpu`、`cuda` 或 `cuda:0`。首次请求会加载模型，其后的请求复用已加载的模型。每次运行一个合成请求，超时后下一次请求会重启推理进程。

| 模型类型    | 音色设置                                                                                            |
| ----------- | --------------------------------------------------------------------------------------------------- |
| CustomVoice | `speaker` 选择预设音色，如 `Vivian` 或 `Serena`。1.7B 模型可用 `instruct` 指定朗读风格。            |
| VoiceDesign | `instruct` 填写音色描述。                                                                           |
| Base        | `ref_audio` 填写参考音频的本地绝对路径，建议用 WAV。`ref_text` 填写逐字稿，留空时只提取说话人音色。 |

模型与音色详情见 [Qwen3-TTS 官方文档](https://github.com/QwenLM/Qwen3-TTS)。

## 使用

- 在适配器已放行的 QQ 群聊或私聊发 `/tts 你好，我是麦麦`。插件将这段文字读成 QQ 语音。
- 在普通聊天中说「请用语音回复」。当 MaiBot 的 Planner 选择 `local.qwen-tts.voice` 回复扩展时，插件会朗读本次回复。扩展参数 `keep_text = true` 会同时保留文字。合成出错或文本超过 `max_chars` 时，MaiBot 继续发送原回复。
- 命令和回复扩展使用当前会话。群聊中想限制 `/tts` 的使用范围，可以在 MaiBot 的 QQ 连接器访问策略中设置放行范围。

若命令提示合成失败，查看插件日志中的 Python 或模型错误。若语音发送失败，检查 SnowLuma 是否在线、QQ 连接器的客户端类型是否为 `auto` 或 `snowluma`，以及适配器发送日志。
