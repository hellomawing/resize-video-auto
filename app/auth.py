#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/auth.py —— 访问密码与会话

一个**访问密码**（不区分用户名，单密码控制整个网页），两种配置来源：

  1. 环境变量 VS_ACCESS_PASSWORD（docker compose 的 environment）
  2. 网页「设置 → 访问密码」里填的密码（持久化到 data/access.json）

优先级：环境变量 > 网页设置。有环境变量时网页上改不了、也退不了（envLocked），
要改只能改部署的环境变量——避免「网页改了但部署层还压着一个旧密码」这种
谁说了算的混乱。

没配任何密码时，整个网页**无需密码即可访问**（历史行为）。如果这台 NAS 暴露在
公网上，建议配置，否则同网段/公网任何人都能浏览、改动你的文件。

密码从不以明文存储：
  * 环境变量来源直接与 VS_ACCESS_PASSWORD 比对，不落盘；
  * 网页来源存的是「盐 + PBKDF2 摘要」，access.json 里只有 hash 没有明文。
      所以它跟 settings.json 分开存——settings 会通过「导出配置」打包带走，
      密码不该跟着导出。

会话：登录成功发放一个带签名的 cookie（HMAC-SHA256），携带过期时间。
签名密钥也持久化在 access.json，这样容器重启不会把本机的登录全部踢掉。

安全边界：本模块只解决「网页访问的密码」。挂载目录的读写权限仍由 Docker
挂载决定（见 app/api/system.py），两者是正交的。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from . import config

# ------------------------------------------------------------------ 常量

# 环境变量名：docker compose 的 environment 里配。优先级高于网页设置。
ENV_PASSWORD = "VS_ACCESS_PASSWORD"
# 存放网页设置的密码摘要 + 会话签名密钥
ACCESS_FILE = config.DATA_DIR / "access.json"
# cookie 名
COOKIE_NAME = "vs_session"
# 会话有效期（秒）：一次登录，浏览器端 43200 秒（12 小时）不活动就失效
SESSION_TTL = 43200

# PBKDF2 参数：200k 次迭代，对 NAS 上的单机应用足够，不强求更高
_PBKDF2_ITER = 200_000
_HASH_LEN = 32
_SALT_LEN = 16


class AuthLockedByEnv(Exception):
    """密码由环境变量控制时，网页想改/退会抛这个。"""


# ------------------------------------------------------------------ 底层读写

def _load() -> dict:
    """读 access.json；文件不存在或损坏返回空 dict。"""
    try:
        with open(ACCESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _store(partial: dict) -> None:
    """合入并写回 access.json（原子写）。"""
    data = _load()
    data.update(partial)
    config.ensure_dirs()
    tmp = ACCESS_FILE.with_suffix(ACCESS_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ACCESS_FILE)


# ------------------------------------------------------------------ 密码

def env_password() -> str | None:
    """环境变量里的密码。未设置返回 None。"""
    val = os.environ.get(ENV_PASSWORD, "")
    return val if val else None


def _web_hash() -> str | None:
    """网页设置里存的密码摘要。未设置返回 None。"""
    return _load().get("hash")


def password_source() -> str | None:
    """当前生效的密码来源：'env' | 'web' | None（无密码）。"""
    if env_password() is not None:
        return "env"
    if _web_hash():
        return "web"
    return None


def is_enabled() -> bool:
    """是否配置了访问密码。"""
    return password_source() is not None


def sync_from_env() -> None:
    """服务启动时调用：让环境变量密码成为网页密码的权威来源。

    除了「校验时环境变量优先」之外，这里还把环境变量密码的摘要写进 access.json，
    覆盖掉网页里可能配过的旧密码。这样就有了一个可靠的复位手段：
    忘记网页密码时，在 docker compose 里配一个 VS_ACCESS_PASSWORD，重启容器，
    网页密码就被重置成这个环境变量值；再想从网页改回别的密码，
    把环境变量去掉重启即可（届时网页可正常改密）。

    只在密码确实变了时才写盘（幂等，避免每次启动都重写文件）。
    """
    env_val = env_password()
    if env_val is None:
        return
    data = _load()
    salt_b = None
    try:
        salt_b = base64.b64decode(data.get("salt")) if data.get("salt") else None
    except (ValueError, TypeError):
        salt_b = None
    if not salt_b or len(salt_b) != _SALT_LEN:
        salt_b = secrets.token_bytes(_SALT_LEN)
    h_encoded = base64.b64encode(_pbkdf2(env_val, salt_b)).decode()
    if data.get("hash") != h_encoded:
        _store({
            "hash": h_encoded,
            "salt": base64.b64encode(salt_b).decode(),
            "updatedAt": time.time(),
            "pwVer": int(data.get("pwVer", 0)) + 1,  # 密码被环境变量覆盖，旧会话失效
        })


def env_locked() -> bool:
    """密码是否由环境变量锁定（网页不能改/退）。"""
    return env_password() is not None


def verify_password(plain: str) -> bool:
    """校验密码。未配置任何密码时返回 False。"""
    if not plain:
        return False
    env = env_password()
    if env is not None:
        return hmac.compare_digest(plain, env)
    h = _web_hash()
    if not h:
        return False
    data = _load()
    salt = data.get("salt")
    if not salt:
        return False
    try:
        stored = base64.b64decode(h)
        salt_b = base64.b64decode(salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(
        _pbkdf2(plain, salt_b), stored)


def _pbkdf2(plain: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"),
                               salt, _PBKDF2_ITER, dklen=_HASH_LEN)


def set_password(plain: str) -> None:
    """设置/更新网页密码。空串 = 关闭密码。被环境变量锁定则抛异常。"""
    if env_locked():
        raise AuthLockedByEnv(
            "访问密码由环境变量 VS_ACCESS_PASSWORD 控制，网页上不能修改。")
    plain = (plain or "").strip()
    if not plain:
        clear_password()
        return
    salt = secrets.token_bytes(_SALT_LEN)
    h = _pbkdf2(plain, salt)
    _store({
        "hash": base64.b64encode(h).decode(),
        "salt": base64.b64encode(salt).decode(),
        "updatedAt": time.time(),
        "pwVer": int(_load().get("pwVer", 0)) + 1,  # 改密使旧会话全部失效
    })


def clear_password() -> None:
    """清掉网页密码（= 关闭访问密码）。被环境变量锁定则抛异常。"""
    if env_locked():
        raise AuthLockedByEnv(
            "访问密码由环境变量 VS_ACCESS_PASSWORD 控制，不能通过网页关闭。")
    # 直接写一份「没有密码字段」的文件：保留会话签名密钥（secret），
    # 丢掉 hash/salt。这里刻意不走 _store 的合并逻辑——合并只增不删，
    # 旧的 hash 会残留，等于没关掉密码。
    data = _load()
    data.pop("hash", None)
    data.pop("salt", None)
    data["updatedAt"] = time.time()
    data["pwVer"] = int(data.get("pwVer", 0)) + 1  # 关密使旧会话全部失效
    config.ensure_dirs()
    tmp = ACCESS_FILE.with_suffix(ACCESS_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ACCESS_FILE)


# ------------------------------------------------------------------ 会话

def _secret() -> bytes:
    """会话签名密钥。首次使用时生成并落盘，此后保持不变（重启不踢登录）。"""
    s = _load().get("secret")
    if not s:
        s = secrets.token_hex(32)
        _store({"secret": s})
        s = _load().get("secret")
    return s.encode() if s else b""


def _token_version() -> int:
    """当前密码版本号。改密/关密/环境变量覆盖时递增，让旧会话全部失效。"""
    return int(_load().get("pwVer", 0))


def issue_token() -> str:
    """签发一个带过期时间的签名 token（用于 cookie）。"""
    exp = int(time.time()) + SESSION_TTL
    ver = _token_version()
    payload = f"{exp}.{ver}".encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(payload + b"." + sig).decode().rstrip("=")
    return token


def verify_token(token: str | None) -> bool:
    """校验 token：签名正确、未过期，且密码版本号与当前一致（改密后旧会话失效）。"""
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        # payload 里也含点号（过期时间.版本号），签名是无点号的 urlsafe base64，
        # 所以从最后一个点断开、把最后一段当签名。
        payload, _, sig = raw.rpartition(b".")
    except (ValueError, TypeError):
        return False
    expect = hmac.new(_secret(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        return False
    try:
        exp_str, _, ver_str = payload.decode().partition(".")
        exp = int(exp_str)
        ver = int(ver_str) if ver_str else 0
    except (ValueError, TypeError):
        return False
    if ver != _token_version():
        return False  # 密码已在签发后变动，此会话作废
    return exp > int(time.time())


def authenticated(cookie_value: str | None) -> bool:
    """请求是否已通过鉴权。未启用密码时一律放行。"""
    if not is_enabled():
        return True
    return verify_token(cookie_value)