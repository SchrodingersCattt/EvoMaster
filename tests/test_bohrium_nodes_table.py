from pathlib import Path

from src.dao.bohrium_node_leases_table import BohriumNodeLeasesTable
from src.dao.bohrium_nodes_table import BohriumNodesTable


class _FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None, rowcount=1):
        self.sql = None
        self.params = None
        self.fetchone_result = fetchone_result
        self.fetchall_result = fetchall_result
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result


class _FakeConnection:
    def __init__(self, fetchone_result=None, fetchall_result=None, rowcount=1):
        self.cursor_obj = _FakeCursor(
            fetchone_result=fetchone_result,
            fetchall_result=fetchall_result,
            rowcount=rowcount,
        )
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def _make_table(monkeypatch, conn, table_cls=BohriumNodesTable):
    monkeypatch.setattr(table_cls, "init_table", lambda self: None)
    table = table_cls()
    monkeypatch.setattr(table, "get_connection", lambda: conn)
    return table


def test_insert_creating_slot_never_overwrites_existing_node(monkeypatch):
    conn = _FakeConnection()
    table = _make_table(monkeypatch, conn)

    inserted = table.insert_creating_slot(
        "u1", "o1", 99, 12345, "inv-1", "create-token", 600
    )

    assert inserted is True
    assert conn.committed is True
    assert conn.cursor_obj.params == (
        "u1",
        "o1",
        99,
        12345,
        "inv-1",
        "create-token",
        600,
    )
    assert "INSERT IGNORE" in conn.cursor_obj.sql
    assert "node_id" not in conn.cursor_obj.sql.split("VALUES", maxsplit=1)[0]
    assert "ON DUPLICATE KEY UPDATE" not in conn.cursor_obj.sql


def test_mark_slot_ready_is_fenced_by_creator_token(monkeypatch):
    conn = _FakeConnection()
    table = _make_table(monkeypatch, conn)

    updated = table.mark_ready(7, "create-token", 42)

    assert updated is True
    assert conn.cursor_obj.params == (42, 7, "create-token")
    assert "state = 'ready'" in conn.cursor_obj.sql
    assert "state = 'creating'" in conn.cursor_obj.sql
    assert "creating_lease_token = %s" in conn.cursor_obj.sql


def test_attach_creating_node_is_fenced_before_waiting_for_ready(monkeypatch):
    conn = _FakeConnection()
    table = _make_table(monkeypatch, conn)

    attached = table.attach_creating_node(7, "create-token", 42)

    assert attached is True
    assert conn.cursor_obj.params == (42, 7, "create-token")
    assert "state = 'creating'" in conn.cursor_obj.sql
    assert "creating_lease_token = %s" in conn.cursor_obj.sql


def test_legacy_insert_never_overwrites_an_existing_slot(monkeypatch):
    conn = _FakeConnection()
    table = _make_table(monkeypatch, conn)

    assert table.insert_node("u1", "o1", 99, 12345, 42) is True

    assert "INSERT IGNORE" in conn.cursor_obj.sql
    assert "ON DUPLICATE KEY UPDATE" not in conn.cursor_obj.sql


def test_find_one_for_reuse_reads_unique_slot_without_ordering(monkeypatch):
    expected = {"node_id": 42}
    conn = _FakeConnection(fetchone_result=expected)
    table = _make_table(monkeypatch, conn)

    row = table.find_one_for_reuse("u1", "o1", 99, 12345)

    assert row is expected
    assert conn.cursor_obj.params == ("u1", "o1", 99, 12345)
    assert "ORDER BY" not in conn.cursor_obj.sql
    assert "LIMIT 1" in conn.cursor_obj.sql


def test_acquire_lease_fences_retried_invocation_with_new_token(monkeypatch):
    conn = _FakeConnection()
    table = _make_table(monkeypatch, conn, BohriumNodeLeasesTable)

    assert table.table_name == "bohrium_node_leases"
    acquired = table.acquire(7, "session-1", "inv-1", "lease-token", 120)

    assert acquired is True
    assert conn.cursor_obj.params == (
        7,
        "session-1",
        "inv-1",
        "lease-token",
        120,
    )
    assert "ON DUPLICATE KEY UPDATE" in conn.cursor_obj.sql
    assert "lease_token = VALUES(lease_token)" in conn.cursor_obj.sql


def test_heartbeat_and_release_are_fenced_by_invocation_and_token(monkeypatch):
    heartbeat_conn = _FakeConnection()
    heartbeat_table = _make_table(monkeypatch, heartbeat_conn, BohriumNodeLeasesTable)

    assert heartbeat_table.heartbeat("inv-1", "lease-token", 120) is True
    assert heartbeat_conn.cursor_obj.params == (120, "inv-1", "lease-token")
    assert "lease_token = %s" in heartbeat_conn.cursor_obj.sql

    release_conn = _FakeConnection()
    release_table = _make_table(monkeypatch, release_conn, BohriumNodeLeasesTable)

    assert release_table.release("inv-1", "lease-token") is True
    assert release_conn.cursor_obj.params == ("inv-1", "lease-token")
    assert "lease_token = %s" in release_conn.cursor_obj.sql


def test_count_live_leases_excludes_expired_rows(monkeypatch):
    conn = _FakeConnection(fetchone_result={"lease_count": 2})
    table = _make_table(monkeypatch, conn, BohriumNodeLeasesTable)

    assert table.count_live(7) == 2
    assert conn.cursor_obj.params == (7,)
    assert "lease_expires_at > NOW()" in conn.cursor_obj.sql


def test_list_ready_without_live_leases_is_read_only_and_oldest_first(monkeypatch):
    rows = [{"id": 1, "node_id": 20079820}]
    conn = _FakeConnection(fetchall_result=rows)
    table = _make_table(monkeypatch, conn)

    assert table.list_ready_without_live_leases(1000) == rows
    assert conn.cursor_obj.params == (1000,)
    assert "n.state = 'ready'" in conn.cursor_obj.sql
    assert "l.lease_expires_at > NOW()" in conn.cursor_obj.sql
    assert "l.id IS NULL" in conn.cursor_obj.sql
    assert "ORDER BY n.last_used_at ASC, n.id ASC" in conn.cursor_obj.sql
    assert conn.committed is False


def test_recycler_release_requires_token_and_still_expired_deadline(monkeypatch):
    conn = _FakeConnection()
    table = _make_table(monkeypatch, conn, BohriumNodeLeasesTable)

    assert table.release_expired("inv-1", "lease-token") is True
    assert conn.cursor_obj.params == ("inv-1", "lease-token")
    assert "lease_token = %s" in conn.cursor_obj.sql
    assert "lease_expires_at <= NOW()" in conn.cursor_obj.sql


def test_expired_creation_stop_transition_is_fenced(monkeypatch):
    conn = _FakeConnection()
    table = _make_table(monkeypatch, conn)

    assert table.mark_stopping_expired_creation(7, 42, "create-token") is True
    assert conn.cursor_obj.params == (7, 42, "create-token")
    assert "state = 'creating'" in conn.cursor_obj.sql
    assert "creating_lease_expires_at <= NOW()" in conn.cursor_obj.sql


def test_node_lifecycle_migration_preserves_slots_and_adds_fenced_leases():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "src/sql/migrate_add_bohrium_node_lifecycle.sql").read_text()

    assert "MODIFY COLUMN `node_id` INT NULL" in sql
    assert "UNIQUE KEY `uk_user_org_project_sku`" not in sql
    assert "ADD UNIQUE INDEX `uk_node_id` (`node_id`)" in sql
    assert "`state` VARCHAR(32)" in sql
    assert "`creating_invocation_id` VARCHAR(64)" in sql
    assert "`creating_lease_token` VARCHAR(64)" in sql
    assert "`creating_lease_expires_at` DATETIME" in sql
    assert "`lifecycle_policy` VARCHAR(32)" in sql
    assert "`idle_timeout_seconds` INT" in sql
    assert "`idle_expires_at` DATETIME" in sql
    assert "CREATE TABLE `bohrium_node_leases`" in sql
    assert "evo_bohrium_node_leases" not in sql
    assert "UNIQUE KEY `uk_invocation_id` (`invocation_id`)" in sql
    assert "INDEX `idx_slot_expiry` (`node_slot_id`, `lease_expires_at`)" in sql
    assert "worker_id" not in sql
