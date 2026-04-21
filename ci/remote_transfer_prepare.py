#!/usr/bin/env python3
"""
构建 packages/bohrium-transfer wheel，上传到 OSS，并生成注入 ARG 的 Dockerfile.remote.ci。

供 remote-image 分支流水线使用：Bohrium 创建镜像接口只接受 Base64 Dockerfile，无法传
--build-arg，因此需在提交前把 MATMASTER_BOHRIUM_TRANSFER_URL / SHA256 写入 Dockerfile。

依赖环境变量（与 src/dao/oss_io 一致）：
  OSS_ENDPOINT, OSS_BUCKET_NAME, OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET

可选：
  REMOTE_IMAGE_TRANSFER_OSS_PREFIX — 对象键前缀，默认 evomaster/calculation
  CI_COMMIT_SHORT_SHA / CI_PIPELINE_ID — 用于生成唯一对象名（缺失时用 fallback）

输出：$REPO_ROOT/Dockerfile.remote.ci（不提交，供 build_remote_image.sh 上传）
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path


def _docker_arg_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _endpoint_host_for_url(endpoint: str) -> str:
    e = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
    return e


def _upload_wheel(wheel_path: Path, object_key: str) -> str:
    try:
        import oss2
        from oss2.credentials import EnvironmentVariableCredentialsProvider
    except ImportError as e:
        raise SystemExit(
            "需要 oss2：pip install oss2（CI 的 before_script 应已安装）"
        ) from e

    endpoint = (os.getenv("OSS_ENDPOINT") or "").strip()
    bucket_name = (os.getenv("OSS_BUCKET_NAME") or "").strip()
    if not endpoint or not bucket_name:
        raise SystemExit("缺少 OSS_ENDPOINT 或 OSS_BUCKET_NAME，无法上传 wheel。")

    auth = oss2.ProviderAuth(EnvironmentVariableCredentialsProvider())
    bucket = oss2.Bucket(auth, endpoint, bucket_name)
    data = wheel_path.read_bytes()
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": f'attachment; filename="{wheel_path.name}"',
    }
    bucket.put_object(object_key, data, headers=headers)
    host = _endpoint_host_for_url(endpoint)
    return f"https://{bucket_name}.{host}/{object_key.lstrip('/')}"


def main() -> int:
    repo = Path(os.environ.get("CI_PROJECT_DIR", os.getcwd())).resolve()
    dockerfile_src = repo / "Dockerfile.remote"
    dockerfile_out = repo / "Dockerfile.remote.ci"
    if not dockerfile_src.is_file():
        print(f"ERROR: missing {dockerfile_src}", file=sys.stderr)
        return 1

    dist_dir = repo / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["uv", "build", "--package", "matmaster-bohrium-transfer", "--wheel"],
        cwd=repo,
        check=True,
    )
    wheels = sorted(dist_dir.glob("matmaster_bohrium_transfer-*.whl"))
    if not wheels:
        print(f"ERROR: no wheel under {dist_dir}", file=sys.stderr)
        return 1
    wheel_path = wheels[-1]
    sha = _sha256_file(wheel_path)

    prefix = (
        os.getenv("REMOTE_IMAGE_TRANSFER_OSS_PREFIX", "evomaster/calculation")
        .strip()
        .strip("/")
    )
    short_sha = (os.getenv("CI_COMMIT_SHORT_SHA") or "local").strip()
    pipe_id = (os.getenv("CI_PIPELINE_ID") or "0").strip()
    object_key = (
        f"{prefix}/matmaster_bohrium_transfer/{short_sha}-{pipe_id}-{wheel_path.name}"
    )

    public_url = _upload_wheel(wheel_path, object_key)
    print(f"remote_transfer_prepare: wheel={wheel_path.name}", file=sys.stderr)
    print(f"remote_transfer_prepare: oss_key={object_key}", file=sys.stderr)
    print(f"remote_transfer_prepare: url={public_url}", file=sys.stderr)
    print(f"remote_transfer_prepare: sha256={sha}", file=sys.stderr)

    raw = dockerfile_src.read_text(encoding="utf-8")
    patched = re.sub(
        r'^ARG MATMASTER_BOHRIUM_TRANSFER_URL=""\s*\n^ARG MATMASTER_BOHRIUM_TRANSFER_SHA256=""\s*$',
        "ARG MATMASTER_BOHRIUM_TRANSFER_URL=\""
        + _docker_arg_quote(public_url)
        + '\"\nARG MATMASTER_BOHRIUM_TRANSFER_SHA256="'
        + _docker_arg_quote(sha)
        + '"',
        raw,
        count=1,
        flags=re.MULTILINE,
    )
    if patched == raw:
        print(
            "ERROR: 未能替换 Dockerfile.remote 中的 "
            "ARG MATMASTER_BOHRIUM_TRANSFER_URL/SHA256 两行，请检查模板是否与脚本一致。",
            file=sys.stderr,
        )
        return 1

    dockerfile_out.write_text(patched, encoding="utf-8")
    print(f"Wrote {dockerfile_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
