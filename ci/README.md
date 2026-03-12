# CI 说明

## 应用部署（API + Worker）— 子流水线 + 打 tag 自动 trigger

- **主流水线**：仅 `build-remote-image` + **trigger** 子流水线。打 tag 时按 tag 内容**自动** trigger 对应一条子流水线（API 或 Worker）；推 test 分支时同时 trigger API + Worker 两条子流水线。
- **Tag 格式**：`b_<服务名>_<版本>_<时间戳>`，例如：
  - `b_matmaster-evo_0.1.0_2026-03-11-13-37` → 只 **trigger API 子流水线**（`ci/api-deploy.yml`），子流水线内 job 名为 `manual-uat`、`deploy-uat`（无 `:api` 后缀），便于平台识别、飞书点「部署」可触发。
  - `b_matmaster-evo-worker_0.1.0_2026-03-11-13-37` → 只 **trigger Worker 子流水线**（`ci/worker-deploy.yml`），子流水线内同样为 `manual-uat`、`deploy-uat`。
- **10009**：`manual-uat` 步骤的 `DEVOPS_DEPLOY_NAME` 固定为 `"manual-uat"`，不传带 `:api` / `:worker` 的 job 名，避免平台报「没有查询 gitlab stage 步骤」。
- **分支**：推 **test 结尾分支**时同时 trigger API + Worker 两条子流水线。

## 一次构建三环境 Remote 镜像（test / uat / prod）

- **触发方式**：推送到分支 `remote-image` 即自动执行 **build-remote-image:all**。
- **效果**：依次在 Bohrium test / uat / prod 创建镜像，然后一次性更新 `src/utils/constant.py` 的 `BOHRIUM_ENV_DEFAULT_IMAGE_IDS` 并 push 回 `remote-image`。
- **CI 变量**（在 GitLab CI/CD Variables 中配置，建议 Protected + Masked）：
  - `BOHRIUM_ACCESS_KEY_TEST`、`BOHRIUM_PROJECT_ID_TEST`
  - `BOHRIUM_ACCESS_KEY_UAT`、`BOHRIUM_PROJECT_ID_UAT`
  - `BOHRIUM_ACCESS_KEY_PROD`、`BOHRIUM_PROJECT_ID_PROD`
若希望用**特殊 tag** 触发（例如在任意分支打 tag `remote-image/all` 再 push 触发），可在 `.gitlab-ci.yml` 的 `build-remote-image:all` 的 `rules` 里增加一条：`if: $CI_COMMIT_TAG == "remote-image/all"`，并注意 tag 触发的 pipeline 中 `CI_COMMIT_BRANCH` 可能为空，脚本里 push 目标需改为固定分支（如 `main`）或从 tag 解析。
