"""Smoke tests for the public package surface.

The unit suite runs against viur-light-mock's ``viur.core`` stand-ins
(see pyproject's test extra) — no App Engine stack required.
"""
import re

import viur.models


def test_version_is_semver():
    assert re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", viur.models.__version__
    )


def test_version_is_exported():
    assert "__version__" in viur.models.__all__


def test_conftest_completes_a_lagging_errors_stub():
    """The published mock (0.3.0) lacks NotImplemented and NotAcceptable —
    the conftest shim adds them, derived from the mock's own error base so
    existing except-clauses keep catching them. No-op on a complete stub."""
    import types

    from tests.conftest import complete_errors_module
    from viur.core import errors as real_mock

    bare = types.ModuleType("errors")
    bare.Forbidden = real_mock.Forbidden

    added = complete_errors_module(bare)

    assert added == ["NotImplemented", "NotAcceptable"]
    assert issubclass(bare.NotAcceptable, real_mock.Forbidden.__mro__[1])
    assert complete_errors_module(bare) == []  # second run: nothing to add
