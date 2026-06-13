from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pymysql
import pytest
from dotenv import dotenv_values

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SQL_FILE = _REPO_ROOT / "src" / "sql" / "create_bohrium_jobs_table.sql"


def _test_db_config() -> dict[str, Any]:
    """只读取 .env.test；本 fixture 会 DROP/CREATE，不允许普通 MYSQL_* 覆盖。"""
    env_path = _REPO_ROOT / ".env.test"
    if not env_path.exists():
        pytest.skip("bohrium_jobs DAO tests require .env.test")
    values = dotenv_values(env_path)

    required = ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_DATABASE")
    missing = [key for key in required if not values.get(key)]
    if missing:
        pytest.skip(f".env.test missing required MySQL keys: {', '.join(missing)}")

    database = str(values["MYSQL_DATABASE"])
    allow = os.getenv("ALLOW_DESTRUCTIVE_BOHRIUM_JOBS_TESTS") == "1"
    is_test_db = (
        database.endswith("_test")
        or database.startswith("test_")
        or database in {"matmaster_test", "matmaster_evo_test"}
    )
    if not is_test_db and not allow:
        pytest.fail(
            "Refusing destructive bohrium_jobs tests against non-test database "
            f"{database!r}; use a *_test/test_* database or set "
            "ALLOW_DESTRUCTIVE_BOHRIUM_JOBS_TESTS=1 for a disposable DB."
        )

    return {
        "host": values["MYSQL_HOST"],
        "port": int(values["MYSQL_PORT"]),
        "user": values["MYSQL_USER"],
        "password": values.get("MYSQL_PASSWORD") or "",
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }


@pytest.fixture(scope="session")
def bohrium_jobs_db_config() -> dict[str, Any]:
    """连库 + DROP/CREATE bohrium_jobs；连不上则 skip 整个依赖它的测试。"""
    cfg = _test_db_config()
    try:
        conn = pymysql.connect(**cfg)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"bohrium_jobs DAO tests require MySQL from .env.test: {exc}")
    ddl = _SQL_FILE.read_text(encoding="utf-8").rstrip().rstrip(";")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS v")
            version = str(cur.fetchone()["v"])
            major_minor_patch = tuple(
                int(p) for p in version.split("-")[0].split(".")[:3]
            )
            if major_minor_patch < (8, 0, 16):
                pytest.skip(f"bohrium_jobs needs MySQL >= 8.0.16, got {version}")
            cur.execute("DROP TABLE IF EXISTS `bohrium_jobs`")
            cur.execute(ddl)
        conn.commit()
    finally:
        conn.close()
    return cfg


@pytest.fixture()
def jobs_table(bohrium_jobs_db_config: dict[str, Any]):
    """每个测试前 TRUNCATE，返回注入测试库配置的 BohriumJobsTable。"""
    from src.dao.bohrium_jobs_table import BohriumJobsTable

    table = BohriumJobsTable(db_config=bohrium_jobs_db_config)
    with table.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE `bohrium_jobs`")
        conn.commit()
    return table


@pytest.fixture()
def db_conn(bohrium_jobs_db_config: dict[str, Any]):
    """裸连接，给约束/并发测试直接执行 SQL 用。"""
    conn = pymysql.connect(**bohrium_jobs_db_config)
    try:
        yield conn
    finally:
        conn.close()
