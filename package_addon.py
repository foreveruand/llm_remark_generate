from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "llm_remark_generator.ankiaddon"
ADDON_PACKAGE = "llm_remark_generator"
ADDON_NAME = "LLM Remark Generator"
INCLUDED = [
    "__init__.py",
    "config_dialog.py",
    "config.py",
    "config.json",
    "http_client.py",
    "llm_client.py",
    "manifest.json",
    "models.py",
    "processor.py",
    "README.md",
    "search/__init__.py",
    "search/base.py",
    "search/brave.py",
    "search/tavily.py",
]


def build_addon(output: Path = OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for relative_path in INCLUDED:
            archive.write(ROOT / relative_path, relative_path)


def main() -> None:
    build_addon()
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
