"""项目 API 路由。"""

from api.v1.projects import views

router = views.router

__all__ = ["router"]
