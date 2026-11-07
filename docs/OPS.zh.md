# Windows 工人 v1

默认关闭。自动任务只有冒烟：`BUS_SMOKE_OK`。不启动 Hermes、Codex、DSH，也不调用模型。

部署目录：`%LOCALAPPDATA%\AgentHub\worker`（其下 `work`、`state`、`logs`）。

## 模型车道为什么不做

- Hermes：空的 `-t` 表示全部工具，不能用。`memory`、`file`、`terminal`、`browser`、`delegation` 会碰到私人资料或本机能力，公开 Issue 不能走。只留 `todo` 再加 `--safe-mode` 仍然共用真实 `HERMES_HOME`，复制凭证到新目录也不允许，所以分析先不做。
- Codex：只读沙箱仍可能读到用户目录，并加载 MCP 或用户配置。这不是隔离。`codex exec -s read-only` 不在本版执行。
- DSH：`--profile headless --permission-mode read-only` 并不限制本地读取和网络，也不调用 `subagent_codex`。

记录在案、但不启动的路径：

- Hermes 0.22.7：`%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\hermes\bin\hermes.exe`，`HERMES_HOME` 是同级的 `Local\hermes`，不是 `~\.hermes`。
- Codex 0.167.0-alpha.3.2：`%LOCALAPPDATA%\OpenAI\Codex\bin\14b838c1d074ff01\codex.exe`（不在 PATH 上）。
- DSH 0.2.2-rc.1：`E:\deepseek harness\resources\runtime\cli\bin\dsh.cmd`。

## 认证

工人只用本机官方 `gh`。预检已看到用户级 gh 2.103.3。还要在这台机器上完成 `gh auth login`（设备码，手机确认）。Cursor 的 GitHub 连接不是运行时登录。工人不读 `GH_TOKEN`，不把凭证文件放进提示或评论。

用 PATH 上的 Python 3.13。不要改、也不要调用 Hermes 自带的 Python 3.14.9。

## 安装

在 PowerShell 5.1 里，先核对压缩包哈希（与 `SHA256SUMS.txt` 一致），再解压。路径不要有空格。

```powershell
$dest = "$env:LOCALAPPDATA\AgentHub\worker"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item .\agent-hub-worker.zip "$dest\agent-hub-worker.zip"
Get-FileHash "$dest\agent-hub-worker.zip" -Algorithm SHA256
Expand-Archive -Path "$dest\agent-hub-worker.zip" -DestinationPath $dest -Force
Copy-Item "$dest\config.example.json" "$dest\config.json"
Set-Location $dest
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001
python -m worker doctor
python -m worker once
```

`config.json` 保持 `"enabled": false` 和 `"dry_run": true`。`doctor` 和 `once` 不改 Issue。`once` 只读。

创建标签（gh 登录之后）：

```powershell
gh label create bus:ready --description "Worker may claim this issue" --color "0e8a16"
```

手机用 Issue 模板 **Bus task**。标签要有 `bus:ready`、`status:todo`、`type:smoke`、`agent:partner`。正文只有 Goal、Task、Risk。Task 写 `smoke`，Risk 写 `none`。不要写命令、网址或路径。

确认演习无误后才打开：

```powershell
# 把 config.json 改成 "enabled": true, "dry_run": false
python -m worker arm
python -m worker once --arm
python -m worker status
```

`start` 才会循环。`stop` 写停止标志，不卸载程序。

```powershell
python -m worker stop
python -m worker clear-stop
python -m worker disarm
```

## 计划任务

不会在导入或安装压缩包时自动创建。要自己跑，而且只在已经 `arm` 且配置打开之后才会真正干活：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-logon-task.ps1 -WorkerRoot "$env:LOCALAPPDATA\AgentHub\worker"
```

任务名 `AgentHubWorker`：登录时、交互、受限权限，不用管理员。卸载：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-logon-task.ps1
```

这不删除 `state`。停止循环后再删任务即可。

## 单测和真机

单测使用假的 GitHub 和假进程，不访问 gh API，也不调用模型。单测覆盖：恶意正文、超时杀进程、重复/重启不二次执行、写回失败不重试、禁止的模型车道、评论不含目标正文。

真机还要你自己看：`gh auth login` 完成、`doctor`、演习、一条冒烟 Issue 被评论并关闭。模型隔离没有在本版里声明为已成立。
