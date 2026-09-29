"""在 NAS 上执行命令 / 传输文件的小工具（调试用）。

凭据按「环境变量 → deploy/.nas-credentials」的顺序查找，**不硬编码在代码里**：
    NAS_HOST / NAS_PORT / NAS_USER / NAS_PASS

为什么放独立文件而不是写进这个脚本：本项目准备开源。密码一旦落在被 git
跟踪的源码里，开源那一刻就跟着公开了，而且回滚不掉（历史里还在）。
凭据文件在 .gitignore 里，发布前删掉并轮换密码即可——
清单见仓库根目录的 OPEN-SOURCE-CHECKLIST.md。

用法：
    python tools/ssh_run.py exec "uname -a"
    python tools/ssh_run.py exec --sudo "docker ps"
    python tools/ssh_run.py detach 'cmd &'   # 发完就不等，用于 setsid 出来的长任务
    python tools/ssh_run.py put 本地路径 远端路径
    python tools/ssh_run.py get 远端路径 本地路径

注意：exec 会一直读到通道 EOF 才返回。如果远端命令把某个后台进程的
stdin/stdout/stderr 留在通道上（典型的 `nohup cmd &` 不重定向），这条
命令就会永远挂住 —— 要么三处都重定向到文件 / /dev/null，要么用 detach。
"""

import os
import sys
import time
from pathlib import Path

import paramiko

_CFG = None
_CFG_SOURCE = None

#: 默认凭据文件位置。想换地方设 NAS_CREDENTIALS_FILE 即可。
CRED_FILE = Path(__file__).resolve().parent.parent / "deploy" / ".nas-credentials"

#: 从凭据文件里认识的键
_KEYS = ("NAS_HOST", "NAS_PORT", "NAS_USER", "NAS_PASS")


def _read_cred_file():
    """读凭据文件，返回 (字典, 实际路径)；文件不存在返回 ({}, None)。

    格式就是朴素的 ``KEY=VALUE``，空行和 ``#`` 开头跳过——这样它既能被
    ``. 文件`` 直接载入，人在编辑器里看也一目了然。
    """
    path = Path(os.environ.get("NAS_CREDENTIALS_FILE") or CRED_FILE)
    if not path.is_file():
        return {}, None
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values, path


def cred(key, default=None):
    """取一个连接之外的配置项，顺序同 cfg()：环境变量 → 凭据文件。

    给那些「连接本身不需要、但脚本要用来拼地址」的东西用，例如
    NAS_HTTP_PORT —— 把内网地址写死进源码，开源那一刻就成了自己的网络指纹。
    """
    val = os.environ.get(key)
    if val:
        return val
    file_values, _ = _read_cred_file()
    return file_values.get(key) or default


def cfg():
    """惰性读凭据，返回 ``(host, port, user, password)``。

    刻意不在 import 时读：tools/deploy_nas.py 要复用这里的连接逻辑，
    写成模块级常量的话，import 那一刻没摆好环境变量就会直接炸。
    """
    global _CFG, _CFG_SOURCE
    if _CFG is not None:
        return _CFG

    # 环境变量优先：临时换一台机器时 export 一下就行，不必改文件
    values = {k: os.environ.get(k) for k in _KEYS}
    source = "环境变量"
    if not (values["NAS_HOST"] and values["NAS_PASS"]):
        file_values, path = _read_cred_file()
        if path is not None:
            for k in _KEYS:
                if not values[k] and file_values.get(k):
                    values[k] = file_values[k]
            source = str(path)

    missing = [k for k in ("NAS_HOST", "NAS_PASS") if not values[k]]
    if missing:
        raise SystemExit(
            "缺少 %s。两种办法二选一：\n"
            "  1) 写进 %s（一行一个 KEY=VALUE）\n"
            "  2) export NAS_HOST=192.168.1.10 NAS_PORT=22 "
            "NAS_USER=admin NAS_PASS='<密码>'\n" % ("、".join(missing), CRED_FILE))

    _CFG_SOURCE = source
    _CFG = (values["NAS_HOST"],
            int(values["NAS_PORT"] or 22),
            values["NAS_USER"] or "root",
            values["NAS_PASS"])
    return _CFG


def cred_source() -> str:
    """凭据是从哪儿读到的，给日志用（不含密码本身）。"""
    cfg()
    return _CFG_SOURCE


def connect():
    host, port, user, password = cfg()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        timeout=20,
        banner_timeout=30,
        auth_timeout=30,
        look_for_keys=False,
        allow_agent=False,
    )
    return cli


def run(cli, cmd, sudo=False, timeout=300):
    if sudo:
        # -S 从 stdin 读密码；-p '' 不打印提示
        cmd = "sudo -S -p '' bash -lc " + _q(cmd)
    full = "bash -lc " + _q(cmd)
    stdin, stdout, stderr = cli.exec_command(full, timeout=timeout, get_pty=False)
    if sudo:
        stdin.write(cfg()[3] + "\n")
        stdin.flush()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def run_detached(cli, cmd, wait=2.0):
    """发出命令后不等它结束就关掉通道。

    给 docker build 这类长任务用：远端用 setsid 脱离会话之后，close 掉通道
    不会影响它继续跑；而如果老老实实等 EOF，任务不结束通道就不会关，
    本地这边会一直挂住。
    """
    chan = cli.get_transport().open_session()
    try:
        chan.exec_command("bash -lc " + _q(cmd))
        time.sleep(wait)
        # 把可能已经产生的输出读掉，避免通道缓冲区写满把远端卡住
        try:
            chan.settimeout(0.2)
            while chan.recv_ready():
                chan.recv(65536)
        except Exception:
            pass
    finally:
        chan.close()
    return 0


def _q(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    action = sys.argv[1]
    cli = connect()
    try:
        if action == "exec":
            args = sys.argv[2:]
            sudo = False
            if args and args[0] == "--sudo":
                sudo = True
                args = args[1:]
            code, out, err = run(cli, " ".join(args) if len(args) > 1 else args[0], sudo=sudo)
            if out:
                print(out, end="")
            if err:
                print("--- stderr ---")
                print(err, end="")
            print("--- exit %d ---" % code)
            return code
        if action == "detach":
            # 远端命令请自行用 setsid 脱离会话，否则关通道会把它一起带走
            args = sys.argv[2:]
            run_detached(cli, " ".join(args) if len(args) > 1 else args[0])
            print("detached")
            return 0
        elif action == "put":
            sftp = cli.open_sftp()
            sftp.put(sys.argv[2], sys.argv[3])
            print("uploaded -> %s" % sys.argv[3])
        elif action == "get":
            sftp = cli.open_sftp()
            sftp.get(sys.argv[2], sys.argv[3])
            print("downloaded -> %s" % sys.argv[3])
        else:
            print("unknown action: %s" % action)
            return 1
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
