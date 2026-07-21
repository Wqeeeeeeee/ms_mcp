"""Passive exports for the fixed 3C-SiC(001) Si-face plugin."""

from .manifest import PLUGIN_MANIFEST
from .plugin import PLUGIN, build, match, plan


def validate(model):
    """Validate a model through the passive plugin contract."""

    return PLUGIN.validate(model)


__all__ = [
    "PLUGIN",
    "PLUGIN_MANIFEST",
    "build",
    "match",
    "plan",
    "validate",
]
