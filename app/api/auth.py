#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/api/auth.py —— 访问密码：状态查询、登录、登出、改密/关密
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import auth
from ..models import AuthLoginIn, AuthPasswordIn, AuthStatusOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _status(request: Request) -> AuthStatusOut:
    """当前访问密码状态，含「这个请求带的会话是否有效」。

    authed 走 auth.authenticated()：没配密码时它一律 True（open 语义下前端也当
    已授权处理），配了密码时它校验 cookie 签名 + 过期 + 密码版本号。
    """
    return AuthStatusOut(enabled=auth.is_enabled(),
                         envLocked=auth.env_locked(),
                         authed=auth.authenticated(
                             request.cookies.get(auth.COOKIE_NAME)))


@router.get("/status", response_model=AuthStatusOut)
def status(request: Request) -> AuthStatusOut:
    """见上方 _status。前端用它判断要不要弹登录框。"""
    return _status(request)


@router.post("/login", response_model=None)
def login(payload: AuthLoginIn, response: Response) -> dict:
    """
    登录。校验通过后写入会话 cookie。

    未配置密码时不要求密码，但仍会发放一个会话 —— 这样前端逻辑统一，
    不必知道「要不要密码」。
    """
    if auth.is_enabled() and not auth.verify_password(payload.password):
        raise HTTPException(status_code=401, detail="访问密码不正确")
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=auth.issue_token(),
        # 会话 cookie：关了浏览器就失效，不落盘
        httponly=True,
        samesite="lax",
        max_age=auth.SESSION_TTL,
    )
    return {"ok": True}


@router.post("/logout", response_model=None)
def logout(response: Response) -> dict:
    """登出：清掉会话 cookie。"""
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@router.post("/password", response_model=AuthStatusOut)
def change_password(payload: AuthPasswordIn, response: Response,
                    request: Request) -> AuthStatusOut:
    """
    设置 / 修改 / 关闭访问密码。

    * current 只有在「已经配置了密码」时才必须正确；首次设置可留空。
    * new_password 传空串 = 关闭访问密码。
    * 密码由环境变量控制时（envLocked），这里会抛 403。
    """
    if auth.env_locked():
        raise HTTPException(
            status_code=403,
            detail="访问密码由环境变量 VS_ACCESS_PASSWORD 控制，网页上不能改。"
                   "要修改请改部署的 environment 后重建容器。")

    # 已经配了密码，必须先核验当前密码
    if auth.password_source() in ("web", "env"):
        if not auth.verify_password(payload.current):
            raise HTTPException(status_code=401, detail="当前密码不正确")

    auth.set_password(payload.new_password)

    # 改密后强制重新登录：清掉会话 cookie，前端据此跳回登录页。
    # 密码是访问凭据，更新后旧会话不应继续有效。
    response.delete_cookie(auth.COOKIE_NAME)
    return _status(request)


def require_auth(request: Request) -> None:
    """
    保护性依赖：所有需要登录的 API 路由都加上它。
    为最小侵入，这个依赖没被全局挂载 —— 没启用密码时它天然放行，
    只有启用了密码才要求会话，见 auth.authenticated()。
    """
    if not auth.authenticated(request.cookies.get(auth.COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="需要先登录")


# 登录/登出/状态/改密自身不要求登录，只有其它 API 路由依赖 require_auth。