"""
viur-models — SQLModel definitions for ViUR applications.

Public surface:

- [`ViURModel`][viur.models.ViURModel] — base class of all models; provides
  the system fields (``id`` → ``key``, ``creationdate``, ``changedate``),
  the skeleton-compatible ``viur_structure()`` and the opaque key encoding.
- [`ViURField`][viur.models.ViURField] — ``sqlmodel.Field()`` wrapper that
  carries the ViUR bone parameters (``descr``, ``visible``, ``params``, …)
  as metadata.
- [`Text`][viur.models.Text] / [`Email`][viur.models.Email] /
  [`Country`][viur.models.Country] — field types that carry their bone type
  in the annotation; [`BoneType`][viur.models.BoneType] +
  [`register_bone_type`][viur.models.register_bone_type] define new ones.
- [`structure_for_model`][viur.models.structure_for_model] — the low-level
  structure builder (usable directly as a library).

Design rationale and the full mapping tables live in
``analysis/01-viurfield-und-structure-mapping.md``.

.. note::
   Importing this package has **no side effects** — it does not patch
   viur-core and does not register any models on its own.
"""
from .base import ViURModel, ViURRecord
from .client import map_validation_error
from .config import ModelsConfig, install_config
from .crossstore import (
    CrossStoreIndex,
    FileRef,
    SkeletonLink,
    SkeletonRef,
    UserRef,
    install_refresh_hooks,
    refresh_crossstore,
    refresh_for_target,
)
from .db import RecordJSON
from .fields import ALLOWED_SCHEMA_EXTRA, VIUR_META_KEY, ViURField
from .links import RelationLink
from .structure import structure_for_model
from .types import (
    BONE_TYPE_REGISTRY,
    BoneType,
    Code,
    Color,
    Country,
    Credential,
    Email,
    Json,
    Language,
    Password,
    Phone,
    Raw,
    SortIndex,
    Spatial,
    Text,
    Uid,
    Uri,
    register_bone_type,
    set_default_languages,
)

__version__ = "0.1.0"

__all__ = [
    "ALLOWED_SCHEMA_EXTRA",
    "BONE_TYPE_REGISTRY",
    "BoneType",
    "Code",
    "Color",
    "Country",
    "CrossStoreIndex",
    "Credential",
    "Email",
    "FileRef",
    "Json",
    "Language",
    "ModelsConfig",
    "Password",
    "Phone",
    "Raw",
    "RecordJSON",
    "RelationLink",
    "SkeletonLink",
    "SkeletonRef",
    "SortIndex",
    "Spatial",
    "Text",
    "Uid",
    "Uri",
    "UserRef",
    "VIUR_META_KEY",
    "ViURField",
    "ViURModel",
    "ViURRecord",
    "install_config",
    "install_refresh_hooks",
    "map_validation_error",
    "refresh_crossstore",
    "refresh_for_target",
    "register_bone_type",
    "set_default_languages",
    "structure_for_model",
    "__version__",
]
