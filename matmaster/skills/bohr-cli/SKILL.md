---
name: bohr-cli
description: "Use Bohrium CLI (bohr) for platform operations: job/node/sandbox management, file/dataset ops, paper/scholar search, AI mentor, wiki, knowledge base, PDF parsing, billing queries."
---

# bohr-cli — Bohrium 平台命令行工具

## Capability Gate

- `bohr` 不在 PATH → 先 `npm install -g @dptech-corp/bohr-cli`
- `bohr auth status --verify -o json` 非 `ok=true` → 先按「认证」一节处理，再执行业务命令

## 认证

沙箱内平台已注入 CLI 直读的 `BOHR_ACCESS_KEY` 和 `BOHR_OPENAPI_HOST`（同环境端点），常规情况免登录：

```bash
bohr auth status --verify   # 应显示 logged_in: true / auth_method: access_key，host 与本环境一致
# 仅当上述变量缺失（旧环境只注入 BOHRIUM_ACCESS_KEY，CLI 不读它）时手动补：
export BOHR_ACCESS_KEY="$BOHRIUM_ACCESS_KEY"
bohr auth login --ak "$BOHRIUM_ACCESS_KEY"     # 或持久化到 ~/.bohrium/cfg.yaml
```

无 AK 或 AK 失效（401）时，改用设备码流程让用户在浏览器完成授权，不要要求用户在对话中粘贴 AK：

```bash
bohr auth login --device --no-wait --json   # 返回 verification URL 和 device_code，把 URL 交给用户
bohr auth login --device-code <code>        # 用户授权后执行，轮询直至登录完成
```

认证排障：

| 症状 | 动作 |
|------|------|
| 401 且 `retryable: false` | 同一凭证登录失败一次即止；引导设备码流程或请用户更新凭证 |
| headless 环境需要交互登录 | 禁止无参数 `bohr auth login`——同步阻塞等授权，超时被杀且每次重跑换新验证码；必须用上面的两步式 |
| `--device-code` 成功但报 `could not obtain access key (failed to parse gateway response ...)` | 已知 CLI/网关兼容问题：vouch token 已生效，job/file 等正常，仅 billing 类受限；如实报告，不要重复登录 |
| 401 但不确定凭证是否真无效 | 先看 `auth status` 的 `host`（默认生产 `open.bohrium.com`，`BOHR_OPENAPI_HOST` 可覆盖）——host 与凭证环境不匹配时表现与凭证无效完全相同 |

回显 AK 只显示前几位掩码；`bohr auth token` 会输出完整凭证，不要打印到对话或写进产物文件。

## 全局选项与输出契约

| 选项 | 说明 |
|------|------|
| `-o json` | JSON 输出（脚本环境必用） |
| `-q '<jq_expr>'` | JQ 过滤，如 `-q '.data.sandboxID'` |
| `--dry-run` | 预览不执行 |
| `--no-interactive` | 禁用交互提示 |

`-o json` 的 envelope 统一为 `{"ok": true|false, "data": {...}, "meta": {...}}`：

- 成功判定只看 `ok=true`；失败时看 `error` 的 `code`/`http`/`retryable`。
- `meta.cli_version` 是实际运行版本；低于 2.2.19 时不要使用下文标注 2.2.19+ 的命令。

## 写操作流程

- 先用 `--help` 确认参数；JSON 配置用程序生成，提交前校验：`python3 -c "import json;json.load(open('job.json'))"`。
- 支持 `--dry-run` 的 create/submit 命令先加 `--dry-run -o json` 验证，再执行一次真实命令。
- 仅以 `ok=true` 且返回有效 ID 作为创建成功；成功后立即记录 ID，不要再用真实写操作试探或重放。需要确认时按返回 ID 查询。
- 写操作失败后，先修正明确错误再重试；不得在结果不确定时盲目重复创建。
- delete/terminate 等清理操作只有在响应成功并经查询确认后才算完成；清理失败时保留并报告真实状态。

## 计算作业 (job)

```bash
bohr job list [-n 20] [-r] [-f] [-i] [-j <group_id>] [--quiet]
bohr machine list -c <cpu|gpu> -s job --json  # 作业场景可用机型
bohr job describe -i <bohr_job_id>    # 单任务详情
bohr job describe -j <group_id>       # 组内任务列表
bohr job submit -i job.json [--input_directory ./input]
bohr job submit --project_id <pid> --image_address <img> --command <cmd> --machine_type <type>
bohr job log -j <jobId> [--out ./logs]
bohr job download -j <jobId> [--out ./results]
bohr job terminate <job_id>           # 优雅停止，保留结果（默认优先）
bohr job kill <job_id>                # 强制终止，不保证结果
bohr job delete <job_id>              # 删除记录（不可恢复）
```

响应中的几类 ID 不要混用：

| 响应字段 | 用于 | 备注 |
|----------|------|------|
| `bohrJobId` | `job describe -i` | 单任务详情 |
| `jobId` | `job log` / `download` / `terminate` / `kill` / `delete` | 平台任务 ID，保存日志必须用它 |
| `jobGroupId`（submit 返回） | `job describe -j` 等组查询 | 与 create 的 `groupId` 不同，勿因不一致重复建组 |
| `groupId`（`job_group create` 返回） | `job submit --job_group_id` | |

状态判读——`status` 与 `webStatus` 是两套独立状态码，必须按原字段分别保留、不要混为一套：

| 观察到 | 含义 | 动作 |
|--------|------|------|
| `status=2` + `webStatus=2` + `exitCode=0` + `endTime` 非空 | 正常完成 | 停止轮询 |
| 停止操作后 `status=6` | 结果回收中的过渡态 | 继续查询 |
| `webStatus=5` 或状态文本/`errorInfo` 明确已停止 | 停止完成 | 收尾 |

保存平台日志：`bohr job log -j <jobId> --out <dir> -o json` → `data.log` 非空则原样写入目标文件；`data.logFiles` 非空则用 `--out` 下载的日志文件按需改名；两者皆空时空日志就是有效结果，不要改去下载任务结果包或重复等待。

submit 配置文件格式：
```json
{
  "project_id": 12345, "job_type": "container",
  "job_name": "...", "image_address": "registry.dp.tech/...",   // 镜像必须写完整地址，不能只写短名
  "machine_type": "c8_m32_1*A100", "command": "...",
  "log_file": "run.log", "result_path": "./results/"
}
```

批量提交（一个任务组投多个任务）→ 流程见 `references/batch-submit.md`

## 作业组 (job_group)

```bash
bohr job_group create -n "name" --project_id <pid>
bohr job_group list [-n 50] [-j <id>]
bohr job_group download -j <id> [-n 10] [--out ./]
bohr job_group terminate <job_group_id>
bohr job_group delete <job_group_id>
```

`list` 按日期过滤时 `--start` 和 `--end` 必须同时指定。

## 开发机 (node)

```bash
bohr node list [--started] [--paused] [--quiet]
bohr node resources                   # 可用机型列表
bohr node get <id>                    # 详情（含 SSH 信息）
bohr node create -n <name> -P <pid> -i <image> -m <machine> [-d <disk_gb>] [-t <hours>]   # 仅 CPU 机型
bohr node create -f config.json       # 从 JSON 配置创建（GPU 机型用这个）
bohr node start <id>
bohr node stop <id>
bohr node restart <id>
bohr node connect <id>                # SSH 连接
bohr node delete <id>                 # 不可恢复
```

状态码：-1=Paused, 0=Waiting, 1=Pending, 2=Started, 3=Starting

| 症状 | 动作 |
|------|------|
| GPU 机型传 `-m` 报 `unknown machine type` | GPU 标识含空格（如 `c16_m62_1 * NVIDIA T4`）在首个空格处被截断；改用 `-f config.json`，写 `machine_type`（完整标签）或 `skuId`（取 `resources` 的 `value`，如 T4=372）。CPU 标识无空格（如 `c2_m4_cpu`），`-m` 可用 |
| 配置了机型仍报 `machine_type is required` | 字段名必须蛇形 `machine_type`，驼峰 `machineType` 会被静默忽略 |

## 沙箱 (sandbox)

```bash
bohr sandbox template list
bohr sandbox machine list
bohr sandbox create [template] [--project-id <pid>] [--timeout <sec>] [--mount-user-storage]
bohr sandbox list
bohr sandbox describe <id>
bohr sandbox exec <id> --command "<cmd>" [--cwd /path] [--background] [--timeout 60]
bohr sandbox ps <id>
bohr sandbox files write <id> <remote_path> --source <local_path>
bohr sandbox files read <id> <remote_path> [--destination <local_path>]
bohr sandbox delete <id> [--yes]
```

- 默认模板为 `sdbxagent`；创建前先 `template list` 确认可用模板
- `--timeout` 控制进程超时，`--sandbox-timeout` 控制沙箱生命周期；后台任务需同时设置足够的 `--sandbox-timeout`
- 用完必须 `delete` 回收资源
- 沙箱内下载依赖或资源时，先使用沙箱默认网络配置；若出现超时、TLS、503 或连接重置，加载 `sandbox-proxy` skill 按目标选择国内镜像或海外代理，不要让用户指定镜像或代理

## 文件系统 (file)

```bash
bohr file list [path] [--project-id <pid>] [--limit 50]
bohr file stat <path>
bohr file mkdir <path>
bohr file upload <local_file> [remote_path] [--space personal|share] [--project-id <pid>]   # 2.2.19+，本地 → 盘
bohr file download <path>
bohr file copy <src> <dst> [--recursive]   # 仅盘内复制，本地文件用 upload
bohr file move <src> <dst>
bohr file delete <path> [--recursive]
```

路径体系：`personal/...`（个人盘，project-id=0）、`share/...`（共享盘，需 project-id）
⚠️ 目录操作 copy/move/delete 必须显式加 `--recursive`；路径含空格时用引号包裹
⚠️ `stat` 查不存在的路径也返回 `ok:true`，存在性以 `data.exist` 字段为准

## 数据集 (dataset)

```bash
bohr dataset list [--projectId <pid>] [-n 10]
bohr dataset get <id>
bohr dataset versions <id>
bohr dataset create -n <name> --path <path_id> -i <project_id> [-l <local_path>]
bohr dataset delete <id>
```

## 容器镜像 (image)

```bash
bohr image list [-t "DeePMD-kit"]     # 按类型列公共镜像
bohr image search <keyword>
bohr image get <id>
bohr image delete <id>
```

## 论文搜索 (paper)

```bash
bohr paper search "<query>" [--size 10] [--year-from 2020] [--jcr Q1,Q2] [--db SCI]
bohr paper patent "<query>" [--size 10]
```

## 学者 (scholar)

```bash
bohr scholar search "<name>" [--size 10] [--school "..."] [--tags "NLP,ML"]
bohr scholar info <id>
```

## 知识库 (kb)

```bash
bohr kb list
bohr kb create "<name>"
bohr kb upload <local_file> --kb-id <id> [--parent-id <node>] [--name <显示名>] [--wait]   # 2.2.19+，--wait 等待索引完成
bohr kb search <kb_id> "<query>" [--top-k 5]
bohr kb delete <id>
```

上传后需索引完成才能被 search 命中；无按 document ID 查询/删除的入口。

## PDF 解析 (pdf)

```bash
bohr pdf parse --url "<pdf_url>" [--sync] [--textual 1] [--table 1]
bohr pdf result "<token>"             # 查询异步结果
```

计费约 0.05 元/页。仅支持 `--url`，无本地文件直接入口。配额耗尽时返回 429 `quota_exceeded`（账号配额问题），重试无效，如实报告即可。

## 科研检索长尾

mentor（科研导师，约 2 元/次）/ lkm（文献知识挖掘）/ wiki（科学百科）/ database（聚合物文献库，需外部提供 db_ak）→ 命令与计费细节见 `references/research-commands.md`

## 其他命令（tools / search / chat / doctor / api）

```bash
bohr tools domains                              # 科学工具域列表
bohr tools search "<query>" [--k 50]
bohr tools info <tool-id>
bohr search "<query>" [--limit 5]               # 网页搜索
bohr chat "<message>" [--model gpt-5-nano]      # AI 对话
bohr doctor [--offline]                         # 环境诊断
bohr api <METHOD> <PATH> [--data <json|@file>] [--params key=value]   # 原始 API
```

## 费用管理 (billing)

```bash
bohr billing balance
bohr billing ledger [--since 30d] [--group-by resource|day|project]
bohr billing recharge --amount <cny> [--channel alipay|wechat] [--wait]
```

## 项目管理 (project)

```bash
bohr project list
bohr project get <id>
bohr project members <id>
bohr project create -n "<name>" [-t <total_limit>] [-m <month_limit>]
bohr project delete <id>
```
