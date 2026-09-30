"""Step 3: state.db schema, migrations and server_id."""

import re
import sqlite3

import pytest

from duoware.store.db import MIGRATIONS, StateDb


def test_schema_created_and_reopened(tmp_path):
    path = tmp_path / "data" / "state.db"
    db = StateDb(path)
    assert db.schema_version == len(MIGRATIONS)
    tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "tags", "layout", "venue_presets", "pairings", "camera_overrides", "camera_caps"} <= tables
    assert db.query("PRAGMA journal_mode")[0][0] == "wal"
    db.set_meta("registry_version", "4")
    db.close()
    again = StateDb(path)              # migrations are idempotent, data is kept
    assert again.schema_version == len(MIGRATIONS)
    assert again.get_int_meta("registry_version") == 4
    assert again.get_int_meta("missing", 7) == 7
    again.close()


def test_server_id_is_16_hex_and_stable(tmp_path):
    path = tmp_path / "state.db"
    a = StateDb(path)
    sid = a.server_id
    assert re.fullmatch(r"[0-9a-f]{16}", sid)
    a.close()
    assert StateDb(path).server_id == sid
    assert StateDb(tmp_path / "other.db").server_id != sid


def test_tag_id_check_constraint(tmp_path):
    db = StateDb(tmp_path / "state.db")
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("INSERT INTO tags VALUES (50, 'node', '{}', 1, 0, 'x')")
    with db.tx() as c:
        c.execute("INSERT INTO tags VALUES (49, 'node', '{}', 1, 0, 'x')")
    assert db.query("SELECT COUNT(*) FROM tags")[0][0] == 1


def test_tx_rolls_back_on_error(tmp_path):
    db = StateDb(tmp_path / "state.db")
    with pytest.raises(RuntimeError):
        with db.tx() as c:
            c.execute("INSERT INTO meta VALUES ('k', 'v')")
            raise RuntimeError("boom")
    assert db.get_meta("k") is None
    with db.tx() as c:
        c.execute("INSERT INTO meta VALUES ('k', 'v')")
    assert db.get_meta("k") == "v"


def test_single_camera_per_cam_id(tmp_path):
    db = StateDb(tmp_path / "state.db")
    with db.tx() as c:
        c.execute("INSERT INTO pairings VALUES ('dev-a', 1, 'hash', 'm', 0, NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("INSERT INTO pairings VALUES ('dev-b', 1, 'hash', 'm', 0, NULL)")
