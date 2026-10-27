"""Reject ambient native dependencies in a Windows release build.

PyInstaller TOCs are parsed as literal data; no packaged code is executed.
Only the active Python installation, its venv, and Windows System32 are
approved. The report records every native source and its SHA-256.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

NATIVE_TYPES = frozenset({"BINARY", "EXTENSION", "EXECUTABLE"})


def approved_runtime_roots() -> list[Path]:
    roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    if sys.platform == "win32":
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
        if not 0 < length < len(buffer):
            raise RuntimeError("Windows System32 directory could not be resolved")
        roots.append(Path(buffer.value).resolve(strict=True))
    return list(dict.fromkeys(roots))


def _native_entries(value):
    if isinstance(value, (tuple, list)):
        if (
            len(value) == 3
            and all(isinstance(part, str) for part in value)
            and value[2] in NATIVE_TYPES
        ):
            yield value
        else:
            for child in value:
                yield from _native_entries(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _native_entries(child)


def audit_native_inputs(toc_paths: list[Path], approved_roots: list[Path]) -> dict:
    roots = list(dict.fromkeys(root.resolve(strict=True) for root in approved_roots))
    entries = {}
    for toc_path in toc_paths:
        toc_path = toc_path.resolve(strict=True)
        toc = ast.literal_eval(toc_path.read_text(encoding="utf-8"))
        for name, source, kind in _native_entries(toc):
            key = (name, source, kind)
            if key in entries:
                continue
            row = {"archiveName": name, "sourcePath": source, "type": kind}
            try:
                source_path = Path(source)
                if not source_path.is_absolute():
                    raise ValueError("native source path must be absolute")
                resolved = source_path.resolve(strict=True)
                if not resolved.is_file():
                    raise ValueError("native source must be a regular file")
                root = next((root for root in roots if resolved.is_relative_to(root)), None)
                row.update(
                    resolvedSourcePath=str(resolved),
                    approvedRoot=str(root) if root else None,
                    bytes=resolved.stat().st_size,
                    sha256=hashlib.sha256(resolved.read_bytes()).hexdigest(),
                    approved=root is not None,
                )
                if root is None:
                    row["reason"] = "native source is outside approved runtime roots"
            except (OSError, ValueError, RuntimeError) as exc:
                row.update(approved=False, reason=str(exc))
            entries[key] = row
    rows = sorted(entries.values(), key=lambda row: (row["archiveName"], row["sourcePath"]))
    rejected = sum(not row["approved"] for row in rows)
    return {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "tocPaths": [str(path.resolve()) for path in toc_paths],
        "approvedRoots": [str(root) for root in roots],
        "nativeCount": len(rows),
        "rejectedCount": rejected,
        "passed": bool(rows) and rejected == 0,
        "error": "no native inputs found" if not rows else None,
        "inputs": rows,
        "limitations": [
            "Approved origin and local hashes do not establish upstream authenticity or absence of malware.",
            "Python and venv runtime files must also come from verified release dependencies.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toc", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit_native_inputs(args.toc, approved_runtime_roots())
    except (OSError, ValueError, SyntaxError, RuntimeError) as exc:
        report = {"passed": False, "error": str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not report["passed"]:
        print(f"Native build provenance rejected: {report.get('rejectedCount', 0)} input(s); {args.output}", file=sys.stderr)
        return 1
    print(f"Native build provenance verified: {report['nativeCount']} input(s); {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
