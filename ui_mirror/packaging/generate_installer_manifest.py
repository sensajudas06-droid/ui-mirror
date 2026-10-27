"""Generate exact NSIS payload cleanup commands without recursive deletion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _relative_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root).parts
    if not parts or any(
        part in {".", ".."} or any(char in part for char in '<>:"/\\|?*\r\n\0')
        for part in parts
    ):
        raise ValueError(f"Unsupported payload path: {path}")
    return "\\".join(parts)


def _nsis_string(value: str) -> str:
    return value.replace("$", "$$")


def generate_manifest(build_dir: Path, output: Path) -> dict[str, object]:
    root = build_dir.absolute()
    if not root.is_dir() or _is_reparse(root):
        raise ValueError("Payload root must be a normal directory")
    files: list[tuple[str, str, int]] = []
    directories: list[str] = []
    names: set[str] = set()
    for current, child_dirs, child_files in os.walk(root, followlinks=False):
        child_dirs.sort(key=str.casefold)
        child_files.sort(key=str.casefold)
        for name in child_dirs + child_files:
            path = Path(current) / name
            if _is_reparse(path):
                raise ValueError(f"Reparse points are not allowed in payload: {path}")
            relative = _relative_name(path, root)
            if relative.casefold() in names:
                raise ValueError(f"Case-insensitive payload path collision: {relative}")
            names.add(relative.casefold())
            if path.is_dir():
                directories.append(relative)
            elif path.is_file():
                with path.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                files.append((relative, digest, path.stat().st_size))
            else:
                raise ValueError(f"Unsupported payload entry: {path}")
    if not files:
        raise ValueError("Payload must contain at least one file")
    files.sort(key=lambda entry: entry[0].casefold())
    directories.sort(key=lambda name: (name.count("\\"), name.casefold()))
    payload_id = hashlib.sha256(
        json.dumps(files, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    lines = [
        "; Generated from the exact release payload; do not edit.",
        f'!define BW_PAYLOAD_ID "{payload_id}"',
        "!macro BW_CHECK_PAYLOAD_PATHS PREFIX RESULT",
        "  Push $R9",
        '  StrCpy ${RESULT} "1"',
    ]
    for relative in [""] + directories + [entry[0] for entry in files]:
        suffix = "\\" + _nsis_string(relative) if relative else ""
        lines.extend([
            f'  Push "$INSTDIR{suffix}"',
            "  Call ${PREFIX}BW_PathIsNotReparse",
            "  Pop $R9",
            '  StrCmp $R9 "1" +2',
            '  StrCpy ${RESULT} "0"',
        ])
    lines.extend(["  Pop $R9", "!macroend", "!macro BW_DELETE_PAYLOAD_FILES"])
    lines.extend(f'  Delete "$INSTDIR\\{_nsis_string(name)}"' for name, _, _ in files)
    lines.extend(["!macroend", "!macro BW_REMOVE_PAYLOAD_DIRS"])
    lines.extend(f'  RMDir "$INSTDIR\\{_nsis_string(name)}"' for name in reversed(directories))
    lines.extend(["!macroend", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8-sig")
    return {"payload_id": payload_id, "files": len(files), "directories": len(directories)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(generate_manifest(args.build_dir, args.output)))


if __name__ == "__main__":
    main()
