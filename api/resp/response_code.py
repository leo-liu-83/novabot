# !/usr/bin/env python3
# -*- encoding : utf-8 -*-
# @Filename    : response_code.py
# @Software    : VSCode
# @Datetime    : 2026/09/29 10:00:00
# @Author      : leo liu
# @Version     : 1.0
# @Description : 返回值封装

from typing import Any, Optional, Union

from fastapi import status
from fastapi.responses import JSONResponse, StreamingResponse

def _resp(status_code: int, data, message: str, *, error: Optional[str]=None,
          vars: Optional[dict]=None) -> JSONResponse:
    content: dict = {
        "code": status_code,
        "message": message,
        "data": data
    }
    # 固定错误提示带稳定 code，前端按 code 翻译；原始异常文本不带 error，保持原样
    if error:
        content["error"] = error
    if vars:
        content["vars"] = vars

    return JSONResponse(status_code=status_code, content=content)

def resp_200(data: Optional[Union[list, dict, str]]=None, *, message: str="Success") -> JSONResponse:
    return _resp(status.HTTP_200_OK, data, message)

def resp_400(data: Optional[Union[list, dict, str]]=None, *, message: str="Bad Request",
             error: Optional[str]=None, vars: Optional[dict]=None) -> JSONResponse:
    return _resp(status.HTTP_400_BAD_REQUEST, data, message, error=error, vars=vars)

def resp_401(data: Optional[Union[list, dict, str]]=None, *, message: str="Unauthorized",
             error: Optional[str]=None, vars: Optional[dict]=None) -> JSONResponse:
    return _resp(status.HTTP_401_UNAUTHORIZED, data, message, error=error, vars=vars)

def resp_403(data: Optional[Union[list, dict, str]]=None, *, message: str="Forbidden",
             error: Optional[str]=None, vars: Optional[dict]=None) -> JSONResponse:
    return _resp(status.HTTP_403_FORBIDDEN, data, message, error=error, vars=vars)

def resp_500(data: Optional[Union[list, dict, str]]=None, *, message: str="Server Internal Error",
             error: Optional[str]=None, vars: Optional[dict]=None) -> JSONResponse:
    return _resp(status.HTTP_500_INTERNAL_SERVER_ERROR, data, message, error=error, vars=vars)

def resp_200_streaming(stream_generator) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    return StreamingResponse(
        status_code=status.HTTP_200_OK,
        content=stream_generator,
        media_type="text/event-stream",
        headers=headers
    )
