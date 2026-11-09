# MaiBot Qwen3-TTS QQ 语音插件

在本机运行 Qwen3-TTS，把合成结果发到当前 QQ 群聊或私聊。MaiBot 的官方 QQ 连接器将语音转换为 OneBot v11 `record` 消息段，再通过 SnowLuma 的 `send_msg` 发送。SnowLuma 会将 WAV 转码为 QQ 使用的 SILK 语音。

## 安装

1. 使用 MaiBot 1.3.18 或以上的 1.3.x 版本、插件 SDK 2.19.x–2.22.x 和 [官方 QQ 连接器 v1.1.13](https://github.com/Mai-with-u/MaiBot-SnowLuma-Adapter/releases/tag/v1.1.13)。在 SnowLuma 登录机器人 QQ 号并启用正向 WebSocket。按 [QQ 连接器文档](https://docs.maibot.chat/manual/adapters/qq-local-client) 配置适配器，在适配器的 `[client]` 下设置 `client_type = "snowluma"`，然后验证 QQ 文字收发。
2. 将本目录复制到 `MaiBot/plugins/maibot-qwen-tts/` 并在 WebUI 启用插件。通过 MaiBot 安装插件时会安装 `uv` 依赖。手动部署时，可在 MaiBot 的 Python 环境中安装 `uv`，或把 `uv` 加入 MaiBot 进程的 PATH。
3. 打开 **WebUI → 插件扩展 → Qwen3-TTS QQ 语音 → 设置**。在「模型规格」选择 `0.6B` 或 `1.7B`，将「手动 Python 路径」和「手动模型目录」留空，然后保存。
4. 点击配置页的 **一键安装环境和模型**。状态区每两秒刷新，显示环境安装步骤、当前依赖和模型下载百分比。关闭页面后任务继续运行。遇到下载失败时可再次点击，复用已安装的依赖和已下载的文件。

自动安装会在插件数据目录的 `qwen-tts/` 下创建独立的 Python 3.12 推理环境和模型目录。0.6B 的模型文件约 2.5 GB，1.7B 约 4.5 GB，Python 环境和下载缓存另外占用空间。请确保 MaiBot 所在机器或容器可以访问 PyPI、PyTorch 下载站和 Hugging Face。

推理设备设为 `auto` 时，初次安装会在支持 CUDA 12.8 的 Windows/Linux x86-64 NVIDIA 环境中安装 CUDA 版 PyTorch，其他环境使用 CPU 版。可用 `cpu` 指定 CPU，或用 `cuda`、`cuda:0` 等指定 GPU。切换模型规格后，保存并安装对应模型即可。已就绪的模型和推理环境会直接复用。

## Instruction Control

选择 **1.7B CustomVoice** 后，在「Instruction Control · 朗读指令」中填写自然语言描述，例如「用轻快、亲切的语气说，语速稍慢」。保存后，`/tts` 和语音回复都会将此指令传给模型。留空使用音色的默认风格。模型和音色列表见 [Qwen3-TTS 官方文档](https://github.com/QwenLM/Qwen3-TTS)。

## 使用已有环境或模型

已配置的 `python` 和 `model_dir` 优先使用。也可以只填写其中一项，通过一键安装补齐另一项。手动指定模型目录时，插件读取目录内的模型，规格下拉框用于选择自动下载的 CustomVoice 模型。

如需手动创建环境，下面的 Windows PowerShell 命令在插件目录中执行，使用 `uv`。

```powershell
uv venv --python 3.12 .venv-tts
$ttsPython = (Resolve-Path .\.venv-tts\Scripts\python.exe).Path

# NVIDIA GPU 示例。根据驱动和 GPU 选择 PyTorch 的 CUDA 构建。
uv pip install --python $ttsPython torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $ttsPython -r requirements-tts.txt
```

Windows 或 Linux 纯 CPU 环境可将安装 PyTorch 的索引改为 `https://download.pytorch.org/whl/cpu`。Linux/macOS 的虚拟环境解释器位于 `.venv-tts/bin/python`。其他平台按 [PyTorch 安装说明](https://pytorch.org/get-started/locally/) 选择匹配的 `torch` 和 `torchaudio`。如果 MaiBot 在容器中运行，`python` 和 `model_dir` 都应填写容器内可访问的路径。

可用以下命令手动下载模型。将命令中的 `0.6B` 改为 `1.7B` 即可下载大模型。

```powershell
uvx --from huggingface-hub hf download Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice --local-dir ./models/Qwen3-TTS-12Hz-0.6B-CustomVoice
```

`model_dir` 应指向包含 `config.json`、模型权重和 `speech_tokenizer/` 的目录。准备完模型后，推理进程以离线模式加载权重。

在 WebUI 中填写本地路径，或编辑插件目录生成的 `config.toml` 后重载插件。以下是手动设置的 Windows 示例。TOML 单引号可以直接容纳路径中的反斜杠。

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

首次请求会加载模型，其后的请求复用已加载的模型。每次运行一个合成请求，超时后下一次请求会重启推理进程。

| 模型类型    | 音色设置                                                                                            |
| ----------- | --------------------------------------------------------------------------------------------------- |
| CustomVoice | `speaker` 选择预设音色，如 `Vivian` 或 `Serena`。1.7B 模型可用 `instruct` 指定朗读风格。            |
| VoiceDesign | `instruct` 填写音色描述。                                                                           |
| Base        | `ref_audio` 填写参考音频的本地绝对路径，建议用 WAV。`ref_text` 填写逐字稿，留空时只提取说话人音色。 |

## 使用

- 在适配器已放行的 QQ 群聊或私聊发 `/tts 你好，我是麦麦`。插件将这段文字读成 QQ 语音。
- 在普通聊天中说「请用语音回复」。当 MaiBot 的 Planner 选择 `local.qwen-tts.voice` 回复扩展时，插件会朗读本次回复。扩展参数 `keep_text = true` 会同时保留文字。合成出错或文本超过 `max_chars` 时，MaiBot 继续发送原回复。
- 命令和回复扩展使用当前会话。群聊中想限制 `/tts` 的使用范围，可以在 MaiBot 的 QQ 连接器访问策略中设置放行范围。

若命令提示合成失败，查看插件日志中的 Python 或模型错误。若语音发送失败，检查 SnowLuma 是否在线、QQ 连接器的客户端类型是否为 `auto` 或 `snowluma`，以及适配器发送日志。
