from __future__ import annotations

import json
import traceback

import pytest

from material_studio_mcp_server.orchestration.capability_registry import (
    CapabilityRegistry,
    CapabilityRegistryError,
    PluginRegistration,
)
from material_studio_mcp_server.runtime import (
    DomainPluginManifest,
    MatchKind,
    MatchResult,
    RUNTIME_CONTRACT_VERSION,
)


def _stage(name: str, inputs: list[str], outputs: list[str]) -> dict:
    return {
        "callable": f"test_plugin.{name}",
        "input_contracts": inputs,
        "output_contracts": outputs,
        "deterministic": True,
        "filesystem_side_effects": False,
        "process_side_effects": False,
        "network_access": False,
        "gui_access": False,
    }


def _manifest(
    plugin_id: str = "sic_surface",
    *,
    contract_version: str = "1.0.0",
    implementation_version: str = "1.2.0",
    supports_create: bool = True,
    supports_patch: bool = False,
    requires_current_model: bool = False,
    dependencies: list[dict] | None = None,
) -> DomainPluginManifest:
    payload = {
        "plugin_id": plugin_id,
        "contract_version": contract_version,
        "implementation_version": implementation_version,
        "description": "Pure fake surface plugin.",
        "capabilities": {
            "materials": ["3C-SiC"],
            "scenarios": ["surface_slab"],
            "operations": ["create_surface_slab"],
        },
        "limits": {
            "min_atoms": 0,
            "max_atoms": None,
            "supported_periodicity_dimensions": [3],
            "supported_model_kinds": ["crystal"],
            "requires_current_model": requires_current_model,
            "supports_create": supports_create,
            "supports_patch": supports_patch,
            "supports_calculation_plan": False,
            "unsupported_capabilities": [],
        },
        "routing": {
            "priority": 0,
            "ambiguity_policy": "fail_closed",
            "forced_selection_requires_capability_match": True,
        },
        "reference_policy": {
            "allowed_access_modes": ["none"],
            "hidden_holdout_access": False,
            "final_reference_coordinate_access": False,
        },
        "runtime_behavior": {
            "deterministic": True,
            "preview_first": True,
            "mutates_input_model": False,
            "owns_revision_state": False,
            "executes_backend_directly": False,
            "registers_public_mcp_tools": False,
            "owns_gui_session": False,
            "network_access_during_match_plan_build_validate": False,
        },
        "contracts": {
            "match": _stage("match", ["ModelingIntent"], ["MatchResult"]),
            "plan": _stage(
                "plan", ["ModelingIntent", "ModelState"], ["ModelingPlan"]
            ),
            "build": _stage(
                "build", ["ModelingPlan"], ["ModelSpec", "SemanticPatch"]
            ),
            "validate": _stage(
                "validate", ["ModelSpec"], ["DomainValidationReport"]
            ),
        },
        "dependencies": dependencies or [],
    }
    return DomainPluginManifest.model_validate_json(json.dumps(payload))


class FakePlugin:
    def __init__(self, manifest: DomainPluginManifest) -> None:
        self.plugin_id = manifest.plugin_id
        self.contract_version = manifest.contract_version
        self.implementation_version = manifest.implementation_version

    def match(self, intent):
        return MatchResult(
            contract_version=RUNTIME_CONTRACT_VERSION,
            plugin_id=self.plugin_id,
            kind=MatchKind.NONE,
            specificity=0,
            reason_codes=("match.none",),
            issues=(),
        )

    def plan(self, intent, current_state):
        raise AssertionError("not called")

    def build(self, plan):
        raise AssertionError("not called")

    def validate(self, model):
        raise AssertionError("not called")


def _registration(plugin_id: str) -> PluginRegistration:
    manifest = _manifest(plugin_id)
    return PluginRegistration(manifest, FakePlugin(manifest))


def _assert_sanitized_registry_error(caught: pytest.ExceptionInfo) -> None:
    sentinel = "resolver-secret-C:/private/path"
    rendered = "".join(traceback.format_exception(caught.value))
    assert sentinel not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sentinel not in rendered


def test_registry_is_immutable_lexical_and_registration_order_invariant() -> None:
    registrations = (
        _registration("sic_zeta"),
        _registration("sic_alpha"),
        _registration("sic_middle"),
    )
    first = CapabilityRegistry(registrations)
    second = CapabilityRegistry(reversed(registrations))

    assert first.plugin_ids == ("sic_alpha", "sic_middle", "sic_zeta")
    assert first.snapshot() == second.snapshot()
    assert tuple(first) == first.snapshot()
    assert all(not hasattr(item, "plugin") for item in first.snapshot())
    with pytest.raises(AttributeError):
        first._entries = ()


def test_registry_accepts_validated_manifest_plugin_pairs_only() -> None:
    manifest = _manifest()
    plugin = FakePlugin(manifest)
    registry = CapabilityRegistry(((manifest, plugin),))
    assert registry.get_manifest("sic_surface") == manifest

    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry((({"plugin_id": "sic_surface"}, plugin),))
    assert caught.value.reason_code == "registry.manifest_type_invalid"

    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(("module.path:Plugin",))
    assert caught.value.reason_code == "registry.registration_type_invalid"


def test_registry_rejects_duplicate_ids_deterministically() -> None:
    first = _registration("sic_same")
    second = _registration("sic_same")
    messages = []
    for registrations in ((first, second), (second, first)):
        with pytest.raises(CapabilityRegistryError) as caught:
            CapabilityRegistry(registrations)
        messages.append(str(caught.value))
        assert caught.value.reason_code == "registry.duplicate_plugin_id"
    assert messages[0] == messages[1]
    assert "0x" not in messages[0]


def test_registry_rejects_unsupported_contract_major() -> None:
    manifest = _manifest(contract_version="2.0.0")
    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(((manifest, FakePlugin(manifest)),))
    assert caught.value.reason_code == "registry.contract_major_unsupported"


@pytest.mark.parametrize(
    ("attribute", "replacement", "reason_code"),
    [
        ("plugin_id", "sic_other", "registry.plugin_id_mismatch"),
        ("contract_version", "1.1.0", "registry.contract_version_mismatch"),
        (
            "implementation_version",
            "1.3.0",
            "registry.implementation_version_mismatch",
        ),
    ],
)
def test_registry_rejects_plugin_identity_mismatch(
    attribute: str,
    replacement: str,
    reason_code: str,
) -> None:
    manifest = _manifest()
    plugin = FakePlugin(manifest)
    setattr(plugin, attribute, replacement)
    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(((manifest, plugin),))
    assert caught.value.reason_code == reason_code


@pytest.mark.parametrize("method_name", ["match", "plan", "build", "validate"])
def test_registry_rejects_missing_or_noncallable_methods(method_name: str) -> None:
    manifest = _manifest()
    plugin = FakePlugin(manifest)
    setattr(plugin, method_name, None)
    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(((manifest, plugin),))
    assert caught.value.reason_code == f"registry.{method_name}_not_callable"


def test_registry_rejects_create_that_requires_current_model() -> None:
    manifest = _manifest(
        supports_create=True,
        supports_patch=True,
        requires_current_model=True,
    )
    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(((manifest, FakePlugin(manifest)),))
    assert caught.value.reason_code == "registry.create_requires_current_model_conflict"


def test_required_dependency_must_resolve_and_constraint_is_resolver_owned() -> None:
    manifest = _manifest(
        dependencies=[
            {
                "dependency_id": "shared.surface-contract",
                "kind": "shared_contract",
                "version_constraint": ">=7,!=9",
                "required": True,
            }
        ]
    )
    observed = []

    def resolver(dependency):
        observed.append(dependency)
        return dependency.version_constraint == ">=7,!=9"

    registry = CapabilityRegistry(
        ((manifest, FakePlugin(manifest)),),
        dependency_resolver=resolver,
    )
    assert registry.plugin_ids == ("sic_surface",)
    assert observed == [manifest.dependencies[0]]

    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(((manifest, FakePlugin(manifest)),))
    assert caught.value.reason_code == "registry.required_dependency_unresolved"
    assert caught.value.dependency_id == "shared.surface-contract"


def test_optional_unresolved_dependencies_remain_explicit_metadata() -> None:
    manifest = _manifest(
        dependencies=[
            {
                "dependency_id": "optional.surface-hints",
                "kind": "shared_service",
                "version_constraint": "architect-owned",
                "required": False,
            }
        ]
    )
    registry = CapabilityRegistry(
        ((manifest, FakePlugin(manifest)),),
        dependency_resolver=lambda dependency: False,
    )
    snapshot = registry.snapshot()[0]
    assert snapshot.unresolved_optional_dependencies == manifest.dependencies
    assert registry.unresolved_optional_dependencies("sic_surface") == manifest.dependencies


def test_dependency_resolver_failures_are_stable_and_do_not_leak_exceptions() -> None:
    manifest = _manifest(
        dependencies=[
            {
                "dependency_id": "required.runtime",
                "kind": "python_package",
                "version_constraint": "secret-path-C:/private",
                "required": True,
            }
        ]
    )

    def resolver(dependency):
        raise RuntimeError("resolver-secret-C:/private/path")

    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(
            ((manifest, FakePlugin(manifest)),),
            dependency_resolver=resolver,
        )
    assert caught.value.reason_code == "registry.dependency_resolution_failed"
    _assert_sanitized_registry_error(caught)


def test_plugin_identity_exception_context_is_sanitized() -> None:
    manifest = _manifest()

    class UnreadableIdentityPlugin:
        contract_version = manifest.contract_version
        implementation_version = manifest.implementation_version

        @property
        def plugin_id(self):
            raise RuntimeError("resolver-secret-C:/private/path")

    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(((manifest, UnreadableIdentityPlugin()),))

    assert caught.value.reason_code == "registry.plugin_identity_unreadable"
    _assert_sanitized_registry_error(caught)


def test_plugin_method_exception_context_is_sanitized() -> None:
    manifest = _manifest()

    class UnreadableMatchPlugin:
        plugin_id = manifest.plugin_id
        contract_version = manifest.contract_version
        implementation_version = manifest.implementation_version

        @property
        def match(self):
            raise RuntimeError("resolver-secret-C:/private/path")

    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(((manifest, UnreadableMatchPlugin()),))

    assert caught.value.reason_code == "registry.match_not_callable"
    _assert_sanitized_registry_error(caught)


def test_dependency_resolver_requires_a_strict_boolean() -> None:
    manifest = _manifest(
        dependencies=[
            {
                "dependency_id": "required.runtime",
                "kind": "python_package",
                "version_constraint": "1",
                "required": True,
            }
        ]
    )
    with pytest.raises(CapabilityRegistryError) as caught:
        CapabilityRegistry(
            ((manifest, FakePlugin(manifest)),),
            dependency_resolver=lambda dependency: 1,
        )
    assert caught.value.reason_code == "registry.dependency_resolution_type_invalid"
