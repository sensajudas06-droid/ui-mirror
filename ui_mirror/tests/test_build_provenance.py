"""Native dependency provenance tests; no release executable is produced."""

import hashlib
import importlib.util
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_module_path = Path(__file__).resolve().parents[1] / "packaging" / "verify_native_inputs.py"
_spec = importlib.util.spec_from_file_location("verify_native_inputs", _module_path)
provenance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(provenance)


class NativeBuildProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.approved = self.root / "python"
        self.approved.mkdir()
        self.toc = self.root / "Analysis-00.toc"

    def audit(self, entries):
        self.toc.write_text(repr(([], {"nested": entries})), encoding="utf-8")
        return provenance.audit_native_inputs([self.toc], [self.approved])

    def test_approved_native_file_records_actual_hash(self):
        source = self.approved / "python313.dll"
        source.write_bytes(b"native runtime bytes")
        report = self.audit([(source.name, str(source), "BINARY")])
        self.assertTrue(report["passed"])
        self.assertEqual(report["nativeCount"], 1)
        self.assertEqual(report["inputs"][0]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_ambient_dll_is_rejected_even_with_matching_root_prefix(self):
        outside = self.root / "python-untrusted"
        outside.mkdir()
        source = outside / "ucrtbase.dll"
        source.write_bytes(b"unrelated PATH dependency")
        report = self.audit([(source.name, str(source), "BINARY")])
        self.assertFalse(report["passed"])
        self.assertEqual(report["rejectedCount"], 1)
        self.assertIsNone(report["inputs"][0]["approvedRoot"])

    def test_missing_native_file_fails_closed(self):
        report = self.audit([("missing.pyd", str(self.approved / "missing.pyd"), "EXTENSION")])
        self.assertFalse(report["passed"])
        self.assertEqual(report["rejectedCount"], 1)

    def test_relative_native_source_fails_closed(self):
        report = self.audit([("relative.dll", "relative.dll", "BINARY")])
        self.assertFalse(report["passed"])
        self.assertIn("absolute", report["inputs"][0]["reason"])

    def test_empty_or_wrong_toc_cannot_pass(self):
        report = self.audit([("readme.txt", str(self.root / "readme.txt"), "DATA")])
        self.assertFalse(report["passed"])
        self.assertEqual(report["error"], "no native inputs found")

    def test_toc_is_literal_data_and_cannot_execute_code(self):
        self.toc.write_text("__import__('sys').exit(9)", encoding="utf-8")
        with self.assertRaises(ValueError):
            provenance.audit_native_inputs([self.toc], [self.approved])

    def test_duplicate_inputs_are_recorded_once(self):
        source = self.approved / "extension.pyd"
        source.write_bytes(b"extension bytes")
        entry = (source.name, str(source), "EXTENSION")
        report = self.audit([entry, entry])
        self.assertTrue(report["passed"])
        self.assertEqual(report["nativeCount"], 1)

    @unittest.skipUnless(os.name == "nt", "Windows build environment regression")
    def test_actual_build_environment_cleanup_and_isolated_tk_preflight(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            self.skipTest("PowerShell is unavailable")
        build_script = str(_module_path.with_name("build.ps1")).replace("'", "''")
        python = sys.executable.replace("'", "''")
        script = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('__BUILD_SCRIPT__',[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'build.ps1 syntax error'}
$outer=@($ast.EndBlock.Statements | Where-Object {$_ -is [System.Management.Automation.Language.TryStatementAst]})[0]
$cleanup=[ScriptBlock]::Create($outer.Body.Statements[0].Extent.Text)
$restore=[ScriptBlock]::Create($outer.Finally.Statements[0].Extent.Text)
$start=-1; $end=-1
for($i=0;$i -lt $outer.Body.Statements.Count;$i++){
    $statement=$outer.Body.Statements[$i]
    if($statement -is [System.Management.Automation.Language.AssignmentStatementAst]){
        if($statement.Left.Extent.Text -eq '$runtimeLayoutJson'){$start=$i}
        if($statement.Left.Extent.Text -eq '$env:PATH'){$end=$i}
    }
}
if($start -lt 0 -or $end -lt $start){throw 'build environment setup not found'}
$setup=[ScriptBlock]::Create(($outer.Body.Statements[$start..$end] | ForEach-Object {$_.Extent.Text}) -join "`n")
$python='__PYTHON__'
$venv=Split-Path (Split-Path $python -Parent) -Parent
$tkAssignment=$ast.Find({param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$tkinterCheck'},$true)
. ([ScriptBlock]::Create($tkAssignment.Extent.Text))
$environmentNames=@('PATH','PYTHONPATH','PYTHONHOME','TCL_LIBRARY','TK_LIBRARY')
if(Test-Path -LiteralPath Env:PYTHONHOME){Remove-Item -LiteralPath Env:PYTHONHOME}
[Environment]::SetEnvironmentVariable('PYTHONPATH','','Process')
[Environment]::SetEnvironmentVariable('TCL_LIBRARY','untrusted-tcl-sentinel','Process')
[Environment]::SetEnvironmentVariable('TK_LIBRARY','untrusted-tk-sentinel','Process')
$previousEnvironment=@{}
foreach($name in $environmentNames){$previousEnvironment[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$caught=$false
try {
    & $cleanup
    & $setup
    & '__PYTHON__' -I -c $tkinterCheck
    if($LASTEXITCODE -ne 0){throw 'actual isolated tkinter preflight failed'}
    throw 'expected-test-failure'
}
catch {
    if($_.Exception.Message -ne 'expected-test-failure'){throw}
    $caught=$true
}
finally {& $restore}
if(-not $caught){throw 'failure cleanup was not exercised'}
foreach($name in $environmentNames){
    $actual=[Environment]::GetEnvironmentVariable($name,'Process')
    $expected=$previousEnvironment[$name]
    if(($null -eq $actual) -ne ($null -eq $expected) -or $actual -cne $expected){throw "restore mismatch: $name"}
}
'BUILD_ENVIRONMENT_REGRESSION_OK'
'''.replace("__BUILD_SCRIPT__", build_script).replace("__PYTHON__", python)
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("BUILD_ENVIRONMENT_REGRESSION_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
