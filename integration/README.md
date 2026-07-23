# Integration suite

These tests run against a **real, installed viur-core** — the opposite of the
unit suite under [`../tests/`](../tests), which runs against viur-light-mock's
wholesale `viur.core` stand-in. They validate the package against the actual
framework, catching mock-vs-core drift the unit suite structurally cannot see.

## Why separate

viur-light-mock registers a `pytest11` plugin that replaces `viur.core.*` in
`sys.modules`. If it is active it shadows the real core — so this suite must run
in an environment where **viur-light-mock is not installed** (or its plugin is
disabled), and with its **own** pytest config (no coverage gate, warnings not
promoted to errors — the appengine stack emits `SyntaxWarning`s on import).

## Running locally

```bash
python -m venv .venv-int && . .venv-int/bin/activate
pip install "viur-core>=3.8,<4" rsa pytest sqlmodel pydantic-extra-types pycountry
pip install --no-deps -e .          # viur-models itself
python -m pytest -c integration/pytest.ini integration
```

`rsa` is a transitive dependency of the appengine testbed that viur-core
imports at load time but does not pin.

If the environment has viur-light-mock installed anyway (e.g. a shared
project venv), disable its plugin explicitly — otherwise it shadows the
real core and the conftest guard aborts the run:

```bash
python -m pytest -p no:viur_light_mock -c integration/pytest.ini integration
```

## What is covered

- **`test_package_import.py`** — the `viur` namespace package composes
  correctly: `viur.models` imports side-effect-free next to the real
  `viur.core` without shadowing it.
- **`test_structure_parity.py`** — the core promise: a ViURModel and its
  skeleton twin (`skeletons.ParityRefSkel`) emit the **same structure**,
  field by field (str, str.email, numeric, bool, date, select) and for the
  system bones (`key`, `creationdate`, `changedate`), compared against the
  real bones' output. `sortindex` is excluded (absolute positions differ
  because the skeleton counts its own system bones).
- **`test_client_parity.py`** — the same twin pair compared on the value
  side: `viur_dump()` vs. `skel.dump()` and `viur_from_client()` errors vs.
  `skel.fromClient()`/`skel.errors` by `(fieldPath, severity)` — pinned the
  real behavior that unsubmitted required fields are `NotSet`, not `Empty`.
