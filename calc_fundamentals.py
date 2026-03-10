#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基本面确定性计算脚本 - calc_fundamentals.py
版本: 2.0.0 (2026-03-10)

功能: 从 stdin 读取 stock-data finance 的 JSON 输出，
      确定性解析并评分基本面指标（净利润增速/营收增速/ROE/经营现金流）。

设计理由:
  技术面已有 calc_indicators.py 做确定性计算（零幻觉），
  但基本面仍依赖大模型手动解析嵌套 JSON → 容易出错。
  本脚本将基本面评分也纳入 Python 确定性计算层。

变更日志:
  v1.0.0 (2026-03-10): 初始版本
  v1.1.0 (2026-03-10): 修复3个Bug:
    - Bug1: stdin UTF-8编码（Windows管道GBK→解析失败）
    - Bug2: _find_row_value空行名匹配（''是任何字符串子串→表头被错误匹配）
    - Bug3: _find_row_value精确匹配优先+跳过空值标题行（"股东权益"空值行干扰ROE计算）
  v2.0.0 (2026-03-10): 扩展多期趋势数据:
    - 新增 trend_data 字段（近4期营收/净利润趋势+净利率变化）
    - 新增 extract_a_trend_data() / extract_us_hk_trend_data()
    - JSON 输出向后兼容（新增字段，旧消费者忽略即可）

用法:
  # A股（summary 接口，一次返回利润表+资产负债表+现金流量表）
  stock-data finance sh600519 summary | python calc_fundamentals.py --market A

  # A股（lrb 接口，返回三张表合并）
  stock-data finance sh600519 lrb | python calc_fundamentals.py --market A

  # 美股（需要分别获取 income 和 balance，管道合并）
  # 方式1: 先保存文件，再合并
  stock-data finance AAPL.O income 4 > aapl_income.json
  stock-data finance AAPL.O balance 2 > aapl_balance.json
  python calc_fundamentals.py --market US --income aapl_income.json --balance aapl_balance.json

  # 港股（需要 zhsy + zcfz + xjll）
  stock-data finance hk00700 zhsy 4 > hk700_zhsy.json
  stock-data finance hk00700 zcfz 2 > hk700_zcfz.json
  stock-data finance hk00700 xjll 4 > hk700_xjll.json
  python calc_fundamentals.py --market HK --income hk700_zhsy.json --balance hk700_zcfz.json --cashflow hk700_xjll.json

依赖: 仅使用 Python 标准库（json/sys/re/argparse），无需 pip install
"""

import json
import sys
import re
import argparse


# ============================================================
# 1. 通用金额解析
# ============================================================

def parse_amount(text):
    """
    解析中文格式金额字符串为浮点数（单位: 亿元）。

    支持格式:
      "1,437.56亿元" → 1437.56
      "745.25亿元"   → 745.25
      "-54.23亿元"   → -54.23
      "381.97亿元"   → 381.97
      "0.00元"       → 0.0
      "30.41元"      → 0.000030 (元→亿元)
      "51.53元"      → 0.000052
      "--"           → None
      ""             → None

    返回: float (亿元) 或 None
    """
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if text in ("--", "", "N/A", "0"):
        return None

    # 提取数字部分（含负号和逗号）
    match = re.match(r'^([+-]?[\d,]+\.?\d*)', text)
    if not match:
        return None

    num_str = match.group(1).replace(",", "")
    try:
        value = float(num_str)
    except ValueError:
        return None

    # 单位换算
    if "万亿" in text:
        value *= 10000  # 万亿→亿
    elif "亿" in text:
        pass  # 已经是亿
    elif "百万" in text or "万元" in text:
        value /= 10000  # 万→亿
    elif "元" in text:
        value /= 100000000  # 元→亿
    # 无单位则假设为原始数值（可能是增长率%等）

    return value


def parse_pct(text):
    """
    解析百分比字符串为浮点数。

    支持格式:
      "6.25%"   → 6.25
      "-2.81"   → -2.81 (同比增速，无%号)
      "24.64%"  → 24.64
      "--"      → None

    返回: float 或 None
    """
    if not text or not isinstance(text, str):
        return None
    text = text.strip().rstrip("%")
    if text in ("--", "", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ============================================================
# 2. 数据解析 - 按市场分类
# ============================================================

def _extract_cell_text(row, col_idx):
    """
    安全提取A股 lrb 格式的嵌套单元格文本。

    数据格式: row = [[[名称,""], [值,""]], ...]
    或: row = [[名称,""], [值,""]]

    自动适配1~3层嵌套，返回字符串。
    """
    if not isinstance(row, list) or len(row) <= col_idx:
        return ""
    cell = row[col_idx]
    # 逐层解包直到找到字符串
    depth = 0
    while isinstance(cell, list) and depth < 3:
        if len(cell) == 0:
            return ""
        cell = cell[0]
        depth += 1
    return str(cell) if cell is not None else ""


def _filter_http_lines(raw_text):
    """过滤 stock-data 的 [HTTP ...] 调试日志行。"""
    lines = raw_text.splitlines()
    clean = [l for l in lines if not l.strip().startswith('[HTTP')]
    return '\n'.join(clean)


def _safe_json_load(raw_text):
    """安全加载 JSON，自动过滤 HTTP 日志。"""
    clean = _filter_http_lines(raw_text)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return None


def _find_row_value(table_rows, row_name, col_idx=1):
    """
    在港美股财务报表的嵌套数组中，按行名查找值。

    匹配优先级: 精确匹配 > 前向包含(目标名在实际名中) > 反向包含
    跳过: 空行名、空值行（标题/分类行）

    返回: (value_text, yoy_pct_text) 或 (None, None)
    """
    exact_match = None
    forward_match = None  # row_name in clean_name
    reverse_match = None  # clean_name in row_name

    for row in table_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        name_cell = row[0]
        if not isinstance(name_cell, list) or len(name_cell) < 1:
            continue
        name = str(name_cell[0]).strip()
        clean_name = re.sub(r'^[\(\)（）\-\+]+', '', name).strip()
        if not clean_name:
            continue

        # 提取值
        val_cell = row[col_idx] if len(row) > col_idx else None
        if not isinstance(val_cell, list) or len(val_cell) == 0:
            continue
        val_text = str(val_cell[0]) if val_cell[0] is not None else ""
        # 跳过空值行（标题/分类行，如"股东权益"无数值）
        if not val_text or val_text.strip() == "":
            continue
        yoy_text = str(val_cell[2]) if len(val_cell) > 2 else None

        if clean_name == row_name:
            exact_match = (val_text, yoy_text)
            break  # 精确匹配直接返回
        elif row_name in clean_name and forward_match is None:
            forward_match = (val_text, yoy_text)
        elif clean_name in row_name and len(clean_name) >= 3 and reverse_match is None:
            reverse_match = (val_text, yoy_text)

    return exact_match or forward_match or reverse_match or (None, None)


def parse_a_summary(obj):
    """
    解析A股 summary 接口。

    返回 dict: {
      report_period, revenue_growth, net_profit_growth, roe, cashflow
    }
    """
    data = obj.get("data", {}).get("data", {})
    period = obj.get("data", {}).get("latest", "未知")

    rev_growth = parse_pct(data.get("yysr_tb"))
    profit_growth = parse_pct(data.get("jrl_tb"))
    roe = parse_pct(data.get("roe_weighted"))

    return {
        "report_period": period,
        "revenue_growth": rev_growth,
        "net_profit_growth": profit_growth,
        "roe": roe,
        "cashflow": None,  # summary 不含现金流
        "cashflow_source": "N/A（summary 不含现金流）",
        "data_source": "summary",
    }


def parse_a_lrb(obj):
    """
    解析A股 lrb/xjll 接口（返回三张表合并）。

    data 结构: [利润表数组, 资产负债表数组, 现金流量表数组]
    每张表: [[表头], [行1], [行2], ...]
    每行: [[名称,""], [值,""]]
    """
    tables = obj.get("data", [])
    result = {
        "report_period": None,
        "revenue_growth": None,
        "net_profit_growth": None,
        "roe": None,
        "cashflow": None,
        "cashflow_value": None,
        "cashflow_source": "N/A",
        "data_source": "lrb",
    }

    for table in tables:
        if not isinstance(table, list) or len(table) < 2:
            continue

        # 表头: [[[表名,""], [报告期,""]], ...] — 嵌套两层列表
        header = table[0]
        table_name = _extract_cell_text(header, 0)

        if "利润" in table_name:
            result["report_period"] = _extract_cell_text(header, 1)
            for row in table[1:]:
                name = _extract_cell_text(row, 0)
                val = _extract_cell_text(row, 1)
                if "净利润增长率" in name:
                    result["net_profit_growth"] = parse_pct(val)
                elif "营业总收入增长率" in name:
                    result["revenue_growth"] = parse_pct(val)

        elif "资产负债" in table_name:
            for row in table[1:]:
                name = _extract_cell_text(row, 0)
                val = _extract_cell_text(row, 1)
                if "净资产收益率" in name:
                    result["roe"] = parse_pct(val)

        elif "现金流" in table_name:
            for row in table[1:]:
                name = _extract_cell_text(row, 0)
                val = _extract_cell_text(row, 1)
                if "经营现金流净额" in name:
                    amount = parse_amount(val)
                    result["cashflow"] = "正" if amount and amount > 0 else "负" if amount is not None else None
                    result["cashflow_value"] = val
                    result["cashflow_source"] = "lrb"

    return result


def parse_us_hk_table(obj, row_names):
    """
    解析美股/港股财务报表的通用函数。

    obj: JSON 对象
    row_names: dict, 要查找的行名映射 {key: [候选行名列表]}

    返回: dict, {key: (value_text, yoy_pct_text)}
    """
    result = {}
    report_date = None

    data = obj.get("data", {})
    tables = data.get("data", [])

    if not tables:
        return result, report_date

    # 取第一个报表期（最新）
    latest = tables[0] if tables else []
    if not latest:
        return result, report_date

    # 第一行是表头，含报告日期
    if latest and isinstance(latest[0], list) and len(latest[0]) > 1:
        date_cell = latest[0][1]
        if isinstance(date_cell, list):
            report_date = str(date_cell[0]) if date_cell else None
        else:
            report_date = str(date_cell)

    for key, candidates in row_names.items():
        for name in candidates:
            val, yoy = _find_row_value(latest, name)
            if val is not None:
                result[key] = (val, yoy)
                break
        if key not in result:
            result[key] = (None, None)

    return result, report_date


# ============================================================
# 2b. 多期趋势数据提取（v2.0 新增）
# ============================================================

def extract_a_trend_data(obj):
    """
    从A股 lrb 格式提取近4期趋势数据。

    A股 lrb 表头含多列报告期（如 [报告期1, 报告期2, ...]），
    每行的各列对应各期数值。本函数遍历利润表的多列，
    提取各期营收/净利润绝对值和同比增速。

    返回: dict trend_data 或 None（数据不足时）
    """
    tables = obj.get("data", [])
    if not isinstance(tables, list):
        return None

    trend = {"periods": [], "revenue": [], "net_profit": [], "net_margin": []}

    for table in tables:
        if not isinstance(table, list) or len(table) < 2:
            continue
        header = table[0]
        table_name = _extract_cell_text(header, 0)

        if "利润" not in table_name:
            continue

        # 提取多列报告期名（表头第1列起）
        num_cols = len(header)
        periods = []
        for ci in range(1, min(num_cols, 5)):  # 最多4期
            p = _extract_cell_text(header, ci)
            if p:
                periods.append((ci, p))

        if not periods:
            return None

        trend["periods"] = [p for _, p in periods]

        # 遍历数据行，查找营收和净利润
        rev_rows = {}   # col_idx -> (value_text, yoy_text)
        np_rows = {}

        for row in table[1:]:
            name = _extract_cell_text(row, 0)
            if not name:
                continue

            for ci, _ in periods:
                val = _extract_cell_text(row, ci)
                if not val:
                    continue

                if "营业总收入增长率" in name:
                    # lrb 增长率行只有一个值（最新期），不是多列
                    # 跳过，我们从绝对值自行计算
                    pass
                elif "营业总收入" in name and "增长" not in name:
                    rev_rows[ci] = val
                elif "净利润增长率" in name:
                    pass
                elif "净利润" in name and "增长" not in name:
                    np_rows[ci] = val

        # 组装趋势数据
        for ci, period in periods:
            rev_text = rev_rows.get(ci)
            rev_amt = parse_amount(rev_text) if rev_text else None
            trend["revenue"].append({
                "period": period,
                "value_text": rev_text or "--",
                "value_yi": rev_amt,
            })

            np_text = np_rows.get(ci)
            np_amt = parse_amount(np_text) if np_text else None
            trend["net_profit"].append({
                "period": period,
                "value_text": np_text or "--",
                "value_yi": np_amt,
            })

            # 净利率 = 净利润 / 营收
            if rev_amt and np_amt and rev_amt != 0:
                margin = round(np_amt / rev_amt * 100, 2)
            else:
                margin = None
            trend["net_margin"].append({
                "period": period,
                "pct": margin,
            })

        # 补算同比增速（当期 vs 同期数据不在 lrb 列中，用相邻期近似）
        for series_key in ("revenue", "net_profit"):
            series = trend[series_key]
            for i, item in enumerate(series):
                if i + 1 < len(series):
                    cur = item.get("value_yi")
                    prev = series[i + 1].get("value_yi")
                    if cur is not None and prev is not None and prev != 0:
                        item["qoq_pct"] = round((cur - prev) / abs(prev) * 100, 2)

        break  # 只处理利润表

    if not trend["periods"]:
        return None
    return trend


def extract_us_hk_trend_data(income_obj, market):
    """
    从美股/港股 income 接口提取近4期趋势数据。

    income_obj.data.data 是多个报表期数组（tables[0]=最新, tables[1]=次新...）。
    遍历前4期，从各期提取营收/净利润/净利率。

    返回: dict trend_data 或 None
    """
    data = income_obj.get("data", {})
    tables = data.get("data", [])
    if not tables:
        return None

    if market == "US":
        row_map = {
            "revenue": ["营业收入"],
            "net_profit": ["净利润"],
        }
    else:  # HK
        row_map = {
            "revenue": ["营业收入"],
            "net_profit": ["归属母公司所有者净利润", "除税后溢利", "净利润"],
        }

    trend = {"periods": [], "revenue": [], "net_profit": [], "net_margin": []}

    for idx in range(min(len(tables), 4)):
        t = tables[idx]
        if not isinstance(t, list) or len(t) < 2:
            continue

        # 报告期
        period = None
        if isinstance(t[0], list) and len(t[0]) > 1:
            date_cell = t[0][1]
            if isinstance(date_cell, list):
                period = str(date_cell[0]) if date_cell else f"Period-{idx}"
            else:
                period = str(date_cell) if date_cell else f"Period-{idx}"
        period = period or f"Period-{idx}"
        trend["periods"].append(period)

        # 查找各指标
        rev_val, rev_yoy = (None, None)
        np_val, np_yoy = (None, None)

        for key, candidates in row_map.items():
            for name in candidates:
                val, yoy = _find_row_value(t, name)
                if val is not None:
                    if key == "revenue":
                        rev_val, rev_yoy = val, yoy
                    else:
                        np_val, np_yoy = val, yoy
                    break

        rev_amt = parse_amount(rev_val)
        np_amt = parse_amount(np_val)

        trend["revenue"].append({
            "period": period,
            "value_text": rev_val or "--",
            "value_yi": rev_amt,
            "yoy_pct": parse_pct(rev_yoy),
        })
        trend["net_profit"].append({
            "period": period,
            "value_text": np_val or "--",
            "value_yi": np_amt,
            "yoy_pct": parse_pct(np_yoy),
        })

        if rev_amt and np_amt and rev_amt != 0:
            margin = round(np_amt / rev_amt * 100, 2)
        else:
            margin = None
        trend["net_margin"].append({
            "period": period,
            "pct": margin,
        })

    if not trend["periods"]:
        return None
    return trend


# ============================================================
# 3. 评分引擎
# ============================================================

def score_fundamentals(parsed):
    """
    根据解析后的基本面数据进行评分（25分满分制）。

    评分规则:
      - 净利润增长 ≥25%: +10分; <去年同期: -5分
      - 营收增长 ≥20%: +5分; <去年同期: -5分
      - ROE ≥17%: +5分; <17%: -5分
      - 经营现金流为正: +5分; 为负或N/A: +0分
      - 保底机制: 最多扣10分（最低15分）
      - 缺失项从满分中剔除

    参数: parsed - dict, 含 revenue_growth/net_profit_growth/roe/cashflow
    返回: dict, 含 score/max_score/items/missing 等
    """
    items = []
    missing = []
    total_score = 0
    total_max = 0
    total_deduct = 0

    # --- 净利润增长 (10分) ---
    npg = parsed.get("net_profit_growth")
    if npg is not None:
        total_max += 10
        if npg >= 25:
            s = 10
            status = "✅"
            detail = f"{npg:.1f}% ≥ 25%"
        elif npg >= 0:
            s = 0
            status = "⚠️"
            detail = f"{npg:.1f}% < 25%（正增长但未达标）"
        else:
            s = 0
            total_deduct += 5
            status = "❌"
            detail = f"{npg:.1f}%（同比下滑，扣5分）"
        total_score += s
        items.append({
            "name": "净利润增长", "max": 10, "score": s,
            "status": status, "detail": detail, "source": "finance",
        })
    else:
        missing.append("净利润增长（数据未获取）")

    # --- 营收增长 (5分) ---
    rg = parsed.get("revenue_growth")
    if rg is not None:
        total_max += 5
        if rg >= 20:
            s = 5
            status = "✅"
            detail = f"{rg:.1f}% ≥ 20%"
        elif rg >= 0:
            s = 0
            status = "⚠️"
            detail = f"{rg:.1f}% < 20%（正增长但未达标）"
        else:
            s = 0
            total_deduct += 5
            status = "❌"
            detail = f"{rg:.1f}%（同比下滑，扣5分）"
        total_score += s
        items.append({
            "name": "营收增长", "max": 5, "score": s,
            "status": status, "detail": detail, "source": "finance",
        })
    else:
        missing.append("营收增长（数据未获取）")

    # --- ROE (5分) ---
    roe = parsed.get("roe")
    if roe is not None:
        total_max += 5
        if roe >= 17:
            s = 5
            status = "✅"
            detail = f"{roe:.1f}% ≥ 17%"
        else:
            s = 0
            total_deduct += 5
            status = "❌"
            detail = f"{roe:.1f}% < 17%"
        total_score += s
        items.append({
            "name": "ROE", "max": 5, "score": s,
            "status": status, "detail": detail, "source": parsed.get("roe_source", "finance"),
        })
    else:
        missing.append("ROE（数据未获取）")

    # --- 经营现金流 (5分) ---
    cf = parsed.get("cashflow")
    cf_source = parsed.get("cashflow_source", "N/A")
    if cf is not None:
        total_max += 5
        if cf == "正":
            s = 5
            status = "✅"
            detail = f"经营现金流为正 ({parsed.get('cashflow_value', '')})"
        else:
            s = 0
            status = "❌"
            detail = f"经营现金流为负 ({parsed.get('cashflow_value', '')})"
        total_score += s
        items.append({
            "name": "经营现金流", "max": 5, "score": s,
            "status": status, "detail": detail, "source": cf_source,
        })
    else:
        missing.append(f"经营现金流（{cf_source}）")

    # --- 扣分保底 ---
    # 最多扣10分（从基础25分中扣），保底15分
    actual_deduct = min(total_deduct, 10)
    raw_score = max(total_score - actual_deduct, 0)

    # 保底机制：仅当有扣分时触发（负增长/不达标导致的扣分）
    # 保底线 = 15/25 × effective_max（按比例缩放）
    if total_max > 0 and actual_deduct > 0:
        floor = round(15.0 / 25.0 * total_max, 1)
        final_score = max(raw_score, floor)
    else:
        final_score = raw_score

    return {
        "score": round(final_score, 1),
        "max_score": total_max,
        "theoretical_max": 25,
        "deductions": actual_deduct,
        "items": items,
        "missing": missing,
        "report_period": parsed.get("report_period"),
        "data_source": parsed.get("data_source"),
    }


# ============================================================
# 4. 主函数
# ============================================================

def main():
    """
    入口: 从 stdin 或文件读取财务 JSON → 解析 → 评分 → 输出。
    """
    parser = argparse.ArgumentParser(
        description="米勒维尼基本面评分脚本 v2.0.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  # A股 (summary, 最简单)
  stock-data finance sh600519 summary | python calc_fundamentals.py --market A

  # A股 (lrb, 含现金流)
  stock-data finance sh600519 lrb | python calc_fundamentals.py --market A

  # 美股 (income + balance, cashflow 已知不可用)
  python calc_fundamentals.py --market US --income aapl_income.json --balance aapl_balance.json

  # 港股 (zhsy + zcfz + xjll)
  python calc_fundamentals.py --market HK --income hk_zhsy.json --balance hk_zcfz.json --cashflow hk_xjll.json
        """,
    )
    parser.add_argument('--market', required=True, choices=['A', 'HK', 'US'],
                        help='市场类型: A(A股)/HK(港股)/US(美股)')
    parser.add_argument('--income', help='利润表 JSON 文件（美股/港股必需）')
    parser.add_argument('--balance', help='资产负债表 JSON 文件（美股/港股需要，用于自算 ROE）')
    parser.add_argument('--cashflow', help='现金流量表 JSON 文件（港股可选）')
    args = parser.parse_args()

    # Windows UTF-8 兼容
    import io
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    # ========== 数据加载 ==========
    parsed = {
        "report_period": None,
        "revenue_growth": None,
        "net_profit_growth": None,
        "roe": None,
        "roe_source": "finance",
        "cashflow": None,
        "cashflow_value": None,
        "cashflow_source": "N/A",
        "data_source": args.market,
    }

    # stdin 也需要 UTF-8 编码（Windows 管道可能是 GBK）
    if hasattr(sys.stdin, 'buffer'):
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8', errors='replace')

    if args.market == "A":
        # A股：从 stdin 读取（summary 或 lrb）
        raw = sys.stdin.read()
        if not raw.strip():
            print("[错误] stdin 为空", file=sys.stderr)
            sys.exit(1)
        obj = _safe_json_load(raw)
        if not obj or obj.get("code", -1) != 0:
            print(f"[错误] JSON 解析失败或 API 返回错误", file=sys.stderr)
            sys.exit(1)

        # 判断是 summary 还是 lrb 格式
        data = obj.get("data", {})
        if isinstance(data, dict) and "latest" in data:
            # summary 格式
            parsed = parse_a_summary(obj)
        elif isinstance(data, list):
            # lrb/xjll 格式（三张表合并）
            parsed = parse_a_lrb(obj)
        else:
            print("[错误] 无法识别A股数据格式", file=sys.stderr)
            sys.exit(1)

    elif args.market in ("US", "HK"):
        # 美股/港股：从文件读取
        if not args.income:
            print("[错误] 美股/港股需要 --income 参数", file=sys.stderr)
            sys.exit(1)

        # --- 利润表 ---
        with open(args.income, 'r', encoding='utf-8') as f:
            income_obj = _safe_json_load(f.read())
        if not income_obj:
            print(f"[错误] 无法解析 income 文件: {args.income}", file=sys.stderr)
            sys.exit(1)

        if args.market == "US":
            row_map = {
                "revenue": ["营业收入"],
                "net_profit": ["净利润"],
            }
        else:  # HK
            row_map = {
                "revenue": ["营业收入"],
                "net_profit": ["归属母公司所有者净利润", "除税后溢利", "净利润"],
            }

        income_data, report_date = parse_us_hk_table(income_obj, row_map)
        parsed["report_period"] = report_date
        parsed["data_source"] = "income"

        # 营收增速
        rev_val, rev_yoy = income_data.get("revenue", (None, None))
        parsed["revenue_growth"] = parse_pct(rev_yoy)

        # 净利润增速
        np_val, np_yoy = income_data.get("net_profit", (None, None))
        parsed["net_profit_growth"] = parse_pct(np_yoy)

        # --- 资产负债表 (自算 ROE) ---
        if args.balance:
            with open(args.balance, 'r', encoding='utf-8') as f:
                balance_obj = _safe_json_load(f.read())
            if balance_obj:
                if args.market == "US":
                    b_map = {"equity": ["股东权益合计", "权益总额"]}
                else:
                    b_map = {"equity": ["归属母公司股东权益", "股东权益合计"]}

                bal_data, _ = parse_us_hk_table(balance_obj, b_map)
                equity_text, _ = bal_data.get("equity", (None, None))
                equity = parse_amount(equity_text)
                net_profit = parse_amount(np_val)

                if equity and net_profit and equity > 0:
                    roe = net_profit / equity * 100
                    parsed["roe"] = round(roe, 2)
                    parsed["roe_source"] = f"自算（净利润{np_val}/权益{equity_text}）"
                else:
                    parsed["roe_source"] = "N/A（权益或净利润缺失）"

        # --- 现金流量表 ---
        if args.cashflow:
            with open(args.cashflow, 'r', encoding='utf-8') as f:
                cf_obj = _safe_json_load(f.read())
            if cf_obj:
                cf_map = {
                    "operating_cf": [
                        "经营活动产生现金净流量",
                        "经营活动现金流净额",
                    ],
                }
                cf_data, _ = parse_us_hk_table(cf_obj, cf_map)
                cf_text, _ = cf_data.get("operating_cf", (None, None))
                cf_amount = parse_amount(cf_text)
                if cf_amount is not None:
                    parsed["cashflow"] = "正" if cf_amount > 0 else "负"
                    parsed["cashflow_value"] = cf_text
                    parsed["cashflow_source"] = "cashflow"

        # 美股 cashflow 已知不可用
        if args.market == "US" and not args.cashflow:
            parsed["cashflow_source"] = "N/A（美股 cashflow 接口不可用）"

    # ========== 评分 ==========
    result = score_fundamentals(parsed)

    # ========== 趋势数据提取 (v2.0 新增) ==========
    trend_data = None
    if args.market == "A":
        # 仅 lrb 格式支持多期（summary 无多期数据）
        if isinstance(obj.get("data"), list):
            trend_data = extract_a_trend_data(obj)
    elif args.market in ("US", "HK"):
        trend_data = extract_us_hk_trend_data(income_obj, args.market)

    # ========== 输出 ==========
    print("=" * 60)
    print(f"基本面评分结果 (calc_fundamentals v2.0.0)")
    print(f"市场: {args.market}  |  报告期: {result['report_period']}")
    print(f"数据来源: {result['data_source']}")
    print("=" * 60)

    for item in result["items"]:
        print(f"  {item['status']} {item['name']}: {item['detail']}")
        print(f"     得分: {item['score']}/{item['max']}分  [来源: {item['source']}]")

    if result["missing"]:
        print(f"\n【缺失项】({len(result['missing'])}项)")
        for m in result["missing"]:
            print(f"  ⚠️ {m}")

    if result["deductions"] > 0:
        print(f"\n  扣分: -{result['deductions']}分（保底机制: 最多扣10分）")

    print(f"\n{'=' * 60}")
    print(f"【基本面评分汇总】")
    print(f"  得分: {result['score']}/{result['max_score']}分")
    print(f"  理论满分: {result['theoretical_max']}分")

    if result['max_score'] > 0:
        mapped = round(result['score'] / result['max_score'] * 100, 1)
        coverage = round(result['max_score'] / result['theoretical_max'] * 100, 1)
        print(f"  映射得分: {mapped}分 (映射到100分制)")
        print(f"  分母覆盖率: {coverage}%")
    else:
        print(f"  映射得分: 无法计算（有效满分为0）")

    # 趋势概览
    if trend_data and trend_data.get("periods"):
        print(f"\n【多期趋势概览】(v2.0 新增)")
        print(f"  覆盖期数: {len(trend_data['periods'])}")
        print(f"  报告期: {' → '.join(trend_data['periods'])}")
        for item in trend_data.get("revenue", [])[:1]:
            if item.get("value_text") != "--":
                print(f"  最新营收: {item['value_text']}")
        for item in trend_data.get("net_profit", [])[:1]:
            if item.get("value_text") != "--":
                print(f"  最新净利润: {item['value_text']}")

    # 硬门槛检查
    if result['max_score'] > 0 and result['score'] < 15:
        print(f"\n  ⛔ 硬门槛触发: 基本面 < 15分 → D级，直接排除")

    print(f"{'=' * 60}")

    # 输出 JSON 供管道调用
    print(f"\n<!-- JSON_OUTPUT_START -->")
    json_out = {
        "version": "2.0.0",
        "market": args.market,
        "report_period": result["report_period"],
        "score": result["score"],
        "max_score": result["max_score"],
        "theoretical_max": result["theoretical_max"],
        "items": result["items"],
        "missing": result["missing"],
        "deductions": result["deductions"],
    }
    if trend_data:
        json_out["trend_data"] = trend_data
    print(json.dumps(json_out, ensure_ascii=False, indent=2))
    print(f"<!-- JSON_OUTPUT_END -->")


if __name__ == "__main__":
    main()
