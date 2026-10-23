"""Make a QA copy of the runtime sample using Google's Maven Central mirror.

Only the dependency repository changes; application and probe logic stay intact.
The original ZIP is preserved and no TLS verification is disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
MIRROR = "https://maven-central.storage-download.googleapis.com/maven2"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "artifacts/leak-validation.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "workspace/qa-runtime.zip")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.source) as source, ZipFile(args.output, "w", compression=ZIP_DEFLATED) as output:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "leak/backend/Dockerfile":
                text = content.decode()
                assert "RUN mvn -B package" in text
                content = text.replace(
                    "RUN mvn -B package",
                    "COPY qa-maven-settings.xml /build/settings.xml\nRUN mvn -B -s /build/settings.xml package",
                ).encode()
            output.writestr(item, content)
        output.writestr("leak/qa-maven-settings.xml", f'<settings><mirrors><mirror><id>google-central</id><mirrorOf>central</mirrorOf><url>{MIRROR}</url></mirror></mirrors></settings>')
    print(json.dumps({"path": str(args.output), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "mirror": MIRROR}))


if __name__ == "__main__":
    main()
