"""Shared pytest fixtures: a minimal but real Skrooge SQLite schema."""

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Keep tests hermetic: never load a developer's real .env file.

    ``Settings.resolve`` calls ``load_dotenv()``, which would otherwise pull a
    project-root ``.env`` into ``os.environ`` and break tests that assert on
    environment precedence. Neutralise it for every test; tests set env vars
    explicitly via ``monkeypatch`` when they need them.
    """
    monkeypatch.setattr("skrooge2firefly.config.load_dotenv", lambda *a, **k: False)


# Column subset the reader actually queries, matching the real Skrooge schema.
_SCHEMA = """
CREATE TABLE bank (id INTEGER PRIMARY KEY, t_name TEXT NOT NULL DEFAULT '');
CREATE TABLE account (
    id INTEGER PRIMARY KEY, t_name TEXT NOT NULL, t_type TEXT NOT NULL DEFAULT 'C',
    t_comment TEXT NOT NULL DEFAULT '', t_close TEXT DEFAULT 'N',
    rd_bank_id INTEGER NOT NULL DEFAULT 0, f_importbalance FLOAT, d_importdate DATE);
CREATE TABLE unit (
    id INTEGER PRIMARY KEY, t_name TEXT NOT NULL, t_symbol TEXT NOT NULL DEFAULT '',
    t_type TEXT NOT NULL DEFAULT 'C', t_internet_code TEXT NOT NULL DEFAULT '',
    i_nbdecimal INT NOT NULL DEFAULT 2);
CREATE TABLE category (id INTEGER PRIMARY KEY, t_name TEXT, t_fullname TEXT);
CREATE TABLE payee (id INTEGER PRIMARY KEY, t_name TEXT NOT NULL DEFAULT '');
CREATE TABLE refund (id INTEGER PRIMARY KEY, t_name TEXT NOT NULL DEFAULT '');
CREATE TABLE operation (
    id INTEGER PRIMARY KEY, i_group_id INTEGER NOT NULL DEFAULT 0,
    t_number TEXT NOT NULL DEFAULT '', d_date DATE NOT NULL,
    rd_account_id INTEGER NOT NULL, t_mode TEXT NOT NULL DEFAULT '',
    r_payee_id INTEGER NOT NULL DEFAULT 0, t_comment TEXT NOT NULL DEFAULT '',
    rc_unit_id INTEGER NOT NULL, t_status TEXT NOT NULL DEFAULT 'N',
    t_template TEXT NOT NULL DEFAULT 'N',
    r_recurrentoperation_id INTEGER NOT NULL DEFAULT 0);
CREATE TABLE suboperation (
    id INTEGER PRIMARY KEY, t_comment TEXT NOT NULL DEFAULT '',
    rd_operation_id INTEGER NOT NULL, r_category_id INTEGER NOT NULL DEFAULT 0,
    f_value FLOAT NOT NULL DEFAULT 0.0, r_refund_id INTEGER NOT NULL DEFAULT 0,
    i_order INTEGER NOT NULL DEFAULT 0);
CREATE TABLE budget (
    id INTEGER PRIMARY KEY, rc_category_id INTEGER NOT NULL DEFAULT 0,
    f_budgeted FLOAT NOT NULL DEFAULT 0.0, i_year INTEGER NOT NULL DEFAULT 2010,
    i_month INTEGER NOT NULL DEFAULT 0);
CREATE TABLE recurrentoperation (
    id INTEGER PRIMARY KEY, d_date DATE NOT NULL, rd_operation_id INTEGER NOT NULL,
    i_period_increment INTEGER NOT NULL DEFAULT 1, t_period_unit TEXT NOT NULL DEFAULT 'M');
CREATE TABLE unitvalue (
    id INTEGER PRIMARY KEY, rd_unit_id INTEGER NOT NULL, d_date DATE NOT NULL,
    f_quantity FLOAT NOT NULL);
"""


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


@pytest.fixture
def skrooge_db(tmp_path: Path) -> Path:
    """An empty Skrooge DB on disk; tests populate it as needed."""
    db = tmp_path / "test.sqlite"
    conn = _connect(str(db))
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def populate():
    """Return a helper that inserts rows into a Skrooge DB file."""

    def _populate(db: Path, table: str, rows: list[dict]) -> None:
        conn = sqlite3.connect(str(db))
        for row in rows:
            cols = ", ".join(row)
            placeholders = ", ".join(f":{c}" for c in row)
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", row)
        conn.commit()
        conn.close()

    return _populate


_TEMPLATE_EXTRA = """
ALTER TABLE category ADD COLUMN rd_category_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE suboperation ADD COLUMN d_date DATE NOT NULL DEFAULT '0000-00-00';
CREATE TABLE parameters (id INTEGER PRIMARY KEY, t_uuid_parent TEXT NOT NULL DEFAULT '',
    t_name TEXT NOT NULL, t_value TEXT NOT NULL DEFAULT '', b_blob BLOB,
    d_lastmodifdate DATE NOT NULL DEFAULT CURRENT_TIMESTAMP, i_tmp INTEGER NOT NULL DEFAULT 0);
CREATE TABLE doctransaction (id INTEGER PRIMARY KEY, t_name TEXT NOT NULL,
    t_mode VARCHAR(1) DEFAULT 'U', d_date DATE NOT NULL, t_savestep VARCHAR(1) DEFAULT 'N',
    t_refreshviews VARCHAR(1) DEFAULT 'Y', i_parent INTEGER);
CREATE TABLE doctransactionitem (id INTEGER PRIMARY KEY,
    rd_doctransaction_id INTEGER NOT NULL, i_object_id INTEGER NOT NULL DEFAULT 0,
    t_object_table TEXT NOT NULL DEFAULT '', t_action TEXT NOT NULL DEFAULT '',
    t_sqlorder TEXT NOT NULL DEFAULT '');
CREATE TABLE doctransactionmsg (id INTEGER PRIMARY KEY,
    rd_doctransaction_id INTEGER NOT NULL, t_message TEXT NOT NULL DEFAULT '',
    t_popup VARCHAR(1) DEFAULT 'Y');
CREATE TABLE operationbalance (r_operation_id INTEGER NOT NULL,
    f_balance FLOAT NOT NULL DEFAULT 0, f_balance_entered FLOAT NOT NULL DEFAULT 0);
CREATE TABLE budgetsuboperation (id INTEGER PRIMARY KEY, id_budget INTEGER,
    id_suboperation INTEGER, i_priority INTEGER);
CREATE TABLE budgetrule (id INTEGER PRIMARY KEY, rc_category_id INTEGER NOT NULL DEFAULT 0);
CREATE TABLE interest (id INTEGER PRIMARY KEY, rd_account_id INTEGER NOT NULL,
    d_date DATE NOT NULL, f_rate FLOAT NOT NULL DEFAULT 0);
CREATE TABLE node (id INTEGER PRIMARY KEY, t_name TEXT NOT NULL DEFAULT '',
    t_fullname TEXT, t_icon TEXT DEFAULT '', f_sortorder FLOAT, t_autostart VARCHAR(1),
    t_data TEXT, rd_node_id INTEGER);
CREATE TABLE vm_budget_tmp (id INTEGER PRIMARY KEY, rc_category_id INTEGER NOT NULL DEFAULT 0);
CREATE TRIGGER cpt_category_fullname1 AFTER INSERT ON category BEGIN
    UPDATE category SET t_fullname=CASE WHEN rd_category_id IS NULL OR rd_category_id=''
        OR rd_category_id=0 THEN new.t_name
        ELSE (SELECT c.t_fullname FROM category c WHERE c.id=new.rd_category_id)
            ||' > '||new.t_name END WHERE id=new.id;
END;
CREATE TRIGGER cpt_category_fullname2 AFTER UPDATE OF t_name, rd_category_id ON category BEGIN
    UPDATE category SET t_fullname=CASE WHEN rd_category_id IS NULL OR rd_category_id=''
        OR rd_category_id=0 THEN new.t_name
        ELSE (SELECT c.t_fullname FROM category c WHERE c.id=new.rd_category_id)
            ||' > '||new.t_name END WHERE id=new.id;
END;
"""


@pytest.fixture
def skrooge_template(tmp_path: Path) -> Path:
    """A Skrooge-shaped template file with seed data the export writer must clear."""
    db = tmp_path / "template.sqlite"
    conn = _connect(str(db))
    conn.executescript(_TEMPLATE_EXTRA)
    conn.executescript(
        """
        INSERT INTO unit (id, t_name, t_symbol, t_type) VALUES
            (1, 'krona suédoise', 'SEK', '1'), (2, 'euro', '€', '2');
        INSERT INTO parameters (t_name, t_value) VALUES ('SKG_DB_VERSION', '1.6');
        INSERT INTO bank (id, t_name) VALUES (1, 'OldBank');
        INSERT INTO account (id, t_name, t_type, rd_bank_id) VALUES (1, 'OldAcct', 'C', 1);
        INSERT INTO payee (id, t_name) VALUES (1, 'OldPayee');
        INSERT INTO category (id, t_name) VALUES (1, 'OldCat');
        INSERT INTO vm_budget_tmp (rc_category_id) VALUES (1);
        INSERT INTO operation (id, d_date, rd_account_id, rc_unit_id)
            VALUES (1, '2019-01-01', 1, 1);
        INSERT INTO suboperation (id, rd_operation_id, f_value) VALUES (1, 1, -5.0);
        INSERT INTO operationbalance (r_operation_id, f_balance) VALUES (1, -5.0);
        INSERT INTO doctransaction (t_name, d_date) VALUES ('old undo', '2019-01-01');
        """
    )
    conn.commit()
    conn.close()
    return db
