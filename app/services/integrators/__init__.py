from flask import current_app

from .base import CAPABILITIES, Alias, Integrator, IntegratorError, SendResult, StatusResult, XmlOptions  # noqa: F401
from .file_export import FileExportIntegrator
from .logo import LogoIntegrator
from .mock import MockIntegrator
from .nilvera import NilveraIntegrator
from .oib import EdmIntegrator, IzibizIntegrator
from .qnb import QnbEfinansIntegrator
from .sovos import SovosIntegrator
from .uyumsoft import UyumsoftIntegrator

# Order shown in Settings: local modes first, then integrators alphabetically
REGISTRY = {cls.key: cls for cls in (
    MockIntegrator, FileExportIntegrator,
    EdmIntegrator, IzibizIntegrator, LogoIntegrator, NilveraIntegrator, QnbEfinansIntegrator, SovosIntegrator,
    UyumsoftIntegrator,
)}


def get_integrator(key=None, config=None):
    from ...models import Setting

    key = key or Setting.get("integrator.name") or "mock"
    if config is None:
        config = (Setting.get("integrator.config") or {}).get(key, {})
    cls = REGISTRY.get(key, MockIntegrator)
    return cls(config=config, data_dir=current_app.config["DATA_DIR"])
