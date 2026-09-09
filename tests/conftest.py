"""Unit-suite environment fixups.

The suite runs on ``spltz-viur-light-mock``'s wholesale ``viur.core``
stand-in. The mock's error stub lags the real core: the published 0.3.0
carries only ``Unauthorized``/``Forbidden``/``BadRequest``/``NotFound``,
while viur-models raises ``NotImplemented`` (unknown structure action) and
``NotAcceptable`` (envelope-v2 enforcement) — both guaranteed by every
supported real viur-core.

Rather than coupling the suite to mock release timing, the missing classes
are completed here, derived from the mock's own error base so ``except``
clauses over that base keep catching them. A mock version that ships them
makes this a no-op.

The completion runs as a session-autouse **fixture**, not at import time:
the mock's ``pytest_configure`` re-installs fresh module objects and
explicitly resets earlier monkeypatching — a fixture runs after that, and
the already-collected test modules hold references to the same (final)
module object, so the ``setattr`` reaches them.
"""
import os
import pathlib
import re
import types

import pytest

# Must happen BEFORE anything imports viur.core — in overlay mode (a real
# viur-core installed) ``viur.core.logging`` decides at IMPORT time, from
# ``conf.instance.is_dev_server`` (i.e. ``$GAE_ENV``), whether to plug in the
# Google Cloud Logging handler. On a test machine that handler's background
# thread tries to ship records to GCP, fails without credentials, and raises
# there — which ``filterwarnings = ["error"]`` turns into a
# PytestUnraisableExceptionWarning charged to whichever test happens to be
# running, so the failure moves around between runs. A test run IS a dev
# server; ``setdefault`` leaves an explicitly set GAE_ENV alone.
os.environ.setdefault("GAE_ENV", "localdev")


def complete_errors_module(errors: types.ModuleType) -> list[str]:
    """Add error classes viur-core guarantees but the mock may lack.

    :returns: The names that were added (empty when the mock is complete).
    """
    reference = errors.Forbidden  # always present — its base is the mock's own
    base = reference.__mro__[1]
    added = []
    for name in ("NotImplemented", "NotAcceptable"):
        if not hasattr(errors, name):
            setattr(errors, name, type(name, (base,), {}))
            added.append(name)
    return added


@pytest.fixture(scope="session", autouse=True)
def _complete_mock_errors():
    from viur.core import errors

    complete_errors_module(errors)
    yield


@pytest.fixture(autouse=True)
def _viur_request_context():
    """A request context for every test — required in **overlay** mode.

    viur-light-mock picks its mode from the environment: with no viur-core
    installed it injects stand-ins whose ``@skey``/``@force_ssl`` are identity
    decorators, so a module action can be called directly. With a real
    viur-core present it steps aside and the genuine decorators run — and
    ``@skey`` reads ``current.request.get()``, which is ``None`` for an unset
    ContextVar, so every action would raise ``AttributeError`` before reaching
    the code under test.

    ``skey_checked=True`` takes viur-core's own "only required once per
    request" path: the unit suite exercises the actions, not the security-key
    machinery (which needs a datastore), so the ``skey=…`` values the tests
    pass stay unvalidated — exactly as in stand-in mode. A test that needs a
    different request simply calls ``current.request.set(…)`` itself; its value
    wins for the duration of the test body.

    Harmless in stand-in mode, where nothing reads the request.
    """
    from viur.core import current

    previous = current.request.get()
    current.request.set(types.SimpleNamespace(skey_checked=True, isPostRequest=True))
    try:
        yield
    finally:
        current.request.set(previous)


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
# (Twin of the guard in integration/conftest.py.)

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
