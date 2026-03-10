#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
总分汇总脚本 - calc_score.py
版本: 1.1.0 (2026-03-10)

功能:
  1. 标的分类: 自动检测 ETF/ETN/个股，ETF/ETN 剔除基本面分母
  2. 总分汇总: 读取技术面+基本面 JSON + 催化剂分数 → 确定性计算映射总分
  3. 评级输出: A+/A/B/C/D 五级 + 仓位建议 + 硬门槛检查 + JSON

用法:
  # 模式A: 从预计算 JSON 文件汇总（最常用）
  python calc_score.py --tech tech.json --fund fund.json --catalyst 8 --code usTSLA

  # 模式B: 从 stdin 读取 K 线 JSON 直接计算技术面（与 --tech 互斥）
  stock-data kline usTSLA day 252 qfq | python calc_score.py --kline-stdin --code usTSLA

  # ETF 标的（自动剔除基本面分母）
  python calc_score.py --tech tech.json --catalyst 10 --code sh510300

  # 手动指定标的类型（覆盖自动检测）
  python calc_score.py --tech tech.json --fund fund.json --catalyst 8 --code AAPL --type stock

变更历史:
  v1.0.0: 初始版本
  v1.1.0: 新增 --kline-stdin 模式（复用 calc_indicators.py 函数直接从 stdin 计算技术面）;
          修复硬门槛绕过（未提供 --fund 时个股标注 fund_missing 而非静默跳过）;
          统一版本号到 v1.1.0

依赖: Python 标准库（json/sys/re/argparse/math/os）+ 同目录 calc_indicators.py
"""

import json
import sys
import re
import argparse
import math
import os

# --kline-stdin 模式依赖: 从同目录 calc_indicators.py 复用公共函数
from calc_indicators import (
    parse_kline_json,
    calculate_ma,
    calculate_rsi,
    calculate_macd,
    check_sepa,
    analyze_volume,
    calculate_score_mapping,
    assess_data_completeness,
    determine_confidence,
)


# ============================================================
# 1. 标的分类
# ============================================================

# A股 ETF 代码前缀（交易所+前缀数字）
_A_ETF_PREFIXES = (
    "sh510", "sh511", "sh512", "sh513", "sh515", "sh516", "sh518",
    "sz159", "sz160", "sz161", "sz162", "sz163", "sz164",
)

# 已知美股 ETF 代码（大写，部分常见品种）
_US_KNOWN_ETFS = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "ARKK", "ARKW", "ARKF",
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLY", "XLU", "XLC", "XLRE",
    "GLD", "SLV", "USO", "UNG", "TLT", "IEF", "HYG", "LQD", "BND",
    "EEM", "EFA", "VWO", "VEA", "IEMG",
    "SOXX", "SMH", "SOXL", "SOXS", "TQQQ", "SQQQ", "UPRO", "SPXU",
    "KWEB", "FXI", "MCHI", "CQQQ", "ASHR",
    "VNQ", "REET", "IYR",
}

# 已知美股 ETN 代码
_US_KNOWN_ETNS = {
    "VXX", "UVXY", "SVXY", "TVIX",
}


def classify_security(code, override_type=None):
    """
    自动检测标的类型。

    参数:
      code          - 标的代码（如 "usTSLA", "sh510300", "hk00700"）
      override_type - 手动覆盖: "etf"/"etn"/"stock"

    返回: "ETF" / "ETN" / "STOCK"
    """
    if override_type:
        t = override_type.upper()
        if t in ("ETF", "ETN", "STOCK"):
            return t
        return "STOCK"  # 无效值降级

    if not code:
        return "STOCK"

    c = code.strip()
    c_lower = c.lower()
    c_upper = c.upper()

    # --- A 股 ETF ---
    for prefix in _A_ETF_PREFIXES:
        if c_lower.startswith(prefix):
            return "ETF"

    # --- 港股 ETF（代码含 ETF 或已知代码段） ---
    if c_lower.startswith("hk"):
        num_part = re.sub(r'^hk0*', '', c_lower)
        if "etf" in c_lower:
            return "ETF"
        # 港股 ETF 代码段: 02800-02899, 03000-03199 等
        try:
            hk_num = int(num_part)
            if (2800 <= hk_num <= 2899) or (3000 <= hk_num <= 3199):
                return "ETF"
        except ValueError:
            pass

    # --- 美股 ETF/ETN ---
    # 提取纯代码部分（去掉 "us" 前缀和 ".O"/".N" 后缀）
    pure_code = c_upper
    if pure_code.startswith("US"):
        pure_code = pure_code[2:]
    pure_code = re.sub(r'\.[A-Z]+$', '', pure_code)

    if pure_code in _US_KNOWN_ETFS:
        return "ETF"
    if pure_code in _US_KNOWN_ETNS:
        return "ETN"

    # 关键词检测（兜底）
    if "ETF" in c_upper:
        return "ETF"
    if "ETN" in c_upper:
        return "ETN"

    return "STOCK"


# ============================================================
# 2. JSON 加载
# ============================================================

def _filter_http_lines(raw_text):
    """过滤 stock-data 的 [HTTP ...] 调试日志行。"""
    lines = raw_text.splitlines()
    clean = [l for l in lines if not l.strip().startswith('[HTTP')]
    return '\n'.join(clean)


def _extract_json_block(text):
    """
    从文本中提取 JSON_OUTPUT_START/END 包裹的 JSON 块。

    calc_indicators.py 和 calc_fundamentals.py 的输出格式:
      <!-- JSON_OUTPUT_START -->
      { ... }
      <!-- JSON_OUTPUT_END -->

    若无标记则尝试直接解析整个文本。
    """
    start_marker = "<!-- JSON_OUTPUT_START -->"
    end_marker = "<!-- JSON_OUTPUT_END -->"

    s_idx = text.find(start_marker)
    e_idx = text.find(end_marker)

    if s_idx != -1 and e_idx != -1:
        json_text = text[s_idx + len(start_marker):e_idx].strip()
    else:
        json_text = _filter_http_lines(text).strip()

    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return None


def load_json_file(filepath):
    """
    从文件加载 JSON（支持 calc_*py 输出格式）。

    返回: dict 或 None
    """
    if not filepath or not os.path.isfile(filepath):
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    return _extract_json_block(text)


# ============================================================
# 2b. --kline-stdin: 从 K 线 JSON 直接计算技术面
# ============================================================

def compute_tech_from_kline(raw_text, market="A", chip_data=None):
    """
    复用 calc_indicators.py 公共函数，从 K 线原始 JSON 计算技术面评分。

    参数:
      raw_text  - stdin 读入的 stock-data kline JSON 文本
      market    - 市场类型: "A"/"HK"/"US"
      chip_data - 筹码数据 dict（可选，来自 stock-data chip）

    返回: dict，格式与 calc_score.py --tech JSON 兼容:
      {
        "raw_score": int,
        "effective_max": int,
        "theoretical_max": int,
        "missing_items": [...],
        "sepa_passed_count": int,
        "source": "kline-stdin",
      }
    """
    kline_data = parse_kline_json(raw_text)
    if not kline_data:
        return None

    closes = [d["close"] for d in kline_data]
    current_price = closes[-1]
    kline_count = len(kline_data)

    all_missing = []
    tech_raw = 0
    tech_max = 0

    # --- SEPA (40分) ---
    sepa = check_sepa(kline_data, current_price)
    tech_raw += sepa["raw_score"]
    tech_max += sepa["effective_max"]
    all_missing.extend(sepa["missing_items"])

    # --- RSI (5分) ---
    rsi = calculate_rsi(closes, 14)
    if rsi is not None:
        rsi_max = 5
        if 40 <= rsi <= 70:
            rsi_score = 5
        elif (30 <= rsi < 40) or (70 < rsi <= 80):
            rsi_score = 3
        else:
            rsi_score = 0
        tech_raw += rsi_score
        tech_max += rsi_max
    else:
        all_missing.append(f"RSI（需15根K线，当前{kline_count}根）")

    # --- MACD (5分) ---
    macd = calculate_macd(closes, 12, 26, 9)
    if macd is not None:
        macd_max = 5
        sig = macd["signal_text"]
        if "多头（" in sig:
            macd_score = 5
        elif "减弱" in sig:
            macd_score = 3
        else:
            macd_score = 0
        tech_raw += macd_score
        tech_max += macd_max
    else:
        all_missing.append(f"MACD（需35根K线，当前{kline_count}根）")

    # --- 量价 (5分) ---
    volume_analysis = analyze_volume(kline_data)
    if "error" not in volume_analysis:
        va = volume_analysis
        vol_raw = 0
        vol_max = 5
        if va["up_down_ratio"] > 1.3:
            vol_raw += 3
        elif va["up_down_ratio"] > 1.0:
            vol_raw += 1
        if 0.8 <= va["volume_ratio"] <= 1.5:
            vol_raw += 2
        elif 0.5 <= va["volume_ratio"] <= 2.0:
            vol_raw += 1
        tech_raw += vol_raw
        tech_max += vol_max
    else:
        all_missing.append("量价分析（K线不足）")

    # --- 理论满分 ---
    # 不含筹码: 55; 含筹码: A股60, 港美股58
    tech_theoretical = 55  # 默认不含筹码

    return {
        "raw_score": tech_raw,
        "effective_max": tech_max,
        "theoretical_max": tech_theoretical,
        "missing_items": all_missing,
        "sepa_passed_count": sepa["passed_count"],
        "source": "kline-stdin",
    }


# ============================================================
# 3. 总分汇总
# ============================================================

# 评级阈值（映射到100分制后）
_RATING_THRESHOLDS = [
    (85, "A+", "强力买入候选"),
    (70, "A",  "买入候选"),
    (55, "B",  "观察名单"),
    (40, "C",  "谨慎观望"),
    (0,  "D",  "排除"),
]

# 仓位建议（按评级）
_POSITION_MAP = {
    "A+": {"min": 15, "max": 20, "desc": "核心仓位 15%~20%"},
    "A":  {"min": 10, "max": 15, "desc": "标准仓位 10%~15%"},
    "B":  {"min": 5,  "max": 10, "desc": "试探仓位 5%~10%"},
    "C":  {"min": 0,  "max": 5,  "desc": "观望或极小仓位 0%~5%"},
    "D":  {"min": 0,  "max": 0,  "desc": "不建议持有"},
}


def aggregate_score(tech_json, fund_json, catalyst_score, sec_type):
    """
    汇总三大维度评分，计算映射总分。

    参数:
      tech_json      - 技术面 JSON（calc_indicators.py 输出），可为 None
      fund_json      - 基本面 JSON（calc_fundamentals.py 输出），可为 None
      catalyst_score - 催化剂分数（0~15 整数），可为 None
      sec_type       - 标的类型: "ETF"/"ETN"/"STOCK"

    返回: dict 汇总结果
    """
    # --- 技术面（理论满分 60） ---
    tech_raw = 0
    tech_max = 0
    tech_theoretical = 60
    tech_missing = []

    if tech_json:
        # 兼容两种格式:
        #   calc_indicators.py 目前无 JSON 输出 → 用户手动构造
        #   或未来版本的 JSON 输出
        tech_raw = tech_json.get("raw_score", tech_json.get("score", 0))
        tech_max = tech_json.get("effective_max", tech_json.get("max_score", 0))
        tech_theoretical = tech_json.get("theoretical_max", 60)
        tech_missing = tech_json.get("missing_items", tech_json.get("missing", []))
    else:
        tech_missing = ["技术面数据（未提供 --tech）"]

    # --- 基本面（理论满分 25） ---
    fund_raw = 0
    fund_max = 0
    fund_theoretical = 25
    fund_missing = []

    is_etf_etn = sec_type in ("ETF", "ETN")

    if is_etf_etn:
        # ETF/ETN: 基本面从分母中完全剔除
        fund_raw = 0
        fund_max = 0
        fund_theoretical = 0
        fund_missing = []
    elif fund_json:
        fund_raw = fund_json.get("score", 0)
        fund_max = fund_json.get("max_score", 0)
        fund_theoretical = fund_json.get("theoretical_max", 25)
        fund_missing = fund_json.get("missing", [])
    else:
        fund_missing = ["基本面数据（未提供 --fund）"]

    # --- 催化剂（理论满分 15） ---
    cat_raw = 0
    cat_max = 0
    cat_theoretical = 15
    cat_missing = []

    if catalyst_score is not None:
        cat_raw = max(0, min(15, catalyst_score))
        cat_max = 15
    else:
        cat_missing = ["催化剂评分（未提供 --catalyst）"]

    # --- 汇总 ---
    total_raw = tech_raw + fund_raw + cat_raw
    total_effective_max = tech_max + fund_max + cat_max
    total_theoretical = tech_theoretical + fund_theoretical + cat_theoretical

    # 映射到100分制
    if total_effective_max > 0:
        mapped_score = round(total_raw / total_effective_max * 100, 1)
    else:
        mapped_score = None

    # 覆盖率
    if total_theoretical > 0:
        coverage_pct = round(total_effective_max / total_theoretical * 100, 1)
    else:
        coverage_pct = 0.0

    all_missing = tech_missing + fund_missing + cat_missing

    return {
        "tech": {
            "raw": tech_raw, "max": tech_max,
            "theoretical": tech_theoretical, "missing": tech_missing,
        },
        "fund": {
            "raw": fund_raw, "max": fund_max,
            "theoretical": fund_theoretical, "missing": fund_missing,
        },
        "catalyst": {
            "raw": cat_raw, "max": cat_max,
            "theoretical": cat_theoretical, "missing": cat_missing,
        },
        "total_raw": total_raw,
        "total_effective_max": total_effective_max,
        "total_theoretical": total_theoretical,
        "mapped_score": mapped_score,
        "coverage_pct": coverage_pct,
        "missing": all_missing,
    }


def determine_rating(mapped_score):
    """
    映射分数 → 评级。

    参数: mapped_score - 100分制映射分或 None
    返回: (rating, description)
    """
    if mapped_score is None:
        return "D", "无法评分（有效满分为0）"

    for threshold, rating, desc in _RATING_THRESHOLDS:
        if mapped_score >= threshold:
            return rating, desc
    return "D", "排除"


def suggest_position(rating):
    """
    评级 → 仓位建议。

    返回: dict {"min": int, "max": int, "desc": str}
    """
    return _POSITION_MAP.get(rating, _POSITION_MAP["D"])


def check_hard_thresholds(fund_json, tech_json, sec_type):
    """
    硬门槛检查（任一触发 → 强制 D 级）。

    规则:
      1. 个股基本面 < 15分 → D 级（ETF/ETN 豁免）
      2. SEPA 通过数 < 4（5项中） → D 级

    返回: dict {"fund_below_15": bool, "sepa_below_4": bool, "red_line": bool}
    """
    result = {
        "fund_below_15": False,
        "sepa_below_4": False,
        "red_line": False,
    }

    # 门槛1: 基本面 < 15（仅个股）
    if sec_type == "STOCK" and fund_json:
        fund_score = fund_json.get("score", 0)
        fund_max = fund_json.get("max_score", 0)
        if fund_max > 0 and fund_score < 15:
            result["fund_below_15"] = True

    # 门槛2: SEPA 通过数 < 4
    if tech_json:
        # 兼容: passed_count 可能在顶层或嵌套
        sepa_passed = tech_json.get("sepa_passed_count",
                      tech_json.get("passed_count", None))
        if sepa_passed is not None and sepa_passed < 4:
            result["sepa_below_4"] = True

    result["red_line"] = result["fund_below_15"] or result["sepa_below_4"]
    return result


# ============================================================
# 4. 主函数
# ============================================================

def main():
    """
    CLI 入口: 解析参数 → 加载数据 → 汇总评分 → 输出。
    """
    parser = argparse.ArgumentParser(
        description="米勒维尼总分汇总脚本 v1.1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  # 模式A: 从预计算 JSON 汇总
  python calc_score.py --tech tech.json --fund fund.json --catalyst 8 --code usTSLA

  # 模式B: 从 stdin 读取 K 线 直接计算技术面
  stock-data kline usTSLA day 252 qfq | python calc_score.py --kline-stdin --code usTSLA

  # ETF（自动剔除基本面）
  python calc_score.py --tech tech.json --catalyst 10 --code sh510300

  # 手动指定类型
  python calc_score.py --tech tech.json --fund fund.json --catalyst 8 --code AAPL --type stock
        """,
    )
    parser.add_argument('--code', required=True,
                        help='标的代码（如 usTSLA, sh600519, hk00700, sh510300）')

    # 技术面输入: --tech 和 --kline-stdin 互斥
    tech_group = parser.add_mutually_exclusive_group()
    tech_group.add_argument('--tech',
                            help='技术面 JSON 文件（calc_indicators.py 输出）')
    tech_group.add_argument('--kline-stdin', action='store_true',
                            help='从 stdin 读取 K 线 JSON 直接计算技术面（与 --tech 互斥）')

    parser.add_argument('--fund', help='基本面 JSON 文件（calc_fundamentals.py 输出，可选）')
    parser.add_argument('--catalyst', type=float, default=None,
                        help='催化剂分数（0~15，支持小数）')
    parser.add_argument('--type', choices=['etf', 'etn', 'stock'], default=None,
                        help='手动指定标的类型（覆盖自动检测）')
    args = parser.parse_args()

    # Windows UTF-8 兼容
    import io
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    # ========== 标的分类 ==========
    sec_type = classify_security(args.code, override_type=args.type)

    # ========== 加载数据 ==========
    tech_json = None
    if args.kline_stdin:
        # 模式B: 从 stdin 读取 K 线 JSON 直接计算技术面
        try:
            raw_text = sys.stdin.read()
        except Exception as e:
            print(f"[错误] 读取 stdin 失败: {e}", file=sys.stderr)
            sys.exit(1)
        if not raw_text.strip():
            print("[错误] stdin 为空，请通过管道传入 stock-data kline 的输出", file=sys.stderr)
            sys.exit(1)
        # 自动判断市场
        c = args.code.lower()
        if c.startswith("us"):
            market = "US"
        elif c.startswith("hk"):
            market = "HK"
        else:
            market = "A"
        tech_json = compute_tech_from_kline(raw_text, market=market)
        if tech_json is None:
            print("[错误] 无法从 stdin 解析有效 K 线数据", file=sys.stderr)
            sys.exit(1)
    elif args.tech:
        tech_json = load_json_file(args.tech)

    fund_json = load_json_file(args.fund) if args.fund else None
    catalyst_score = args.catalyst

    # ========== 汇总评分 ==========
    agg = aggregate_score(tech_json, fund_json, catalyst_score, sec_type)

    # ========== 硬门槛检查 ==========
    thresholds = check_hard_thresholds(fund_json, tech_json, sec_type)

    # ========== 评级 ==========
    if thresholds["red_line"]:
        rating, rating_desc = "D", "硬门槛触发 → 强制排除"
    else:
        rating, rating_desc = determine_rating(agg["mapped_score"])

    position = suggest_position(rating)

    # ========== 文本输出 ==========
    print("=" * 60)
    print(f"总分汇总结果 (calc_score v1.1.0)")
    print(f"标的: {args.code}  |  类型: {sec_type}")
    print("=" * 60)

    # 各维度得分
    print(f"\n【技术面】 {agg['tech']['raw']}/{agg['tech']['max']}分"
          f"  (理论满分: {agg['tech']['theoretical']})")
    print(f"【基本面】 {agg['fund']['raw']}/{agg['fund']['max']}分"
          f"  (理论满分: {agg['fund']['theoretical']})"
          + (" [ETF/ETN: 已剔除]" if sec_type in ("ETF", "ETN") else ""))
    print(f"【催化剂】 {agg['catalyst']['raw']}/{agg['catalyst']['max']}分"
          f"  (理论满分: {agg['catalyst']['theoretical']})")

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"【汇总】")
    print(f"  原始总分: {agg['total_raw']}/{agg['total_effective_max']}")
    print(f"  理论满分: {agg['total_theoretical']}")
    if agg['mapped_score'] is not None:
        print(f"  映射得分: {agg['mapped_score']}分 (100分制)")
    else:
        print(f"  映射得分: 无法计算（有效满分为0）")
    print(f"  分母覆盖率: {agg['coverage_pct']}%")

    # 缺失项
    if agg['missing']:
        print(f"\n【缺失项】({len(agg['missing'])}项)")
        for m in agg['missing']:
            print(f"  ⚠️ {m}")

    # 硬门槛
    if thresholds["red_line"]:
        print(f"\n{'=' * 60}")
        print(f"⛔ 【硬门槛触发】")
        if thresholds["fund_below_15"]:
            fund_s = fund_json.get("score", 0) if fund_json else 0
            print(f"  · 基本面 {fund_s}分 < 15分 → D级")
        if thresholds["sepa_below_4"]:
            print(f"  · SEPA通过数 < 4 → D级")

    # 评级与仓位
    print(f"\n{'=' * 60}")
    print(f"【最终评级】 {rating}  —  {rating_desc}")
    print(f"【仓位建议】 {position['desc']}")
    print(f"{'=' * 60}")

    # ========== JSON 输出 ==========
    print(f"\n<!-- JSON_OUTPUT_START -->")
    json_out = {
        "version": "1.1.0",
        "security_code": args.code,
        "security_type": sec_type,
        "tech_score": {
            "raw": agg["tech"]["raw"],
            "max": agg["tech"]["max"],
            "theoretical": agg["tech"]["theoretical"],
        },
        "fund_score": {
            "raw": agg["fund"]["raw"],
            "max": agg["fund"]["max"],
            "theoretical": agg["fund"]["theoretical"],
        },
        "catalyst_score": {
            "raw": agg["catalyst"]["raw"],
            "max": agg["catalyst"]["max"],
        },
        "total": {
            "raw": agg["total_raw"],
            "effective_max": agg["total_effective_max"],
            "mapped": agg["mapped_score"],
        },
        "missing": agg["missing"],
        "coverage_pct": agg["coverage_pct"],
        "hard_thresholds": thresholds,
        "rating": rating,
        "rating_desc": rating_desc,
        "position_pct": position["max"],
        "position_desc": position["desc"],
    }
    print(json.dumps(json_out, ensure_ascii=False, indent=2))
    print(f"<!-- JSON_OUTPUT_END -->")


if __name__ == "__main__":
    main()
