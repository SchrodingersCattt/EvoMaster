# CI 说明

## 应用部署（API + Worker）— 打 tag 部署

- **单条主流水线**：API 与 Worker 的 job 均在主流水线内执行（`include` 而非 `trigger`），同一 `pipeline_id`，DevOps 平台可按该 ID 查询各 stage，避免「没有查询到 gitlab stage 步骤」。
- **打哪个 tag 就只跑哪个服务的 job**：
  - **Tag 格式**：`b_<服务名>_<版本>_<时间戳>`，例如：
    - `b_matmaster-evo_0.1.0_2026-03-11-13-37` → 只跑 **API** 的 build / manual-uat / deploy-uat 等 job，部署到 `matmaster-evo`。
    - `b_matmaster-evo-worker_0.1.0_2026-03-11-13-37` → 只跑 **Worker** 的 job，部署到 `matmaster-evo-worker`。
  - 无单独 UAT server_name，UAT 为流水线在 manual-uat / deploy-uat 等阶段等待审核或手动部署。
- **分支**：推 **test 结尾分支**时同时跑 API + Worker 的 job。

## 一次构建三环境 Remote 镜像（test / uat / prod）

- **触发方式**：推送到分支 `remote-image` 即自动执行 **build-remote-image:all**。
- **效果**：依次在 Bohrium test / uat / prod 创建镜像，然后一次性更新 `src/utils/constant.py` 的 `BOHRIUM_ENV_DEFAULT_IMAGE_IDS` 并 push 回 `remote-image`。
- **CI 变量**（在 GitLab CI/CD Variables 中配置，建议 Protected + Masked）：
  - `BOHRIUM_ACCESS_KEY_TEST`、`BOHRIUM_PROJECT_ID_TEST`
  - `BOHRIUM_ACCESS_KEY_UAT`、`BOHRIUM_PROJECT_ID_UAT`
  - `BOHRIUM_ACCESS_KEY_PROD`、`BOHRIUM_PROJECT_ID_PROD`
若希望用**特殊 tag** 触发（例如在任意分支打 tag `remote-image/all` 再 push 触发），可在 `.gitlab-ci.yml` 的 `build-remote-image:all` 的 `rules` 里增加一条：`if: $CI_COMMIT_TAG == "remote-image/all"`，并注意 tag 触发的 pipeline 中 `CI_COMMIT_BRANCH` 可能为空，脚本里 push 目标需改为固定分支（如 `main`）或从 tag 解析。
