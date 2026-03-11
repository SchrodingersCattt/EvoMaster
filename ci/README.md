# CI 说明

## 一次构建三环境 Remote 镜像（test / uat / prod）

- **触发方式**：推送到分支 `remote-image` 即自动执行 **build-remote-image:all**。
- **效果**：依次在 Bohrium test / uat / prod 创建镜像，然后一次性更新 `src/utils/constant.py` 的 `BOHRIUM_ENV_DEFAULT_IMAGE_IDS` 并 push 回 `remote-image`。
- **CI 变量**（在 GitLab CI/CD Variables 中配置，建议 Protected + Masked）：
  - `BOHRIUM_ACCESS_KEY_TEST`、`BOHRIUM_PROJECT_ID_TEST`
  - `BOHRIUM_ACCESS_KEY_UAT`、`BOHRIUM_PROJECT_ID_UAT`
  - `BOHRIUM_ACCESS_KEY_PROD`、`BOHRIUM_PROJECT_ID_PROD`

若希望用**特殊 tag** 触发（例如在任意分支打 tag `remote-image/all` 再 push 触发），可在 `.gitlab-ci.yml` 的 `build-remote-image:all` 的 `rules` 里增加一条：`if: $CI_COMMIT_TAG == "remote-image/all"`，并注意 tag 触发的 pipeline 中 `CI_COMMIT_BRANCH` 可能为空，脚本里 push 目标需改为固定分支（如 `main`）或从 tag 解析。

## 单环境构建（沿用原逻辑）

- **test**：push 到以 `test` 结尾的分支且改动了 `Dockerfile.remote` 或 `ci/build_remote_image.sh` 时自动跑。
- **uat**：push 到以 `uat` 结尾的分支时出现 job，需手动执行。
- **prod**：push 到 `main` 或以 `prod` 结尾的分支且改动 remote 相关文件时出现 job，需手动执行。

单环境 job 使用 GitLab 的 **Environment**（test / uat / production）作用域变量：`BOHRIUM_ACCESS_KEY`、`BOHRIUM_PROJECT_ID`。
