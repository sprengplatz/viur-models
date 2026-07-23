"""MkDocs hooks for viur-models.

mkdocstrings imports the package's source modules to read their docstrings —
and model modules import ``viur.core`` (skeletons, bones). Without the real
viur-core installed (which would drag in the entire App Engine stack), the
import fails.

The hook installs the lightweight ``viur.light_mock`` stand-ins before any
documentation source is parsed, so the docs build behaves like the unit-test
runs. (Importing the package itself has no side effects.)
"""
from __future__ import annotations


def on_config(config, **kwargs):
    """Install viur-core stand-ins before mkdocstrings touches the source."""
    from viur.light_mock import install_viur_core_mocks

    install_viur_core_mocks()
    return config
