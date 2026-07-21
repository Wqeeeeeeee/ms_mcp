from __future__ import annotations

from pathlib import Path

from material_studio_mcp_server.runtime import model_spec_digest
from material_studio_mcp_server.specs import ModelSpec
from material_studio_mcp_server.translators import (
    crystal_cif_summary,
    write_crystal_cif,
)


def test_public_translator_emits_deterministic_cif_entirely_in_memory(
    built_model: ModelSpec,
    monkeypatch,
) -> None:
    writes: list[str] = []

    def capture_write_text(path: Path, text: str, *, encoding: str) -> int:
        assert encoding == "utf-8"
        writes.append(text)
        return len(text)

    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "write_text", capture_write_text)
    digest_before = model_spec_digest(built_model)

    first_path = write_crystal_cif(built_model.model, Path("memory-first.cif"))
    second_path = write_crystal_cif(built_model.model, Path("memory-second.cif"))

    assert first_path.name == "memory-first.cif"
    assert second_path.name == "memory-second.cif"
    assert len(writes) == 2
    assert writes[0] == writes[1]
    assert writes[0].isascii()
    assert writes[0].endswith("\n")
    assert model_spec_digest(built_model) == digest_before

    atom_rows = [
        line
        for line in writes[0].splitlines()
        if line.startswith("  ") and not line.lstrip().startswith("_")
    ]
    assert len(atom_rows) == 80
    assert sum(" Si " in f" {line} " for line in atom_rows) == 32
    assert sum(" C " in f" {line} " for line in atom_rows) == 32
    assert sum(" H " in f" {line} " for line in atom_rows) == 16
    assert "_cell_length_a    8.7192" in writes[0]
    assert "_cell_length_b    8.7192" in writes[0]


def test_public_cif_summary_is_stable_and_coordinate_free(built_model: ModelSpec) -> None:
    first = crystal_cif_summary(built_model.model, Path("surface.cif"))
    second = crystal_cif_summary(built_model.model, Path("surface.cif"))

    assert first == second
    assert first["structure_format"] == "cif"
    assert first["atom_count"] == 80
    assert first["elements"] == {"C": 32, "H": 16, "Si": 32}
    assert "basis_atoms" not in first
    assert "coordinates" not in first
