# Windows 工人 v1.1.1

默认关闭。冒烟仍是默认自动任务：`BUS_SMOKE_OK`。分析（Hermes）和实现（DSH → `subagent_codex`）两个车道默认关，要在配置里单独打开。Partner 仍是协调者，工人不代替云端派工。

部署目录：`%LOCALAPPDATA%\AgentHub\worker`（其下 `work`、`state`、`logs`）。

## 哪条车道会跑

| 车道 | 配置 | 标签 | 进程 |
| --- | --- | --- | --- |
| 冒烟 | 工人 `enabled` 且非 dry-run，并已 `arm` | `agent:partner` `type:smoke` | 不启动模型，写 `BUS_SMOKE_OK` 后关闭 |
| 分析 | 再加 `lanes.enabled.analyze: true` | `agent:hermes` `type:analyze` | Hermes oneshot。评论截断并去掉密钥样式。不关闭 Issue |
| 实现 | 再加 `lanes.enabled.implement: true` | `agent:codex` 或 `agent:dsh`，加 `type:implement` | DSH headless，在新克隆里调用一次 `subagent_codex`。只有成功规则满足才关闭 |

三条都还要：作者 `Siniv26`、`bus:ready`、`status:todo`、正文 Risk 为 `none`。别人的评论不参与领取。领取时的快照不变；Issue 中途被改就改为 `status:blocked`，不公布模型输出。子进程已开始过就不再跑第二次。GitHub 写回失败不重试。超时会杀掉子进程。

Risk 写 `approval` 或 `high` 时不跑模型，评论说明要人来处理，状态改为 `status:blocked`。

## 仍不做的后端

- **DSH 分析**（`agent:dsh` + `type:analyze`）不做。`--permission-mode read-only` 只限制写入。Windows 上读取、网络和进程可见性不在这个沙箱里。公开评论不能靠它当隔离。
- **原生 Codex**（`codex.exe`）不启动。`codex exec -s read-only` 仍可能读到用户目录并加载 MCP 或用户配置，而且它不是写入沙箱。`dangerously-bypass` 拒绝使用。实现车道走 DSH，不直接执行这条二进制。
- Hermes 空的 `-t` 表示全部工具，工人不会省略 `-t`。`memory`、`file`、`terminal`、`browser`、`delegation` 不在允许列表里。

分析车道实际命令：

```text
hermes -z "<brief>" --provider deepseek -m deepseek-flash --safe-mode --ignore-rules -t todo
```

`--safe-mode` 关掉用户配置、规则、记忆注入、插件、shell hooks 和 MCP。`HERMES_HOME` 仍是现有的 `Local\hermes`，好让 Hermes 自己读 provider 密钥。不会把凭证复制到新目录。这不是完整的文件系统或网络沙箱。二进制不在记录路径上，或 `--help` 里没有 `--safe-mode`、`--ignore-rules` 和 `-t`，模型不会启动。

实现车道实际命令（任务从 stdin 进入，不进命令行）：

```text
dsh --profile headless --permission-mode workspace-write --approval-policy never --json
```

工作目录是 `%LOCALAPPDATA%\AgentHub\worker\work\<claim-id>` 里用 `gh repo clone Siniv26/agent-hub` 得到的新克隆。不传 `--add-dir`。提示词禁止 merge、deploy、delete；shell 工具若出现这些操作，子进程会被杀掉，Issue 标成 `status:blocked`。`workspace-write` 只尽量把写入限制在这个克隆里。读取和网络仍然开放。DSH 配置里的 `subagent_codex` permissionMode 工人不改，也不要设成 `dangerously-bypass`。

成功关闭实现 Issue 的条件：进程成功、`final.reason.kind` 为 `completed`、恰好一次 `subagent_codex`、输出里恰好一个 `https://github.com/Siniv26/agent-hub/pull/<数字>`，且没有单独一行 `BLOCKED`。否则保持打开并标 `status:blocked`。

记录在案的路径：

- Hermes 0.22.7：`%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\hermes\bin\hermes.exe`，`HERMES_HOME` 是同级的 `Local\hermes`，不是 `~\.hermes`。
- Codex 0.167.0-alpha.3.2：`%LOCALAPPDATA%\OpenAI\Codex\bin\14b838c1d074ff01\codex.exe`（不在 PATH 上，工人不启动）。
- DSH 0.2.2-rc.1：`E:\deepseek harness\resources\runtime\cli\bin\dsh.cmd`。路径有空格。这就是安装位置，不用改文件夹名，也不用目录联接。Windows 上经 `cmd.exe /d /s /c` 调用这个 `.cmd`。`/s` 会去掉命令最外层的一对引号，所以整条命令再包一层引号，空格才不会把路径拆成 `E:\deepseek`。Issue 正文从 stdin 进入，不拼进命令行。

## 配置

`config.example.json` 里相关开关：

```json
{
  "enabled": false,
  "dry_run": true,
  "lanes": {
    "enabled": {
      "analyze": false,
      "implement": false
    },
    "analyze_timeout_seconds": 300,
    "implement_timeout_seconds": 900
  }
}
```

- 没有 `lanes` 的旧 `config.json` 等于两条模型车道都关。冒烟行为与 v1 相同。
- `analyze_timeout_seconds` 允许 30 到 600，默认 300。已核实的 Hermes oneshot 大约 222 秒。
- `implement_timeout_seconds` 允许 60 到 1200，默认 900。
- 模型车道还要 `enabled: true`、`dry_run: false`、并且 `python -m worker arm`。`dry_run: true` 时只读，不启动子进程，不写 Issue。
- 不能在配置里改工具允许列表或二进制路径。未知键会被拒绝。

## 认证

工人只用本机官方 `gh`。预检已看到用户级 gh 2.103.3。还要在这台机器上完成 `gh auth login`（设备码，手机确认）。Cursor 的 GitHub 连接不是运行时登录。工人不读 `GH_TOKEN`，不把凭证文件放进提示或评论。子进程环境同样丢掉 `GH_TOKEN`。模型密钥由 Hermes 或 DSH 从它们自己的现有目录读取，工人不复制。

用 PATH 上的 Python 3.13。不要改、也不要调用 Hermes 自带的 Python 3.14.9。

## 安装

在 PowerShell 5.1 里，先核对压缩包哈希（与 `SHA256SUMS.txt` 一致），再解压。部署根目录不要有空格。

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

`config.json` 保持 `"enabled": false` 和 `"dry_run": true`。两条 `lanes.enabled` 保持 false。`doctor` 和 `once` 不改 Issue。`once` 只读。

## 升级已有目录

不要删除 `state`、`logs`、`work`，也不要删 `ARMED` 或 `STOP`。压缩包里没有 `config.json`，解压不会覆盖它。

```powershell
$dest = "$env:LOCALAPPDATA\AgentHub\worker"
Copy-Item "$dest\config.json" "$dest\config.json.bak" -ErrorAction SilentlyContinue
Copy-Item .\agent-hub-worker.zip "$dest\agent-hub-worker.zip"
Get-FileHash "$dest\agent-hub-worker.zip" -Algorithm SHA256
Expand-Archive -Path "$dest\agent-hub-worker.zip" -DestinationPath $dest -Force
Set-Location $dest
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
python -m worker doctor
python -m worker once
```

不要在这次升级里运行 `install-logon-task.ps1`。旧 `config.json` 不必为了保持关闭而改；缺 `lanes` 就是关。要打开某一条时再编辑，例如只开分析：

```json
"lanes": {
  "enabled": { "analyze": true, "implement": false },
  "analyze_timeout_seconds": 300,
  "implement_timeout_seconds": 900
}
```

然后仍要 `enabled: true`、`dry_run: false`、`arm`。先 `python -m worker once` 看 dry-run，再 `python -m worker once --arm`。

创建标签（gh 登录之后，若还没有）：

```powershell
gh label create bus:ready --description "Worker may claim this issue" --color "0e8a16"
```

手机模板：

- **Bus task**：`type:smoke` `agent:partner`，Task 写 `smoke`
- **Bus analyze**：`type:analyze` `agent:hermes`，Task 写 `analyze`
- **Bus implement**：`type:implement` `agent:codex`，Task 写 `implement`。`agent:dsh` 加 `type:implement` 是同一条实现车道

正文只有 Goal、Task、Risk。Goal 一行，不要写命令、网址或路径。Risk 自动跑写 `none`。要人审批写 `approval` 或 `high`。

确认演习无误后才打开工人：

```powershell
# 把 config.json 改成 "enabled": true, "dry_run": false
# 模型车道仍建议先保持 false，单独再开
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

不会在导入或安装压缩包时自动创建。这次升级不要安装它。要自己跑，而且只在已经 `arm` 且配置打开之后才会真正干活：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-logon-task.ps1 -WorkerRoot "$env:LOCALAPPDATA\AgentHub\worker"
```

任务名 `AgentHubWorker`：登录时、交互、受限权限，不用管理员。卸载：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-logon-task.ps1
```

这不删除 `state`。停止循环后再删任务即可。

## 单测和真机

单测使用假的 GitHub 和假进程，不访问 gh API，也不调用模型。覆盖：恶意正文、高风险不跑、车道关闭不跑、超时杀进程、重复/重启不二次执行、写回失败不重试、输出去密钥并截断、DSH 分析仍延期、缺二进制或工具允许列表失败则不启动。

真机还要你自己看，工人不会在这里代跑：

- `gh auth login` 完成、`doctor`、冒烟演习
- 分析：一条 `agent:hermes` Issue。若 Hermes 在 `--safe-mode` 下读不到现有密钥而失败，不要去掉 `--safe-mode`，也不要复制凭证；把该 Issue 留在 `status:blocked`
- 实现：DSH 的 `subagent_codex` 不是 `dangerously-bypass`；只接受一条指向本仓库的 PR。读取和网络隔离没有在本版里声明为已成立
