"""战役雷达 — 浮动时间轴窗口"""
import math
from datetime import datetime, timedelta

from PyQt5.QtWidgets import QWidget, QApplication, QToolTip, QDesktopWidget, QMenu
from PyQt5.QtCore import Qt, QRect, QPoint
from PyQt5.QtGui import (QPainter, QPen, QColor, QFont, QBrush, QFontMetrics,
                         QCursor)

# ── 配色 ──
BG      = QColor(15, 15, 35, 240)
HEADER  = QColor(22, 33, 62)
TEXT    = QColor(160, 174, 192)
TEXT_HL = QColor(226, 232, 240)
RED     = QColor(233, 69, 96)
GREEN   = QColor(34, 197, 94)
YELLOW  = QColor(255, 193, 7)
GRID    = QColor(45, 55, 72, 100)

CAMP_PROFIT = QColor(34, 197, 94, 100)
CAMP_LOSS   = QColor(233, 69, 96, 100)
CAMP_ACTIVE = QColor(255, 193, 7, 80)

DIR_COLORS = {
    'bullish':       QColor(34, 197, 94),
    'bullish_bias':  QColor(100, 200, 100),
    'neutral':       YELLOW,
    'bearish_bias':  QColor(200, 100, 100),
    'bearish':       RED,
}

W, H = 720, 118
HEADER_H = 24
FOOTER_H = 22
TIMELINE_Y = HEADER_H
TIMELINE_H = H - HEADER_H - FOOTER_H

SIG_COLORS = {
    'buy_signal':  QColor(255, 193, 7),
    'sell_signal': QColor(233, 69, 96),
}


class RadarWindow(QWidget):
    """战役雷达浮动窗"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        # 默认放到右上角
        self._move_default()

        self.code = ''
        self.data = None
        self._drag_pos = None
        self._status_text = '等待通达信...'
        self._signals = []
        self._camp_rects = []
        self._sig_rects = []
        self._ts_start = None
        self._ts_end = None
        self._last_update = ''

        self.setMouseTracking(True)

    def _move_default(self):
        """屏幕右上角"""
        screen = QDesktopWidget().availableGeometry()
        self.move(screen.width() - W - 20, 100)

    # ── 公开接口 ──

    def show_status(self, code, data):
        self.code = code
        self.data = data
        self._last_update = datetime.now().strftime('%H:%M:%S')
        if data and data.get('tracked'):
            self._status_text = f"{data['name']}  评分:{data['score']}/14  {data['direction_label']}  [{data.get('zone_label','')}]"
            self._build_cache(data)
        else:
            self._status_text = f'{code} 未跟踪' if code else '等待通达信...'
            self._signals = []
            self._camp_rects = []
            self._sig_rects = []
        self.update()

    def _build_cache(self, data):
        signals = data.get('signal_events', []) or []
        self._signals = signals
        self._camp_rects = []
        self._sig_rects = []

        ts = data['timeline_start']
        te = data['timeline_end']
        td = data['ts_days']
        self._ts_start = ts
        self._ts_end = te
        tw = W - 40

        def x_of(dt):
            days = (dt - ts).days
            return 20 + int(days / td * tw) if td > 0 else 20

        # 战役色块
        for camp in data.get('campaigns', []):
            try:
                op_dt = datetime.strptime(camp['open']['date'], '%Y%m%d')
                cl_dt = (datetime.strptime(camp['close']['date'], '%Y%m%d')
                         if camp.get('close') else te)
                pct = camp.get('stats', {}).get('total_pct', 0)
                color = CAMP_PROFIT if pct >= 0 else CAMP_LOSS
                if camp['status'] == 'active':
                    color = CAMP_ACTIVE
                x1 = x_of(op_dt)
                x2 = x_of(cl_dt)
                self._camp_rects.append({
                    'rect': QRect(x1, TIMELINE_Y + 6, max(4, x2 - x1), 20),
                    'color': color,
                    'label': f"战#{camp['id'].split('_')[-1]} {pct:+.1f}%",
                })
            except Exception:
                pass

        # 信号标记（菱形 ★买/★卖）
        sig_y = TIMELINE_Y + TIMELINE_H // 2 + 2
        for sig in signals:
            try:
                dt = datetime.strptime(sig['date'], '%Y%m%d')
                x = x_of(dt)
                st = sig['type']
                c = SIG_COLORS.get(st, YELLOW)
                self._sig_rects.append({
                    'x': x, 'y': sig_y,
                    'color': c, 'type': st,
                    'date': sig['date'],
                })
            except Exception:
                pass

    # ── 事件 ──

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if event.y() <= HEADER_H:
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()
                return
            # 点信号弹出日期
            for s in self._sig_rects:
                if abs(event.x() - s['x']) < 6:
                    t = '★买' if s['type'] == 'buy_signal' else '★卖'
                    QToolTip.showText(event.globalPos(),
                                      f"{s['date']} {t}", self)
                    break

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        """双击显示菜单"""
        menu = QMenu(self)
        act_close = menu.addAction('关闭雷达')
        act_top = menu.addAction('窗口置顶' if not self.windowFlags() & Qt.WindowStaysOnTopHint else '取消置顶')
        chosen = menu.exec_(event.globalPos())
        if chosen == act_close:
            self.close()
        elif chosen == act_top:
            flags = self.windowFlags()
            if flags & Qt.WindowStaysOnTopHint:
                self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
            else:
                self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
            self.show()

    # ── 绘图 ──

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(BG)
        p.setPen(QPen(GRID, 1))
        p.drawRoundedRect(1, 1, W - 2, H - 2, 6, 6)
        self._draw_header(p)
        self._draw_timeline(p)
        self._draw_footer(p)
        p.end()

    def _draw_header(self, p):
        p.setBrush(HEADER)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(1, 1, W - 2, HEADER_H, 6, 6)
        p.drawRect(1, HEADER_H // 2, W - 2, HEADER_H // 2)

        # 左侧：代码 + 名称
        p.setPen(TEXT_HL)
        f = QFont('Microsoft YaHei', 9)
        p.setFont(f)
        code = self.code or '等待...'
        name = self.data.get('name', '') if self.data else ''
        p.drawText(10, 0, W - 200, HEADER_H, Qt.AlignVCenter, f'{code}  {name}')

        # 右侧：状态
        if self.data and self.data.get('tracked'):
            sc = self.data['score']
            dir_label = self.data['direction_label']
            st = f'评分:{sc}/14  {dir_label}'
            fm = QFontMetrics(f)
            tw = fm.width(st)
            p.setPen(TEXT)
            p.drawText(W - tw - 24, 0, tw, HEADER_H,
                       Qt.AlignVCenter | Qt.AlignRight, st)

        # 更新时间
        p.setPen(QColor(100, 110, 130))
        p.setFont(QFont('Consolas', 7))
        p.drawText(W - 100, HEADER_H - 10, 90, 10,
                   Qt.AlignRight, self._last_update)

        # ×
        p.setPen(RED)
        p.setFont(QFont('Microsoft YaHei', 9))
        p.drawText(W - 18, 0, 16, HEADER_H, Qt.AlignCenter, '×')

    def _draw_timeline(self, p):
        ts = self._ts_start
        te = self._ts_end
        if not ts or not te:
            p.setPen(TEXT)
            p.setFont(QFont('Microsoft YaHei', 9))
            p.drawText(self.rect(), Qt.AlignCenter, self._status_text)
            return

        td = max(1, (te - ts).days)
        tw = W - 40
        y0 = TIMELINE_Y
        y1 = TIMELINE_Y + TIMELINE_H

        # 网格
        p.setPen(QPen(GRID, 1, Qt.DashLine))
        p.setFont(QFont('Consolas', 7))
        for day_offset in range(0, td + 1, 7):
            dt = ts + timedelta(days=day_offset)
            x = 20 + int(day_offset / td * tw)
            p.drawLine(x, y0 + 14, x, y1 - 2)
            if dt.day <= 7:
                p.setPen(TEXT)
                p.drawText(x - 12, y1 - 10, 50, 10, Qt.AlignLeft,
                           dt.strftime('%m/%d'))
                p.setPen(QPen(GRID, 1, Qt.DashLine))

        # 底线
        p.setPen(QPen(GRID, 1))
        p.drawLine(18, y1 - 2, W - 18, y1 - 2)

        # 战役色块
        for cr in self._camp_rects:
            p.setBrush(cr['color'])
            p.setPen(Qt.NoPen)
            r = cr['rect']
            p.drawRoundedRect(r, 3, 3)
            p.setPen(TEXT_HL)
            p.setFont(QFont('Microsoft YaHei', 7))
            p.drawText(r, Qt.AlignCenter, cr['label'])

        # ★信号
        sig_y = y0 + TIMELINE_H // 2 + 2
        p.setFont(QFont('Microsoft YaHei', 7))
        for s in self._sig_rects:
            p.setBrush(s['color'])
            p.setPen(Qt.NoPen)
            x = s['x']
            # 菱形
            pts = [QPoint(x, sig_y - 4), QPoint(x + 4, sig_y),
                   QPoint(x, sig_y + 4), QPoint(x - 4, sig_y)]
            p.drawPolygon(pts)
            # 标文字
            if s['type'] == 'buy_signal':
                p.setPen(YELLOW)
                p.drawText(x + 5, sig_y - 8, 10, 10, Qt.AlignLeft, '买')
            elif s['type'] == 'sell_signal':
                p.setPen(RED)
                p.drawText(x + 5, sig_y - 8, 10, 10, Qt.AlignLeft, '卖')

        # 当前日期线
        if self._signals:
            try:
                last_dt = datetime.strptime(self._signals[-1]['date'], '%Y%m%d')
                lx = 20 + int((last_dt - ts).days / td * tw)
                p.setPen(QPen(YELLOW, 1, Qt.DashLine))
                p.drawLine(lx, y0 + 14, lx, y1 - 2)
            except Exception:
                pass

        # 空状态
        if not self._camp_rects and not self._sig_rects:
            p.setPen(TEXT)
            p.setFont(QFont('Microsoft YaHei', 8))
            p.drawText(20, y0, tw, TIMELINE_H, Qt.AlignCenter,
                       '暂无战役 — 运行 operation_tracker.py 建立')
        else:
            # 信号统计
            n_buy = sum(1 for s in self._signals if s['type'] == 'buy_signal')
            n_sell = sum(1 for s in self._signals if s['type'] == 'sell_signal')
            stats = f'★{n_buy}买 {n_sell}卖'
            p.setPen(TEXT)
            p.setFont(QFont('Consolas', 7))
            p.drawText(20, y0 + 2, 100, 12, Qt.AlignLeft, stats)

    def _draw_footer(self, p):
        y = H - FOOTER_H
        p.setBrush(QColor(10, 10, 25, 200))
        p.setPen(Qt.NoPen)
        p.drawRect(1, y, W - 2, FOOTER_H)

        if not self.data or not self.data.get('tracked'):
            return

        # 方向色块
        dir_key = self.data.get('direction', '')
        dir_color = DIR_COLORS.get(dir_key, TEXT)
        p.setBrush(dir_color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(8, y + 3, 50, FOOTER_H - 6, 3, 3)
        p.setPen(QColor(15, 15, 35))
        p.setFont(QFont('Microsoft YaHei', 8))
        label_map = {'bullish': '↑上涨', 'bullish_bias': '↑偏多',
                     'neutral': '→中性', 'bearish_bias': '↓偏空',
                     'bearish': '↓下跌'}
        p.drawText(8, y + 3, 50, FOOTER_H - 6, Qt.AlignCenter,
                   label_map.get(dir_key, dir_key))

        # 文字
        p.setPen(TEXT)
        p.setFont(QFont('Microsoft YaHei', 8))
        adv = self.data.get('advice_action', '')
        level = self.data.get('best_signal_level', '')
        dom = self.data.get('dominant_level', '')
        pos = self.data.get('position_zone', '')
        info = f'建议:{adv}  |  级别:{level}  |  主导:{dom}'
        if pos:
            info += f'  |  位置:{pos}'
        p.drawText(66, y, W - 80, FOOTER_H, Qt.AlignVCenter, info)
