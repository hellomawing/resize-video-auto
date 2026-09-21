# 开源 / 发布前检查清单

这个仓库在作者本机上是「能直接一条命令部署」的状态，所以带着一些
**只应该存在于本机的文件**。把它们一起发出去，等于把家里 NAS 的账号密码
一起公开了。

> 无论是人来发布，还是交给别的 AI 助手来发布，**先跑这一条**：
>
> ```bash
> python tools/check_release_ready.py
> ```
>
> 它只读不写。退出码非 0 就说明第 1、2 项没过，**先别发布**。

---

## 1. 删掉唯一的密钥文件

| 文件 | 里面有什么 | 怎么办 |
|---|---|---|
| `deploy/.nas-credentials` | **NAS 的账号密码（明文）** | **发布前删除** |

```bash
rm -f deploy/.nas-credentials
```

这是本仓库里**唯一**一个真·密钥文件，删掉它不影响代码运行——
`tools/ssh_run.py` 会退回读环境变量，重新填回去也就是几秒钟的事
（格式见该文件头部注释）。

其余的本机文件（`data/`、`deploy/.env`、`.workbuddy/`、`.tmp-*/`）
**不要删**，它们是你自己跑着的服务在用的数据；只需要做到**别发布**。
详见第 3 节。

## 2. 确认它没进过 git 历史

`.gitignore` 已经覆盖了这些文件，但**曾经提交过的内容，`.gitignore` 不会追溯**：

```bash
git ls-files | grep -E "nas-credentials|deploy/\.env$|^data/"   # 应当无输出
git log --all --oneline -- deploy/.nas-credentials              # 应当无输出
```

两条都无输出 = 干净，密码没离开过这台机器，删掉文件就够了。

**如果有输出**，说明已经进了历史，得用 `git filter-repo` 清掉；
并且**必须同时去 NAS 上把密码改掉**——因为历史里那份可能已经被 clone、
被备份、被上传过。这时候删文件只是止损，真正让它失效的是改密码。

## 3. 发布时不要带上的本机文件

这些文件**已被 `.gitignore` 排除**，走 git（GitHub / `git archive`）发布天然不会
带出去。但如果你或某个助手是**把整个目录打包**（`zip -r` / `tar czf`）发布的，
就必须手动排除：

| 路径 | 里面是什么 |
|---|---|
| `deploy/.nas-credentials` | NAS 明文密码（第 1 节已要求删除） |
| `deploy/.env` | 真实 PUID/PGID、镜像地址 |
| `data/` | 运行时数据库与配置：任务历史、真实文件路径 |
| `.workbuddy/` | 本机 AI 助手的会话记录与项目记忆 |
| `.tmp-deploy/` `.tmp-test/` `.tmp-verify/` | 打包与测试的临时产物 |

打包示例：

```bash
git archive --format=zip -o video-splitter.zip HEAD   # 只含被跟踪的文件，最省心
```

## 4. 扫一遍硬编码的本机信息

```bash
git grep -nE "192\.168\.[0-9]+\.[0-9]+|[A-Za-z]:\\\\+Users\\\\+[A-Za-z]"
```

命中处逐条确认是「文档举例」还是「你的真实环境」：

- 内网 IP（如 `192.168.x.x`）建议一律换成占位符（`192.168.1.10` 之类），
  别把自家的网段指纹带出去
- Windows 用户名 / 家目录路径换成 `C:\Users\me` 这类占位
- `/vol1/1000/...` 这类飞牛默认路径可以保留：它是 fnOS 的标准 uid 布局，
  写成例子有解释价值（Dockerfile 里那条注释就在讲真实踩过的权限坑）

## 5. 法务与内容

- `README.md` 末尾「许可」一节：确认许可证就是你想用的那个
- 图标：若是取自素材库，确认授权范围允许再分发
- 确认 `fnos/*.fpk`、`web/dist/`、`data/` 等构建产物与运行数据没被提交
  （`.gitignore` 已覆盖）

## 6. 发布前最后一眼

```bash
git status --ignored --short          # 磁盘上到底还留着哪些本机文件
python tools/check_release_ready.py   # 机械自检，比人眼可靠
```

---

## 附：为什么密码放在独立文件里，而不是写进脚本

`tools/ssh_run.py` 需要用 NAS 密码。它当初的写法是「从环境变量读，不落盘」，
好处是绝不会泄露，坏处是**每次新开一个对话 / 终端就失效**，得重新 export。

权衡后改成「环境变量优先，退回读 `deploy/.nas-credentials`」：

- 密码**不写进被 git 跟踪的脚本**——一旦写进去，开源那一刻就永久公开了，
  而且删不掉（git 历史里那份还在，任何 clone 过的人都有）
- 独立文件在 `.gitignore` 里，发布前删掉即可，一行命令的事
- 日常使用体验和写进脚本一样：`python tools/deploy_nas.py` 就能跑

**核心原则：能被 `git ls-files` 列出来的文件，永远不放密钥。**
