"""Immutable in-memory registry for semiconductor domain plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, TypeAlias

from material_studio_mcp_server.runtime import (
    RUNTIME_CONTRACT_VERSION,
    DomainPluginManifest,
    PluginDependency,
    SemiconductorDomainPlugin,
)


DependencyResolver: TypeAlias = Callable[[PluginDependency], bool]


class CapabilityRegistryError(ValueError):
    """Deterministic registration rejection without raw object details."""

    def __init__(
        self,
        reason_code: str,
        *,
        plugin_id: str | None = None,
        dependency_id: str | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.plugin_id = plugin_id
        self.dependency_id = dependency_id
        details = []
        if plugin_id is not None:
            details.append(f"plugin_id={plugin_id}")
        if dependency_id is not None:
            details.append(f"dependency_id={dependency_id}")
        suffix = f" ({', '.join(details)})" if details else ""
        super().__init__(f"{reason_code}{suffix}")


@dataclass(frozen=True, slots=True)
class PluginRegistration:
    """One already-instantiated manifest/plugin registration."""

    manifest: DomainPluginManifest
    plugin: SemiconductorDomainPlugin


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Immutable registry metadata that deliberately excludes plugin objects."""

    manifest: DomainPluginManifest
    unresolved_optional_dependencies: tuple[PluginDependency, ...]

    @property
    def plugin_id(self) -> str:
        return self.manifest.plugin_id

    @property
    def contract_version(self) -> str:
        return self.manifest.contract_version

    @property
    def implementation_version(self) -> str:
        return self.manifest.implementation_version


@dataclass(frozen=True, slots=True)
class _RegistryEntry:
    manifest: DomainPluginManifest
    plugin: SemiconductorDomainPlugin
    unresolved_optional_dependencies: tuple[PluginDependency, ...]

    def snapshot(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(
            manifest=self.manifest,
            unresolved_optional_dependencies=self.unresolved_optional_dependencies,
        )


RegistrationInput: TypeAlias = (
    PluginRegistration
    | tuple[DomainPluginManifest, SemiconductorDomainPlugin]
)


@dataclass(frozen=True, slots=True, init=False)
class CapabilityRegistry:
    """Validated registry whose ordering and contents cannot change after init."""

    _entries: tuple[_RegistryEntry, ...]

    def __init__(
        self,
        registrations: Iterable[RegistrationInput] = (),
        *,
        dependency_resolver: DependencyResolver | None = None,
    ) -> None:
        if dependency_resolver is not None and not callable(dependency_resolver):
            raise CapabilityRegistryError("registry.dependency_resolver_not_callable")

        normalized = tuple(_normalize_registration(item) for item in registrations)
        ordered = tuple(sorted(normalized, key=lambda item: item.manifest.plugin_id))
        _reject_duplicate_plugin_ids(ordered)

        entries = tuple(
            _validate_registration(registration, dependency_resolver)
            for registration in ordered
        )
        object.__setattr__(self, "_entries", entries)

    @classmethod
    def from_pairs(
        cls,
        registrations: Iterable[
            tuple[DomainPluginManifest, SemiconductorDomainPlugin]
        ],
        *,
        dependency_resolver: DependencyResolver | None = None,
    ) -> "CapabilityRegistry":
        return cls(registrations, dependency_resolver=dependency_resolver)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[CapabilitySnapshot]:
        return iter(self.snapshot())

    def __contains__(self, plugin_id: object) -> bool:
        return any(entry.manifest.plugin_id == plugin_id for entry in self._entries)

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(entry.manifest.plugin_id for entry in self._entries)

    def snapshot(self) -> tuple[CapabilitySnapshot, ...]:
        return tuple(entry.snapshot() for entry in self._entries)

    def get_manifest(self, plugin_id: str) -> DomainPluginManifest | None:
        entry = self._entry_for(plugin_id)
        return entry.manifest if entry is not None else None

    def unresolved_optional_dependencies(
        self,
        plugin_id: str,
    ) -> tuple[PluginDependency, ...]:
        entry = self._entry_for(plugin_id)
        return entry.unresolved_optional_dependencies if entry is not None else ()

    def _entry_for(self, plugin_id: str) -> _RegistryEntry | None:
        for entry in self._entries:
            if entry.manifest.plugin_id == plugin_id:
                return entry
        return None

    def _routing_entries(self) -> tuple[_RegistryEntry, ...]:
        return self._entries


def _normalize_registration(item: RegistrationInput) -> PluginRegistration:
    if isinstance(item, PluginRegistration):
        registration = item
    elif isinstance(item, tuple) and len(item) == 2:
        registration = PluginRegistration(manifest=item[0], plugin=item[1])
    else:
        raise CapabilityRegistryError("registry.registration_type_invalid")

    if not isinstance(registration.manifest, DomainPluginManifest):
        raise CapabilityRegistryError("registry.manifest_type_invalid")
    return PluginRegistration(
        manifest=registration.manifest,
        plugin=registration.plugin,
    )


def _reject_duplicate_plugin_ids(
    registrations: tuple[PluginRegistration, ...],
) -> None:
    previous: str | None = None
    for registration in registrations:
        plugin_id = registration.manifest.plugin_id
        if plugin_id == previous:
            raise CapabilityRegistryError(
                "registry.duplicate_plugin_id",
                plugin_id=plugin_id,
            )
        previous = plugin_id


def _validate_registration(
    registration: PluginRegistration,
    dependency_resolver: DependencyResolver | None,
) -> _RegistryEntry:
    manifest = registration.manifest
    plugin = registration.plugin
    plugin_id = manifest.plugin_id

    supported_major = int(RUNTIME_CONTRACT_VERSION.split(".", 1)[0])
    manifest_major = int(manifest.contract_version.split(".", 1)[0])
    if manifest_major != supported_major:
        raise CapabilityRegistryError(
            "registry.contract_major_unsupported",
            plugin_id=plugin_id,
        )

    identities: dict[str, object] = {}
    for attribute in (
        "plugin_id",
        "contract_version",
        "implementation_version",
    ):
        identity_unreadable = False
        try:
            identities[attribute] = getattr(plugin, attribute)
        except Exception:
            identity_unreadable = True
        if identity_unreadable:
            raise CapabilityRegistryError(
                "registry.plugin_identity_unreadable",
                plugin_id=plugin_id,
            )
        if type(identities[attribute]) is not str:
            raise CapabilityRegistryError(
                "registry.plugin_identity_type_invalid",
                plugin_id=plugin_id,
            )

    mismatch_codes = {
        "plugin_id": "registry.plugin_id_mismatch",
        "contract_version": "registry.contract_version_mismatch",
        "implementation_version": "registry.implementation_version_mismatch",
    }
    for attribute, reason_code in mismatch_codes.items():
        if identities[attribute] != getattr(manifest, attribute):
            raise CapabilityRegistryError(reason_code, plugin_id=plugin_id)

    for method_name in ("match", "plan", "build", "validate"):
        method_unreadable = False
        try:
            method = getattr(plugin, method_name)
        except Exception:
            method_unreadable = True
        if method_unreadable:
            raise CapabilityRegistryError(
                f"registry.{method_name}_not_callable",
                plugin_id=plugin_id,
            )
        if not callable(method):
            raise CapabilityRegistryError(
                f"registry.{method_name}_not_callable",
                plugin_id=plugin_id,
            )

    if manifest.limits.supports_create and manifest.limits.requires_current_model:
        raise CapabilityRegistryError(
            "registry.create_requires_current_model_conflict",
            plugin_id=plugin_id,
        )

    unresolved_optional: list[PluginDependency] = []
    dependencies = tuple(
        sorted(
            manifest.dependencies,
            key=lambda dependency: (
                dependency.dependency_id,
                dependency.kind.value,
                dependency.version_constraint,
                dependency.required,
            ),
        )
    )
    for dependency in dependencies:
        available = False
        if dependency_resolver is not None:
            resolution_failed = False
            try:
                resolved = dependency_resolver(dependency)
            except Exception:
                resolution_failed = True
            if resolution_failed:
                raise CapabilityRegistryError(
                    "registry.dependency_resolution_failed",
                    plugin_id=plugin_id,
                    dependency_id=dependency.dependency_id,
                )
            if type(resolved) is not bool:
                raise CapabilityRegistryError(
                    "registry.dependency_resolution_type_invalid",
                    plugin_id=plugin_id,
                    dependency_id=dependency.dependency_id,
                )
            available = resolved

        if not available and dependency.required:
            raise CapabilityRegistryError(
                "registry.required_dependency_unresolved",
                plugin_id=plugin_id,
                dependency_id=dependency.dependency_id,
            )
        if not available:
            unresolved_optional.append(dependency)

    return _RegistryEntry(
        manifest=manifest,
        plugin=plugin,
        unresolved_optional_dependencies=tuple(unresolved_optional),
    )


__all__ = [
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilitySnapshot",
    "DependencyResolver",
    "PluginRegistration",
    "RegistrationInput",
]
