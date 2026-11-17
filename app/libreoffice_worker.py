import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from app.rendering import libreoffice_executable


def _executable() -> str:
    command = libreoffice_executable()
    if command is None:
        raise RuntimeError("LibreOffice executable was not found.")
    return command


def _convert(source: Path, output_dir: Path, target_format: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    conversion_format = target_format
    if target_format == "pdf":
        conversion_format = "pdf:impress_pdf_Export:" + json.dumps({
            "ExportHiddenSlides": {"type": "boolean", "value": "true"},
            "ExportNotesPages": {"type": "boolean", "value": "false"},
            "ExportOnlyNotesPages": {"type": "boolean", "value": "false"},
        })
    with tempfile.TemporaryDirectory(prefix="slide-navigator-lo-") as directory:
        profile = (Path(directory) / "profile").as_uri()
        command = [
            _executable(),
            f"-env:UserInstallation={profile}",
            "--headless",
            "--convert-to",
            conversion_format,
            "--outdir",
            str(output_dir),
            str(source),
        ]
        options: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": 180,
            "check": False,
        }
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(command, **options)
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "LibreOffice conversion failed.")
    converted = output_dir / f"{source.stem}.{target_format}"
    if not converted.is_file():
        detail = result.stderr.strip().splitlines()
        raise RuntimeError(
            detail[-1] if detail else "LibreOffice did not create the converted file."
        )
    return converted


def run_worker(action: str, source: Path, output: Path) -> dict[str, Any]:
    if action == "convert":
        converted = _convert(source, output.parent, "pptx")
        if converted.resolve() != output.resolve():
            shutil.copyfile(converted, output)
        return {"converted_path": str(output)}

    import pymupdf as fitz

    with tempfile.TemporaryDirectory(prefix="slide-navigator-pdf-") as directory:
        pdf_path = _convert(source, Path(directory), "pdf")
        document = fitz.open(pdf_path)
        try:
            output.mkdir(parents=True, exist_ok=True)
            slides: list[dict[str, Any]] = []
            for number, page in enumerate(document, start=1):
                thumbnail = output / f"slide-{number:05}.png"
                scale = 640 / page.rect.width
                page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(
                    thumbnail
                )
                slides.append({"slide_number": number, "path": str(thumbnail)})
            return {"slides": slides}
        finally:
            document.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("convert", "render"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            result = run_worker(arguments.action, arguments.source, arguments.output)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
