"""Exercise installer ownership and cleanup inside disposable test directories."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_manifest", PROJECT / "packaging" / "generate_installer_manifest.py"
)
assert SPEC and SPEC.loader
MANIFEST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MANIFEST)
MAKENSIS = Path(r"C:\Program Files (x86)\NSIS\makensis.exe")


def nsis_path(path: Path) -> str:
    return str(path).replace("$", "$$")


class InstallerSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        build = PROJECT / "build"
        build.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="installer-safety-test-", dir=build)
        self.root = Path(self.temp.name).resolve()
        if not self.root.is_relative_to(build.resolve()):
            raise RuntimeError("Test directory escaped the project build directory")
        self.addCleanup(self.temp.cleanup)
        self.payload = self.root / "payload"
        (self.payload / "_internal" / "data").mkdir(parents=True)
        (self.payload / "_internal" / "empty").mkdir()
        (self.payload / "owned.txt").write_text("application", encoding="utf-8")
        (self.payload / "_internal" / "data" / "owned.bin").write_bytes(b"payload")
        (self.payload / "_internal" / "money$1.txt").write_text("literal dollar", encoding="utf-8")
        self.include = self.root / "payload.nsh"
        self.info = MANIFEST.generate_manifest(self.payload, self.include)

    def test_manifest_is_exact_and_has_no_recursive_delete(self) -> None:
        source = self.include.read_text(encoding="utf-8-sig")
        self.assertEqual(self.info["files"], 3)
        self.assertNotIn("RMDir /r", source)
        self.assertNotIn('Delete "$INSTDIR\\*', source)
        self.assertLess(
            source.index('RMDir "$INSTDIR\\_internal\\data"'),
            source.index('RMDir "$INSTDIR\\_internal"'),
        )

    def test_manifest_rejects_symlinked_payload(self) -> None:
        link = self.payload / "linked"
        try:
            link.symlink_to(self.root / "outside", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Windows symlink creation is unavailable: {exc}")
        with self.assertRaisesRegex(ValueError, "Reparse"):
            MANIFEST.generate_manifest(self.payload, self.include)

    @unittest.skipUnless(os.name == "nt" and MAKENSIS.is_file(), "NSIS Windows harness required")
    def test_actual_installer_script_compiles_with_tiny_payload(self) -> None:
        compiled = subprocess.run([
            str(MAKENSIS), "/V2", f"/DPROJECT_DIR={PROJECT}",
            f"/DBUILD_DIR={self.payload}", f"/DOUTPUT_DIR={self.root}",
            f"/DPAYLOAD_MANIFEST={self.include}", str(PROJECT / "packaging" / "installer.nsi"),
        ], capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.assertTrue((self.root / "Blackwood-UIMirror-Setup.exe").is_file())

    @unittest.skipUnless(os.name == "nt" and MAKENSIS.is_file(), "NSIS Windows harness required")
    def test_ownership_guards_and_unknown_file_retention(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        unowned = self.root / "unowned"
        (unowned / "_internal").mkdir(parents=True)
        (unowned / "_internal" / "keep.txt").write_text("unrelated", encoding="utf-8")
        owned = self.root / "owned"
        shutil.copytree(self.payload, owned)
        (owned / "_internal" / "keep.txt").write_text("keep me", encoding="utf-8")
        # A native file removed from a later package must not silently survive
        # an accepted overlay upgrade and become untracked by its uninstaller.
        retired_runtime = owned / "_internal" / "ucrtbase.dll"
        retired_runtime.write_bytes(b"old package runtime sentinel")
        (owned / "_internal" / "unknown").mkdir()
        (owned / "_internal" / "unknown" / "save.txt").write_text("keep too", encoding="utf-8")
        copied = self.root / "copied"
        copied.mkdir()
        report = self.root / "result.txt"
        harness = self.root / "harness.nsi"
        executable = self.root / "harness.exe"
        source = [
            "Unicode True",
            "RequestExecutionLevel user",
            "SilentInstall silent",
            f'OutFile "{nsis_path(executable)}"',
            f'!include "{nsis_path(self.include)}"',
            f'!include "{nsis_path(PROJECT / "packaging" / "installer-safety.nsh")}"',
            '!insertmacro BW_DEFINE_SAFETY_FUNCTIONS ""',
            "Section",
            f'  FileOpen $R8 "{nsis_path(report)}" w',
        ]

        def check(name: str, path: Path, function: str) -> None:
            source.extend([
                f'  Push "{nsis_path(path)}"',
                f"  Call {function}",
                "  Pop $0",
                rf'  FileWrite $R8 "{name}=$0$\r$\n"',
            ])

        def marker(folder: Path, bound: Path, payload_id: str) -> None:
            source.extend([
                f'  WriteINIStr "{nsis_path(folder)}\\${{BW_MARKER}}" "Ownership" "ProductId" "${{BW_PRODUCT_ID}}"',
                f'  WriteINIStr "{nsis_path(folder)}\\${{BW_MARKER}}" "Ownership" "Format" "1"',
                f'  WriteINIStr "{nsis_path(folder)}\\${{BW_MARKER}}" "Ownership" "InstallPath" "{nsis_path(bound)}"',
                f'  WriteINIStr "{nsis_path(folder)}\\${{BW_MARKER}}" "Ownership" "PayloadId" "{payload_id}"',
            ])

        check("empty", empty, "BW_IsInstallDirectorySafe")
        check("missing", self.root / "new", "BW_IsInstallDirectorySafe")
        check("legacy_nonempty", unowned, "BW_IsInstallDirectorySafe")
        check("drive_root", Path(self.root.anchor), "BW_IsInstallDirectorySafe")
        marker(owned, owned, "${BW_PAYLOAD_ID}")
        check("owned", owned, "BW_IsInstallDirectorySafe")
        check("current_uninstaller", owned, "BW_IsCurrentPayloadOwner")
        marker(copied, owned, "${BW_PAYLOAD_ID}")
        check("copied_uninstaller", copied, "BW_IsCurrentPayloadOwner")
        marker(owned, owned, "wrong-payload")
        check("stale_uninstaller", owned, "BW_IsCurrentPayloadOwner")
        check("different_payload_upgrade", owned, "BW_IsInstallDirectorySafe")
        # Model the install section's actual overlay only if its shared guard
        # accepts the old package. All paths and files are disposable fixtures.
        source.extend([
            f'  Push "{nsis_path(owned)}"',
            "  Call BW_IsInstallDirectorySafe",
            "  Pop $0",
            "  StrCmp $0 1 0 upgrade_attempt_done",
            f'  SetOutPath "{nsis_path(owned)}"',
            f'  File /r "{nsis_path(self.payload)}\\*.*"',
            "upgrade_attempt_done:",
            f'  IfFileExists "{nsis_path(retired_runtime)}" 0 retired_runtime_missing',
            r'  FileWrite $R8 "retired_runtime_retained=1$\r$\n"',
            "  Goto retired_runtime_checked",
            "retired_runtime_missing:",
            r'  FileWrite $R8 "retired_runtime_retained=0$\r$\n"',
            "retired_runtime_checked:",
        ])
        marker(owned, owned, "${BW_PAYLOAD_ID}")
        for name, registered in [("other_registered_install", self.root / "active-B"), ("same_registered_install", owned / ".")]:
            source.extend([
                f'  Push "{nsis_path(owned)}"',
                f'  Push "{nsis_path(registered)}"',
                "  Call BW_PathsMatch",
                "  Pop $0",
                rf'  FileWrite $R8 "{name}=$0$\r$\n"',
            ])
        source.extend([
            f'  StrCpy $INSTDIR "{nsis_path(owned)}"',
            '  !insertmacro BW_CHECK_PAYLOAD_PATHS "" $0',
            r'  FileWrite $R8 "payload_paths=$0$\r$\n"',
            "  !insertmacro BW_DELETE_PAYLOAD_FILES",
            "  !insertmacro BW_REMOVE_PAYLOAD_DIRS",
            "  FileClose $R8",
            "SectionEnd",
        ])
        harness.write_text("\n".join(source), encoding="utf-8-sig")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        compiled = subprocess.run(
            [str(MAKENSIS), "/V2", str(harness)], capture_output=True, text=True,
            timeout=60, creationflags=flags,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        ran = subprocess.run([str(executable)], timeout=30, creationflags=flags)
        self.assertEqual(ran.returncode, 0)
        values = dict(line.split("=", 1) for line in report.read_text(encoding="ascii").splitlines())
        self.assertEqual(values, {
            "empty": "1", "missing": "1", "legacy_nonempty": "0", "drive_root": "0",
            "owned": "1", "current_uninstaller": "1", "copied_uninstaller": "0",
            "stale_uninstaller": "0", "other_registered_install": "0", "same_registered_install": "1",
            "different_payload_upgrade": "0", "retired_runtime_retained": "1",
            "payload_paths": "1",
        })
        self.assertFalse((owned / "owned.txt").exists())
        self.assertFalse((owned / "_internal" / "data" / "owned.bin").exists())
        self.assertFalse((owned / "_internal" / "money$1.txt").exists())
        self.assertEqual((owned / "_internal" / "keep.txt").read_text(encoding="utf-8"), "keep me")
        self.assertEqual((owned / "_internal" / "unknown" / "save.txt").read_text(encoding="utf-8"), "keep too")
        self.assertEqual(retired_runtime.read_bytes(), b"old package runtime sentinel")
        self.assertEqual((unowned / "_internal" / "keep.txt").read_text(encoding="utf-8"), "unrelated")


if __name__ == "__main__":
    unittest.main()
