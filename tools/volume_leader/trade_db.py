"""实时交易台账 — SQLite 单表记录入场→出场完整生命周期"""
import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRADES_DB = REPO_ROOT / 'signals' / 'tracking' / 'realtime_trades.db'


def _conn():
    os.makedirs(TRADES_DB.parent, exist_ok=True)
    c = sqlite3.connect(str(TRADES_DB))
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c


def _add_column_if_missing(c, table, col_name, col_def):
    """安全加列，忽略已存在错误"""
    try:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {col_name} {col_def}')
    except sqlite3.OperationalError:
        pass  # 列已存在


def init_db():
    """建表（幂等）"""
    c = _conn()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_time TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            entry_price REAL NOT NULL,
            filter_level TEXT NOT NULL DEFAULT 'any',
            entry_type TEXT NOT NULL DEFAULT '★买',
            period TEXT NOT NULL DEFAULT 'min5',

            -- 入场 5min K线快照
            bar_ts INTEGER,
            ma5 REAL, ma10 REAL, ma20 REAL,
            expma12 REAL, expma50 REAL,
            expma_cross TEXT,
            close_price REAL,
            volume REAL,

            -- 止损
            band_low REAL,

            -- 60分钟环境
            min60_above_expma50 INTEGER DEFAULT 0,
            zone TEXT,
            cascade_type TEXT,
            resonance_detail TEXT,

            -- 出场
            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            bars_held INTEGER,

            -- 做T点（JSON数组）
            t_points TEXT DEFAULT '[]',

            -- 生命周期
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS t_points_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER REFERENCES trades(id),
            t_time TEXT NOT NULL,
            t_price REAL NOT NULL,
            t_type TEXT DEFAULT '★卖做T',
            pnl_from_entry REAL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_filter ON trades(filter_level);
        CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);
    ''')
    # 迁移：旧库无 band_low 列
    _add_column_if_missing(c, 'trades', 'band_low', 'REAL')
    c.commit()
    c.close()


def record_entry(entry):
    """创建新交易记录，返回 trade_id"""
    c = _conn()
    # 进场条件快照提取
    conditions = entry.get('entry_conditions', {})
    cascade = entry.get('cascade_type', '')
    detail_parts = entry.get('detail', '')

    row = (
        entry.get('time', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        entry.get('code', ''),
        entry.get('name', ''),
        entry.get('price', 0),
        entry.get('filter_level', 'any'),
        entry.get('entry_type', '★买'),
        entry.get('period', 'min5'),
        conditions.get('bar_ts'),
        conditions.get('ma5'),
        conditions.get('ma10'),
        conditions.get('ma20'),
        conditions.get('expma12'),
        conditions.get('expma50'),
        conditions.get('expma_cross'),
        conditions.get('close_price'),
        conditions.get('volume'),
        conditions.get('band_low'),
        1 if conditions.get('min60_above_expma50') else 0,
        entry.get('zone', ''),
        cascade,
        entry.get('resonance_detail', ''),
        entry.get('t_points_json', '[]'),
    )

    cur = c.execute('''
        INSERT INTO trades (entry_time, code, name, entry_price,
            filter_level, entry_type, period,
            bar_ts, ma5, ma10, ma20, expma12, expma50, expma_cross, close_price, volume,
            band_low, min60_above_expma50, zone, cascade_type, resonance_detail, t_points)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', row)
    trade_id = cur.lastrowid
    c.commit()
    c.close()
    return trade_id


def record_exit(code, exit_time, exit_price, exit_reason, pnl_pct=None, bars_held=None):
    """关闭指定标的最近一笔 open 交易"""
    c = _conn()
    # SQLite 不支持 UPDATE ... ORDER BY LIMIT，用子查询取最近一笔 open 的 id
    row = c.execute(
        'SELECT id, entry_price FROM trades WHERE code=? AND status=? ORDER BY id DESC LIMIT 1',
        (code, 'open')
    ).fetchone()
    if not row:
        c.close()
        return False
    trade_id, entry_price = row
    calc_pnl = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0
    c.execute(
        'UPDATE trades SET exit_time=?, exit_price=?, exit_reason=?, pnl_pct=?, bars_held=?, status=? WHERE id=?',
        (exit_time, exit_price, exit_reason, pnl_pct or calc_pnl, bars_held, 'closed', trade_id)
    )
    c.commit()
    c.close()
    return True


def record_t_point(code, t_time, t_price, t_type='★卖做T'):
    """记录做T点，不结束交易"""
    c = _conn()
    # 找最近 open 交易
    cur = c.execute('''
        SELECT id, entry_price FROM trades
        WHERE code=? AND status='open'
        ORDER BY id DESC LIMIT 1
    ''', (code,))
    row = cur.fetchone()
    if not row:
        c.close()
        return None
    trade_id, entry_price = row
    pnl = round((t_price - entry_price) / entry_price * 100, 2) if entry_price else 0

    # 写入 t_points_log
    c.execute('''
        INSERT INTO t_points_log (trade_id, t_time, t_price, t_type, pnl_from_entry)
        VALUES (?,?,?,?,?)
    ''', (trade_id, t_time, t_price, t_type, pnl))

    # 更新 trades 表的 t_points JSON
    cur = c.execute('SELECT t_points FROM trades WHERE id=?', (trade_id,))
    tps = json.loads(cur.fetchone()[0] or '[]')
    tps.append({'time': t_time, 'price': t_price, 'type': t_type, 'pnl_pct': pnl})
    c.execute('UPDATE trades SET t_points=? WHERE id=?', (json.dumps(tps, ensure_ascii=False), trade_id))

    c.commit()
    c.close()
    return trade_id


def get_open_entries(code=None):
    """获取 open 状态的交易"""
    c = _conn()
    if code:
        cur = c.execute('SELECT * FROM trades WHERE status=? AND code=? ORDER BY id DESC', ('open', code))
    else:
        cur = c.execute('SELECT * FROM trades WHERE status=? ORDER BY id DESC', ('open',))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    c.close()
    return [dict(zip(cols, r)) for r in rows]


def get_stats_by_level():
    """按 filter_level 统计"""
    c = _conn()
    cur = c.execute('''
        SELECT filter_level,
               COUNT(*) as total,
               SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as closed,
               ROUND(AVG(CASE WHEN status='closed' THEN pnl_pct END), 2) as avg_pnl,
               ROUND(SUM(CASE WHEN status='closed' AND pnl_pct>0 THEN 1 ELSE 0 END) * 100.0 /
                     NULLIF(SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END), 0), 1) as win_rate,
               SUM(CASE WHEN exit_reason='止损' THEN 1 ELSE 0 END) as stopped
        FROM trades
        GROUP BY filter_level
        ORDER BY CASE filter_level
            WHEN 'resonance' THEN 1 WHEN 'jincha' THEN 2 WHEN 'ma' THEN 3 WHEN 'any' THEN 4 END
    ''')
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    c.close()
    return [dict(zip(cols, r)) for r in rows]


def get_all_trades(limit=50):
    """获取最近交易列表"""
    c = _conn()
    cur = c.execute('SELECT * FROM trades ORDER BY id DESC LIMIT ?', (limit,))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    c.close()
    return [dict(zip(cols, r)) for r in rows]
