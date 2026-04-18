"""Workflow engine registration.

Import workflow modules here to register their definitions with the engine singleton.
"""

from . import content_pipeline as _content_pipeline  # noqa: F401
from . import test_workflow as _test_workflow  # noqa: F401
from . import recon as _recon  # noqa: F401
