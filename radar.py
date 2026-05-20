"""战役雷达 — 通达信寄生式战役时间轴"""
import sys
import re
import ctypes
import win32gui

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

from radar_window import RadarWindow
from radar_data import build_radar_data, is_tracked


def _get_title_gbk(hwnd):
    """GetWindowTextA + GBK 解码（通达信是 GBK 编码程序）"""
    user32 = ctypes.windll.user32
    buf = ctypes.create_string_buffer(512)
    length = user32.GetWindowTextA(hwnd, buf, 512)
    if length > 0:
        return buf.value.decode('gbk', errors='replace')
    return ''


def find_tdx_window():
    """找通达信主窗口，从标题 `... - [K线图-名称]` 提取股票名称。
    返回 (main_hwnd, stock_code, stock_name)
    """
    main_hwnd = None

    def enum_cb(hwnd, ctx):
        try:
            cls = win32gui.GetClassName(hwnd)
            if cls == 'TdxW_MainFrame_Class' and win32gui.IsWindowVisible(hwnd):
                ctx.append(hwnd)
        except Exception:
            pass
        return True

    candidates = []
    win32gui.EnumWindows(enum_cb, candidates)
    if not candidates:
        return None, None, None

    main_hwnd = candidates[0]
    title = _get_title_gbk(main_hwnd)

    # 提取 [...] 中的内容，格式: "xxx-股票名称"
    m = re.search(r'\[(.+?)\]', title)
    if not m:
        return main_hwnd, None, None

    inside = m.group(1)
    parts = inside.split('-', 1)
    stock_name = parts[-1].strip() if len(parts) > 1 else inside.strip()
    code = name_to_code(stock_name)
    return main_hwnd, code, stock_name


# ── 名称 → 代码 反向映射 ──

def _build_name_map():
    from config import NAME_MAP
    mapping = {}
    for code, name in NAME_MAP.items():
        mapping[name] = code
        for suffix in ['ETF', 'ETF广发', 'ETF大成', 'ETF南方', 'ETF华夏']:
            if name.endswith(suffix):
                short = name[:-len(suffix)]
                mapping[short] = code
                mapping[f'{short}ETF'] = code
        if not name.endswith('ETF') and not name.endswith('指'):
            mapping[name] = code
    return mapping

_NAME_TO_CODE = None

def name_to_code(name):
    global _NAME_TO_CODE
    if _NAME_TO_CODE is None:
        _NAME_TO_CODE = _build_name_map()
    if not name:
        return None
    if name in _NAME_TO_CODE:
        return _NAME_TO_CODE[name]
    for n, c in _NAME_TO_CODE.items():
        if n in name or name in n:
            return c
    return None


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('量化战役雷达')
    app.setQuitOnLastWindowClosed(False)

    win = RadarWindow()
    win.show()

    last_code = ''
    poll_count = 0

    def poll():
        nonlocal last_code, poll_count
        poll_count += 1

        hwnd, code, stock_name = find_tdx_window()
        if not hwnd:
            if poll_count % 10 == 0:
                win.show_status('', None)
                win._status_text = '未找到通达信窗口'
                win.update()
                print('[雷达] 未找到通达信窗口')
            return
        if not code:
            return
        if code != last_code:
            last_code = code
            print(f'[雷达] 检测到 {code} {stock_name or ""}')
            if is_tracked(code):
                data = build_radar_data(code)
                n_sig = len(data['signal_events'])
                print(f'[雷达] 已加载: {data["name"]} 评分{data["score"]}/16 {n_sig}个信号')
                win.show_status(code, data)
            else:
                print(f'[雷达] {code} 未跟踪')
                win.show_status(code, None)
                win._status_text = f'{code} 未跟踪'
                win.update()

    print('[雷达] 启动完成，等待通达信...')
    QTimer.singleShot(100, poll)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(1500)

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
