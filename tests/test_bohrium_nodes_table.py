from src.dao.bohrium_nodes_table import BohriumNodesTable


class _FakeCursor:
    rowcount = 1

    def __init__(self, fetchone_result=None):
        self.sql = None
        self.params = None
        self.fetchone_result = fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.fetchone_result


class _FakeConnection:
    def __init__(self, fetchone_result=None):
        self.cursor_obj = _FakeCursor(fetchone_result=fetchone_result)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def test_insert_node_upserts_user_org_project_sku_slot(monkeypatch):
    table = BohriumNodesTable()
    conn = _FakeConnection()
    monkeypatch.setattr(table, "get_connection", lambda: conn)

    inserted = table.insert_node("u1", "o1", 99, 12345, 42)

    assert inserted is True
    assert conn.committed is True
    assert conn.cursor_obj.params == ("u1", "o1", 99, 12345, 42)
    assert "ON DUPLICATE KEY UPDATE" in conn.cursor_obj.sql
    assert "node_id = VALUES(node_id)" in conn.cursor_obj.sql


def test_find_one_for_reuse_reads_unique_slot_without_ordering(monkeypatch):
    expected = {"node_id": 42}
    table = BohriumNodesTable()
    conn = _FakeConnection(fetchone_result=expected)
    monkeypatch.setattr(table, "get_connection", lambda: conn)

    row = table.find_one_for_reuse("u1", "o1", 99, 12345)

    assert row is expected
    assert conn.cursor_obj.params == ("u1", "o1", 99, 12345)
    assert "ORDER BY" not in conn.cursor_obj.sql
    assert "LIMIT 1" in conn.cursor_obj.sql
