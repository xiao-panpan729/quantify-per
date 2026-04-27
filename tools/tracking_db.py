# -*- coding: utf-8 -*-
"""
跟踪数据库管理模块
SQLite 存储：跟踪名单 + 趋势线快照 + 验证记录

数据库位置: D:\\quantify-per\\data\\tracking.db

用法:
    from tracking_db import TrackingDB
    db = TrackingDB()

    # 添加/查看跟踪名单
    db.add_stock('159740', 'sz', '恒生科技ETF')
    db.list_stocks()

    # 保存趋势线快照
    db.save_snapshot('159740', 'min60', trend_values, bar_datetimes)

    # 读取最近N根趋势线
    snap = db.get_latest_snapshot('159740', 'min60')

    # 记录验证结果
    db.log_verify('159740', 'min60', 'PASS', max_diff=0.001)

    # 命令行初始化/查看
    python tracking_db.py init      # 初始化数据库
    python tracking_db.py status    # 查看状态
"""

import sqlite3
import os
import sys
import io
from datetime import datetime

DB_PATH = r'D:\quantify-per\data\tracking.db'

# 支持的周期列表
VALID_PERIODS = ['daily', 'min15', 'min30', 'min60']


class TrackingDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # 允许按列名访问
        self._init_tables()

    def _init_tables(self):
        """创建表（如果不存在）"""
        cur = self.conn.cursor()

        # 表1：跟踪名单
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tracking_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'sz',
                name TEXT DEFAULT '',
                added_date TEXT DEFAULT (date('now')),
                is_active INTEGER DEFAULT 1,
                UNIQUE(code, market)
            )
        """)

        # 表2：趋势线快照（每次计算后存入）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trend_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                period TEXT NOT NULL,
                snapshot_time TEXT DEFAULT (datetime('now')),
                -- JSON 数组存储最近的趋势线值和对应时间
                trend_values TEXT,     -- JSON: [val1, val2, ...]
                bar_times TEXT,         -- JSON: ["2026-04-17 10:30", ...]
                close_prices TEXT,      -- JSON: [c1, c2, ...]
                n_bars INTEGER DEFAULT 50,   -- 快照包含的bar数
                n_param INTEGER DEFAULT 55,  -- 趋势线N参数
                last_trend REAL,             -- 最新一根的趋势线值
                last_close REAL,             -- 最新一根的收盘价
                UNIQUE(code, period, snapshot_time)
            )
        """)

        # 表3：验证日志（pytdx API 抽验记录）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS verify_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verify_time TEXT DEFAULT (datetime('now')),
                code TEXT NOT NULL,
                period TEXT NOT NULL,
                result TEXT CHECK(result IN ('PASS','FAIL','WARN','SKIP')) NOT NULL,
                compared_count INTEGER DEFAULT 0,  -- 对比了多少根
                max_diff REAL DEFAULT 0,          -- 最大差值
                avg_diff REAL DEFAULT 0,          -- 平均差值
                note TEXT DEFAULT ''
            )
        """)

        # 表4：参数配置（各周期的 N 值等）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS period_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT DEFAULT '*',
                period TEXT NOT NULL,
                param_name TEXT NOT NULL,
                param_value REAL NOT NULL,
                confirmed INTEGER DEFAULT 0,       -- 是否已通过 TDX 验证确认
                confirmed_date TEXT,
                note TEXT DEFAULT '',
                UNIQUE(code, period, param_name)
            )
        """)

        # 索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_code_period ON trend_snapshots(code, period)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_time ON trend_snapshots(snapshot_time)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_verify_code_period ON verify_log(code, period)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_verify_time ON verify_log(verify_time)")

        self.conn.commit()
        print('[OK] 数据库已就绪: %s' % self.db_path)

    def close(self):
        self.conn.close()

    # ================================================================
    # 跟踪名单操作
    # ================================================================

    def add_stock(self, code, market='sz', name=''):
        """添加跟踪标的，已存在则更新名称"""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT OR IGNORE INTO tracking_list (code, market, name) VALUES (?, ?, ?)",
                (code, market, name)
            )
            if cur.rowcount == 0:
                # 已存在，更新名称
                if name:
                    cur.execute(
                        "UPDATE tracking_list SET name=? WHERE code=? AND market=?",
                        (name, code, market)
                    )
            self.conn.commit()
            print('[OK] 跟踪标的: %s (%s)' % (code, name or '未命名'))
        except Exception as e:
            print('[ERROR] 添加失败: %s' % e)

    def remove_stock(self, code, market='sz'):
        """移除跟踪标的（软删除）"""
        cur = self.conn.cursor()
        cur.execute("UPDATE tracking_list SET is_active=0 WHERE code=? AND market=?", (code, market))
        self.conn.commit()
        print('[OK] 已移除: %s' % code)

    def list_stocks(self):
        """列出所有活跃的跟踪标的"""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM tracking_list WHERE is_active=1 ORDER BY id")
        rows = cur.fetchall()
        print('\n--- 跟踪名单 ---')
        print('%-4s %-12s %-4s %-15s %-10s' % ('ID', '代码', '市场', '名称', '加入日期'))
        print('-' * 55)
        for r in rows:
            print('%-4d %-12s %-4s %-15s %s' % (r['id'], r['code'], r['market'], r['name'] or '-', r['added_date']))
        return [dict(r) for r in rows]

    # ================================================================
    # 趋势线快照操作
    # ================================================================

    def save_snapshot(self, code, period, trend_values, bar_times=None,
                      close_prices=None, n_bars=None, n_param=None):
        """
        保存一次趋势线快照

        参数:
            code: 标的代码如 '159740'
            period: 周期如 'daily'/'min15'/'min30'/'min60'
            trend_values: list[float], 趋势线值序列（从旧到新）
            bar_times: list[str], 对应时间标签 (可选)
            close_prices: list[float], 对应收盘价 (可选)
            n_bars: 本次快照包含多少根
            n_param: 趋势线使用的N参数
        """
        import json
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        last_trend = float(trend_values[-1]) if trend_values else None
        last_close = float(close_prices[-1]) if close_prices else None

        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO trend_snapshots
            (code, period, snapshot_time, trend_values, bar_times, close_prices,
             n_bars, n_param, last_trend, last_close)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            code, period, now,
            json.dumps(trend_values),
            json.dumps(bar_times) if bar_times else None,
            json.dumps(close_prices) if close_prices else None,
            len(trend_values) if not n_bars else n_bars,
            n_param or 55,
            last_trend,
            last_close,
        ))
        self.conn.commit()

        # 自动清理：每只标的前周期只保留最近 30 个快照
        self._cleanup_snapshots(code, period, keep=30)

        return True

    def get_latest_snapshot(self, code, period):
        """
        获取某只标的某周期的最新一次快照
        返回 dict 或 None
        """
        import json
        cur = self.conn.cursor()
        cur.execute("""
            SELECT * FROM trend_snapshots
            WHERE code=? AND period=?
            ORDER BY snapshot_time DESC LIMIT 1
        """, (code, period))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        # 解析 JSON 字段
        for field in ('trend_values', 'bar_times', 'close_prices'):
            if d.get(field):
                d[field] = json.loads(d[field])
        return d

    def get_snapshot_history(self, code, period, limit=5):
        """获取最近 N 次快照历史"""
        import json
        cur = self.conn.cursor()
        cur.execute("""
            SELECT snapshot_time, n_bars, n_param, last_trend, last_close
            FROM trend_snapshots
            WHERE code=? AND period=?
            ORDER BY snapshot_time DESC LIMIT ?
        """, (code, period, limit))
        return [dict(r) for r in cur.fetchall()]

    # ================================================================
    # 验证记录
    # ================================================================

    def log_verify(self, code, period, result, compared_count=0,
                   max_diff=0, avg_diff=0, note=''):
        """记录一次 pytdx 抽验结果"""
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO verify_log
            (verify_time, code, period, result, compared_count, max_diff, avg_diff, note)
            VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?)
        """, (code, period, result.upper(), compared_count, max_diff, avg_diff, note))
        self.conn.commit()
        print('[VERIFY] %s %s → %s (max_diff=%.4f)' % (code, period, result.upper(), max_diff))

    def get_verify_stats(self, code=None, days=7):
        """获取近期验证统计"""
        cur = self.conn.cursor()
        if code:
            cur.execute("""
                SELECT result, COUNT(*) as cnt
                FROM verify_log
                WHERE code=? AND verify_time >= date('now', '-%d days')
                GROUP BY result
            """ % days, (code,))
        else:
            cur.execute("""
                SELECT result, COUNT(*) as cnt
                FROM verify_log
                WHERE verify_time >= date('now', '-%d days')
                GROUP BY result
            """ % days)
        return {r['result']: r['cnt'] for r in cur.fetchall()}

    # ================================================================
    # 参数配置
    # ================================================================

    def set_n_param(self, period, n_value, code='*', confirmed=False, note=''):
        """设置某个周期的 N 参数"""
        cur = self.conn.cursor()
        conf_date = datetime.now().strftime('%Y-%m-%d') if confirmed else None
        cur.execute("""
            INSERT OR REPLACE INTO period_params
            (code, period, param_name, param_value, confirmed, confirmed_date, note)
            VALUES (?, ?, 'trend_N', ?, ?, ?, ?)
        """, (code, period, int(n_value), 1 if confirmed else 0, conf_date, note))
        self.conn.commit()
        status = '✅已确认' if confirmed else '⏳待确认'
        print('[PARAM] %s N=%d %s %s' % (period, n_value, status, note))

    def get_all_params(self):
        """获取所有参数配置"""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM period_params ORDER BY code, period")
        return [dict(r) for r in cur.fetchall()]

    def print_status(self):
        """打印数据库整体状态摘要"""
        print('\n' + '=' * 55)
        print('跟踪数据库状态')
        print('=' * 55)

        # 跟踪名单
        stocks = self.list_stocks()

        # 各周期最新快照
        print('\n--- 各周期最新快照 ---')
        print('%-8s %-8s %-20s %-12s %-10s' % ('代码', '周期', '时间', '趋势线(最后)', '收盘价'))
        print('-' * 65)
        cur = self.conn.cursor()
        for s in stocks:
            for p in VALID_PERIODS:
                snap = self.get_latest_snapshot(s['code'], p)
                if snap:
                    t_str = snap['snapshot_time'][5:16]  # MM-DD HH:MM
                    tl = '%.3f' % snap['last_trend'] if snap['last_trend'] else '-'
                    cl = '%.3f' % snap['last_close'] if snap['last_close'] else '-'
                    print('%-8s %-8s %-20s %-12s %-10s' % (s['code'], p, t_str, tl, cl))

        # 参数配置
        params = self.get_all_params()
        if params:
            print('\n--- 趋势线 N 参数配置 ---')
            print('%-6s %-8s %-8s %s' % ('范围', '周期', 'N值', '状态'))
            print('-' * 45)
            for p in params:
                scope = p['code'] if p['code'] != '*' else '(全局)'
                st = '✅确认(%s)' % p['confirmed_date'][:10] if p['confirmed'] else '⏳待定'
                print('%-6s %-8s %-8d %s' % (scope, p['period'], int(p['param_value']), st))

        # 近期验证统计
        stats = self.get_verify_stats(days=30)
        if stats:
            total = sum(stats.values())
            print('\n--- 近30天验证统计 (共%d次) ---' % total)
            for res, cnt in sorted(stats.items()):
                pct = 100.0 * cnt / total if total else 0
                marker = {'PASS': '✓', 'FAIL': '✗', 'WARN': '⚠', 'SKIP': '-'}.get(res, '?')
                print('  %s %s: %d次 (%.1f%%)' % (marker, res, cnt, pct))

        # 数据库大小
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        print('\n数据库大小: %.1f KB' % (db_size / 1024.0))

    # ================================================================
    # 内部工具
    # ================================================================

    def _cleanup_snapshots(self, code, period, keep=30):
        """清理旧快照，只保留最新的 N 条"""
        cur = self.conn.cursor()
        cur.execute("""
            DELETE FROM trend_snapshots
            WHERE rowid NOT IN (
                SELECT rowid FROM trend_snapshots
                WHERE code=? AND period=?
                ORDER BY snapshot_time DESC LIMIT ?
            )
            AND code=? AND period=?
        """, (code, period, keep, code, period))
        if cur.rowcount > 0:
            self.conn.commit()


# ================================================================
# CLI 入口
# ================================================================

if __name__ == '__main__':
    db = TrackingDB()

    if len(sys.argv) < 2:
        db.print_status()
    elif sys.argv[1] == 'init':
        # 初始化 + 导入当前跟踪名单
        # 直接硬编码默认标的（避免跨目录导入 config.py）
        default_stocks = [
            ('sz159740', 'sz', '恒生科技ETF'),
            ('sh520600', 'sh', '港股通汽车ETF'),
        ]
        for code, mkt, name in default_stocks:
            db.add_stock(code, mkt, name)
        # 设置已知正确的参数
        db.set_n_param('lc60', 40, confirmed=True, note='TDX验证: 04-17/20 8根全部diff=0')
        print('\n[INIT] 完成！默认跟踪标的和已确认参数已写入。')
        db.print_status()

    elif sys.argv[1] == 'status':
        db.print_status()

    elif sys.argv[1] == 'add' and len(sys.argv) >= 3:
        code = sys.argv[2]
        mkt = sys.argv[3] if len(sys.argv) > 3 else 'sz'
        name = sys.argv[4] if len(sys.argv) > 4 else ''
        db.add_stock(code, mkt, name)

    elif sys.argv[1] == 'verify-log':
        # 显示验证日志
        cur = db.conn.cursor()
        cur.execute("SELECT * FROM verify_log ORDER BY verify_time DESC LIMIT 20")
        rows = cur.fetchall()
        print('\n--- 最近20条验证记录 ---')
        for r in rows:
            print('  %s | %s %s → %s | max=%.4f | %s' % (
                r['verify_time'], r['code'], r['period'],
                r['result'], r['max_diff'] or 0, r['note']))

    else:
        print('用法:')
        print('  python tracking_db.py              # 查看状态')
        print('  python tracking_db.py init         # 初始化（导入默认标的+参数）')
        print('  python tracking_db.py add CODE [MARKET] [NAME]')
        print('  python tracking_db.py verify-log   # 查看验证日志')

    db.close()
