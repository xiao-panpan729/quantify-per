"""
research_report.py — 东方财富研报数据接口
==========================================

5类研报（行业/个股/策略/宏观/晨报），按板块+时间范围查询。

与 macro_sensitivity / shock_detector 同级，供节点地图标注"板块产业政策事件"。

数据源: reportapi.eastmoney.com（免费公开API，无需token）

Usage:
  # CLI — 查行业研报
  python tools/research_report.py --industry 1036 --begin 2026-01-01 --end 2026-06-08
  python tools/research_report.py --industry 1036 --begin 2026-01-01 --save results.json

  # CLI — 查个股研报
  python tools/research_report.py --stock 002594 --begin 2026-01-01 --end 2026-06-08

  # CLI — 查宏观/策略/晨报
  python tools/research_report.py --type macro --begin 2026-01-01
  python tools/research_report.py --type strategy --begin 2026-01-01
  python tools/research_report.py --type morning --begin 2026-01-01

  # CLI — 行业列表
  python tools/research_report.py --list-industry
  python tools/research_report.py --search-industry 半导

  # Python API
  from tools.research_report import fetch_industry_reports
  reports = fetch_industry_reports('1036', '2026-01-01', '2026-06-08')
"""

import json, sys, time
from datetime import datetime, timedelta

import requests

# ── API 端点 ──
LIST_API = 'https://reportapi.eastmoney.com/report/list'    # 行业研报 (GET)
LIST2_API = 'https://reportapi.eastmoney.com/report/list2'   # 个股研报 (POST)
JG_API = 'https://reportapi.eastmoney.com/report/jg'         # 宏观/策略/晨报 (GET)

DETAIL_URLS = {
    'industry': 'https://data.eastmoney.com/report/zw_industry.jshtml?encodeUrl={}',
    'strategy': 'https://data.eastmoney.com/report/zw_strategy.jshtml?encodeUrl={}',
    'macro':    'https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl={}',
    'morning':  'https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl={}',
}

# qType 映射
QTYPE = {
    'industry': '1',   # /list 接口
    'strategy': '2',   # /jg 接口
    'macro':    '3',   # /jg 接口
    'morning':  '4',   # /jg 接口
}

# ── 行业分类（东财行业代码） ──
# 来源: 从 e2660money 包导出, 覆盖51个申万一级+部分细分
INDUSTRIES = {
    "1001": "食品饮料", "1002": "农林牧渔", "1003": "轻工制造", "1004": "商贸零售",
    "1005": "纺织服饰", "1006": "公用事业", "1007": "交通运输", "1008": "房地产",
    "1009": "建筑材料", "1010": "建筑装饰", "1011": "钢铁", "1012": "有色金属",
    "1013": "基础化工", "1014": "医药生物", "1015": "社会服务", "1016": "美容护理",
    "1017": "电子", "1018": "计算机", "1019": "传媒", "1020": "通信",
    "1021": "银行", "1022": "非银金融", "1023": "综合", "1024": "电力设备",
    "1025": "机械设备", "1026": "国防军工", "1027": "汽车", "1028": "家用电器",
    "1029": "环保", "1030": "煤炭", "1031": "光伏设备", "1032": "风电设备",
    "1033": "电池", "1034": "能源金属", "1035": "医疗器械", "1036": "半导体",
    "1037": "消费电子", "1038": "软件开发", "1039": "IT服务", "1040": "游戏",
    "1041": "中药", "1042": "医药商业", "1043": "化学制药", "1044": "生物制品",
    "480": "其他专用机械", "481": "汽车零部件", "482": "其他电子",
    "483": "其他化学制品", "484": "其他软件服务", "485": "其他医药",
    "486": "其他有色", "487": "其他建材",
}

# 反向: 名称→代码
_NAME_TO_CODE = {v: k for k, v in INDUSTRIES.items()}


# ── 帮助函数 ──

def _session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://data.eastmoney.com/report/',
    })
    return s


def _fmt_date(d):
    """确保日期是 YYYY-MM-DD 格式"""
    if isinstance(d, datetime):
        return d.strftime('%Y-%m-%d')
    return str(d)[:10]


# ── 行业查询 ──

def list_industries():
    """返回全部行业列表: list[dict]"""
    return [{'code': k, 'name': v} for k, v in sorted(INDUSTRIES.items())]


def search_industries(keyword):
    """按关键词搜索行业"""
    if not keyword:
        return list_industries()
    result = []
    for code, name in INDUSTRIES.items():
        if keyword in name:
            result.append({'code': code, 'name': name})
    return result


def get_industry_code(name):
    """根据行业名称查代码"""
    return _NAME_TO_CODE.get(name)


# ── 核心查询 ──

def fetch_industry_reports(industry_code, begin_time=None, end_time=None,
                            page_no=1, page_size=20):
    """查行业研报

    Args:
        industry_code: 东财行业代码 (如 '1036'=半导体)
        begin_time, end_time: YYYY-MM-DD
    Returns:
        list[dict] 或 None
    """
    if not end_time:
        end_time = datetime.now().strftime('%Y-%m-%d')
    if not begin_time:
        begin_time = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    params = {
        'pageSize': page_size,
        'pageNo': page_no,
        'beginTime': _fmt_date(begin_time),
        'endTime': _fmt_date(end_time),
        'qType': '1',
        'industryCode': industry_code,
        'industry': '*', 'rating': '*', 'ratingChange': '*',
        'orgCode': '', 'rcode': '', 'fields': '',
    }
    try:
        r = _session().get(LIST_API, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'[research_report] 行业研报请求失败: {e}')
        return None

    return _parse_list_response(data, 'industry')


def fetch_stock_reports(stock_code, begin_time=None, end_time=None,
                         page_no=1, page_size=20):
    """查个股研报（POST请求）"""
    if not end_time:
        end_time = datetime.now().strftime('%Y-%m-%d')
    if not begin_time:
        begin_time = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    payload = {
        'pageSize': page_size, 'pageNo': page_no,
        'pageNum': page_no, 'pageNumber': page_no, 'p': page_no,
        'beginTime': _fmt_date(begin_time),
        'endTime': _fmt_date(end_time),
        'code': stock_code,
        'industryCode': '*', 'rating': None, 'ratingChange': None,
        'orgCode': None, 'rcode': '',
    }
    try:
        r = _session().post(LIST2_API, json=payload,
                            headers={'Content-Type': 'application/json'}, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'[research_report] 个股研报请求失败: {e}')
        return None

    return _parse_list_response(data, 'stock')


def _fetch_jg(qtype, begin_time=None, end_time=None, page_no=1, page_size=20):
    """查宏观/策略/晨报（/jg 接口）"""
    if not end_time:
        end_time = datetime.now().strftime('%Y-%m-%d')
    if not begin_time:
        begin_time = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    params = {
        'pageSize': page_size, 'pageNo': page_no,
        'beginTime': _fmt_date(begin_time), 'endTime': _fmt_date(end_time),
        'qType': qtype,
        'fields': '', 'industry': '*', 'rating': '*', 'ratingChange': '*',
        'orgCode': '', 'rcode': '',
    }
    try:
        r = _session().get(JG_API, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'[research_report] /jg 请求失败: {e}')
        return None

    return _parse_list_response(data, 'jg')


def fetch_macro_reports(begin_time=None, end_time=None, page_no=1, page_size=20):
    """查宏观研究"""
    return _fetch_jg('3', begin_time, end_time, page_no, page_size)


def fetch_strategy_reports(begin_time=None, end_time=None, page_no=1, page_size=20):
    """查策略报告"""
    return _fetch_jg('2', begin_time, end_time, page_no, page_size)


def fetch_morning_reports(begin_time=None, end_time=None, page_no=1, page_size=20):
    """查券商晨报"""
    return _fetch_jg('4', begin_time, end_time, page_no, page_size)


# ── 统一解析 ──

def _parse_list_response(data, source_type):
    """解析/list 和 /jg 返回的JSON"""
    if not data or not isinstance(data, dict):
        return None
    raw_list = data.get('data')
    if not raw_list or not isinstance(raw_list, list):
        return None

    results = []
    for item in raw_list:
        encode_url = item.get('encodeUrl', '') or ''
        info_code = item.get('infoCode', '') or ''
        ref_id = encode_url or info_code

        # 判断研报子类型
        rtype = 'industry'
        col = str(item.get('column', ''))
        if col.startswith('002001001'):
            rtype = 'macro'
        elif col.startswith('002001002'):
            rtype = 'strategy'
        elif col.startswith('002003001'):
            rtype = 'morning'
        elif source_type == 'stock':
            rtype = 'stock'

        # 构建详情页URL
        if rtype in DETAIL_URLS and encode_url:
            detail_url = DETAIL_URLS[rtype].format(encode_url)
        elif info_code:
            detail_url = f'https://data.eastmoney.com/report/zw_industry.jshtml?infocode={info_code}'
        else:
            detail_url = ''

        results.append({
            'title': item.get('title', '').strip(),
            'org_name': item.get('orgSName', '') or item.get('orgName', ''),
            'publish_date': (item.get('publishDate', '') or '')[:10],
            'industry_name': item.get('industryName', ''),
            'industry_code': str(item.get('industryCode', '')),
            'stock_name': item.get('stockName', ''),
            'stock_code': str(item.get('stockCode', '')),
            'rating_name': item.get('ratingName', ''),
            'report_type': rtype,
            'encode_url': encode_url,
            'info_code': info_code,
            'url': detail_url,
        })
    return results


# ── 显示 ──

def display_reports(reports, title='研报'):
    """格式化打印研报列表"""
    if not reports:
        print(f'{title}: 无数据')
        return
    print(f'\n{"="*100}')
    print(f'{title}: {len(reports)} 条')
    print(f'{"="*100}')
    print(f'{"日期":<12} {"机构":<16} {"评级":<8} {"股票/行业":<14} 标题')
    print(f'-'*100)
    for r in reports:
        date = r['publish_date'] or '??'
        org = (r['org_name'] or '??')[:14]
        rating = (r['rating_name'] or '')[:6]
        subject = r['stock_name'] or r['industry_name'] or ''
        subject = subject[:12]
        title_str = r['title'][:50]
        print(f'{date:<12} {org:<16} {rating:<8} {subject:<14} {title_str}')
    print(f'{"="*100}\n')


# ── CLI 入口 ──

def main():
    import argparse
    p = argparse.ArgumentParser(description='东方财富研报查询工具')
    p.add_argument('--industry', help='行业代码，如 1036(半导体)')
    p.add_argument('--stock', help='股票代码，如 002594(比亚迪)')
    p.add_argument('--type', choices=['macro', 'strategy', 'morning'],
                   help='研报类型: macro(宏观) strategy(策略) morning(晨报)')
    p.add_argument('--begin', default='', help='开始日期 YYYY-MM-DD')
    p.add_argument('--end', default='', help='结束日期 YYYY-MM-DD')
    p.add_argument('--page', type=int, default=1, help='页码')
    p.add_argument('--size', type=int, default=20, help='每页条数')
    p.add_argument('--save', help='保存到JSON文件')
    p.add_argument('--list-industry', action='store_true', help='列出全部行业')
    p.add_argument('--search-industry', help='按关键词搜索行业')

    args = p.parse_args()
    begin = args.begin or None
    end = args.end or None

    # 行业列表
    if args.list_industry:
        for ind in list_industries():
            print(ind['code'], ind['name'], sep='\t')
        return

    if args.search_industry:
        for ind in search_industries(args.search_industry):
            print(ind['code'], ind['name'], sep='\t')
        return

    # 查询研报
    reports = None
    label = ''

    if args.industry:
        reports = fetch_industry_reports(
            args.industry, begin, end, args.page, args.size)
        name = INDUSTRIES.get(args.industry, args.industry)
        label = f'行业研报 [{name}({args.industry})]'
    elif args.stock:
        reports = fetch_stock_reports(
            args.stock, begin, end, args.page, args.size)
        label = f'个股研报 [{args.stock}]'
    elif args.type == 'macro':
        reports = fetch_macro_reports(begin, end, args.page, args.size)
        label = '宏观研究'
    elif args.type == 'strategy':
        reports = fetch_strategy_reports(begin, end, args.page, args.size)
        label = '策略报告'
    elif args.type == 'morning':
        reports = fetch_morning_reports(begin, end, args.page, args.size)
        label = '券商晨报'
    else:
        p.print_help()
        return

    if reports is None:
        print(f'{label}: 请求失败')
        return

    display_reports(reports, label)

    if args.save:
        with open(args.save, 'w', encoding='utf-8') as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
        print(f'已保存: {args.save}')


if __name__ == '__main__':
    main()
