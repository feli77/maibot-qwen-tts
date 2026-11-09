"""MaiBot command and reply extension for local Qwen3-TTS."""

from base64 import b64encode
from typing import Any, cast

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, ReplyExtension

from .tts import SynthesisError, SynthesisWorker

COMPONENT_TIMEOUT_MS = 180_000


class PluginSettings(PluginConfigBase):
    enabled: bool = True
    config_version: str = "1.0.0"


class TTSSettings(PluginConfigBase):
    __ui_label__ = "本机 Qwen3-TTS"

    python: str = Field(default="", description="TTS 环境中 Python 可执行文件的绝对路径")
    model_dir: str = Field(default="", description="完整 Qwen3-TTS 模型目录的绝对路径")
    device: str = Field(default="auto", pattern=r"^(auto|cpu|cuda(:[0-9]+)?)$", description="推理设备")
    speaker: str = Field(default="Vivian", description="CustomVoice 预设音色")
    language: str = Field(default="Auto", description="朗读语言，如 Auto、Chinese 或 English")
    instruct: str = Field(default="", description="VoiceDesign 的音色描述，或 1.7B CustomVoice 的朗读风格")
    ref_audio: str = Field(default="", description="Base 模型使用的本地参考音频绝对路径，建议用 WAV")
    ref_text: str = Field(default="", description="参考音频的逐字稿，留空时只提取说话人音色")
    max_chars: int = Field(default=120, ge=1, le=300, description="单次朗读的最大字符数")
    timeout_seconds: int = Field(default=120, ge=5, le=150, description="模型启动和单次推理的总超时")


class Config(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)


class QwenTTSPlugin(MaiBotPlugin):
    config_model = Config

    async def on_load(self) -> None:
        self._worker = SynthesisWorker()

    async def on_unload(self) -> None:
        await self._worker.close()

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        # Each synthesis reads the Runner's current config. The worker switches models as needed.
        pass

    async def _synthesize(self, text: str) -> str:
        settings = cast(Config, self.config).tts
        if len(text) > settings.max_chars:
            raise SynthesisError(f"朗读文本最多 {settings.max_chars} 字，请缩短后重试。")
        audio = await self._worker.synthesize(
            settings.python,
            settings.model_dir,
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
        if not settings.model_dir or not settings.python:
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
