"""Test typing annotations and runtime imports across meshcore_tray."""

import importlib
import inspect
import pkgutil
import typing
import pytest
import meshcore_tray


def test_models_type_hints():
    import meshcore_tray.core.models as models
    for name, obj in inspect.getmembers(models):
        if inspect.isclass(obj) and obj.__module__ == models.__name__:
            hints = typing.get_type_hints(obj)
            assert isinstance(hints, dict)


def test_storage_type_hints():
    import meshcore_tray.storage as storage
    hints = typing.get_type_hints(storage.Storage)
    assert isinstance(hints, dict)


def test_settings_widget_imports():
    from meshcore_tray.ui.settings_widget import ColorPickerButton
    hints = typing.get_type_hints(ColorPickerButton)
    assert isinstance(hints, dict)


def test_sidebar_json_import():
    import meshcore_tray.ui.sidebar as sidebar
    assert hasattr(sidebar, "json")
