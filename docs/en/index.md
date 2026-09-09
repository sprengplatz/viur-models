# viur-models

SQLModel definitions for ViUR applications.

## What you get

- **One shared model layer** — SQLModel definitions and model helpers
  that multiple ViUR projects can depend on instead of copying
  definitions around.
- **Side-effect-free import** — importing `viur.models` does not patch
  viur-core and does not register any models on its own.
- **Two test layers** — a unit suite in
  [viur-light-mock](https://github.com/sprengplatz/viur-light-mock)'s overlay
  mode (real viur-core, in-memory datastore) with a 100 % coverage gate, plus
  an integration suite against the real skeleton registry.

## Bone reference

The [bone reference](bones.md) shows, for every viur-core bone, the
skeleton declaration next to its Model field equivalent. The
[bone overview](bones-overview.md) is the compact version of the same.

## Status

Alpha; see the [changelog](changelog.md) for what is implemented.
