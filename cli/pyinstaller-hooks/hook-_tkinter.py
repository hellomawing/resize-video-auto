# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 自定义 hook：裁掉 Tcl/Tk 里用不上的数据文件。

为什么需要它
------------
打包出来的可执行文件里带了 tkinter，只是为了在向导第 1 步弹一个「选择文件夹」
的对话框。PyInstaller 会把整套 Tcl/Tk 数据目录塞进来，其中绝大部分跟这件事
毫无关系：

    _tcl_data/tzdata     609 个文件   Tcl 的时区数据库
    _tcl_data/msgs       127 个文件   Tcl 的多语言消息目录
    _tk_data/demos        86 个文件   Tk 的示例程序
    _tk_data/msgs         18 个文件   Tk 的多语言消息目录

加起来占了 tcl/tk 数据文件的八成以上。onefile 模式下每次运行都要把它们解压
到临时目录、退出时再逐个删掉 —— 文件越多，这两步越慢。实测在开了实时防护的
Windows 上，删 1000 多个小文件要几十秒，而裁到 170 多个之后快了约 4 倍。

保留 `encoding/`：Tcl 靠它做文本编码转换，中文 Windows 下要用到 cp936，
删了有可能让对话框起不来。省下的那点时间不值这个风险。

hookspath 里同名的 hook 会覆盖 PyInstaller 内置的 hook-_tkinter.py，
所以这个文件生效时，内置那份不会再执行。
"""

from PyInstaller.utils.hooks.tcl_tk import tcltk_info

# 这些子目录（出现在 _tcl_data/ 或 _tk_data/ 下的任意一层）整体丢掉
DROP_DIRS = ("tzdata", "msgs", "demos")

# 这两个前缀之下的数据才受裁剪影响，其余条目原样保留
TARGET_PREFIXES = ("_tcl_data/", "_tk_data/")


def _keep(entry) -> bool:
    dest = str(entry[0]).replace("\\", "/")
    if not dest.startswith(TARGET_PREFIXES):
        return True
    return not any(part in DROP_DIRS for part in dest.split("/")[1:])


def hook(hook_api):
    # 先复刻内置 hook 的健全性检查：该有数据却收集不到时，构建期就报错，
    # 而不是等到用户双击运行才发现对话框弹不出来。
    if tcltk_info.tcl_data_missing:
        raise SystemExit("ERROR: 找不到 Tcl 数据目录（%r），无法打包 tkinter！"
                         % (tcltk_info.tcl_data_dir,))
    if tcltk_info.tk_data_missing:
        raise SystemExit("ERROR: 找不到 Tk 数据目录（%r），无法打包 tkinter！"
                         % (tcltk_info.tk_data_dir,))

    files = tcltk_info.data_files
    kept = [f for f in files if _keep(f)]
    print("[hook-_tkinter] Tcl/Tk 数据文件 %d -> %d（丢掉 %s）"
          % (len(files), len(kept), "/".join(DROP_DIRS)))
    hook_api.add_datas(kept)
