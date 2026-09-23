#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交互式发布脚本：部署到局域网飞牛 NAS，或生成新版本并发布到公网。

用法：
    python tools/release.py

主菜单（输入序号 + 回车）：
    1. Docker 方式部署到局域网飞牛
       把当前源码打包上传到 NAS，在 NAS 上本地构建镜像并重建容器
       （复用 tools/deploy_nas.py，不经过 Docker Hub，适合日常改代码快速上线）。
    2. fpk 方式部署到局域网飞牛
       用 fnpack 构建 fpk 应用包 → 上传 NAS → appcenter-cli 安装/升级并启动。
    3. 生成新版本 / 版本号 / 版本说明，发布到 Docker Hub + GitHub Release
       提版本号 → 写版本说明 → 更新 manifest/config → tag →
       构建 fpk → GitHub Release → （*需要你确认*）推镜像到 Docker Hub。
    4. 退出

⚠️ 约束：**任何发布到 Docker Hub 的动作都必须弹出 y/N 确认，绝不自动推。**
   脚本只在你说 y 之后才执行 docker buildx --push。

依赖：
    - 本机 SSH 到 NAS 的凭据 deploy/.nas-credentials（不提交，见 ssh_run.py）
    - fnpack.exe 用于打 fpk；Docker 用于构建镜像
    - gh CLI（C:/Program Files/GitHub CLI/gh.exe）用于 GitHub Release
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ssh_run  # noqa: E402

APP_NAME = "video-splitter"
# Docker Hub 用户名（发布镜像到公网用）
DOCKER_HUB_USER = "mawing"
GH_BIN = os.environ.get(
    "GH_BIN", r"C:/Program Files/GitHub CLI/gh.exe")
GH_REPO = "hellomawing/resize-video-auto"
# fnpack.exe 路径（默认在 fnpack 临时目录；可用 FNPACK_BIN 覆盖）
FNPACK_BIN = os.environ.get("FNPACK_BIN",
                            r"C:/Users/hello/AppData/Local/Temp/fnpack/fnpack.exe")

# NAS 上的 fpk 临时路径
NAS_FPK = "/tmp/video-splitter-release.fpk"
NAS_INSTALL_ENV = "/tmp/video-splitter-release.env"

# 安装向导字段（appcenter-cli 读取，缺失会报 19000）
WIZARD_ENV = {
    "wizard_puid": "1000",
    "wizard_pgid": "1001",
    "wizard_timezone": "Asia/Shanghai",
    "wizard_mount_paths": "",
}


def log(msg=""):
    print(msg, flush=True)


def banner(title: str):
    log("\n" + "=" * 60)
    log("  " + title)
    log("=" * 60)


# ------------------------------------------------------------------ 输入工具

def ask(question: str, default: str = "") -> str:
    """普通单行输入。"""
    suffix = (" [%s] " % default) if default else " "
    try:
        return input(question + suffix).strip() or default
    except (EOFError, KeyboardInterrupt):
        sys.exit(130)


def confirm(question: str, default: bool = False) -> bool:
    """y/N 确认，默认 No。"""
    if default:
        hint = "Y/n"
    else:
        hint = "y/N"
    ans = ask(question, "")
    return ans.lower() in ("y", "yes", "1", "true")


def ask_multiline(prompt: str) -> str:
    """收集多行文本：每行一条，输入单独一个 ``.`` 表示结束。
    非交互环境下（无 tty / 通过管道）提示不可用时直接读 stdin 到 EOF。"""
    print(prompt)
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    lines = []
    hint = "  （输入单独一行 . 结束输入；空行跳过）"
    log(hint)
    while True:
        line = ask("  > ")
        if line == ".":
            break
        if line:
            lines.append(line)
    return "\n".join(lines)


def run_proc(cmd, cwd: Path = ROOT, **kw):
    """跑本地命令，实时打印输出，返回 (returncode, stdout合并)。"""
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), text=True,
                          encoding="utf-8", errors="replace", **kw)
    return proc.returncode


def run_local(cmd, cwd: Path = ROOT):
    """跑本地命令取输出，统一 utf-8/replace 避免 Windows GBK 编码崩溃。"""
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


# ------------------------------------------------------------------ 版本工具

def read_version_set() -> dict:
    """读取各处的当前版本号。"""
    v = {}
    # app/config.py
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                  (ROOT / "app" / "config.py").read_text(encoding="utf-8"))
    v["app"] = m.group(1) if m else "?"
    # fnos/manifest
    for line in (ROOT / "fnos" / "manifest").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            v["manifest"] = line.split("=", 1)[1].strip()
    v.setdefault("manifest", "?")
    # git tag
    r = run_local(["git", "describe", "--tags", "--abbrev=0"])
    v["tag"] = r.stdout.strip() or "(无 tag)"
    return v


def bump_version(current: str) -> str:
    """建议下一个 patch 版本号。1.0.0 -> 1.0.1。"""
    parts = current.split(".")
    if len(parts) == 3 and parts[2].isdigit():
        return ".".join(parts[:2] + [str(int(parts[2]) + 1)])
    return current + ""


def git_log_since(tag: str) -> str:
    """取自上个 tag 以来的提交，用于生成版本说明草稿。"""
    if tag == "(无 tag)":
        r = run_local(["git", "log", "--oneline", "-20"])
    else:
        r = run_local(["git", "log", "--oneline", "%s..HEAD" % tag])
    return r.stdout.strip() or "（没有可用的提交记录）"


def set_app_version(version: str):
    path = ROOT / "app" / "config.py"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'(APP_VERSION\s*=\s*")[^"]*(")',
                  r"\g<1>%s\g<2>" % version, text, count=1)
    path.write_text(text, encoding="utf-8")


def set_manifest_version(version: str):
    path = ROOT / "fnos" / "manifest"
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        if line.startswith("version="):
            out.append("version=%s" % version)
        else:
            out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")


# ------------------------------------------------------------------ 1) Docker 部署

def docker_deploy_to_nas():
    banner("1. Docker 部署到局域网飞牛")
    log("将当前源码打包上传到 NAS，在 NAS 本地构建镜像并重建容器。")
    log("（不经过 Docker Hub；复用 tools/deploy_nas.py）\n")
    if not confirm("现在开始 Docker 方式部署？"):
        return
    rc = run_proc([sys.executable, "tools/deploy_nas.py"], ROOT)
    if rc == 0:
        log("\n✔ Docker 部署完成。可在飞牛 Docker → Compose 页签查看。")
    else:
        log("\n✘ Docker 部署失败 / 中止（退出码 %d）。" % rc)


# ------------------------------------------------------------------ 2) fpk 部署

def build_fpk() -> Path:
    """用 fnpack 构建 fpk，返回产物路径。"""
    if not Path(FNPACK_BIN).exists():
        log("!! 找不到 fnpack：%s" % FNPACK_BIN)
        log("   请先安装，或用 FNPACK_BIN 环境变量指定路径。")
        return None
    rc = run_proc([FNPACK_BIN, "build", "-d", "."], ROOT / "fnos")
    fpk = ROOT / "fnos" / "video-splitter.fpk"
    if rc != 0 or not fpk.exists():
        log("!! fpk 构建失败。")
        return None
    log("✔ fpk 构建完成：%s（%.0f KB）" % (fpk, fpk.stat().st_size / 1024))
    return fpk


def fpk_deploy_to_nas():
    banner("2. fpk 方式部署到局域网飞牛")
    local_fpk = build_fpk()
    if not local_fpk:
        return

    if not confirm("上传 fpk 到 NAS 并用 appcenter-cli 安装/升级？",
                   default=True):
        return

    cli = ssh_run.connect()
    try:
        # 1) 上传 fpk 与安装向导 env
        sftp = cli.open_sftp()
        sftp.put(str(local_fpk), NAS_FPK)
        env_text = "\n".join("%s=%s" % (k, v) for k, v in WIZARD_ENV.items())
        with sftp.open(NAS_INSTALL_ENV, "w") as f:
            f.write(env_text + "\n")
        sftp.close()
        log("✔ 已上传 fpk 与安装向导配置到 NAS")

        # 2) 检测是否已安装
        _, out, _ = ssh_run.run(cli, "appcenter-cli check %s 2>/dev/null"
                                % APP_NAME, sudo=True)
        installed = "installed" in out.lower()
        stage = "升级已安装的应用" if installed else "全新安装"
        log("NAS 上应用当前状态：%s（%s）" % ("已安装" if installed else "未安装", stage))

        # 3) 执行安装
        cmd = ("appcenter-cli install-fpk %s -e %s" % (NAS_FPK, NAS_INSTALL_ENV))
        code, out, err = ssh_run.run(cli, cmd, sudo=True, timeout=600)
        if out.strip():
            log(out)
        if err.strip():
            log(err)
        if code != 0:
            log("!! appcenter-cli install-fpk 失败（退出码 %d）" % code)
            log("   应用中心日志：/var/log/trim_app_center/error.log")
            return

        # 4) 启动
        log("\n启动应用…")
        ssh_run.run(cli, "appcenter-cli start %s" % APP_NAME, sudo=True)
        time.sleep(6)

        # 5) 等待健康
        for _ in range(30):
            _, out, _ = ssh_run.run(
                cli, "docker ps --filter name=^/%s$ --format '{{.Status}}'"
                % APP_NAME)
            if "healthy" in out:
                log("✔ 应用已启动： %s" % out.strip())
                break
            time.sleep(4)
        else:
            log("!! 60 秒内未到 healthy，请到应用中心查看状态。")

        host, port, _, _ = ssh_run.cfg()
        log("控制台： http://%s:8099" % host)
    finally:
        cli.close()


# ------------------------------------------------------------------ 3) 发布新版

def gather_version_and_notes(versions: dict) -> tuple[str, str, str]:
    """询问版本号、版本说明，返回 (version, notes, commit_msg)。"""
    log("当前版本：app/config.py = %s ｜ manifest = %s ｜ git tag = %s"
        % (versions.get("app"), versions.get("manifest"), versions.get("tag")))

    # 从 manifest（权威）推断建议版本
    base = versions.get("manifest") or versions.get("app") or "1.0.0"
    if base == "?":
        base = "1.0.0"
    suggested = bump_version(base)
    log("建议版本：%s" % suggested)
    version = ask("新版本号（回车用建议值）", suggested)
    version = version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        log("!! 版本号格式应为 x.y.z，例如 %s。" % suggested)
        version = ask("重新输入新版本号", suggested)

    log("\n-- 版本说明草稿（自上个 tag 以来的提交）--")
    auto = git_log_since(versions.get("tag") or "")
    log(auto + "\n")

    log("\n-- 请输入版本说明（输入单独一行 . 结束，可含多行）--")
    manual = ask_multiline("")
    notes = manual.strip() if manual.strip() else auto
    log("\n最终版本说明：\n%s" % notes)

    commit_msg = ask("commit / tag 信息（可选，回车用默认）",
                     "release: v%s" % version)
    return version, notes, commit_msg


def check_release_ready() -> bool:
    log("\n-- 发布自检（check_release_ready.py）--")
    rc = run_proc([sys.executable, "tools/check_release_ready.py"], ROOT)
    if rc != 0:
        log("!! 发布自检未通过。仍要继续请确认（见下方二次确认）。")
    return rc == 0


def push_docker(version: str) -> bool:
    """推镜像到 Docker Hub。必须先经过 y/N 确认。"""
    log("\n-- 发布到 Docker Hub --")
    log("镜像：%s/%s:%s 以及 :latest（多架构 amd64 + arm64）"
        % (DOCKER_HUB_USER, APP_NAME, version))
    if not confirm("推送到 Docker Hub？这是对外发布，需要你明确同意"
                   "（y 推送 / N 跳过）"):
        log("已跳过推送 Docker Hub。镜像未对外发布。")
        return False

    env = {
        "DOCKERHUB_USER": DOCKER_HUB_USER,
        "VERSION": version,
        # 复用 APP_VERSION 保持一致
        "IMAGE": "%s/%s" % (DOCKER_HUB_USER, APP_NAME),
    }
    full_env = dict(os.environ)
    full_env.update(env)
    log("正在构建并推送（首次较慢）…")
    rc = run_proc(["bash", "docker/build-and-push.sh"], ROOT, env=full_env)
    if rc != 0:
        log("!! Docker Hub 推送失败（退出码 %d）。" % rc)
        return False
    log("✔ 已推送 %s/%s:%s + :latest" % (DOCKER_HUB_USER, APP_NAME, version))
    return True


def create_github_release(version: str, notes: str, fpk: Path):
    if not Path(GH_BIN).exists():
        log("!! 未找到 gh CLI，跳过 GitHub Release。镜像包已可用。")
        return
    # notes 写到临时文件传给 gh，避免参数过长/特殊字符问题
    notes_file = ROOT / ".tmp-release-notes.md"
    notes_file.write_text(notes, encoding="utf-8")
    try:
        rc = subprocess.run(
            [GH_BIN, "release", "create",
             "v%s" % version,
             "--repo", GH_REPO,
             "--title", "v%s" % version,
             "--notes-file", str(notes_file),
             str(fpk)],
            cwd=str(ROOT), text=True, encoding="utf-8", errors="replace"
        ).returncode
        if rc != 0:
            log("!! GitHub Release 创建失败。可手动处理：")
            log("    %s release create v%s --repo %s --notes-file 说明.md %s"
                % (GH_BIN, version, GH_REPO, fpk))
            return
        log("✔ GitHub Release 已创建： https://github.com/%s/releases/tag/v%s"
            % (GH_REPO, version))
    finally:
        notes_file.unlink(missing_ok=True)


def release_new_version():
    banner("3. 生成新版本并发布到 Docker Hub + GitHub Release")
    versions = read_version_set()

    # 检查工作树是否干净（发布前应提交）
    r = run_local(["git", "status", "--porcelain"])
    if r.stdout.strip():
        log("⚠️  工作区有未提交的改动：")
        for line in r.stdout.splitlines():
            log("    " + line)
        if not confirm("先自动 git add 并提交全部改动？", default=True):
            log("已取消。请先自行提交后再运行发布。")
            return

    version, notes, commit_msg = gather_version_and_notes(versions)

    banner("版本说明预览")
    log(notes)

    if not confirm("按这个版本号和说明继续？"):
        log("已取消。")
        return

    # 1) 自检（只警告，不强制拦，但会提示）
    ready_ok = check_release_ready()

    # 2) 更新版本号并提交
    log("\n-- 更新版本号 --")
    set_app_version(version)
    set_manifest_version(version)
    log("已更新 app/config.py 与 fnos/manifest 为 %s" % version)

    # 3) 构建 fpk（新版本用）
    log("\n-- 构建 fpk --")
    fpk = build_fpk()

    log("\n-- git 提交与打 tag --")
    subprocess.run(["git", "add", "-A"], cwd=str(ROOT))
    subprocess.run(["git", "commit", "-am", commit_msg], cwd=str(ROOT))
    subprocess.run(["git", "tag", "v%s" % version], cwd=str(ROOT))
    log("✔ 已提交并打 tag v%s" % version)

    # 自检没通过时的二次确认
    if not ready_ok:
        if not confirm("发布自检未通过（可能含本机私密信息），仍要发布到公网？"):
            log("已取消公网发布。本地 commit/tag 已保留。")
            return

    # 4) Docker Hub 推送 —— 必须显式同意，绝不自动推
    pushed = push_docker(version)

    # 5) 推 git 分支 + tag
    log("\n-- 推送 git tag 与分支 --")
    tip = ("镜像已发布到 Docker Hub" if pushed
           else "镜像尚未推 Docker Hub")
    if confirm("推送 git 分支与 v%s tag 到 GitHub？（%s）"
               % (version, tip), default=True):
        subprocess.run(["git", "push", "origin", "main"], cwd=str(ROOT))
        subprocess.run(["git", "push", "origin", "tag", "v%s" % version],
                       cwd=str(ROOT))
        log("✔ 已推送。")

    # 6) GitHub Release （附带 fpk）
    log("\n-- GitHub Release --")
    if confirm("创建 GitHub Release v%s（附带 fpk）？" % version,
               default=True):
        create_github_release(version, notes, fpk)

    log("\n✔ 发布流程结束。下次部署 NAS 请用主菜单 1 或 2。")


# ------------------------------------------------------------------ 主菜单

def main() -> int:
    printed = False
    while True:
        banner("video-splitter 发布工具")
        if not printed:
            log("部署到局域网飞牛，或生成新版本发布到公网（Docker Hub / GitHub）。")
            log("任何 Docker Hub 推送都需你确认，不会自动执行。\n")
            printed = True
        log("  1. Docker 方式部署到局域网飞牛")
        log("  2. fpk 方式部署到局域网飞牛")
        log("  3. 生成新版本 + 发布到 Docker Hub + GitHub Release")
        log("  4. 退出")
        choice = ask("\n请选择 [1/2/3/4]", "1")
        if choice == "1":
            docker_deploy_to_nas()
        elif choice == "2":
            fpk_deploy_to_nas()
        elif choice == "3":
            release_new_version()
        elif choice in ("4", "q", "quit", "exit"):
            log("再见。")
            return 0
        else:
            log("无效输入，请重新选择。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())