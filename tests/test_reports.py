from __future__ import annotations

import json

import pytest

from ms_mcp.parsers.reports import diagnose_report, parse_job_report


@pytest.mark.parametrize(
    ("log_text", "category"),
    [
        ("License checkout failed for Forcite.", "license_error"),
        ("SCF cycle not converged after max iterations.", "convergence_error"),
        ("Warning: atoms are too close; overlap detected.", "model_geometry_error"),
        ("Traceback: AttributeError in MaterialsScript API.", "script_api_error"),
    ],
)
def test_diagnose_known_failure_categories(tmp_path, log_text, category):
    (tmp_path / "stderr.log").write_text(log_text, encoding="utf-8")
    (tmp_path / "returncode.txt").write_text("1", encoding="utf-8")

    report = parse_job_report(tmp_path)
    diagnosis = diagnose_report(report)

    assert diagnosis["category"] == category
    assert diagnosis["evidence"]


def test_parse_success_report(tmp_path):
    (tmp_path / "stdout.log").write_text("Job successfully completed. Energy = -12.5", encoding="utf-8")
    (tmp_path / "returncode.txt").write_text("0", encoding="utf-8")
    (tmp_path / "result.xsd").write_text("<XSD/>", encoding="utf-8")

    report = parse_job_report(tmp_path)
    diagnosis = diagnose_report(report)

    assert report["status"] == "success"
    assert report["energy"] == -12.5
    assert diagnosis["category"] == "none"


def test_parse_job_report_deduplicates_repeated_log_lines(tmp_path):
    repeated = "Warning: atoms are too close; overlap detected."
    (tmp_path / "stdout.log").write_text(f"{repeated}\n{repeated}\n", encoding="utf-8")
    (tmp_path / "stderr.log").write_text(f"{repeated}\n", encoding="utf-8")

    report = parse_job_report(tmp_path)

    assert report["warnings"] == [repeated]
    assert report["status"] == "warning"


def test_parse_job_report_reads_structured_result_metadata(tmp_path):
    metadata = {
        "success": True,
        "returncode": 0,
        "stderr": "",
        "parsed_json": {
            "warning": "Warning: atoms are too close; overlap detected.",
            "details": {"message": "Traceback: AttributeError in MaterialsScript API."},
        },
    }
    (tmp_path / "result_metadata.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )

    report = parse_job_report(tmp_path)

    assert any(path.endswith("result_metadata.json") for path in report["files_read"])
    assert "Warning: atoms are too close; overlap detected." in report["warnings"]
    assert any("AttributeError" in error for error in report["errors"])
