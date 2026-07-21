from __future__ import annotations

import ast
import builtins
import glob
import hashlib
import importlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import warnings

import pytest

import material_studio_mcp_server.ms_roundtrip.benchmark as benchmark_module
from material_studio_mcp_server.ms_roundtrip import (
    MaterialsStudioRoundtripAdapter,
    RoundtripExecutionResult,
    RoundtripPlan,
)


ROOT = Path(__file__).resolve().parents[2]
ROUNDTRIP_PACKAGE = ROOT / "src" / "material_studio_mcp_server" / "ms_roundtrip"
ROUNDTRIP_TESTS = Path(__file__).resolve().parent
EVALUATOR_HARNESS = ROUNDTRIP_TESTS / "test_benchmark.py"

REFERENCE_STORE = ROOT / "benchmarks" / ("ref" + "erences")
REFERENCE_TESTS = ROOT / "tests" / ("reference" + "_data")
ORACLE_MODULE = ".".join(
    ("tests", "domains", "surface", "test_blind_" + "benchmark")
)
ORACLE_FACTORY = "_analytical_" + "oracle_cif_bytes"
REFERENCE_MODULE = ".".join(("material_studio_mcp_server", "reference" + "_data"))


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _imports(tree: ast.AST) -> tuple[tuple[str, tuple[str, ...]], ...]:
    imports: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                (
                    node.module or "",
                    tuple(alias.name for alias in node.names),
                )
            )
    return tuple(imports)


def _contains_protected_path_literal(value: str) -> bool:
    normalized = value.replace("\\", "/").casefold()
    markers = (
        "/".join(("benchmarks", "ref" + "erences")),
        "/".join(("validation", "artifacts")),
        "validation_" + "artifacts",
        "validation-" + "artifacts",
        "/".join(("hidden", "holdouts")),
        "hidden_" + "holdouts",
        "hidden-" + "holdouts",
        "/".join(("reference", "coordinates")),
        "reference_" + "coordinates",
        "reference-" + "coordinates",
    )
    return any(marker in normalized for marker in markers)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_roundtrip_source_has_no_reference_dependency_or_discovery_primitive() -> None:
    forbidden_import_prefixes = (REFERENCE_MODULE, "tests." + "reference_data")
    forbidden_call_names = {
        "__import__",
        "glob",
        "iglob",
        "import_module",
        "iterdir",
        "listdir",
        "rglob",
        "scandir",
        "walk",
    }
    forbidden_copy_calls = {"copy", "copy2", "copyfile", "copytree"}

    for path in sorted(ROUNDTRIP_PACKAGE.glob("*.py")):
        tree = _parse(path)
        imports = _imports(tree)
        assert not any(
            module.startswith(forbidden_import_prefixes)
            for module, _names in imports
        ), path
        assert not any(module == ORACLE_MODULE for module, _names in imports), path
        assert not any(
            module.split(".", 1)[0] in {"glob", "pkgutil", "shutil"}
            for module, _names in imports
        ), path
        assert not any(
            module.split(".", 1)[0] == "logging" for module, _names in imports
        ), path

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not _contains_protected_path_literal(node.value), path
            if not isinstance(node, ast.Call):
                continue
            called = _qualified_name(node.func)
            terminal = called.rsplit(".", 1)[-1]
            assert terminal not in forbidden_call_names, (path, called)
            assert terminal not in forbidden_copy_calls, (path, called)
            assert called not in {"print", "builtins.print"}, (path, called)
            if terminal in {
                "open",
                "read_bytes",
                "read_text",
                "stable_read_file",
                "sha256_file",
                "file_digest",
            }:
                rendered = ast.unparse(node)
                assert not _contains_protected_path_literal(rendered), (path, rendered)


def test_roundtrip_tests_keep_oracle_bytes_in_the_isolated_evaluator_harness() -> None:
    oracle_imports: list[tuple[Path, tuple[str, ...]]] = []
    audited = tuple(
        path
        for path in sorted(ROUNDTRIP_TESTS.glob("*.py"))
        if path != Path(__file__).resolve()
    )
    for path in audited:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for module, names in _imports(tree):
            assert not module.startswith(REFERENCE_MODULE), path
            assert not module.startswith("tests." + "reference_data"), path
            if module == ORACLE_MODULE:
                oracle_imports.append((path, names))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not _contains_protected_path_literal(node.value), path

        if path != EVALUATOR_HARNESS:
            assert ORACLE_FACTORY not in source, path

    assert oracle_imports == [(EVALUATOR_HARNESS, (ORACLE_FACTORY,))]

    tree = _parse(EVALUATOR_HARNESS)
    prepare = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_prepare_offline_run"
    )
    calls = tuple(node for node in ast.walk(prepare) if isinstance(node, ast.Call))
    adapter_line = min(
        node.lineno
        for node in calls
        if _qualified_name(node.func).rsplit(".", 1)[-1] == "run"
        and "MaterialsStudioRoundtripAdapter" in ast.unparse(node)
    )
    submission_line = min(
        node.lineno
        for node in calls
        if _qualified_name(node.func).endswith("CandidateSubmission")
    )
    oracle_line = min(
        node.lineno
        for node in calls
        if _qualified_name(node.func).endswith(ORACLE_FACTORY)
    )
    evaluator_line = min(
        node.lineno
        for node in calls
        if _qualified_name(node.func).endswith("EvaluationRoots")
    )
    assert adapter_line < submission_line < oracle_line < evaluator_line

    for path in (*sorted(ROUNDTRIP_PACKAGE.glob("*.py")), *audited):
        tree = _parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _qualified_name(node.func).endswith("ReferenceAccess")
            ):
                keyword_values = {keyword.arg: keyword.value for keyword in node.keywords}
                for field in (
                    "raw_structure_access",
                    "final_coordinate_access",
                    "hidden_holdout_access",
                ):
                    value = keyword_values[field]
                    assert isinstance(value, ast.Constant) and value.value is False, (
                        path,
                        field,
                    )


class _ProtectedAccessTripwire:
    def __init__(self, roots: tuple[Path, ...], canary: str) -> None:
        self._roots = tuple(self._normalize(path) for path in roots)
        self._canary = canary
        self._sensitive_markers = (
            "/".join(("benchmarks", "ref" + "erences")),
            "validation_" + "artifacts",
            "validation-" + "artifacts",
            "hidden_" + "holdouts",
            "hidden-" + "holdouts",
            "reference_" + "coordinates",
            "reference-" + "coordinates",
        )
        self.attempts: list[tuple[str, str]] = []

    @staticmethod
    def _normalize(path: object) -> str | None:
        if isinstance(path, int):
            return None
        try:
            value = os.fsdecode(os.fspath(path))
        except TypeError:
            return None
        return os.path.normcase(os.path.abspath(value))

    def _is_protected(self, path: object) -> bool:
        observed = self._normalize(path)
        if observed is None:
            return False
        return any(
            observed == root or observed.startswith(root + os.sep)
            for root in self._roots
        )

    def reject_path(self, operation: str, path: object) -> None:
        if self._is_protected(path):
            rendered = os.fsdecode(os.fspath(path))
            self.attempts.append((operation, rendered))
            raise AssertionError(f"protected artifact access through {operation}")

    def reject_module(self, operation: str, module: object) -> None:
        if not isinstance(module, str):
            return
        if module == REFERENCE_MODULE or module.startswith(REFERENCE_MODULE + "."):
            self.attempts.append((operation, module))
            raise AssertionError(f"protected module access through {operation}")
        if module == ORACLE_MODULE or module.startswith(ORACLE_MODULE + "."):
            self.attempts.append((operation, module))
            raise AssertionError(f"coordinate oracle import through {operation}")

    def reject_payload(self, operation: str, payload: object) -> None:
        try:
            value = bytes(payload)
        except (TypeError, ValueError):
            return
        folded = value.decode("utf-8", errors="ignore").replace("\\", "/").casefold()
        if self._canary.encode("ascii") in value or any(
            marker in folded for marker in self._sensitive_markers
        ):
            self.attempts.append((operation, "canary-bytes"))
            raise AssertionError(f"protected bytes reached {operation}")

    def reject_text(self, operation: str, value: object) -> None:
        rendered = str(value)
        normalized = rendered.replace("\\", "/").casefold()
        if self._canary in rendered or any(
            root.replace("\\", "/").casefold() in normalized for root in self._roots
        ) or any(marker in normalized for marker in self._sensitive_markers):
            self.attempts.append((operation, "protected-text"))
            raise AssertionError(f"protected data reached {operation}")


class _GuardedHash:
    def __init__(self, value, tripwire: _ProtectedAccessTripwire) -> None:
        self._value = value
        self._tripwire = tripwire

    def update(self, payload=b"") -> None:
        self._tripwire.reject_payload("hash.update", payload)
        self._value.update(payload)

    def copy(self) -> "_GuardedHash":
        return _GuardedHash(self._value.copy(), self._tripwire)

    def __getattr__(self, name: str):
        return getattr(self._value, name)


def _install_tripwire(
    monkeypatch: pytest.MonkeyPatch,
    tripwire: _ProtectedAccessTripwire,
) -> None:
    original_builtin_open = builtins.open
    original_io_open = io.open
    original_import = builtins.__import__
    original_import_module = importlib.import_module
    original_sha256 = hashlib.sha256
    original_hashlib_new = hashlib.new
    original_log = logging.Logger._log
    original_warn = warnings.warn

    def guarded_builtin_open(file, *args, **kwargs):
        tripwire.reject_path("builtins.open", file)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_io_open(file, *args, **kwargs):
        tripwire.reject_path("io.open", file)
        return original_io_open(file, *args, **kwargs)

    def guarded_import(name, *args, **kwargs):
        tripwire.reject_module("builtins.__import__", name)
        return original_import(name, *args, **kwargs)

    def guarded_import_module(name, package=None):
        tripwire.reject_module("importlib.import_module", name)
        return original_import_module(name, package)

    def guarded_sha256(data=b"", *args, **kwargs):
        tripwire.reject_payload("hashlib.sha256", data)
        return _GuardedHash(original_sha256(data, *args, **kwargs), tripwire)

    def guarded_hashlib_new(name, data=b"", *args, **kwargs):
        tripwire.reject_payload("hashlib.new", data)
        return _GuardedHash(
            original_hashlib_new(name, data, *args, **kwargs), tripwire
        )

    def guarded_log(logger, level, msg, args, *positional, **kwargs):
        tripwire.reject_text("logging", (msg, args))
        return original_log(logger, level, msg, args, *positional, **kwargs)

    def guarded_warn(message, *args, **kwargs):
        tripwire.reject_text("warnings", message)
        return original_warn(message, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(importlib, "import_module", guarded_import_module)
    monkeypatch.setattr(hashlib, "sha256", guarded_sha256)
    monkeypatch.setattr(hashlib, "new", guarded_hashlib_new)
    monkeypatch.setattr(logging.Logger, "_log", guarded_log)
    monkeypatch.setattr(warnings, "warn", guarded_warn)

    def guard_path_method(name: str) -> None:
        original = getattr(Path, name)

        def guarded(path: Path, *args, **kwargs):
            tripwire.reject_path(f"Path.{name}", path)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, guarded)

    for name in (
        "exists",
        "glob",
        "is_dir",
        "is_file",
        "iterdir",
        "lstat",
        "mkdir",
        "open",
        "read_bytes",
        "read_text",
        "rglob",
        "rmdir",
        "stat",
        "unlink",
        "write_bytes",
        "write_text",
    ):
        guard_path_method(name)

    def guard_os_single(name: str) -> None:
        original = getattr(os, name)

        def guarded(path, *args, **kwargs):
            tripwire.reject_path(f"os.{name}", path)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(os, name, guarded)

    for name in (
        "access",
        "listdir",
        "lstat",
        "makedirs",
        "mkdir",
        "open",
        "readlink",
        "remove",
        "rmdir",
        "scandir",
        "stat",
        "unlink",
        "walk",
    ):
        guard_os_single(name)

    def guard_os_pair(name: str) -> None:
        original = getattr(os, name)

        def guarded(source, destination, *args, **kwargs):
            tripwire.reject_path(f"os.{name}:source", source)
            tripwire.reject_path(f"os.{name}:destination", destination)
            return original(source, destination, *args, **kwargs)

        monkeypatch.setattr(os, name, guarded)

    for name in ("link", "rename", "replace"):
        guard_os_pair(name)

    original_glob = glob.glob
    original_iglob = glob.iglob

    def guarded_glob(pathname, *args, **kwargs):
        tripwire.reject_path("glob.glob", pathname)
        return original_glob(pathname, *args, **kwargs)

    def guarded_iglob(pathname, *args, **kwargs):
        tripwire.reject_path("glob.iglob", pathname)
        return original_iglob(pathname, *args, **kwargs)

    monkeypatch.setattr(glob, "glob", guarded_glob)
    monkeypatch.setattr(glob, "iglob", guarded_iglob)

    for name in ("copy", "copy2", "copyfile", "copytree", "move"):
        original = getattr(shutil, name)

        def guarded(source, destination, *args, _name=name, _original=original, **kwargs):
            tripwire.reject_path(f"shutil.{_name}:source", source)
            tripwire.reject_path(f"shutil.{_name}:destination", destination)
            return _original(source, destination, *args, **kwargs)

        monkeypatch.setattr(shutil, name, guarded)


def _synthetic_protected_roots(tmp_path: Path) -> tuple[Path, ...]:
    return (
        tmp_path / "benchmarks" / ("ref" + "erences"),
        tmp_path / ("validation_" + "artifacts"),
        tmp_path / ("hidden_" + "holdouts"),
        tmp_path / ("reference_" + "coordinates"),
    )


def _protected_roots(tmp_path: Path) -> tuple[Path, ...]:
    return (
        REFERENCE_STORE,
        REFERENCE_TESTS,
        *_synthetic_protected_roots(tmp_path),
    )


def _create_synthetic_protected_artifacts(
    tmp_path: Path,
    canary: str,
) -> dict[Path, bytes]:
    artifacts: dict[Path, bytes] = {}
    for index, root in enumerate(_synthetic_protected_roots(tmp_path)):
        root.mkdir(parents=True)
        artifact = root / f"private-{index}.cif"
        payload = f"{canary}:{index}\n".encode("ascii")
        artifact.write_bytes(payload)
        artifacts[artifact] = payload
    return artifacts


def test_fresh_import_does_not_access_protected_stores_or_import_oracles(
    tmp_path: Path,
) -> None:
    canary = "PRIVATE_MS_ROUNDTRIP_CANARY_7D31"
    protected = _create_synthetic_protected_artifacts(tmp_path, canary)
    environment = os.environ.copy()
    source_root = str(ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join((source_root, existing))
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["MS_ROUNDTRIP_AUDIT_ROOTS"] = json.dumps(
        [str(path) for path in _protected_roots(tmp_path)]
    )
    environment["MS_ROUNDTRIP_AUDIT_MODULES"] = json.dumps([ORACLE_MODULE])
    script = r'''
import json
import os
import sys

def normalize(value):
    if isinstance(value, int):
        return None
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(value))))
    except TypeError:
        return None

roots = tuple(normalize(value) for value in json.loads(os.environ["MS_ROUNDTRIP_AUDIT_ROOTS"]))
blocked_modules = tuple(json.loads(os.environ["MS_ROUNDTRIP_AUDIT_MODULES"]))

def reject_path(event, value):
    observed = normalize(value)
    if observed is None:
        return
    if any(observed == root or observed.startswith(root + os.sep) for root in roots):
        raise AssertionError("protected path reached during fresh import: " + event)

def audit(event, args):
    if event in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.rmdir"} and args:
        reject_path(event, args[0])
    if event == "shutil.copyfile" and len(args) >= 2:
        reject_path(event, args[0])
        reject_path(event, args[1])
    if event == "import" and args and isinstance(args[0], str):
        name = args[0]
        if any(name == root or name.startswith(root + ".") for root in blocked_modules):
            raise AssertionError("protected module reached during fresh import")

class BlockedModuleFinder:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == root or fullname.startswith(root + ".") for root in blocked_modules):
            raise AssertionError("protected module requested during fresh import")
        return None

sys.addaudithook(audit)
sys.meta_path.insert(0, BlockedModuleFinder())
before = tuple(sorted(os.listdir(".")))
import material_studio_mcp_server.ms_roundtrip
after = tuple(sorted(os.listdir(".")))
assert before == after
assert not any(
    name == root or name.startswith(root + ".")
    for name in sys.modules
    for root in blocked_modules
)
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


def test_preview_and_execute_do_not_access_hash_copy_import_or_log_protected_data(
    tmp_path: Path,
    request_factory,
    fake_runner,
    fake_gui,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "PRIVATE_MS_ROUNDTRIP_CANARY_91A4"
    protected = _create_synthetic_protected_artifacts(tmp_path, canary)
    tripwire = _ProtectedAccessTripwire(_protected_roots(tmp_path), canary)

    def forbidden_evaluator(*args, **kwargs):
        raise AssertionError("adapter path entered the isolated evaluator")

    caplog.set_level(logging.DEBUG)
    caplog.clear()
    with monkeypatch.context() as guarded:
        _install_tripwire(guarded, tripwire)
        for name in (
            "evaluate_benchmark_case",
            "load_benchmark_case",
            "read_candidate_artifact",
            "verify_isolation_roots",
        ):
            guarded.setattr(benchmark_module, name, forbidden_evaluator)

        adapter = MaterialsStudioRoundtripAdapter(
            runner=fake_runner,
            gui_backend=fake_gui,
            real_environment=False,
        )
        preview = adapter.run(request_factory())
        assert isinstance(preview, RoundtripPlan)
        result = adapter.run(request_factory(execution_mode="execute"))
        assert isinstance(result, RoundtripExecutionResult)
        assert result.status == "PASS"

        serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        tripwire.reject_text("returned receipt", serialized)
        assert tripwire.attempts == []

    streams = capsys.readouterr()
    disclosed = "\n".join(
        (streams.out, streams.err, *[record.getMessage() for record in caplog.records])
    )
    assert canary not in disclosed
    for root in _protected_roots(tmp_path):
        assert str(root) not in disclosed
    assert {path: path.read_bytes() for path in protected} == protected
