"""``install_config`` against the REAL viur-core conf (strict mode).

The unit suite exercises the presets against viur-light-mock's conf stub;
here we prove the namespace attach works on core's actual ``Conf`` object,
which rejects unknown *reads* in strict mode but must accept plugin
namespaces set as attributes (the ``conf.actions`` idiom).
"""
from viur.core import conf

from viur.models import ModelsConfig, install_config
from viur.models import db


def test_install_config_attaches_to_real_conf():
    cfg = install_config()
    try:
        assert isinstance(cfg, ModelsConfig)
        assert conf.models is cfg
        # idempotent: values set between calls survive
        cfg.databases["default"] = {"engine": "memory"}
        assert install_config() is cfg
        assert conf.models.databases["default"] == {"engine": "memory"}
    finally:
        delattr(conf, "models")


def test_memory_preset_builds_engine_on_real_conf():
    cfg = install_config()
    try:
        cfg.databases["default"] = {"engine": "memory"}
        engine = db.configure_from_conf()
        assert engine.url.render_as_string() == "sqlite://"
    finally:
        delattr(conf, "models")
        db.reset()
