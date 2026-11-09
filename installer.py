"""Prepare managed Qwen3-TTS assets outside MaiBot's Python environment."""

import asyncio
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Literal

ModelSize = Literal["0.6B", "1.7B"]
TORCH_VERSION = "2.11.0"
HF_REQUIREMENT = "huggingface-hub==2.4.0"
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
PROGRESS_PREFIX = "QWEN_TTS_PROGRESS "
MODEL_FILES = (
    "config.json",
    "generation_config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "merges.txt",
    "vocab.json",
    "tokenizer_config.json",
    "speech_tokenizer/config.json",
    "speech_tokenizer/configuration.json",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/preprocessor_config.json",
)


def _python_in(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _model_name(size: ModelSize) -> str:
    return f"Qwen3-TTS-12Hz-{size}-CustomVoice"


def _has_model_files(directory: Path) -> bool:
    try:
        return all((directory / name).stat().st_size > 0 for name in MODEL_FILES)
    except OSError:
        return False


def _uv_executable() -> str:
    try:
        from uv import find_uv_bin

        return os.fspath(find_uv_bin())
    except (ImportError, FileNotFoundError):
        executable = shutil.which("uv")
        if executable is None:
            raise RuntimeError("找不到 uv，请通过 MaiBot 安装插件依赖，或将 uv 加入 MaiBot 的 PATH。") from None
        return executable


def _torch_build(device: str) -> str:
    supports_cuda = sys.platform in ("win32", "linux") and platform.machine().lower() in ("amd64", "x86_64")
    if device.startswith("cuda"):
        if not supports_cuda:
            raise RuntimeError("一键安装 CUDA 环境适用于 Windows 或 Linux x86-64，请选择 CPU 或使用手动环境。")
        return "cu128"
    if device == "auto" and supports_cuda:
        try:
            result = subprocess.run(
                ["nvidia-smi"],
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=CREATE_FLAGS,
            )
            match = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", result.stdout)
            if result.returncode == 0 and match and tuple(map(int, match.groups())) >= (12, 8):
                return "cu128"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "cpu"


class Installer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.task: asyncio.Task[None] | None = None
        self.stage = ""
        self.detail = ""
        self.failed = False
        self._closed = False

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def _environment(self, build: str) -> Path:
        return self.root / f"python312-torch{TORCH_VERSION}-{build}"

    def python_path(self, device: str) -> Path | None:
        builds = ("cu128", "cpu")
        if device == "cpu":
            builds = ("cpu", "cu128")
        elif device.startswith("cuda"):
            builds = ("cu128",)
        for build in builds:
            environment = self._environment(build)
            python = _python_in(environment)
            if (environment / ".ready").is_file() and python.is_file():
                return python
        return None

    def _model_directory(self, size: ModelSize) -> Path:
        return self.root / "models" / _model_name(size)

    def model_path(self, size: ModelSize) -> Path | None:
        model = self._model_directory(size)
        return model if (model / ".ready").is_file() and _has_model_files(model) else None

    def start(self, size: ModelSize, device: str, *, manual_python: bool, manual_model: bool) -> None:
        if self._closed or self.busy:
            return
        self.stage = "正在检查已安装的环境和模型…"
        self.detail = ""
        self.failed = False
        self.task = asyncio.create_task(self._install(size, device, manual_python, manual_model))

    async def close(self) -> None:
        self._closed = True
        if self.busy:
            assert self.task is not None
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def _install(self, size: ModelSize, device: str, manual_python: bool, manual_model: bool) -> None:
        try:
            need_python = not manual_python and self.python_path(device) is None
            need_model = not manual_model and self.model_path(size) is None
            if need_python or need_model:
                self.root.mkdir(parents=True, exist_ok=True)
                uv = (_uv_executable(), "--no-config", "--color", "never", "--cache-dir", str(self.root / "uv-cache"))
                if need_python:
                    await self._install_python(uv, device)
                if need_model:
                    await self._install_model(uv, size)
        except (OSError, RuntimeError, ValueError) as exc:
            self.failed = True
            self.stage = f"{self.stage} · 失败，可重试"
            self.detail = (str(exc) or type(exc).__name__)[-1500:]
        else:
            target = "推理环境" if manual_model else f"{size} CustomVoice 所需文件"
            self.stage = f"{target}已就绪。"
            self.detail = ""

    async def _install_python(self, uv: tuple[str, ...], device: str) -> None:
        build = await asyncio.to_thread(_torch_build, device)
        environment = self._environment(build)
        ready = environment / ".ready"
        ready.unlink(missing_ok=True)
        python = str(_python_in(environment))
        build_label = "CUDA 12.8" if build == "cu128" else "CPU"
        await self._run(
            f"环境 1/4 · 创建 Python 3.12 环境（{build_label}）",
            *uv,
            "venv",
            "--python",
            "3.12",
            "--allow-existing",
            str(environment),
        )
        pip_install = (*uv, "pip", "install", "--python", python)
        index = () if sys.platform == "darwin" else ("--index-url", f"https://download.pytorch.org/whl/{build}")
        await self._run(
            f"环境 2/4 · 安装 PyTorch（{build_label}）",
            *pip_install,
            *index,
            f"torch=={TORCH_VERSION}",
            f"torchaudio=={TORCH_VERSION}",
        )
        requirements = Path(__file__).with_name("requirements-tts.txt").resolve()
        await self._run("环境 3/4 · 安装 Qwen3-TTS 依赖", *pip_install, "-r", str(requirements))
        check = "import torch, torchaudio, soundfile; from qwen_tts import Qwen3TTSModel"
        if build == "cu128":
            check += "; assert torch.cuda.is_available(), 'CUDA 12.8 不可用，请检查 NVIDIA 驱动或将推理设备设为 cpu'"
        await self._run("环境 4/4 · 验证推理依赖", python, "-I", "-X", "utf8", "-c", check)
        ready.touch()

    async def _install_model(self, uv: tuple[str, ...], size: ModelSize) -> None:
        model = self._model_directory(size)
        ready = model / ".ready"
        ready.unlink(missing_ok=True)
        # Keep the Hub client with byte progress separate from Transformers' Hub dependency.
        tools = self.root / "download-tools"
        python = _python_in(tools)
        if not (tools / ".ready").is_file() or not python.is_file():
            (tools / ".ready").unlink(missing_ok=True)
            await self._run("模型 · 准备下载工具", *uv, "venv", "--python", "3.12", "--allow-existing", str(tools))
            await self._run(
                "模型 · 安装 Hugging Face 下载工具", *uv, "pip", "install", "--python", str(python), HF_REQUIREMENT
            )
            (tools / ".ready").touch()
        await self._run(
            f"模型 · 下载 {size} CustomVoice",
            str(python),
            "-I",
            "-X",
            "utf8",
            "-u",
            str(Path(__file__).resolve()),
            f"Qwen/{_model_name(size)}",
            str(model),
        )
        if not _has_model_files(model):
            raise RuntimeError("模型文件不完整，请重试。")
        ready.touch()

    async def _run(self, stage: str, *command: str) -> None:
        self.stage = stage
        self.detail = ""
        env = {
            "UV_CONCURRENT_DOWNLOADS": "4",
            "UV_CONCURRENT_INSTALLS": "2",
            "UV_CONCURRENT_BUILDS": "1",
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "UV_PYTHON_INSTALL_DIR": str(self.root / "python"),
        }
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=CREATE_FLAGS,
            env=env,
        )
        assert process.stdout is not None
        tail: deque[str] = deque(maxlen=8)
        try:
            async for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    tail.append(line)
                    self.detail = line.removeprefix(PROGRESS_PREFIX)[-500:]
            if await process.wait():
                raise RuntimeError("\n".join(tail)[-1500:] or f"子进程退出（{process.returncode}）")
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.communicate()


def _download_model(repo: str, destination: str) -> None:
    """Run in the small download environment, with a line-based progress feed."""
    from huggingface_hub import HfApi, hf_hub_download
    from tqdm.auto import tqdm

    info = HfApi().model_info(repo, files_metadata=True, token=False)
    sizes = {file.rfilename: file.size for file in info.siblings or () if file.size and file.size > 0}
    if not info.sha or any(name not in sizes for name in MODEL_FILES):
        raise RuntimeError("远端模型仓库缺少所需文件或文件元数据。")
    total = sum(sizes[name] for name in MODEL_FILES)
    completed = 0
    reported = -1

    def report(received: int) -> None:
        nonlocal reported
        received = min(received, total)
        if received <= reported:
            return
        reported = received
        mib = 1024 * 1024
        print(f"{PROGRESS_PREFIX}{received / total:.1%} · {received / mib:,.1f} / {total / mib:,.1f} MiB", flush=True)

    class FileProgress(tqdm):
        def __init__(self, *args, **kwargs):
            # Hugging Face Hub 2.4 uses this label for Xet's compressed transfer bar.
            self._file_bytes = kwargs.get("desc") != "Downloading bytes"
            super().__init__(*args, **kwargs)

        def display(self, msg=None, pos=None):
            if self.unit == "B" and self._file_bytes:
                report(completed + min(self.n, size))

    report(0)
    for name in MODEL_FILES:
        size = sizes[name]
        path = hf_hub_download(
            repo, name, revision=info.sha, local_dir=destination, token=False, tqdm_class=FileProgress
        )
        if Path(path).stat().st_size != size:
            raise RuntimeError(f"文件大小校验失败：{name}")
        completed += size
        report(completed)


if __name__ == "__main__":
    _download_model(*sys.argv[1:])
