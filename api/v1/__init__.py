# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : __init__.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description :

from fastapi import APIRouter

from api.v1.auth import views as auth_views
from api.v1.chat import views as chat_views
from api.v1.sessions import views as session_views
from api.v1.projects import views as project_views
from api.v1.admin import views as admin_views
from api.v1.admin import reports as report_views
from api.v1.web import views as web_views
from api.v1.mcp import views as mcp_views
from api.v1.skills import views as skill_views
from api.v1.persona import views as persona_views
from api.v1 import models as model_views
from api.v1 import system as system_views

api_v1 = APIRouter()

api_v1.include_router(auth_views.router)
api_v1.include_router(chat_views.router)
api_v1.include_router(session_views.router)
api_v1.include_router(project_views.router)
api_v1.include_router(model_views.router)
api_v1.include_router(admin_views.router)
api_v1.include_router(report_views.router)
api_v1.include_router(web_views.router)
api_v1.include_router(mcp_views.router)
api_v1.include_router(skill_views.router)
api_v1.include_router(persona_views.router)
api_v1.include_router(system_views.router)
