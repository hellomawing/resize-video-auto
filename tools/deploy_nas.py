#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把当前源码更新部署到 NAS 上（Docker compose 方式）。

用法：
    python tools/deploy_nas.py

凭据从 deploy/.nas-credentials 自动读取（环境变量优先），正常情况下不需要
export 任何东西。该文件被 .gitignore 排除、只在本机存在，**发布前必须删掉并
轮换密码**——清单见仓库根目录 OPEN-SOURCE-CHECKLIST.md，
自检跑 python tools/check_release_ready.py。

为什么走「上传源码在 NAS 本地构建」而不是 docker push / pull：本地构建可控，
也不必把镜像推上公网。全新构建实测约 19 分钟（几乎全耗在 Debian 装 ffmpeg
的依赖链上），只改应用层代码时 apt / pip / 前端依赖全部命中缓存，1~3 分钟
就能完事——别按 19 分钟预估而不敢动。

它做的事：
    ① 本地打源码包（排除 node_modules / dist / __pycache__ / .git）
    ② 上传 → 先删掉旧的源码目录再解包（否则删过的文件会在 NAS 上残留）
    ③ 同步本地 deploy/docker-compose.yml 到 NAS（挂载配置的唯一来源）
    ④ 检查有没有正在跑的任务 —— 重建容器会把它们直接掐断，可 --wait 等
    ⑤ docker build（用 setsid 脱离 SSH 会话，否则断连会把构建一起带走）
    ⑥ docker compose up -d --force-recreate
    ⑦ 等健康检查通过，并打印版本信息

探针为什么都不走鉴权接口：
    本脚本拿不到网页访问密码（它在 NAS 本地的 deploy/.env 里），所以凡是要判断
    「服务好不好」的地方一律用免鉴权端点：存活看 `/healthz`（容器自己的
    HEALTHCHECK 也是它），密码状态看 `/api/auth/status`。**别改回 /api/health**——
    配了访问密码之后它返回 401，会把健康的容器判成没起来；查任务更危险：
    401 的响应体 `{"detail": ...}` 能被 json 解析成「没有 items」，于是「没查到」
    被静默当成「没有任务在跑」，重建时正好把在跑的任务掐断。任务状态改成
    `docker exec` 进容器用应用自己的 db 层查（见 check_running_jobs）。

参数：
    --app NAME      项目名，默认 video-splitter
    --remote DIR    NAS 上的部署根目录，默认 /vol1/1000/video-splitter
    --port N        容器对外端口，默认 8099
    --tag TAG       镜像 tag，默认 <app>:<app/config.py 里的 APP_VERSION>
    --skip-build    跳过构建，只重建容器（改了 deploy/ 里的东西时才这么用）
    --build-only    只上传源码并构建镜像，不重建容器、不等健康检查
    --wait N        有任务在跑时最多等 N 秒（默认不等，直接停）
    --force         不管有没有任务在跑，直接重建
    --dry-run       只打包并打印将要执行的远端命令，不做任何改动

必知点：
  - **deploy/ 目录不在源码包里**（打包时排除），所以不会随源码解包被删。
    部署时会把本地 `deploy/docker-compose.yml` 和 `.env.example` 同步到 NAS 的
    `src/deploy/`（见 sync_deploy_compose），本地 compose 是挂载配置的唯一
    来源；但 **`.env` 绝不覆盖**——里面的 PUID/PGID/镜像地址是每台机器各自的
    真实值，保留 NAS 本地的（PGID 常和 PUID 不一样）。
  - **用 setsid 脱离 SSH 会话构建**，否则构建会被断连一起带走，直接
    `ssh nas "docker build ..."` 挂在前台时更是如此。
  - 源码里若新增了顶层目录，记得同时加到下面的 SOURCE_ITEMS，否则传不上去。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ssh_run  # noqa: E402

# 要打包进镜像的顶层条目。deploy/ 刻意不在其中（见文件开头的说明）。
SOURCE_ITEMS = ("app", "core", "docker", "web", "requirements.txt")

# 打包时跳过的目录名 / 后缀
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "dist", ".venv", ".tmp-deploy"}


def log(msg=""):
    print(msg, flush=True)


def app_version() -> str:
    """镜像 tag 的版本号，取 app/config.py 里的 APP_VERSION。"""
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                  (ROOT / "app" / "config.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "1.0.0"


# ------------------------------------------------------------------ 本地打包

def pack(dest_dir: Path) -> Path:
    """把源码打成 tar.gz。返回值就是包路径。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "src.tar.gz"
    total = 0
    with tarfile.open(out, "w:gz") as tf:
        for name in SOURCE_ITEMS:
            base = ROOT / name
            if not base.exists():
                log("  [警告] 源码里没有 %s，跳过" % name)
                continue
            if base.is_file():
                tf.add(base, arcname=name)
                total += 1
                continue
            for p in sorted(base.rglob("*")):
                rel = p.relative_to(base)
                if any(part in SKIP_DIRS for part in rel.parts):
                    continue
                if p.suffix in (".pyc", ".pyo"):
                    continue
                tf.add(p, arcname=str(Path(name) / rel))
                total += 1
    mb = out.stat().st_size / 1024 / 1024
    log("  已打包 %d 个文件，%.2f MB" % (total, mb))
    if mb > 20:
        log("  [警告] 包偏大，检查一下有没有把构建产物打进去")
    return out


# ------------------------------------------------------------------ 远端动作

def http_code(cli, port: int, path: str, timeout: int = 5) -> str:
    """只取 HTTP 状态码。"""
    _, out, _ = ssh_run.run(
        cli, "curl -s -m %d -o /dev/null -w '%%{http_code}' "
             "http://127.0.0.1:%d%s" % (timeout, port, path))
    return out.strip()


def http_body(cli, port: int, path: str, timeout: int = 5) -> str:
    """只取响应体。"""
    _, out, _ = ssh_run.run(
        cli, "curl -s -m %d http://127.0.0.1:%d%s" % (timeout, port, path))
    return out


#: 在容器里查任务状态的小程序。刻意复用应用自己的 db 层：不用把 jobs 表结构
#: 在这边再抄一遍，将来加字段也不会两边不一致。
#: 注意两点：list_jobs 返回的是 (总数, 列表)；整段代码里**不能出现单引号**，
#: 它要被原样塞进 `python -c '...'`。
_JOBS_PROBE = (
    'import json;from app import db;'
    'print(json.dumps([{"src": j["src"], "status": j["status"], '
    '"progress": j["progress"]} '
    'for j in db.list_jobs(status="queued,running", limit=100)[1]]))'
)


def check_running_jobs(cli, app: str):
    """查容器里还在排队/运行的任务，返回 ``(jobs, note)``。

    为什么**不走 HTTP 接口**：配了访问密码之后 ``/api/jobs`` 需要登录，而 401 的
    响应体是 ``{"detail": "需要访问密码"}``——它照样能被 json 解析，
    ``data.get("items")`` 得到 None，于是「没查到」被静默当成「没有任务在跑」。
    重建容器时正好把在跑的任务掐断，这是这套检查最危险的错法。

    改成直接问容器自己：docker exec 进去用应用自己的 db 层查。既不需要网页密码
    （脚本本来也拿不到，密码在 NAS 本地的 deploy/.env 里），也不受鉴权开关影响。

    返回：
        jobs 是 list  → 查到了（可能是空的）
        jobs 是 None  → 查不到，note 说明原因；调用方必须按「未知」处理，
                        不能当成「没有任务」
    """
    _, ps_out, _ = ssh_run.run(
        cli, "docker ps --filter name=^/%s$ --format '{{.Names}}'" % app)
    if app not in ps_out:
        # 容器都没在跑，自然不可能有任务在跑
        return [], "容器没在运行"

    code, out, err = ssh_run.run(
        cli, "docker exec %s python -c '%s'" % (app, _JOBS_PROBE))
    if code != 0 or not out.strip():
        return None, (err.strip() or out.strip() or "docker exec 没有输出")[:200]
    try:
        jobs = json.loads(out)
    except Exception:
        return None, "返回值不是 JSON：%s" % out.strip()[:120]
    if not isinstance(jobs, list):
        return None, "返回值不是列表"
    return jobs, ""


def wait_jobs_done(cli, app: str, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        jobs, note = check_running_jobs(cli, app)
        if jobs is None:
            log("    查不到任务状态（%s），不再等" % note)
            return False
        if not jobs:
            return True
        j = jobs[0]
        log("    还有 %d 个任务在跑（%s 进度 %s%%），等 10 秒再看…"
            % (len(jobs), Path(j.get("src") or "").name,
               int((j.get("progress") or 0) * 100)))
        time.sleep(10)
    return False


def build(cli, remote: str, tag: str, timeout: int = 2700) -> bool:
    logfile = "%s/build.log" % remote
    # setsid + 三个 fd 全部重定向：少重定向 stdin 时 docker build 会占住
    # SSH 通道的 stdin，本地这边会一直挂住不返回
    cmd = ("cd %s/src && setsid bash -c 'docker build -f docker/Dockerfile -t %s . "
           "> %s 2>&1; echo __EXIT__$? >> %s' < /dev/null > /dev/null 2>&1 &"
           % (remote, tag, logfile, logfile))
    ssh_run.run_detached(cli, cmd)
    log("  构建已启动，日志：%s（首次约 19 分钟，命中缓存时 1~3 分钟）" % logfile)

    deadline = time.time() + timeout
    last_line = ""
    started = time.time()
    while time.time() < deadline:
        code, out, _ = ssh_run.run(
            cli, "tail -c 800 %s 2>/dev/null | tr '\\r' '\\n' | tail -n 1" % logfile)
        line = out.strip()
        if line and line != last_line:
            last_line = line
            log("    %3ds  %s" % (int(time.time() - started), line[:150]))
        if "__EXIT__" in line:
            try:
                rc = int(line.rsplit("__EXIT__", 1)[1].strip())
            except ValueError:
                rc = 1
            if rc == 0:
                log("  构建成功（耗时 %d 秒）" % int(time.time() - started))
                return True
            log("  [失败] 构建返回 %d，完整日志：" % rc)
            _, tail, _ = ssh_run.run(cli, "tail -n 40 %s" % logfile)
            log(tail)
            return False
        time.sleep(5)
    log("  [失败] 等待构建超时（%d 秒），它可能还在后台跑，去 NAS 上看 %s"
        % (timeout, logfile))
    return False


def recreate(cli, remote: str, app: str) -> bool:
    code, out, err = ssh_run.run(
        cli, "cd %s/src/deploy && docker compose -p %s up -d --force-recreate"
             % (remote, app), timeout=180)
    if out.strip():
        log("    " + out.strip().replace("\n", "\n    "))
    if err.strip():
        log("    " + err.strip().replace("\n", "\n    "))
    if code != 0:
        log("  [失败] compose 返回 %d。常见原因：src/deploy/.env 或 "
            "docker-compose.yml 不在了（它们不在源码包里，不会被覆盖，"
            "但会被手滑删掉）" % code)
        return False
    return True


def sync_deploy_compose(cli, remote: str) -> bool:
    """把本地 deploy/docker-compose.yml（和 .env.example）推到 NAS 的
    src/deploy/，使本地文件成为挂载配置的唯一来源。

    只推这两个模板文件，**绝不碰 .env**（里面是 PUID/PGID/镜像地址等每台
    机器各自的真实值，部署时应该保留 NAS 本地的，不能覆盖）。
    """
    local = ROOT / "deploy"
    remote_dir = "%s/src/deploy" % remote
    sftp = cli.open_sftp()
    try:
        for name in ("docker-compose.yml", ".env.example"):
            p = local / name
            if not p.is_file():
                log("  [警告] 本地没有 %s，跳过同步" % p)
                continue
            sftp.put(str(p), "%s/%s" % (remote_dir, name))
            log("  已同步 %s → %s/%s" % (name, remote_dir, name))
    finally:
        sftp.close()
    return True


def wait_healthy(cli, app: str, port: int, timeout: int = 120):
    """等容器 healthy + 接口能应答。entrypoint 要跑挂载自检 + gosu 降权，
    起来后 10~15 秒才算稳。

    探针用 **/healthz**，不是 /api/health：后者一旦配了访问密码就需要登录，
    而脚本拿不到那个密码（它在 NAS 本地的 deploy/.env 里），只会一直收到 401，
    于是容器明明健康也被判成「没起来」。/healthz 是刻意留的免鉴权探针，
    容器自己的 HEALTHCHECK 用的也是它，两边口径一致。
    """
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        _, out, _ = ssh_run.run(
            cli, "docker ps --filter name=^/%s$ --format '{{.Status}}'" % app)
        status = out.strip()
        if "healthy" in status and http_code(cli, port, "/healthz") == "200":
            return status
        time.sleep(4)
    return None


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser(
        description="把当前源码更新部署到 NAS 上（Docker compose 方式）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default="video-splitter", help="项目名")
    ap.add_argument("--remote", default="/vol1/1000/video-splitter",
                    help="NAS 上的部署根目录")
    ap.add_argument("--port", type=int, default=8099, help="容器对外端口")
    ap.add_argument("--tag", default=None,
                    help="镜像 tag，默认 <app>:<app/config.py 里的 APP_VERSION>")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过构建，只重建容器")
    ap.add_argument("--build-only", action="store_true",
                    help="只上传源码并构建镜像，不重建容器、不等健康检查")
    ap.add_argument("--wait", type=int, default=0, metavar="N",
                    help="有任务在跑时最多等 N 秒")
    ap.add_argument("--force", action="store_true",
                    help="不管有没有任务在跑（或查不到任务状态）都直接重建")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打包并打印要执行的命令，不改动 NAS")
    args = ap.parse_args()

    if args.build_only and args.skip_build:
        log("!! --build-only 与 --skip-build 互相矛盾，二选一。")
        return 2

    app = args.app
    remote = args.remote.rstrip("/")
    tag = args.tag or ("%s:%s" % (app, app_version()))

    steps = 4 if args.build_only else 7

    def step(idx: int, text: str):
        log("\n[%d/%d] %s" % (idx, steps, text))

    log("=" * 58)
    log("部署 %s → %s" % (app, remote))
    log("镜像 tag：%s" % tag)
    if args.build_only:
        log("模式：--build-only（只构建镜像，不碰容器）")
    log("=" * 58)

    step(1, "打源码包")
    pkg = pack(ROOT / ".tmp-deploy")
    remote_pkg = "%s/src.tar.gz" % remote

    if args.dry_run:
        log("\n[dry-run] 将要执行的远端命令：")
        log("  put %s -> %s" % (pkg, remote_pkg))
        log("  cd %s && rm -rf %s && tar -xzf src.tar.gz -C src"
            % (remote, " ".join("src/" + i for i in SOURCE_ITEMS)))
        if not args.build_only:
            log("  put deploy/docker-compose.yml deploy/.env.example -> %s/src/deploy/"
                % remote)
        log("  cd %s/src && docker build -f docker/Dockerfile -t %s ." % (remote, tag))
        if not args.build_only:
            log("  cd %s/src/deploy && docker compose -p %s up -d --force-recreate"
                % (remote, app))
        log("\n[dry-run] 没有连接 NAS，什么都没改。")
        return 0

    src = ssh_run.cred_source()
    if src.endswith(".nas-credentials"):
        log("\n凭据来自 %s（仅本机存在；发布前需删除并轮换密码）" % src)
    else:
        log("\n凭据来自%s" % src)
    cli = ssh_run.connect()
    try:
        step(2, "上传并解包")
        sftp = cli.open_sftp()
        try:
            sftp.put(str(pkg), remote_pkg)
        finally:
            sftp.close()
        log("  已上传 → %s" % remote_pkg)

        # 先删旧源码再解包：直接覆盖的话，本地已删除的文件会永远留在 NAS 上，
        # 而它们还会被镜像 COPY 进去（典型症状是「改了没生效」）。
        # deploy/ 刻意不删——它不在包里，.env 在里面。
        wipe = " ".join("src/" + i for i in SOURCE_ITEMS)
        code, out, err = ssh_run.run(
            cli, "cd %s && rm -rf %s && tar -xzf src.tar.gz -C src && "
                 "echo OK" % (remote, wipe), timeout=180)
        if code != 0 or "OK" not in out:
            log("  [失败] 解包出错：%s%s" % (out, err))
            return 1
        log("  已解包（旧源码已清掉，deploy/ 未动）")

        if args.build_only:
            step(3, "构建镜像 %s（不重建容器）" % tag)
            if not build(cli, remote, tag):
                return 1
            log("\n完成：镜像已刷新，容器没有动。")
            log("提示：要让它跑起来，在 NAS 上重跑本脚本（不带 --build-only）即可。")
            return 0

        # 把本地 deploy/docker-compose.yml 同步过去，让本地文件作为挂载配置的
        # 唯一来源（.env 不碰，见函数文档）。这样每次部署都会用本地这份 compose。
        sync_deploy_compose(cli, remote)

        if not args.skip_build:
            step(3, "检查有没有正在跑的任务")
            jobs, note = check_running_jobs(cli, app)
            if jobs is None:
                # 查不到 ≠ 没有。宁可停下来问一句，也别默默把在跑的任务掐断。
                log("  [警告] 查不到任务状态：%s" % note)
                log("  为了不误杀在跑的任务，这里不自动继续。确认没任务在跑后加 --force。")
                if not args.force:
                    log("\n已停在解包之后、构建之前。NAS 上的源码是新的，"
                        "容器还是旧的，重跑本脚本会继续。")
                    return 1
                log("  （--force：继续）")
            elif jobs:
                log("  有 %d 个任务还在排队/运行：" % len(jobs))
                for j in jobs[:5]:
                    log("    %s  %s  %s%%"
                        % (j.get("status"), Path(j.get("src") or "").name,
                           int((j.get("progress") or 0) * 100)))
                log("  重建容器会把这些任务直接掐断——切一个 6.5GB 的片子要跑 2 分半，")
                log("  很容易正好赶上。想等它们跑完，加 --wait 300；")
                log("  确定要立刻重建，加 --force。")
                if args.force:
                    log("  （--force：继续）")
                elif args.wait and wait_jobs_done(cli, app, args.wait):
                    log("  任务都跑完了，继续")
                else:
                    log("\n已停在解包之后、构建之前。NAS 上的源码是新的，"
                        "容器还是旧的，重跑本脚本会继续。")
                    return 1
            else:
                log("  没有在跑的任务，继续")

            step(4, "构建镜像 %s" % tag)
            if not build(cli, remote, tag):
                return 1
        else:
            step(3, "跳过检查")
            step(4, "--skip-build：跳过构建")

        step(5, "重建容器")
        if not recreate(cli, remote, app):
            return 1

        step(6, "等健康检查")
        status = wait_healthy(cli, app, args.port)
        if not status:
            log("  [失败] 120 秒内没等到 healthy。看日志：")
            _, out, _ = ssh_run.run(cli, "docker logs --tail 40 %s" % app)
            log(out)
            return 1
        log("  容器 %s" % status)

        step(7, "验证")
        # 存活判断只认免鉴权的 /healthz（和容器自己的 HEALTHCHECK 同一个口径）
        code = http_code(cli, args.port, "/healthz")
        if code != "200":
            log("  [失败] /healthz 返回 %s，服务没真正起来" % code)
            return 1
        log("  /healthz 200")

        # /api/auth/status 也是免鉴权端点，正好用来判断这台机器配没配访问密码
        enabled = False
        try:
            enabled = bool(json.loads(
                http_body(cli, args.port, "/api/auth/status")).get("enabled"))
        except Exception:
            pass
        log("  访问密码：%s" % ("已启用" if enabled else "未启用"))

        # 版本 / Python / ffmpeg 在 /api/health 里，而它受访问密码保护：
        # 配了密码就拿不到，这不是故障——存活判断上面已经做完了。
        try:
            health = json.loads(http_body(cli, args.port, "/api/health"))
        except Exception:
            health = {}
        if health.get("version"):
            log("  版本 %s ｜ Python %s ｜ ffmpeg %s"
                % (health.get("version"), health.get("python"),
                   (health.get("ffmpeg") or {}).get("version", "")[:40]))
        else:
            log("  版本 %s ｜（/api/health 需要登录，Python / ffmpeg 详情到网页「概览」看）"
                % app_version())

        host, port, _, _ = ssh_run.cfg()
        log("\n完成。控制台： http://%s:%d" % (host, args.port))
        log("提示：compose 方式部署要到 NAS 的 Docker → Compose 页签里看。")
        return 0
    finally:
        cli.close()


if __name__ == "__main__":
    raise SystemExit(main())