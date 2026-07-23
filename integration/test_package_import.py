"""Smoke test: the package imports cleanly next to the real viur-core."""
import viur.core
import viur.models


def test_package_imports_alongside_real_core():
    assert viur.models.__version__
    # Both live in the ``viur`` namespace package — importing one must not
    # shadow the other.
    assert viur.core.__name__ == "viur.core"
    assert viur.models.__name__ == "viur.models"
