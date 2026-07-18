# Bohr-CLI 题库设计原则

本文约束 `evaluation/question_bank/platform/bohr_*_agnostic.yaml` 的出题与校验设计,随题库代码一起演进(改题请连同本文一起 review)。原始出处为飞书评测文档,2026-07-18 迁入 repo 作为唯一权威版本;各版本评测报告只引用、不复制。

## 一、题面设计约束

- 题面只描述用户目标,不写具体子命令、flag、固定答案字段或排障步骤;实现路径由 Agent 自行发现。
- 例外(2.2.19 轮教训):校验器要求**精确字段名或值形态**时,题面必须给出最小字段清单——只列反引号 key 与值形态/枚举,不给示例值、不提 CLI 命令(先例:BWO_gpu_compare_004、BWO_dp_compare_008 的 `rmse_e`/`rmse_f`、BDD_cleanup_plan_001 的 `delete/keep/archive` 枚举)。删格式说明必须同步放宽校验,二者只能留一个。
- 写操作使用唯一资源前缀,只允许清理本轮创建的资源;涉及费用的任务设置低成本上限并保证善后(如 `sleep 300` 自然终止兜底)。
- 评测 fixture 只提供必要的非敏感目标信息,不在题面或产物中写 AK、Bearer、数据库凭据或 SSH 密钥。

## 二、校验设计约束

- 校验侧优先确认真实 CLI 调用、平台返回 ID、状态变化和实际副作用;最终报告文件只承载摘要。
- **grounding 优先用 receipt 校验**(`bohr_cli_operation_invoked`,按执行回执 operation 白名单 + `require_ok`),而非命令字符串正则:对 flag/引号/管道/`+`快捷/同义命令免疫,且能证明真实执行成功(2.2.19 轮 receipt 试点抓出 `pdf parse --help` 被正则误判为已解析的假阳性)。仍用 `tool_args_regex` 的场景(如 scp 等非 bohr 命令)注意 `\+?` 快捷形式与同义命令别名。
- schema 对 agent 产物的**形态要求跟着 CLI 真实返回走**:CLI 返回对象就用 `oneOf` 收对象/数组,不强迫 agent 转换形态(2.2.19 轮 7 处 0/3 皆因此)。语义质量约束(minItems、枚举、数值)该严则严。
- `turn_budget` 等预算类 criteria 在两条打分路径均为非阻塞,只作观测;校准以观测为准(node 类题实测 56-60 轮)。

## 三、扩题方向约束

- PDF、billing、dataset、scholar、wiki、mentor、lkm、image、project:已有对应用户场景,优先补真实 CLI 调用和执行回执校验,不再增加同质题。
- job submit/monitor 与 paper search:覆盖已偏密集,除任务组级能力外暂不继续增加普通提交、轮询或通用检索题。
- 依赖平台/CLI 未上线能力的题(upload、KB、Node、event、database、pdf 配额):**先冒烟验证前置条件,通过后再正式入题**;已入题但被平台阻塞的,保留在题库作为能力缺口记录,分数不计入 agent 能力信号。
- auth、profile、config、update、extension、completion、raw API:更适合作为 CLI 冒烟或集成测试,不纳入智能体业务场景题。

## 四、关联

- 各版本评测报告在飞书,一版本一篇、旧报告顶部链接指向下一篇;最新一篇的「遗留问题登记表」是当前状态唯一权威。截至 2026-07-18 最新为 [Bohr-CLI 2.2.19 评测报告](https://dptechnology.feishu.cn/docx/GefSd2OZdo6MnoxjzO3cpt5lnce)。
- 题目分类学:`evaluation/skills/evaluation-iteration/references/question_taxonomy.md`。
