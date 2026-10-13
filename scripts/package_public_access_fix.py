"""Small bind-mount update for the previously exported Docker image."""

import hashlib
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {"app/deployment.py": "public-access-fix/deployment.py",
         "deploy/compose.public-access.yaml": "compose.public-access.yaml",
         "deploy/PUBLIC-ACCESS-FIX.md": "public-access-fix/README.md"}


def build():
    output = ROOT / "output/releases/xiaoman-public-access-fix.tar.gz"
    with tarfile.open(output, "w:gz") as archive:
        for source, destination in FILES.items():
            data = (ROOT / source).read_bytes()
            info = tarfile.TarInfo(destination)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    with output.open("rb") as package:
        digest = hashlib.file_digest(package, "sha256").hexdigest()
    output.with_suffix(".gz.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    return output


if __name__ == "__main__":
    print(build())
