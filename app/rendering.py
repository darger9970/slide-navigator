"""Shared renderer discovery for the app and isolated worker."""
import os
import shutil
from pathlib import Path


def libreoffice_executable():
    configured = os.environ.get("SLIDE_NAVIGATOR_LIBREOFFICE_PATH")
    if configured and Path(configured).is_file():
        return configured
    command = shutil.which("soffice") or shutil.which("libreoffice")
    if command:
        return command
    if os.name == "nt":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        if os.environ.get("LOCALAPPDATA"):
            roots.append(str(Path(os.environ["LOCALAPPDATA"]) / "Programs"))
        for root in roots:
            for binary in ("soffice.com", "soffice.exe"):
                path = Path(root) / "LibreOffice" / "program" / binary
                if path.is_file():
                    return str(path)
    return None
