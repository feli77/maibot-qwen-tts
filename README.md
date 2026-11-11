# MaiBot Qwen3-TTS QQ 语音插件

在本机运行 Qwen3-TTS，把合成结果作为语音发到当前 QQ 群聊或私聊。插件使用 MaiBot 的标准语音消息，可通过 QQ 官方机器人适配器或统一 QQ 连接器发送。

## 安装

1. 使用 MaiBot 及随宿主安装的插件 SDK。使用 QQ 开放平台时，按 [QQ 官方机器人适配器文档](https://docs.maibot.chat/manual/adapters/qq-official) 配置当前实例。通过 SnowLuma 或 NapCat 接入 QQ 时，按 [统一 QQ 连接器文档](https://docs.maibot.chat/manual/adapters/qq-local-client) 安装并配置适配器。安装插件前先验证 QQ 文字收发。
2. 将本目录复制到目标 Bot 实例的 `plugins/maibot-qwen-tts/` 并在 WebUI 启用插件。通过 MaiBot 安装插件时会安装 `uv` 依赖。手动部署时，可在 MaiBot 的 Python 环境中安装 `uv`，或把 `uv` 加入 MaiBot 进程的 PATH。
3. 打开 **WebUI → 插件扩展 → Qwen3-TTS QQ 语音 → 设置**。在「模型规格」选择 `0.6B` 或 `1.7B`，将「手动 Python 路径」和「手动模型目录」留空，然后保存。
4. 点击配置页的 **一键安装或升级环境和模型**。状态区显示环境安装步骤、当前依赖和模型下载百分比。关闭页面后任务继续运行。遇到下载失败时可再次点击，复用已安装的依赖和已下载的文件。

自动安装会在当前 Bot 实例的 `data/plugins/local.qwen-tts/qwen-tts/` 下创建独立的 Python 推理环境和模型目录。0.6B 的模型文件约 2.5 GB，1.7B 约 4.5 GB，Python 环境和下载缓存另外占用空间。请确保 MaiBot 所在机器或容器可以访问 PyPI、PyTorch 下载站和 Hugging Face。模型默认通过 Hugging Face 的 HTTP 下载，在 MaiBot 进程环境中设置 `HF_HUB_DISABLE_XET=0` 可启用 Xet。

推理设备设为 `auto` 时，插件在 Windows/Linux x86-64 上通过 `nvidia-smi` 选择兼容的 CUDA 构建或 CPU 构建。可用 `cpu` 指定 CPU，或用 `cuda`、`cuda:0` 等指定 GPU。

覆盖原有插件文件时，请保留插件目录内的 `config.toml` 和实例下的插件数据目录。升级时可继续使用已就绪的旧推理环境。如果配置页显示「可升级」，点击安装按钮即可在同一数据目录下安装新版环境，并复用已下载的模型。切换模型规格后，保存并安装对应模型即可。

## Instruction Control

选择 **1.7B CustomVoice** 后，在「Instruction Control · 朗读指令」中填写自然语言描述，例如「用轻快、亲切的语气说，语速稍慢」。保存后，`/tts` 和语音回复都会将此指令传给模型。留空使用音色的默认风格。模型和音色列表见 [Qwen3-TTS 官方文档](https://github.com/QwenLM/Qwen3-TTS)。

## 使用已有环境或模型

已配置的 `python` 和 `model_dir` 优先使用。也可以只填写其中一项，通过一键安装补齐另一项。手动指定模型目录时，插件读取目录内的模型，规格下拉框用于选择自动下载的 CustomVoice 模型。

手动创建虚拟环境时，参照 [安装器](installer.py) 使用对应的 Python 版本，再按 [PyTorch 安装说明](https://pytorch.org/get-started/locally/) 为当前设备安装匹配的 `torch` 和 `torchaudio`。如果虚拟环境位于插件目录下的 `.venv-tts`，可在 Windows PowerShell 中运行以下命令安装其余依赖。

```powershell
$ttsPython = (Resolve-Path .\.venv-tts\Scripts\python.exe).Path
uv pip install --python $ttsPython -r requirements-tts.txt
```

Linux/macOS 的虚拟环境解释器位于 `.venv-tts/bin/python`。如果 MaiBot 在容器中运行，`python` 和 `model_dir` 都应填写容器内可访问的路径。

可用以下命令手动下载模型。将命令中的 `0.6B` 改为 `1.7B` 即可下载大模型。

```powershell
uvx --from huggingface-hub hf download Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice --local-dir ./models/Qwen3-TTS-12Hz-0.6B-CustomVoice
```

`model_dir` 应指向包含 `config.json`、模型权重和 `speech_tokenizer/` 的目录。准备完模型后，推理进程以离线模式加载权重。

在 WebUI 中填写本地路径，或将以下设置填入插件目录生成的 `config.toml` 的 `[tts]` 段后重载插件。Windows 路径可以用 TOML 单引号包住，直接保留反斜杠。

```toml
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

若命令提示合成失败，查看插件日志中的 Python 或模型错误。若语音发送失败，查看当前 QQ 适配器的连接状态和发送日志。使用统一 QQ 连接器时，同时检查所选的 SnowLuma 或 NapCat 客户端是否在线。
