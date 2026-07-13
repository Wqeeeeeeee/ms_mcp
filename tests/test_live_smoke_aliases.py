from material_studio_mcp_server.server import (
    _diagnostic_export_requested_from_text,
    _explicit_live_gui_open_requested,
    _explicit_live_hotload_requested,
)


def test_same_window_real_time_gui_hotloading_alias_is_recognized() -> None:
    assert _explicit_live_hotload_requested("same window real-time GUI hot-loading")


def test_current_window_hotload_and_view_export_alias_is_recognized() -> None:
    request = "Push it to the current window and export view parameters."

    assert _explicit_live_hotload_requested(request)
    assert _explicit_live_gui_open_requested(request)


def test_chinese_current_model_show_alias_is_recognized() -> None:
    request = "\u628a\u5f53\u524d\u6a21\u578b\u63a8\u5230\u5f53\u524d\u7a97\u53e3\u5e76\u5bfc\u51fa\u5f53\u524d\u6a21\u578b\u89c6\u89d2\u53c2\u6570\u3002"

    assert _explicit_live_hotload_requested(request)
    assert _explicit_live_gui_open_requested(request)
    assert _diagnostic_export_requested_from_text(request)

def test_chinese_current_window_hotload_alias_is_recognized() -> None:
    request = "\u628a\u5b83\u63a8\u5230\u5f53\u524d\u7a97\u53e3\u5e76\u5bfc\u51fa\u89c6\u89d2\u53c2\u6570\u3002"

    assert _explicit_live_hotload_requested(request)


def test_all_view_diagnostic_export_alias_is_recognized() -> None:
    assert _diagnostic_export_requested_from_text(
        "Build silicon crystal and export all-view diagnostics and check whether the model is normal."
    )


def test_same_window_normality_and_export_alias_is_recognized() -> None:
    request = "Same-window hot-loading, export view audit, and check whether the model is normal."

    assert _explicit_live_hotload_requested(request)
    assert _explicit_live_gui_open_requested(request)
    assert _diagnostic_export_requested_from_text(request)


def test_same_window_hotload_phrase_without_materials_studio_is_recognized_for_gui_open() -> None:
    request = "same-window real-time hot-loading and export view parameters"

    assert _explicit_live_hotload_requested(request)
    assert _explicit_live_gui_open_requested(request)
    assert _diagnostic_export_requested_from_text(request)
