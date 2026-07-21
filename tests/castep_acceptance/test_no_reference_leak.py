from __future__ import annotations

import ast
import builtins
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import material_studio_mcp_server.castep_acceptance.benchmark as benchmark_module

from ._helpers import run_fake_acceptance


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "castep_acceptance"
REFERENCE_STORE = ROOT / "benchmarks" / ("ref" + "erences")
REFERENCE_TESTS = ROOT / "tests" / ("reference" + "_data")
REFERENCE_MODULE = ".".join(("material_studio_mcp_server", "reference" + "_data"))
ORACLE_MODULE = ".".join(
    ("tests", "domains", "surface", "test_blind_" + "benchmark")
)


def _contains_protected_literal(value: str) -> bool:
    folded = value.replace("\\", "/").casefold()
    markers = (
        "/".join(("benchmarks", "ref" + "erences")),
        "validation_" + "artifacts",
        "validation-" + "artifacts",
        "hidden_" + "holdouts",
        "hidden-" + "holdouts",
        "reference_" + "coordinates",
        "reference-" + "coordinates",
    )
    return any(marker in folded for marker in markers)


def test_acceptance_source_has_no_reference_or_oracle_dependency() -> None:
    forbidden_imports = (
        REFERENCE_MODULE,
        "tests." + "reference_data",
        ORACLE_MODULE,
    )
    forbidden_roots = {"glob", "requests", "shutil", "socket", "subprocess", "urllib"}
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules = (node.module or "",)
            else:
                modules = ()
            assert not any(
                module == forbidden or module.startswith(forbidden + ".")
                for module in modules
                for forbidden in forbidden_imports
            ), path
            assert not any(
                module.split(".", 1)[0] in forbidden_roots for module in modules
            ), path
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not _contains_protected_literal(node.value), path


def _synthetic_protected_artifacts(tmp_path: Path, canary: str) -> dict[Path, bytes]:
    artifacts: dict[Path, bytes] = {}
    roots = (
        tmp_path / "benchmarks" / ("ref" + "erences"),
        tmp_path / ("validation_" + "artifacts"),
        tmp_path / ("hidden_" + "holdouts"),
        tmp_path / ("reference_" + "coordinates"),
    )
    for index, root in enumerate(roots):
        root.mkdir(parents=True)
        path = root / f"private-{index}.cif"
        payload = f"{canary}:{index}\n".encode("ascii")
        path.write_bytes(payload)
        artifacts[path] = payload
    return artifacts


def test_fresh_preview_import_never_resolves_backend_or_protected_store(
    tmp_path: Path,
) -> None:
    canary = "PRIVATE_CASTEP_ACCEPTANCE_CANARY_8A41"
    protected = _synthetic_protected_artifacts(tmp_path, canary)
    workspace = tmp_path / "fresh-preview-workspace"
    environment = os.environ.copy()
    source_root = str(ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join((source_root, existing))
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["CASTEP_ACCEPTANCE_AUDIT_ROOTS"] = json.dumps(
        [str(REFERENCE_STORE), str(REFERENCE_TESTS), *[str(path.parent) for path in protected]]
    )
    environment["CASTEP_ACCEPTANCE_WORKSPACE"] = str(workspace)
    script = r'''
import json
import os
import sys
from pathlib import Path

def normalized(value):
    if isinstance(value, int):
        return None
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(value))))
    except TypeError:
        return None

roots = tuple(normalized(value) for value in json.loads(os.environ["CASTEP_ACCEPTANCE_AUDIT_ROOTS"]))
blocked_modules = (
    "material_studio_mcp_server.server",
    "tests.domains.surface.test_blind_benchmark",
)

def reject_path(event, value):
    observed = normalized(value)
    if observed is not None and any(
        observed == root or observed.startswith(root + os.sep) for root in roots
    ):
        raise AssertionError("protected path reached during preview: " + event)

def audit(event, args):
    if event in {"open", "os.listdir", "os.scandir"} and args:
        reject_path(event, args[0])

class BlockedModuleFinder:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == root or fullname.startswith(root + ".") for root in blocked_modules):
            raise AssertionError("backend or protected module resolved during preview")
        return None

sys.addaudithook(audit)
sys.meta_path.insert(0, BlockedModuleFinder())
from material_studio_mcp_server.castep_acceptance import (
    CastepAcceptanceHarness,
    CastepAcceptanceRequest,
)

def forbidden():
    raise AssertionError("preview resolved a backend")

workspace = Path(os.environ["CASTEP_ACCEPTANCE_WORKSPACE"])
plan = CastepAcceptanceHarness(
    tool_resolver=forbidden,
    gui_backend_resolver=forbidden,
    real_environment=True,
).run(
    CastepAcceptanceRequest(
        request_id="fresh-preview-reference-isolation",
        workspace_root=workspace,
    )
)
assert plan.preview_files_runner_or_gui_touched is False
assert not workspace.exists()
assert "material_studio_mcp_server.server" not in sys.modules
'''
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert {path: path.read_bytes() for path in protected} == protected


def test_fake_execute_cannot_access_protected_paths_or_evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "PRIVATE_CASTEP_ACCEPTANCE_CANARY_2C79"
    protected = _synthetic_protected_artifacts(tmp_path, canary)
    protected_roots = (
        REFERENCE_STORE,
        REFERENCE_TESTS,
        *tuple(path.parent for path in protected),
    )
    normalized_roots = tuple(
        os.path.normcase(os.path.abspath(str(path))) for path in protected_roots
    )

    def reject(operation: str, value: object) -> None:
        if isinstance(value, int):
            return
        try:
            observed = os.path.normcase(
                os.path.abspath(os.fsdecode(os.fspath(value)))
            )
        except TypeError:
            return
        if any(
            observed == root or observed.startswith(root + os.sep)
            for root in normalized_roots
        ):
            raise AssertionError(f"protected artifact access through {operation}")

    original_builtin_open = builtins.open
    original_io_open = io.open

    def guarded_builtin_open(file, *args, **kwargs):
        reject("builtins.open", file)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_io_open(file, *args, **kwargs):
        reject("io.open", file)
        return original_io_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(io, "open", guarded_io_open)

    for name in (
        "exists",
        "glob",
        "is_dir",
        "is_file",
        "iterdir",
        "lstat",
        "open",
        "read_bytes",
        "read_text",
        "rglob",
        "stat",
        "write_bytes",
        "write_text",
    ):
        original = getattr(Path, name)

        def guarded(path: Path, *args, _name=name, _original=original, **kwargs):
            reject(f"Path.{_name}", path)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, guarded)

    def forbidden_evaluator(*args, **kwargs):
        raise AssertionError("calculation adapter entered the evaluator")

    monkeypatch.setattr(
        benchmark_module,
        "evaluate_benchmark_case",
        forbidden_evaluator,
    )
    result, _runner, _gui = run_fake_acceptance(monkeypatch, tmp_path / "adapter")
    assert result.verification.reference_store_accessed is False
    assert result.verification.status == "NOT_RUN"
    streams = capsys.readouterr()
    assert canary not in streams.out
    assert canary not in streams.err
    persisted: dict[Path, bytes] = {}
    for path in protected:
        with original_builtin_open(path, "rb") as handle:
            persisted[path] = handle.read()
    assert persisted == protected
