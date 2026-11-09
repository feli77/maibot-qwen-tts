"""Run Qwen3-TTS in a reusable subprocess with its own Python environment."""

import asyncio
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

MAX_WAV_BYTES = 4 * 1024 * 1024


class SynthesisError(Exception):
    def __init__(self, user_message: str, detail: str = "") -> None:
        super().__init__(f"{user_message} {detail}" if detail else user_message)
        self.user_message = user_message


class SynthesisWorker:
    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._model_key: tuple[Path, Path, str] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def synthesize(
        self, python: str, model_dir: str, device: str, timeout: int, request: dict[str, str]
    ) -> bytes:
        if self._lock.locked():
            raise SynthesisError("语音合成忙，请稍后重试。")

        async with self._lock:
            if self._closed:
                raise SynthesisError("语音插件正在重载，请稍后重试。")
            if not python or not model_dir:
                raise SynthesisError("请先在插件设置中填写本机 TTS 的 Python 路径和模型目录。")
            interpreter = Path(python.strip())
            model = Path(model_dir.strip())
            if not interpreter.is_absolute() or not interpreter.is_file():
                raise SynthesisError("TTS 的 Python 路径无效，请填写可执行文件的绝对路径。")
            if not model.is_absolute() or not (model / "config.json").is_file():
                raise SynthesisError("Qwen3-TTS 模型目录无效，请检查本地模型文件。")

            model_key = (interpreter, model, device)
            try:
                async with asyncio.timeout(timeout):
                    if self._process is None or self._process.returncode is not None or model_key != self._model_key:
                        await self._stop()
                        if self._closed:
                            raise SynthesisError("语音插件正在重载，请稍后重试。")
                        self._process = await asyncio.create_subprocess_exec(
                            str(interpreter),
                            "-I",
                            "-X",
                            "utf8",
                            "-u",
                            str(Path(__file__).resolve()),
                            str(model),
                            device,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                        self._model_key = model_key
                    if self._closed:
                        raise SynthesisError("语音插件正在重载，请稍后重试。")
                    return await self._request(request)
            except TimeoutError as exc:
                await self._stop()
                raise SynthesisError("语音合成超时，请缩短文本或检查推理设备。") from exc
            except (OSError, EOFError, ValueError) as exc:
                await self._stop()
                raise SynthesisError("无法读取本机 TTS 的结果，请检查插件日志。", str(exc)) from exc
            except asyncio.CancelledError:
                await self._stop()
                raise

    async def _request(self, request: dict[str, str]) -> bytes:
        process = self._process
        assert process is not None and process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(request).encode("ascii") + b"\n")
        await process.stdin.drain()
        line = await process.stdout.readline()
        if not line:
            raise EOFError("TTS 子进程已退出")

        header = json.loads(line)
        if isinstance(header, dict):
            if isinstance(header.get("error"), str):
                raise SynthesisError("本机语音合成失败，请检查插件日志和模型配置。", header["error"])
            size = header.get("size")
            if type(size) is int and 44 < size <= MAX_WAV_BYTES:
                return await process.stdout.readexactly(size)
        raise ValueError("TTS 子进程返回了无效的 WAV 长度")

    async def close(self) -> None:
        self._closed = True
        self._kill()
        async with self._lock:
            await self._stop()

    def _kill(self) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

    async def _stop(self) -> None:
        self._kill()
        process, self._process = self._process, None
        if process is not None:
            assert process.stdin is not None
            process.stdin.close()
            await process.communicate()


def _load_model(model_dir: str, device: str) -> Any:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    import torch
    from qwen_tts import Qwen3TTSModel

    if device == "auto":
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.float32
    if device.startswith("cuda"):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA 不可用，请检查 PyTorch 安装，或将 device 改为 cpu")
        with torch.cuda.device(device):
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16

    return Qwen3TTSModel.from_pretrained(
        model_dir, device_map=device, dtype=dtype, attn_implementation="sdpa", local_files_only=True
    )


def _generate_wav(model: Any, request: dict[str, str]) -> bytes:
    import soundfile as sf

    arguments = {"text": request["text"], "language": request["language"], "max_new_tokens": 1024}
    model_type = model.model.tts_model_type
    if model_type == "custom_voice":
        # The smaller CustomVoice checkpoint does not support style instructions.
        instruct = None if getattr(model.model, "tts_model_size", None) == "0b6" else request["instruct"] or None
        waves, rate = model.generate_custom_voice(
            **arguments, speaker=request["speaker"], instruct=instruct
        )
    elif model_type == "voice_design":
        if not request["instruct"]:
            raise ValueError("VoiceDesign 模型需要在 instruct 中填写音色描述")
        waves, rate = model.generate_voice_design(**arguments, instruct=request["instruct"])
    elif model_type == "base":
        path = Path(request["ref_audio"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError("Base 模型需要在 ref_audio 中填写有效的本地参考音频绝对路径")
        ref_wave, ref_rate = sf.read(str(path), dtype="float32")
        if ref_wave.ndim == 2:
            ref_wave = ref_wave.mean(axis=1)
        waves, rate = model.generate_voice_clone(
            **arguments,
            ref_audio=(ref_wave, ref_rate),
            ref_text=request["ref_text"] or None,
            x_vector_only_mode=not request["ref_text"],
        )
    else:
        raise ValueError(f"不支持的 Qwen3-TTS 模型类型：{model_type}")

    if not waves or not len(waves[0]):
        raise ValueError("模型返回了空音频")
    buffer = io.BytesIO()
    sf.write(buffer, waves[0], rate, format="WAV", subtype="PCM_16")
    if buffer.tell() > MAX_WAV_BYTES:
        raise ValueError("生成的音频过长，请缩短朗读文本")
    return buffer.getvalue()


def _run_worker(model_dir: str, device: str) -> None:
    output = sys.stdout.buffer
    model = None
    # Keep library startup banners and inference logs out of the binary protocol.
    with redirect_stdout(sys.stderr):
        for line in sys.stdin.buffer:
            try:
                request = json.loads(line)
                if model is None:
                    model = _load_model(model_dir, device)
                audio = _generate_wav(model, request)
                header = {"size": len(audio)}
            except Exception as exc:
                audio = b""
                header = {"error": f"{type(exc).__name__}: {str(exc)[:1000]}"}
            output.write(json.dumps(header).encode("ascii") + b"\n")
            output.write(audio)
            output.flush()


if __name__ == "__main__":
    _run_worker(*sys.argv[1:])
