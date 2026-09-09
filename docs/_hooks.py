"""MkDocs hook: ``viur.core`` stand-ins for a docs build without viur-core.

mkdocstrings reads the package sources; ``viur.models.sqllist`` imports
``viur.core.Module``. With a real viur-core installed (the ``[overlay]``
install of ``docs.yml``) nothing is touched.
"""
from __future__ import annotations


def on_config(config, **kwargs):
    # viur.core calls google.auth.default() at import; no ADC on the docs runner.
    # (Twins: tests/conftest.py, integration/conftest.py.)
    import google.auth
    from google.auth.credentials import AnonymousCredentials

    google.auth.default = lambda *args, **kwargs: (AnonymousCredentials(), "viur-models-test")
    try:
        import viur.core.module  # noqa: F401 — real core present
    except ModuleNotFoundError:
        from viur.light_mock import install_viur_core_mocks

        install_viur_core_mocks()
    return config
