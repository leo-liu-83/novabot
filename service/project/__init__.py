"""项目（Project）：把磁盘上一个已有目录登记为工作根。"""

from .errors import ProjectError
from .project_service import ProjectService, project_service

__all__ = ["ProjectError", "ProjectService", "project_service"]
