from flask import current_app

from .base import Alias, Integrator, IntegratorError, SendResult, StatusResult  # noqa: F401
from .file_export import FileExportIntegrator
from .mock import MockIntegrator
from .nilvera import NilveraIntegrator

REGISTRY = {cls.key: cls for cls in (MockIntegrator, FileExportIntegrator, NilveraIntegrator)}


def get_integrator(key=None, config=None):
    from ...models import Setting

    key = key or Setting.get("integrator.name") or "mock"
    if config is None:
        config = (Setting.get("integrator.config") or {}).get(key, {})
    cls = REGISTRY.get(key, MockIntegrator)
    return cls(config=config, data_dir=current_app.config["DATA_DIR"])
