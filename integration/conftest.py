"""Integration-suite bootstrap — runs against a **real** viur-core.

Unlike the unit suite (``tests/``, which runs against viur-light-mock's
wholesale ``viur.core`` stand-in), this suite imports the real framework.
It must therefore run in an environment where viur-core is installed and
viur-light-mock's pytest plugin is **not** active (otherwise the mock would
shadow ``viur.core``). See integration/README.md.
"""
import os
import pathlib
import re
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))

# Make ``import skeletons`` resolve to integration/skeletons/ irrespective of
# the working directory pytest was launched from.
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Fail fast (with a clear message) if this suite is run under viur-light-mock's
# wholesale stand-in instead of the real core — e.g. invoked without
# `-c integration/pytest.ini`, so the pytest11 plugin shadowed viur.core.
try:
    import viur.core.module  # only the real core ships this
except ModuleNotFoundError:  # pragma: no cover - misconfiguration guard
    import pytest
    pytest.exit(
        "integration/ needs the REAL viur-core (viur.core.module missing — is "
        "viur-light-mock shadowing it?). Run: python -m pytest -c "
        "integration/pytest.ini integration",
        returncode=4,
    )

from viur.core import conf  # noqa: E402 — after sys.path tweak, before skel import

# viur-core only accepts skeletons whose file path (with the project/core base
# stripped) starts with an entry in ``conf.skeleton_search_path`` — the default
# includes ``/skeletons/``. Pointing ``project_base_path`` at this directory
# makes integration/skeletons/*.py strip to ``/skeletons/*.py`` and match,
# regardless of the launch directory. Set before any test imports a skeleton.
conf.instance.project_base_path = pathlib.Path(_HERE)


# --------------------------------------------------------------------------- #
# Guard: one table name per suite                                             #
# --------------------------------------------------------------------------- #
# Every test model lands in the single, process-wide ``SQLModel.metadata``. A
# second class with the same ``__tablename__`` does not fail with a readable
# message: SQLAlchemy raises inside the class statement, pytest drops the
# half-imported module from ``sys.modules``, the next module importing it
# re-executes it — and the FIRST error that surfaces is "Table … already
# defined" in a file that has nothing to do with the duplicate. Checking the
# source text before collection turns that cascade into one clear sentence.
# (Twin of the guard in tests/conftest.py.)

_TABLENAME = re.compile(r"""^\s*__tablename__\s*=\s*["']([^"']+)["']""")


def duplicate_tablenames(directory: pathlib.Path) -> dict[str, list[str]]:
    """``__tablename__`` literals declared more than once across a suite's
    modules -> ``{name: ["file:line", …]}``."""
    seen: dict[str, list[str]] = {}
    for path in sorted(directory.glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if match := _TABLENAME.match(line):
                seen.setdefault(match.group(1), []).append(f"{path.name}:{number}")
    return {name: places for name, places in seen.items() if len(places) > 1}


def pytest_sessionstart(session: pytest.Session) -> None:
    duplicates = duplicate_tablenames(pathlib.Path(__file__).parent)
    if duplicates:
        pytest.exit(
            "duplicate __tablename__ across this suite's test models — every "
            "model shares SQLModel.metadata, so the second definition breaks "
            "collection of unrelated files:\n"
            + "\n".join(f"  {name}: {', '.join(places)}" for name, places in duplicates.items()),
            returncode=4,
        )
