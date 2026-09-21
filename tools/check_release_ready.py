#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开源 / 发布前自检：确认没有本机私密信息会跟着仓库一起出去。

用法：
    python tools/check_release_ready.py

只读不写。退出码 0 = 可以发布；1 = 有硬性问题，先别发。
每项的处置办法见根目录 OPEN-SOURCE-CHECKLIST.md。

为什么需要它：这个仓库在作者本机是「能一条命令直接部署」的状态，
密码与运行数据都在本地文件里。靠人记住「发布前删掉」是不可靠的，
所以把判断写成可执行的检查，交给谁发布都一样能跑。

检查分两档：
  FAIL —— 密钥被 git 跟踪 / 进过历史，这会**永久泄露**，必须清掉才能发
  WARN —— 本机指纹（内网 IP、Windows 用户名）被跟踪，属于「该换成占位符」，
          不拦发布，但会逐条列出来让人过一眼
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 绝不允许被 git 跟踪的文件：第一个是明文密码，其余是本机配置
FORBIDDEN_FILES = (
    "deploy/.nas-credentials",
    ".nas-credentials",
    "deploy/.env",
)

#: 绝不允许被跟踪的目录（本机运行数据 / 临时产物）
FORBIDDEN_DIRS = (
    "data/",
    ".workbuddy/",
    ".tmp-deploy/",
    ".tmp-test/",
    ".tmp-verify/",
    "web/dist/",
)

#: 明文密码的写法：NAS_PASS=<真值> 之类。值以 < 开头视为占位符，放行。
PASS_RE = re.compile(r"NAS_PASS\s*=\s*['\"]?([^\s'\"]+)")
PASS_ALLOW = {"", "xxx", "***", "yourpass", "password"}

#: 本机指纹。命中只警告，因为举例和真实环境长得一样，得人来看。
#: 注意这里走的是 POSIX ERE（git grep -E），**不支持 \d**，别写成 \d。
#: 用捕获组单独把「变量部分」抠出来，好跟占位符白名单比对——
#: 不然 `192.168.1.10`、`C:\Users\me` 这种标准占位写法也会被自己举报一遍。
FINGERPRINT_PATTERNS = (
    re.compile(r"\b(192\.168\.[0-9]{1,3}\.[0-9]{1,3})\b"),
    re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}([A-Za-z0-9_.-]+)"),
    re.compile(r"/(?:Users|home)/([A-Za-z0-9_.-]+)/"),
)

#: 公认的占位值，出现这些不算泄露
PLACEHOLDER_IPS = {"192.168.1.10", "192.168.0.1", "192.168.1.1", "10.0.0.1",
                   "127.0.0.1", "0.0.0.0"}
PLACEHOLDER_USERS = {"me", "you", "user", "username", "yourname", "youruser",
                     "name", "example", "someone"}

#: 讲这件事的文件本身会写占位符，不必自我举报
FINGERPRINT_SKIP = {
    "OPEN-SOURCE-CHECKLIST.md",
    "tools/check_release_ready.py",
}

failures: list[str] = []
warnings: list[str] = []


def git(*args: str) -> str:
    """跑一条 git 命令，返回 stdout；非 0 退出或无 git 时返回空串。"""
    try:
        p = subprocess.run(("git",) + args, cwd=str(ROOT),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return p.stdout if p.returncode == 0 else ""


def tracked() -> list[str]:
    return [f.strip() for f in git("ls-files").splitlines() if f.strip()]


def grep(pattern: str) -> list[str]:
    """在被跟踪的文件里搜内容，返回 ``文件:行号:内容`` 列表。"""
    out = git("grep", "-nIE", pattern, "--", ".")
    return [ln for ln in out.splitlines() if ln.strip()]


# --------------------------------------------------------------- 各项检查

def check_tracked_sensitive(files: list[str]) -> None:
    """密钥文件有没有被 git 跟踪。"""
    hits = [f for f in files if f in FORBIDDEN_FILES]
    hits += [f for f in files if f.startswith(FORBIDDEN_DIRS)]
    if hits:
        failures.append(
            "这些本机文件被 git 跟踪了（发布出去=永久泄露）：\n      "
            + "\n      ".join(sorted(hits)[:20])
            + "\n      处理：git rm --cached 它们，并确认 .gitignore 已覆盖")
    else:
        print("  OK   没有本机私密文件被跟踪")


def check_history() -> None:
    """这些文件有没有进过 git 历史（进过就删不掉了）。"""
    hits = []
    for path in FORBIDDEN_FILES:
        if git("log", "--all", "--format=%h", "--", path).strip():
            hits.append(path)
    if hits:
        failures.append(
            "这些文件在 git 历史里出现过（%s）：.gitignore 不追溯历史。\n"
            "      处理：用 git filter-repo 清历史，并且**必须同时轮换 NAS 密码**——\n"
            "      历史里那份可能已经被 clone 或备份过。"
            % "、".join(hits))
    else:
        print("  OK   git 历史里没有这些文件")


def check_ignored() -> None:
    """密钥文件当前是否真的被忽略（而不是碰巧还没被 add）。"""
    path = "deploy/.nas-credentials"
    if not (ROOT / path).is_file():
        print("  OK   %s 已不存在（已删除）" % path)
        return
    ignored = git("check-ignore", path).strip()
    untracked = git("ls-files", "--others", "--exclude-standard", "--", path).strip()
    if ignored and not untracked:
        print("  OK   %s 存在，但被 .gitignore 排除" % path)
        print("       （发布前记得删掉它，见 OPEN-SOURCE-CHECKLIST.md 第 1 节）")
    else:
        failures.append(
            "%s 存在且**没有被忽略**，一次 git add -A 就会提交上去。\n"
            "      处理：往 .gitignore 加一行 " + path
            + "，或直接删掉这个文件")
    # 顺带提一句别的本机文件还在磁盘上，提醒别整个目录打包
    present = [d.rstrip("/") for d in FORBIDDEN_DIRS
               if (ROOT / d.rstrip("/")).exists()]
    if present:
        print("       另外这些本机目录还在磁盘上，打包整个目录发布时要排除：%s"
              % "、".join(present))


def check_plain_password() -> None:
    """被跟踪的源码里有没有写死的明文密码。"""
    hits = []
    for line in grep("NAS_PASS"):
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path, no, text = parts
        m = PASS_RE.search(text)
        if not m:
            continue
        value = m.group(1)
        # 占位符一律放行：`<密码>`、`...`、`***`、`xxx` 这类
        if value in PASS_ALLOW or value.startswith("<"):
            continue
        if value and set(value) <= set(".*xX-_"):
            continue
        hits.append("%s:%s" % (path, no))
    if hits:
        failures.append(
            "这些地方把密码明文写进了被跟踪的源码：\n      "
            + "\n      ".join(hits)
            + "\n      处理：改成读 deploy/.nas-credentials 或环境变量，"
              "并轮换密码")
    else:
        print("  OK   源码里没有明文密码")


def check_fingerprint() -> None:
    """内网 IP / 本机用户名路径。只警告，逐条列出来。"""
    hits = []
    for pattern in FINGERPRINT_PATTERNS:
        for line in grep(pattern.pattern):
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            path, no, text = parts
            if path in FINGERPRINT_SKIP:
                continue
            m = pattern.search(text)
            if not m:
                continue
            value = m.group(1)
            if value in PLACEHOLDER_IPS or value in PLACEHOLDER_USERS:
                continue
            hits.append("%s:%s（%s）" % (path, no, value))
    if hits:
        warnings.append(
            "这些地方带着本机指纹（内网 IP 或用户名路径），开源前建议换成占位符：\n      "
            + "\n      ".join(sorted(set(hits))[:20])
            + ("\n      …还有更多" if len(hits) > 20 else ""))
    else:
        print("  OK   没扫到内网 IP 与本机用户名路径")


def main() -> int:
    if not git("rev-parse", "--git-dir").strip():
        print("这不是一个 git 仓库，没法检查。")
        return 2

    files = tracked()
    print("开源前自检（共 %d 个被跟踪的文件）" % len(files))
    print()
    check_tracked_sensitive(files)
    check_history()
    check_ignored()
    check_plain_password()
    check_fingerprint()

    print()
    for w in warnings:
        print("  WARN %s" % w)
    if warnings:
        print()
    if failures:
        for f in failures:
            print("  FAIL %s" % f)
        print()
        print("结论：**先别发布**，上面 %d 项要处理。" % len(failures))
        return 1
    print("结论：可以发布。%s"
          % ("（上面 %d 条警告建议看一眼）" % len(warnings) if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
