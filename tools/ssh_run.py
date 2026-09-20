"""在 NAS 上执行命令 / 传输文件的小工具（调试用）。

凭据从环境变量读取，不写进代码：
    NAS_HOST / NAS_PORT / NAS_USER / NAS_PASS

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
import paramiko

HOST = os.environ["NAS_HOST"]
PORT = int(os.environ.get("NAS_PORT", "22"))
USER = os.environ.get("NAS_USER", "root")
PASS = os.environ["NAS_PASS"]


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        hostname=HOST,
        port=PORT,
        username=USER,
        password=PASS,
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
        stdin.write(PASS + "\n")
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
