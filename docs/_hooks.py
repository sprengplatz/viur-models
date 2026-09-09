"""MkDocs hook: ``viur.core`` stand-ins for a docs build without viur-core.

mkdocstrings reads the package sources; ``viur.models.sqllist`` imports
``viur.core.Module``. With a real viur-core installed (the ``[overlay]``
install of ``docs.yml``) nothing is touched.
"""
from __future__ import annotations


def on_config(config, **kwargs):
    try:
        import viur.core.module  # noqa: F401 — real core present
    except ModuleNotFoundError:
        from viur.light_mock import install_viur_core_mocks

        install_viur_core_mocks()
    return config
