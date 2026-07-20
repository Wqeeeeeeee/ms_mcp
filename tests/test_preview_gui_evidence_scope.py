from __future__ import annotations

from material_studio_mcp_server import server


def _audit() -> dict:
    return {
        "model": {"atom_count": 1},
        "views": [
            {
                "name": "front",
                "supported": True,
                "atom_projection_count": 1,
                "health": {"warnings": []},
            }
        ],
        "health": {"ok": True, "warnings": [], "errors": []},
    }


def _unbound_gui_summary() -> dict:
    return {
        "current_revision_gui_evidence_applicable": False,
        "current_revision_gui_evidence_status": "not_bound_to_current_revision",
        "current_revision_gui_evidence_sources": [],
        "hot_loaded": False,
        "selected_window_matches_current": False,
        "foreground_window_matches_current": False,
        "window_identity_verification": "mismatched",
        "visual_validation": "not_available",
    }


def test_preview_ignores_unbound_window_identity_in_view_review_and_readiness() -> None:
    gui = _unbound_gui_summary()
    view_review = server._view_review_from_audit(_audit(), gui)
    report = {
        "ok": True,
        "health_ok": True,
        "execution_mode": "preview",
        "normality": "preview_ready",
        "project_id": "preview_scope",
        "revision": 0,
        "script_valid": True,
        "gui": gui,
        "view_review": view_review,
        "live_readiness": {},
    }

    readiness = server._live_readiness_summary(report)

    assert view_review["status"] == "passed"
    assert view_review["ok"] is True
    assert view_review["current_revision_gui_evidence_applicable"] is False
    assert "gui_window_identity_mismatched" not in view_review["risk_flags"]
    assert "gui_selected_window_stale" not in view_review["risk_flags"]
    assert "gui_window_identity_mismatched" not in readiness["blocking_reasons"]
    assert "gui:selected_window_stale" not in readiness["review_reasons"]
    assert readiness["state"] == "preview_ready"


def test_bound_current_revision_keeps_window_identity_gate() -> None:
    gui = {
        **_unbound_gui_summary(),
        "current_revision_gui_evidence_applicable": True,
        "current_revision_gui_evidence_status": "bound_to_current_revision",
        "current_revision_gui_evidence_sources": ["current_request_gui_open_artifact"],
        "hot_loaded": True,
    }
    view_review = server._view_review_from_audit(_audit(), gui)
    report = {
        "ok": True,
        "health_ok": True,
        "execution_mode": "execute",
        "normality": "review_warnings",
        "project_id": "bound_scope",
        "revision": 0,
        "script_valid": True,
        "gui": gui,
        "view_review": view_review,
        "live_readiness": {},
    }

    readiness = server._live_readiness_summary(report)

    assert view_review["status"] == "failed"
    assert view_review["ok"] is False
    assert "gui_window_identity_mismatched" in view_review["critical_flags"]
    assert "gui_window_identity_mismatched" in readiness["blocking_reasons"]


def test_gui_summary_reports_unbound_observed_window_without_binding_it() -> None:
    response = {
        "project_id": "current_revision",
        "revision": 1,
        "execution_mode": "preview",
        "planned_outputs": {"structure": "C:/tmp/current_revision.cif"},
    }
    gui_status = {
        "window_found": True,
        "supported": True,
        "selected_window_handle": 77,
        "windows": [
            {
                "handle": 77,
                "title": "unrelated - Materials Studio",
                "is_selected": True,
                "is_foreground": True,
                "project_id": "another_project",
                "revision": 4,
                "source_path": "C:/tmp/another_project.cif",
                "project_wrapper_metadata": {
                    "project_id": "another_project",
                    "revision": 4,
                    "source_path": "C:/tmp/another_project.cif",
                },
            }
        ],
    }

    summary = server._gui_report_summary(response, gui_status=gui_status, gui_open=None)

    assert summary["current_revision_gui_evidence_applicable"] is False
    assert summary["current_revision_gui_evidence_status"] == "not_bound_to_current_revision"
    assert summary["current_revision_gui_evidence_sources"] == []
    assert summary["window_found"] is True
    assert summary["matching_window_count"] == 0
    assert summary["loaded_current_revision"] is False


def test_compact_receipts_preserve_gui_evidence_scope() -> None:
    report = {
        "project_id": "compact_scope",
        "revision": 2,
        "execution_mode": "preview",
        "structure": {"path": "C:/tmp/compact_scope.cif", "exists": True},
        "gui": _unbound_gui_summary(),
        "view_review": {
            "available": True,
            "status": "passed",
            "ok": True,
            "view_names": ["front"],
            "supported_view_names": ["front"],
            "views": [],
            "current_revision_gui_evidence_applicable": False,
            "current_revision_gui_evidence_status": "not_bound_to_current_revision",
            "current_revision_gui_evidence_sources": [],
        },
    }
    current = server._gui_current_revision_status_from_report(report)
    live_summary = server._gui_current_revision_live_summary(current)
    response = {
        "ok": True,
        "project_id": report["project_id"],
        "revision": report["revision"],
        "execution_mode": report["execution_mode"],
        "modeling_report": report,
        "gui_current_revision": current,
        "live_summary": live_summary,
        "view_parameter_summary": server._view_parameter_summary(report),
    }

    compact = server._compact_live_response(response, "compact")

    assert compact["gui_current_revision"][
        "current_revision_gui_evidence_applicable"
    ] is False
    assert compact["gui_current_revision"][
        "current_revision_gui_evidence_status"
    ] == "not_bound_to_current_revision"
    assert compact["live_summary"][
        "gui_current_revision_gui_evidence_applicable"
    ] is False
    assert compact["live_summary"][
        "gui_current_revision_gui_evidence_status"
    ] == "not_bound_to_current_revision"
    assert compact["view_parameter_summary"][
        "current_revision_gui_evidence_applicable"
    ] is False
    assert compact["view_parameter_summary"][
        "current_revision_gui_evidence_status"
    ] == "not_bound_to_current_revision"

    report["gui"] = {
        **_unbound_gui_summary(),
        "current_revision_gui_evidence_applicable": True,
        "current_revision_gui_evidence_status": "bound_to_current_revision",
        "current_revision_gui_evidence_sources": [
            "current_request_gui_open_artifact"
        ],
        "hot_loaded": True,
    }
    report["view_review"].update(
        {
            "current_revision_gui_evidence_applicable": True,
            "current_revision_gui_evidence_status": "bound_to_current_revision",
            "current_revision_gui_evidence_sources": [
                "current_request_gui_open_artifact"
            ],
        }
    )
    current = server._gui_current_revision_status_from_report(report)
    response["modeling_report"] = report
    response["gui_current_revision"] = current
    response["live_summary"] = server._gui_current_revision_live_summary(current)
    response["view_parameter_summary"] = server._view_parameter_summary(report)

    compact = server._compact_live_response(response, "compact")

    assert compact["gui_current_revision"][
        "current_revision_gui_evidence_applicable"
    ] is True
    assert compact["live_summary"][
        "gui_current_revision_gui_evidence_status"
    ] == "bound_to_current_revision"
    assert compact["view_parameter_summary"][
        "current_revision_gui_evidence_sources"
    ] == ["current_request_gui_open_artifact"]
