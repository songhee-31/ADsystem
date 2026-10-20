"""SQLite 저장소."""
import hashlib
import sqlite3
import threading

import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    customer_id TEXT PRIMARY KEY,
    login_id    TEXT,
    name        TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS entities (
    customer_id TEXT,
    level       TEXT,          -- campaign / adgroup / keyword
    entity_id   TEXT,
    name        TEXT,
    campaign_id TEXT,
    adgroup_id  TEXT,
    PRIMARY KEY (customer_id, entity_id)
);
CREATE TABLE IF NOT EXISTS daily_stats (
    customer_id TEXT,
    level       TEXT,          -- account / campaign / adgroup / keyword
    entity_id   TEXT,
    date        TEXT,
    imp         INTEGER DEFAULT 0,
    clk         INTEGER DEFAULT 0,
    cost        REAL    DEFAULT 0,
    conv        REAL    DEFAULT 0,
    conv_amt    REAL    DEFAULT 0,
    PRIMARY KEY (customer_id, level, entity_id, date)
);
CREATE TABLE IF NOT EXISTS stat_days (
    customer_id TEXT,
    date        TEXT,
    PRIMARY KEY (customer_id, date)
);
CREATE TABLE IF NOT EXISTS change_events (
    uid           TEXT PRIMARY KEY,
    customer_id   TEXT,
    ts            TEXT,        -- 2026-10-19 17:29
    date          TEXT,        -- 2026-10-19
    category      TEXT,        -- 키워드 / 입찰가 / 예산 / ON·OFF / 타겟팅 / 소재 / 구조 / 기타
    change_type   TEXT,        -- 원문 변경 유형
    actor         TEXT,
    campaign_id   TEXT,
    campaign_name TEXT,
    adgroup_id    TEXT,
    adgroup_name  TEXT,
    keyword_ids   TEXT,        -- 키워드 단위 변경일 때 쉼표 구분
    detail        TEXT,
    source        TEXT         -- api / import / demo
);
CREATE INDEX IF NOT EXISTS ix_events_date ON change_events(customer_id, date);
CREATE INDEX IF NOT EXISTS ix_stats ON daily_stats(customer_id, level, entity_id);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def event_uid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def insert_events(conn, events):
    """events: dict 리스트. uid가 같으면 무시(중복 방지). 새로 들어간 개수 반환."""
    n = 0
    with _lock:
        for e in events:
            cur = conn.execute(
                """INSERT OR IGNORE INTO change_events
                (uid, customer_id, ts, date, category, change_type, actor, campaign_id, campaign_name,
                 adgroup_id, adgroup_name, keyword_ids, detail, source)
                VALUES (:uid,:customer_id,:ts,:date,:category,:change_type,:actor,:campaign_id,:campaign_name,
                        :adgroup_id,:adgroup_name,:keyword_ids,:detail,:source)""",
                {k: e.get(k, "") for k in ("uid", "customer_id", "ts", "date", "category", "change_type", "actor",
                                           "campaign_id", "campaign_name", "adgroup_id", "adgroup_name",
                                           "keyword_ids", "detail", "source")},
            )
            n += cur.rowcount
        conn.commit()
    return n


def upsert_stats(conn, rows):
    """rows: (customer_id, level, entity_id, date, imp, clk, cost, conv, conv_amt)"""
    with _lock:
        conn.executemany(
            """INSERT OR REPLACE INTO daily_stats
            (customer_id, level, entity_id, date, imp, clk, cost, conv, conv_amt) VALUES (?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()


def upsert_entities(conn, rows):
    """rows: (customer_id, level, entity_id, name, campaign_id, adgroup_id)"""
    with _lock:
        conn.executemany("INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?)", rows)
        conn.commit()


def mark_stat_day(conn, customer_id, date):
    with _lock:
        conn.execute("INSERT OR IGNORE INTO stat_days VALUES (?,?)", (customer_id, date))
        conn.commit()


def has_stat_day(conn, customer_id, date):
    return conn.execute("SELECT 1 FROM stat_days WHERE customer_id=? AND date=?", (customer_id, date)).fetchone() is not None
