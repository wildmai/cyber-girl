"""Explicit, resumable, checksum-verified downloads for the local Qwen brain."""

import argparse
import platform
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from scripts.prepare_avatar import download

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive"
REVISION = "0a41c68809d375475f954be12ba7c40efa56c2a9"
FILENAME = "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
SIZE = 5627044224
SHA256 = "2ca636d9e81d3d23ca9b60c234fe185d30ec082eeba69ce770fdb0c76559a4f5"
BUILD = "b11583"
BINARIES = {
    ("Darwin", "arm64"): ("macos-arm64.tar.gz", 12112217,
        "d23832f4b847a722a64c9bf57e518c3df81dfab325942182ff17792d5d33351f"),
    ("Windows", "AMD64"): ("win-cpu-x64.zip", 19509591,
        "2bceb716d9d93b950c008c9d81701650965c33e957de9e1d4da325c35c41abfe"),
}


def prepare_binary():
    spec = BINARIES.get((platform.system(), platform.machine()))
    if not spec:
        raise RuntimeError("此自动安装支持 Apple Silicon Mac 和 Windows x64；其他平台请自行安装 llama-server 并设置 LLAMA_SERVER_PATH。")
    suffix, size, digest = spec
    filename = f"llama-{BUILD}-bin-{suffix}"
    destination = ROOT / "runtime/llama.cpp"
    with tempfile.TemporaryDirectory(prefix="xiaoman-llama-") as temporary:
        temporary = Path(temporary)
        archive = temporary / filename
        download(f"https://github.com/ggml-org/llama.cpp/releases/download/{BUILD}/{filename}",
                 archive, {"size": size, "lfs": {"oid": digest}})
        extracted = temporary / "extracted"
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(archive) as source:
                for member in source.namelist():
                    if not (extracted / member).resolve().is_relative_to(extracted.resolve()):
                        raise RuntimeError("无效的运行时压缩包路径")
                source.extractall(extracted)
            name = "llama-server.exe"
        else:
            with tarfile.open(archive) as source:
                source.extractall(extracted, filter="data")
            name = "llama-server"
        server = next(extracted.rglob(name))
        shutil.copytree(server.parent, destination, dirs_exist_ok=True)
    print(f"Prepared llama.cpp {BUILD}: {destination}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary-only", action="store_true")
    parser.add_argument("--model-only", action="store_true")
    args = parser.parse_args()
    if not args.model_only:
        prepare_binary()
    if not args.binary_only:
        download(f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{FILENAME}?download=true",
                 ROOT / "models/llm" / FILENAME, {"size": SIZE, "lfs": {"oid": SHA256}})
        print("Verified local Qwen3.5-9B Q4_K_M.", flush=True)


if __name__ == "__main__":
    main()
