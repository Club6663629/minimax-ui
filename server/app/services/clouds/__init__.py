"""云平台远程开关机（多平台可扩）。

导出：CloudInstance / CloudProvider / CloudAPIError / registry
"""
from . import registry  # noqa: F401
from .base import CloudAPIError, CloudInstance, CloudProvider  # noqa: F401

__all__ = ["registry", "CloudInstance", "CloudProvider", "CloudAPIError"]
