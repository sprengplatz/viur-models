# viur-models

SQLModel definitions for ViUR applications.

## What you get

- **One shared model layer** — SQLModel definitions and model helpers
  that multiple ViUR projects can depend on instead of copying
  definitions around.
- **Side-effect-free import** — importing `viur.models` does not patch
  viur-core and does not register any models on its own.
- **Two test layers** — a fast unit suite against
  [viur-light-mock](https://github.com/sprengplatz/viur-light-mock) with a
  100 % coverage gate, plus an integration suite against the real
  viur-core.

## Quick taste

```python
import viur.models   # no side effects on import

print(viur.models.__version__)
```

## Bone reference

The [bone reference](bones.md) shows, for every viur-core bone, the
skeleton declaration next to its ViURModel field equivalent.

## Status

Alpha; see the [changelog](changelog.md) for what is implemented.
