---
name: bohr-cli
description: "Use Bohrium CLI (bohr) for platform operations: job/node/sandbox management, file/dataset ops, paper/scholar search, AI mentor, wiki, knowledge base, PDF parsing, billing queries."
---

# bohr-cli — Bohrium 平台命令行工具

## Capability Gate

- 需要 `bohr` 可执行文件在 PATH 中（`npm install -g @dptech-corp/bohr-cli`）
- 需要有效认证：`bohr auth login --ak $BOHRIUM_ACCESS_KEY` 或已有 `~/.bohrium/cfg.yaml`
- 验证：`bohr auth status --verify`

## 认证

```bash
bohr auth login --ak "$BOHRIUM_ACCESS_KEY"
bohr auth status --verify
```

## 全局选项

| 选项 | 说明 |
|------|------|
| `-o json` | JSON 输出（脚本环境必用） |
| `-q '<jq_expr>'` | JQ 过滤 |
| `--dry-run` | 预览不执行 |
| `--no-interactive` | 禁用交互提示 |

## 计算作业 (job)

```bash
bohr job list [-n 20] [-r] [-f] [-i] [-j <group_id>] [--quiet]
bohr machine list -c <cpu|gpu> -s job --json  # 作业场景可用机型
bohr job describe -i <bohr_job_id>    # 单任务详情
bohr job describe -j <group_id>       # 组内任务列表
bohr job submit -i job.json [--input_directory ./input]
bohr job submit --project_id <pid> --image_address <img> --command <cmd> --machine_type <type>
bohr job log -j <id> [--out ./logs]
bohr job download -j <id> [--out ./results]
bohr job terminate <job_id>           # 优雅停止，保留结果
bohr job kill <job_id>                # 强制终止，不保证结果
bohr job delete <job_id>              # 删除记录（不可恢复）
```

提交和查询结果可能同时包含三类 ID，不要混用：

- `bohrJobId` 用于 `bohr job describe -i`
- `jobId` 用于 `bohr job terminate`、`kill` 和 `delete`
- `jobGroupId` 用于 `bohr job_group` 子命令

`job describe` 可能同时返回 `status` 和 `webStatus`，两者是不同的状态字段，必须按原字段分别保留。停止后 `status=6` 可能只是结果回收中的过渡态；继续查询，直到 `webStatus=5` 或状态文本、`errorInfo` 明确表明任务已停止，不要把两套状态码混为一套。

submit 配置文件格式：
```json
{
  "project_id": 12345, "job_type": "container",
  "job_name": "...", "image_address": "registry.dp.tech/...",
  "machine_type": "c8_m32_1*A100", "command": "...",
  "log_file": "run.log", "result_path": "./results/"
}
```

## 作业组 (job_group)

```bash
bohr job_group create -n "name" --project_id <pid>
bohr job_group list [-n 50] [-j <id>]
bohr job_group download -j <id> [-n 10] [--out ./]
bohr job_group terminate <job_group_id>
bohr job_group delete <job_group_id>
```

## 开发机 (node)

```bash
bohr node list [--started] [--paused] [--quiet]
bohr node resources                   # 可用机型列表
bohr node get <id>                    # 详情（含 SSH 信息）
bohr node create -n <name> -p <pid> -i <image> -m <machine> [-d <disk_gb>] [-t <hours>]
bohr node start <id>
bohr node stop <id>
bohr node restart <id>
bohr node connect <id>                # SSH 连接
bohr node delete <id>                 # 不可恢复
```

状态码：-1=Paused, 0=Waiting, 1=Pending, 2=Started, 3=Starting

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

重要注意事项：
- 默认模板为 `sdbxagent`；创建前先 `template list` 确认可用模板
- `--timeout` 控制进程超时，`--sandbox-timeout` 控制沙箱生命周期
- 后台任务需同时设置足够的 `--sandbox-timeout`
- 用完必须 `delete` 回收资源
- 结构化输出用 `-o json`，envelope 格式：`{"ok": true, "data": {...}, "meta": {...}}`
- 提取字段：`bohr sandbox create -o json -q '.data.sandboxID'`
- 沙箱内下载依赖或资源时，先使用沙箱默认网络配置；若出现超时、TLS、503 或连接重置，加载 `sandbox-proxy` skill 按目标选择国内镜像或海外代理，不要让用户指定镜像或代理

## 文件系统 (file)

```bash
bohr file list [path] [--project-id <pid>] [--limit 50]
bohr file stat <path>
bohr file mkdir <path>
bohr file copy <src> <dst> [--recursive]
bohr file move <src> <dst>
bohr file delete <path> [--recursive]
```

路径体系：`personal/...`（个人盘，project-id=0）、`share/...`（共享盘，需 project-id）
⚠️ 目录操作 copy/move/delete 必须显式加 `--recursive`

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

## 科研导师 (mentor)

```bash
bohr mentor "<question>" [--discipline All|Physics|Chemistry|Biology|Materials] [--journal-type foreign|chinese]
```

响应时间 30-60 秒，带文献引用。每次调用约 2 元。

## 文献知识挖掘 (lkm)

```bash
bohr lkm search "<query>" [--top-k 10] [--mode hybrid|semantic|lexical] [--sort comprehensive|relevance|recent]
bohr lkm reasoning --query "<question>"
bohr lkm graph --paper-id <id>
```

## 科学百科 (wiki)

```bash
bohr wiki search "<query>" [--lang zh-CN|en-US]
bohr wiki article <entry_id>
bohr wiki levels
bohr wiki graph <id>
```

## 知识库 (kb)

```bash
bohr kb list
bohr kb create "<name>"
bohr kb search <kb_id> "<query>" [--top-k 5]
bohr kb delete <id>
```

## PDF 解析 (pdf)

```bash
bohr pdf parse --url "<pdf_url>" [--sync] [--textual 1] [--table 1]
bohr pdf result "<token>"             # 查询异步结果
```

计费约 0.05 元/页。

## 科学工具 (tools)

```bash
bohr tools domains
bohr tools search "<query>" [--k 50]
bohr tools info <tool-id>
```

## 网页搜索 (search)

```bash
bohr search "<query>" [--limit 5]
```

## AI 对话 (chat)

```bash
bohr chat "<message>" [--model gpt-5-nano]
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

## 诊断 (doctor)

```bash
bohr doctor [--offline]
```

## 原始 API

```bash
bohr api <METHOD> <PATH> [--data <json|@file>] [--params key=value]
```

## 常见陷阱

1. **目录操作忘加 --recursive**：copy/move/delete 目录必须显式加
2. **job_group list 日期**：`--start` 和 `--end` 必须同时指定
3. **镜像地址要完整**：必须含 `registry.dp.tech/...`，不能只写短名
4. **批量提交串行**：多任务提交不可并发，逐个 submit
5. **沙箱用完要删**：`bohr sandbox delete <id>` 否则持续计费
6. **kill vs terminate**：默认优先用 terminate（保留结果）
7. **文件路径含空格**：用引号包裹整个路径
