"""MaiBot command and reply extension for local Qwen3-TTS."""

from base64 import b64encode
from pathlib import Path
from typing import Any, ClassVar, cast

from maibot_sdk import (
    Command,
    ConfigWidget,
    ConfigWidgetState,
    Field,
    MaiBotPlugin,
    PluginConfigBase,
    ReplyExtension,
)

from .installer import Installer, ModelSize
from .tts import SynthesisError, SynthesisWorker

COMPONENT_TIMEOUT_MS = 180_000


class PluginSettings(PluginConfigBase):
    enabled: bool = True
    config_version: str = "1.0.0"


class TTSSettings(PluginConfigBase):
    __ui_label__ = "本机 Qwen3-TTS"

    model_size: ModelSize = Field(
        default="0.6B",
        description="选择自动下载和加载的 CustomVoice 模型。1.7B 支持 Instruction Control。",
        json_schema_extra={"label": "模型规格"},
    )
    device: str = Field(
        default="auto",
        pattern=r"^(auto|cpu|cuda(:[0-9]+)?)$",
        description="auto 自动选择 CUDA 或 CPU，也可指定 cpu、cuda 或 cuda:0 等。",
        json_schema_extra={"label": "推理设备"},
    )
    speaker: str = Field(
        default="Vivian", description="CustomVoice 预设音色，如 Vivian 或 Serena。",
        json_schema_extra={"label": "音色"},
    )
    language: str = Field(
        default="Auto", description="朗读语言，如 Auto、Chinese 或 English。",
        json_schema_extra={"label": "语言"},
    )
    instruct: str = Field(
        default="",
        description="1.7B CustomVoice 可用自然语言指定语气、情绪和语速。0.6B 会忽略此项。VoiceDesign 使用此项描述音色。",
        json_schema_extra={
            "label": "Instruction Control · 朗读指令",
            "x-widget": "textarea",
            "placeholder": "例如：用轻快、亲切的语气说，语速稍慢。",
        },
    )
    max_chars: int = Field(default=120, ge=1, le=300, json_schema_extra={"label": "单次朗读字数上限"})
    timeout_seconds: int = Field(
        default=120, ge=5, le=150, description="包含模型启动和单次推理。",
        json_schema_extra={"label": "合成超时（秒）"},
    )
    python: str = Field(
        default="",
        description="一键安装后留空。使用自己的推理环境时，填写 Python 可执行文件的绝对路径。",
        json_schema_extra={"label": "手动 Python 路径", "group": "手动安装和参考音频"},
    )
    model_dir: str = Field(
        default="",
        description="一键安装后留空。填写本地模型目录的绝对路径时，优先使用该模型。",
        json_schema_extra={"label": "手动模型目录", "group": "手动安装和参考音频"},
    )
    ref_audio: str = Field(
        default="", description="Base 模型使用的本地参考音频绝对路径，建议用 WAV。",
        json_schema_extra={"label": "参考音频", "group": "手动安装和参考音频"},
    )
    ref_text: str = Field(
        default="", description="参考音频的逐字稿，留空时只提取说话人音色。",
        json_schema_extra={"label": "参考音频逐字稿", "group": "手动安装和参考音频"},
    )


class Config(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)


class QwenTTSPlugin(MaiBotPlugin):
    config_model = Config
    config_widgets: ClassVar[list[ConfigWidget]] = [
        ConfigWidget(id="setup", kind="status", label="Qwen3-TTS 安装状态"),
        ConfigWidget(id="install", kind="action", label="一键安装环境和模型"),
    ]

    async def on_load(self) -> None:
        self._worker = SynthesisWorker()
        self._installer = Installer(Path(self.ctx.paths.data_dir).resolve() / "qwen-tts")

    async def on_unload(self) -> None:
        await self._installer.close()
        await self._worker.close()

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        # Each synthesis reads the Runner's current config. The worker switches models as needed.
        pass

    def _runtime_paths(self, settings: TTSSettings) -> tuple[Path | None, Path | None]:
        python = settings.python.strip()
        model = settings.model_dir.strip()
        return (
            Path(python) if python else self._installer.python_path(settings.device),
            Path(model) if model else self._installer.model_path(settings.model_size),
        )

    @staticmethod
    def _path_status(python: Path | None, model: Path | None) -> tuple[bool, bool]:
        return (
            python is not None and python.is_absolute() and python.is_file(),
            model is not None and model.is_absolute() and (model / "config.json").is_file(),
        )

    async def get_config_widget_state(self) -> dict[str, ConfigWidgetState]:
        settings = cast(Config, self.config).tts
        installer = self._installer
        python, model = self._runtime_paths(settings)
        valid_python, valid_model = self._path_status(python, model)
        if settings.python.strip():
            python_status = "手动路径有效" if valid_python else "手动路径无效"
        else:
            python_status = f"已安装（{python.parent.parent.name}）" if python else "待安装"
        if settings.model_dir.strip():
            model_status = "手动路径有效" if valid_model else "手动路径无效"
        else:
            model_status = f"{settings.model_size} CustomVoice {'已下载' if valid_model else '待下载'}"
        lines = [f"推理环境 · {python_status}", f"模型 · {model_status}"]
        if installer.stage:
            lines.extend(filter(None, (installer.stage, installer.detail)))
        lines.append("请先保存模型和推理设备设置。自动安装的项目可将对应路径留空。")
        return {
            "setup": ConfigWidgetState(
                text="\n".join(lines)[:2000],
                tone="success" if valid_python and valid_model else "error" if installer.failed else "neutral",
            ),
            "install": ConfigWidgetState(disabled=installer.busy or (valid_python and valid_model)),
        }

    async def on_config_widget_action(self, action_id: str) -> None:
        if action_id != "install":
            raise ValueError("未知的安装操作")
        settings = cast(Config, self.config).tts
        valid_python, valid_model = self._path_status(*self._runtime_paths(settings))
        if settings.python.strip() and not valid_python:
            raise ValueError("手动 Python 路径无效，请修正路径或清空后保存。")
        if settings.model_dir.strip() and not valid_model:
            raise ValueError("手动模型目录无效，请修正路径或清空后保存。")
        self._installer.start(
            settings.model_size,
            settings.device,
            manual_python=bool(settings.python.strip()),
            manual_model=bool(settings.model_dir.strip()),
        )

    async def _synthesize(self, text: str) -> str:
        settings = cast(Config, self.config).tts
        if len(text) > settings.max_chars:
            raise SynthesisError(f"朗读文本最多 {settings.max_chars} 字，请缩短后重试。")
        python, model = self._runtime_paths(settings)
        if python is None or model is None:
            raise SynthesisError("请先在插件设置中一键安装推理环境和模型，或填写本地路径。")
        audio = await self._worker.synthesize(
            str(python),
            str(model),
            settings.device,
            settings.timeout_seconds,
            {
                "text": text,
                "language": settings.language.strip() or "Auto",
                "speaker": settings.speaker.strip(),
                "instruct": settings.instruct.strip(),
                "ref_audio": settings.ref_audio.strip(),
                "ref_text": settings.ref_text.strip(),
            },
        )
        return b64encode(audio).decode("ascii")

    @Command(
        "qwen_tts",
        description="用本机模型朗读文本并发送 QQ 语音",
        pattern=r"^/tts(?:\s+(?P<text>[\s\S]*))?$",
        timeout_ms=COMPONENT_TIMEOUT_MS,
    )
    async def command_tts(
        self, *, platform: str, stream_id: str, matched_groups: dict[str, str | None], **_: Any
    ) -> tuple[bool, str, bool]:
        if platform != "qq" or not stream_id:
            return False, "", False

        text = (matched_groups.get("text") or "").strip()
        if not text:
            await self.ctx.send.text("请发送 /tts 加上要朗读的文字。", stream_id)
            return False, "缺少朗读文本", True

        try:
            audio = await self._synthesize(text)
        except SynthesisError as exc:
            self.ctx.logger.warning("Qwen3-TTS 合成失败：%s", exc)
            await self.ctx.send.text(exc.user_message, stream_id)
            return False, exc.user_message, True

        try:
            if await self.ctx.send.custom("voice", audio, stream_id, processed_plain_text=text):
                return True, "已发送语音", True
        except Exception:
            self.ctx.logger.exception("调用 MaiBot 语音发送能力失败")

        await self.ctx.send.text("QQ 语音发送失败，请检查 SnowLuma 连接和适配器日志。", stream_id)
        return False, "QQ 语音发送失败", True

    @ReplyExtension(
        "voice",
        description="用户希望听语音或明确要求朗读时，将这次简短回复用本机 Qwen3-TTS 合成为 QQ 语音。",
        parameters={
            "keep_text": {"type": "boolean", "default": False, "description": "同时发送原文字回复"},
        },
        platforms="qq",
        timeout_ms=COMPONENT_TIMEOUT_MS,
    )
    async def reply_voice(
        self, *, phase: str, parameters: dict[str, Any], messages: list[dict[str, Any]], **_: Any
    ) -> dict[str, Any]:
        settings = cast(Config, self.config).tts
        if not all(self._path_status(*self._runtime_paths(settings))):
            return {}
        if phase == "prepare":
            return {"extra_prompt": f"用适合朗读的自然口语作答，回复正文控制在 {settings.max_chars} 字以内。"}

        text = "\n".join(
            segment["data"].strip()
            for message in messages
            for segment in message["segments"]
            if segment["type"] == "text" and segment["data"].strip()
        )
        if not text:
            return {}
        try:
            audio = await self._synthesize(text)
        except SynthesisError as exc:
            self.ctx.logger.warning("Qwen3-TTS 合成失败，发送原回复：%s", exc)
            return {}

        voice = {"type": "voice", "data": text, "binary_data_base64": audio}
        if parameters.get("keep_text", False):
            return {"messages": [*messages, {"segments": [voice], "quote_previous": False}]}

        result = []
        voice_added = False
        for message in messages:
            segments = message["segments"]
            attachments = [segment for segment in segments if segment["type"] != "text"]
            if len(attachments) < len(segments) and not voice_added:
                result.append({"segments": [voice], "quote_previous": message["quote_previous"]})
                voice_added = True
                if attachments:
                    result.append({"segments": attachments, "quote_previous": False})
            elif attachments:
                result.append({**message, "segments": attachments})
        return {"messages": result}


def create_plugin() -> QwenTTSPlugin:
    return QwenTTSPlugin()
