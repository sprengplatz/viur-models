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
