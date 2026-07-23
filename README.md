# viur-models

SQLModel definitions for ViUR applications.

[![Tests](https://github.com/sprengplatz/viur-models/actions/workflows/test.yml/badge.svg)](https://github.com/sprengplatz/viur-models/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Status

**Alpha.** Implemented: `ViURField`, the bone-type field types
(`Text`, `Email`, `Country`, + `BoneType`/`register_bone_type` for your
own), the skeleton-compatible structure/dump emission, fromClient error
mapping, opaque key encoding — and the **`SQLList` module prototype**
(list/view/add/edit/delete/structure over envelope v2, hooks from
viur-actions, session-per-action). All of it is verified against the
real viur-core (and the real envelope renderer) by the integration
suite. See [analysis/](analysis/README.md) for the design and
[CHANGELOG.md](CHANGELOG.md) for the running summary.

## Requirements

- Python ≥ 3.12
- viur-core ≥ 3.8, < 4

## Install

```bash
pip install viur-models
```

## Quick taste

```python
from viur.models import Country, Email, Text, ViURField, ViURModel

class Feedback(ViURModel, table=True):        # a plain SQLModel underneath
    name: str = ViURField(descr="Name", max_length=100)
    mail: Email = ViURField(descr="E-Mail")   # bone type "str.email"
    message: Text = ViURField(default="")     # bone type "text"
    country: Country | None = ViURField(default=None)  # "select.country"

Feedback.viur_structure()   # skeleton-compatible structure dict
Feedback(id=42).viur_key    # opaque key string, like a datastore key
```

The bone type is decided by the **Python type** — `str`, `int`, `bool`,
`datetime`, `enum`/`Literal` map automatically; semantic types come from
the pydantic ecosystem (e.g. `Country` is pydantic-extra-types'
`CountryAlpha2`) or are one `Annotated` alias away:

```python
Slug = t.Annotated[str, BoneType("str.slug")]
```

The bone-by-bone mapping (skeleton declaration vs. field equivalent) is
documented in [docs/bones.md](docs/bones.md).

Serve a model like a skeleton module — same endpoints, same envelope-v2
wire format:

```python
# deploy/modules/feedback.py
from viur.models.sqllist import SQLList
from models.feedback import Feedback

class feedback(SQLList):
    model = Feedback

    def can(self, instance):     # fail-closed by default; open up per
        return True              # action via canView/canEdit/… overrides

# deploy/main.py — next to viur.actions.install():
import viur.models.db
viur.models.db.configure("postgresql+pg8000://…")   # NullPool on App Engine
```

## Development

Two test layers, run separately:

**Unit (fast, mocked) — `tests/`, 100 % coverage gate:**

```bash
git clone https://github.com/sprengplatz/viur-models
cd viur-models
pip install --no-deps -e .
pip install pytest pytest-cov 'coverage[toml]' viur-light-mock viur-actions sqlmodel pydantic-extra-types pycountry email-validator
pytest                  # 100% coverage required
```

`viur-light-mock` provides the `viur.core.*` stand-ins so these run without
the App Engine stack.

**Integration (real core) — `integration/`, no coverage gate:**

```bash
pip install "viur-core>=3.8,<4" rsa pytest viur-actions sqlmodel pydantic-extra-types pycountry email-validator
pip install --no-deps -e .
python -m pytest -c integration/pytest.ini integration
```

Runs against the **real** framework — the layer that catches
mock-vs-core drift. See [integration/README.md](integration/README.md).

> **Coverage policy.** The 100 % gate applies to the **unit** layer only.
> The integration layer runs **without** a coverage requirement — a coverage
> target there would pressure mocking the very framework it exists to exercise.

## License

MIT — see [LICENSE](LICENSE).
