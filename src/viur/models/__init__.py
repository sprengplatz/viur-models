"""viur-models — SQLModel-backed models for ViUR.

``Model``/``Record`` (base classes), ``Field`` (bone metadata), the bone-typed
field types, ``structure_for_model``, and the boot wiring ``install``/``setup``.
Importing has no side effects. Bone mapping tables: ``docs/bones.md``.
"""
from .base import Model, Record
from .boot import install, setup
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
from .fields import ALLOWED_SCHEMA_EXTRA, VIUR_META_KEY, Field
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
    "Credential",
    "CrossStoreIndex",
    "Email",
    "Field",
    "FileRef",
    "Json",
    "Language",
    "Model",
    "ModelsConfig",
    "Password",
    "Phone",
    "Raw",
    "Record",
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
    "install",
    "install_config",
    "install_refresh_hooks",
    "map_validation_error",
    "refresh_crossstore",
    "refresh_for_target",
    "register_bone_type",
    "set_default_languages",
    "setup",
    "structure_for_model",
    "__version__",
]
