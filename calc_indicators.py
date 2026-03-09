#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
技术指标计算脚本 - calc_indicators.py
版本: 1.3.0 (2026-03-10)

功能: 从 stdin 读取 stock-data kline 的 JSON 输出，
      精确计算 MA/RSI/MACD/SEPA/量价/筹码 等技术指标。

变更历史:
  v1.0.0: 初始版本
  v1.1.0: 更新数据完整性评级为A-F六级
  v1.2.0: SEPA从6项改为5项（移除MA200走平）;
          新增score_chip()筹码确定性打分;
          新增calculate_score_mapping()分值映射;
          main()输出有效满分/缺失项/置信等级/映射得分
  v1.3.0: [Bug1] coverage_pct 新增 theoretical_max 参数，按维度满分计算;
          [Bug2] 52周高低点改用 high/low（非收盘价）;
          [Bug3] main()支持 --chip/--market 参数自动合并筹码;
          [Bug4] parse_kline_json 自动过滤 [HTTP] 日志行(Windows兼容);
          [Bug5] MACD评分改用 signal_text 映射(补全3分档);
          [Bug7] assess_data_completeness 移除废弃的 score_cap 字段

用法:
  # Linux/macOS
  stock-data kline sh600519 day 252 qfq 2>/dev/null | python calc_indicators.py

  # Windows PowerShell（无需 sed，脚本自动过滤 HTTP 日志）
  stock-data kline sh600519 day 252 qfq | python calc_indicators.py

  # 含筹码评分
  stock-data kline usSNDK day 252 qfq | python calc_indicators.py --chip sndk_chip.json --market US

依赖: 仅使用 Python 标准库（json/sys/math/argparse），无需 pip install
"""

import json
import sys
import math


# ============================================================
# 1. 数据解析
# ============================================================

def parse_kline_json(raw_text):
    """
    解析 stock-data kline 的 JSON 输出。

    支持两种格式:
      - v2.2.2+ (nodes): data.nodes 数组，每个节点含 date/open/last/high/low/volume/amount/exchange
      - v2.2.1  (array): data.<code>.<period> 二维数组 [日期, 开盘, 收盘, 最高, 最低, 成交量]

    v1.3.0: 自动过滤 stock-data 的 [HTTP ...] 调试日志行，
            Windows PowerShell 无需 2>/dev/null 和 sed 过滤。

    返回: list[dict]，按日期从旧到新排序，每个 dict 包含:
          date, open, close, high, low, volume, amount
    """
    # 自动过滤 stock-data 的 HTTP 调试日志（[HTTP ...] 开头的行）
    lines = raw_text.splitlines()
    clean_lines = [l for l in lines if not l.strip().startswith('[HTTP')]
    clean_text = '\n'.join(clean_lines)

    try:
        obj = json.loads(clean_text)
    except json.JSONDecodeError:
        # 清理后解析失败，再尝试原始文本（可能没有日志行）
        try:
            obj = json.loads(raw_text)
        except json.JSONDecodeError as e:
            print(f"[错误] JSON 解析失败: {e}", file=sys.stderr)
            return []

    # 检查返回码
    if obj.get("code", 0) != 0:
        msg = obj.get("msg", "未知错误")
        print(f"[错误] API 返回错误: {msg}", file=sys.stderr)
        return []

    data = obj.get("data", {})

    # 格式一: nodes 数组 (v2.2.2+)
    nodes = data.get("nodes")
    if nodes and isinstance(nodes, list):
        return _parse_nodes(nodes)

    # 格式二: 嵌套数组 (v2.2.1 兼容)
    for code_key, periods in data.items():
        if isinstance(periods, dict):
            for period_key, rows in periods.items():
                if isinstance(rows, list) and len(rows) > 0:
                    return _parse_array(rows)

    print("[错误] 未找到有效的 K 线数据", file=sys.stderr)
    return []


def _parse_nodes(nodes):
    """解析 v2.2.2+ nodes 格式"""
    result = []
    for n in nodes:
        try:
            result.append({
                "date":   str(n.get("date", "")),
                "open":   float(n.get("open", 0)),
                "close":  float(n.get("last", 0)),
                "high":   float(n.get("high", 0)),
                "low":    float(n.get("low", 0)),
                "volume": float(n.get("volume", 0)),
                "amount": float(n.get("amount", 0)),
            })
        except (ValueError, TypeError):
            continue
    # 按日期从旧到新排序
    result.sort(key=lambda x: x["date"])
    return result


def _parse_array(rows):
    """解析 v2.2.1 数组格式 (向后兼容)"""
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            result.append({
                "date":   str(row[0]),
                "open":   float(row[1]),
                "close":  float(row[2]),
                "high":   float(row[3]),
                "low":    float(row[4]),
                "volume": float(row[5]),
                "amount": float(row[6]) if len(row) > 6 else 0.0,
            })
        except (ValueError, TypeError):
            continue
    result.sort(key=lambda x: x["date"])
    return result


# ============================================================
# 2. 技术指标计算
# ============================================================

def calculate_ma(prices, period):
    """
    简单移动平均线 (SMA)。
    数据不足返回 None。
    """
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_ema(prices, period):
    """
    指数移动平均线 (EMA) 序列。
    返回与 prices 等长的 list，前 period-1 个为 None。
    """
    if len(prices) < period:
        return [None] * len(prices)

    k = 2.0 / (period + 1)
    ema_values = [None] * len(prices)

    # 第一个 EMA = 前 period 个价格的 SMA
    ema_values[period - 1] = sum(prices[:period]) / period

    for i in range(period, len(prices)):
        ema_values[i] = prices[i] * k + ema_values[i - 1] * (1 - k)

    return ema_values


def calculate_rsi(prices, period=14):
    """
    RSI (Wilder 平滑法)。
    需要至少 period + 1 根 K 线。
    返回最新 RSI 值，数据不足返回 None。
    """
    min_required = period + 1
    if len(prices) < min_required:
        return None

    # 计算价格变动
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    # 初始平均涨跌幅（前 period 个变动的简单平均）
    gains = [max(c, 0) for c in changes[:period]]
    losses = [max(-c, 0) for c in changes[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder 平滑
    for i in range(period, len(changes)):
        change = changes[i]
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """
    MACD 指标。
    需要至少 slow + signal 根 K 线 (默认 35 根)。
    返回 dict: {dif, dea, histogram, signal_text}，数据不足返回 None。
    """
    min_required = slow + signal
    if len(prices) < min_required:
        return None

    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)

    # DIF = EMA(fast) - EMA(slow)
    dif_values = []
    for i in range(len(prices)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif_values.append(ema_fast[i] - ema_slow[i])
        else:
            dif_values.append(None)

    # 提取有效的 DIF 值计算 DEA
    valid_dif = [d for d in dif_values if d is not None]
    if len(valid_dif) < signal:
        return None

    dea_values = calculate_ema(valid_dif, signal)
    dif = valid_dif[-1]
    dea = dea_values[-1]
    histogram = 2 * (dif - dea)

    # 信号判断
    if dif > dea and histogram > 0:
        signal_text = "多头（DIF > DEA，柱状图为正）"
    elif dif < dea and histogram < 0:
        signal_text = "空头（DIF < DEA，柱状图为负）"
    elif dif > dea and histogram < 0:
        signal_text = "多头减弱（DIF > DEA，但柱状图缩短）"
    else:
        signal_text = "空头减弱（DIF < DEA，但柱状图缩短）"

    return {
        "dif": round(dif, 4),
        "dea": round(dea, 4),
        "histogram": round(histogram, 4),
        "signal_text": signal_text,
    }


# ============================================================
# 3. SEPA 趋势模板检查 (5 项, v5.2)
# ============================================================

def check_sepa(kline_data_or_closes, current_price):
    """
    米勒维尼 SEPA 趋势模板 5 项检查 (v5.2)。

    检查项（每项 8 分，共 40 分）:
      1. 股价 > MA50
      2. MA50 > MA150 > MA200（多头排列）
      3. 股价 > MA200
      4. 距 52 周高点 < 25%
      5. 距 52 周低点 > 25%

    v5.2 变更: 移除原第 4 项"MA200 走平或上升"（与多头排列共线性高）；
              原"MA50>MA150"和"MA150>MA200"合并为一项"多头排列"；
              新增"距 52 周低点 > 25%"作为独立检查项。

    v1.3.0 修正: 52周高低点改用 high/low 计算（之前误用 closes）。
                 兼容传入 kline_data (list[dict]) 或 closes (list[float])。

    返回 dict，含 items (每项详情)、passed_count、score（0-40）。
    """
    # 兼容两种传参：kline_data (list[dict]) 或 closes (list[float])
    if kline_data_or_closes and isinstance(kline_data_or_closes[0], dict):
        closes = [d["close"] for d in kline_data_or_closes]
        highs = [d["high"] for d in kline_data_or_closes]
        lows = [d["low"] for d in kline_data_or_closes]
    else:
        closes = kline_data_or_closes
        highs = closes  # 降级：仅有 closes 时退回旧行为
        lows = closes

    items = []
    ma50 = calculate_ma(closes, 50)
    ma150 = calculate_ma(closes, 150)
    ma200 = calculate_ma(closes, 200)
    score_per_item = 8  # 每项 8 分，5 项共 40 分

    # ----- 第 1 项: 股价 > MA50 -----
    if ma50 is not None:
        passed = current_price > ma50
        items.append({
            "name": "股价 > MA50",
            "passed": passed,
            "applicable": True,
            "score": score_per_item if passed else 0,
            "max_score": score_per_item,
            "detail": f"股价={current_price:.2f}, MA50={ma50:.2f}",
        })
    else:
        items.append({
            "name": "股价 > MA50",
            "passed": None,
            "applicable": False,
            "score": 0,
            "max_score": 0,  # 不可算 → 剔除分母
            "detail": f"N/A（需50根K线，当前{len(closes)}根）",
        })

    # ----- 第 2 项: MA50 > MA150 > MA200（多头排列）-----
    if ma50 is not None and ma150 is not None and ma200 is not None:
        passed = (ma50 > ma150) and (ma150 > ma200)
        items.append({
            "name": "MA50 > MA150 > MA200（多头排列）",
            "passed": passed,
            "applicable": True,
            "score": score_per_item if passed else 0,
            "max_score": score_per_item,
            "detail": f"MA50={ma50:.2f}, MA150={ma150:.2f}, MA200={ma200:.2f}",
        })
    else:
        items.append({
            "name": "MA50 > MA150 > MA200（多头排列）",
            "passed": None,
            "applicable": False,
            "score": 0,
            "max_score": 0,
            "detail": f"N/A（需200根K线，当前{len(closes)}根）",
        })

    # ----- 第 3 项: 股价 > MA200 -----
    if ma200 is not None:
        passed = current_price > ma200
        items.append({
            "name": "股价 > MA200",
            "passed": passed,
            "applicable": True,
            "score": score_per_item if passed else 0,
            "max_score": score_per_item,
            "detail": f"股价={current_price:.2f}, MA200={ma200:.2f}",
        })
    else:
        items.append({
            "name": "股价 > MA200",
            "passed": None,
            "applicable": False,
            "score": 0,
            "max_score": 0,
            "detail": f"N/A（需200根K线，当前{len(closes)}根）",
        })

    # ----- 计算 52 周高低点（使用最高价/最低价，非收盘价） -----
    period_highs = highs[-252:] if len(highs) >= 252 else highs
    period_lows = lows[-252:] if len(lows) >= 252 else lows
    high_52w = max(period_highs) if period_highs else None
    low_52w = min(period_lows) if period_lows else None

    # ----- 第 4 项: 距 52 周高点 < 25% -----
    if high_52w is not None and high_52w > 0 and len(closes) >= 50:
        pct_from_high = (high_52w - current_price) / high_52w * 100
        passed = pct_from_high < 25
        items.append({
            "name": "距52周高点 < 25%",
            "passed": passed,
            "applicable": True,
            "score": score_per_item if passed else 0,
            "max_score": score_per_item,
            "detail": f"距高点{pct_from_high:.1f}%, 52周高={high_52w:.2f}",
        })
    else:
        items.append({
            "name": "距52周高点 < 25%",
            "passed": None,
            "applicable": False,
            "score": 0,
            "max_score": 0,
            "detail": f"N/A（需至少50根K线，当前{len(closes)}根）",
        })

    # ----- 第 5 项: 距 52 周低点 > 25% -----
    if low_52w is not None and low_52w > 0 and len(closes) >= 50:
        pct_from_low = (current_price - low_52w) / low_52w * 100
        passed = pct_from_low > 25
        items.append({
            "name": "距52周低点 > 25%",
            "passed": passed,
            "applicable": True,
            "score": score_per_item if passed else 0,
            "max_score": score_per_item,
            "detail": f"距低点{pct_from_low:.1f}%, 52周低={low_52w:.2f}",
        })
    else:
        items.append({
            "name": "距52周低点 > 25%",
            "passed": None,
            "applicable": False,
            "score": 0,
            "max_score": 0,
            "detail": f"N/A（需至少50根K线，当前{len(closes)}根）",
        })

    # ----- 统计 -----
    passed_count = sum(1 for it in items if it["passed"] is True)
    total_valid = sum(1 for it in items if it["applicable"])
    raw_score = sum(it["score"] for it in items)
    effective_max = sum(it["max_score"] for it in items)
    missing_items = [it["name"] for it in items if not it["applicable"]]

    return {
        "items": items,
        "passed_count": passed_count,
        "total_valid": total_valid,
        "total": len(items),
        "raw_score": raw_score,
        "effective_max": effective_max,
        "missing_items": missing_items,
    }


# ============================================================
# 4. 量价分析
# ============================================================

def analyze_volume(kline_data):
    """
    量价分析: 近20日均量、放量/缩量趋势、涨日量 vs 跌日量。

    参数: kline_data - 完整 K 线数据列表 (已按日期从旧到新排序)
    返回: dict，含各项量价指标
    """
    if len(kline_data) < 5:
        return {"error": f"N/A（需至少5根K线，当前{len(kline_data)}根）"}

    recent = kline_data[-20:] if len(kline_data) >= 20 else kline_data

    volumes = [d["volume"] for d in recent]
    avg_volume = sum(volumes) / len(volumes)
    latest_volume = volumes[-1]

    # 放量/缩量判断
    volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 0

    if volume_ratio > 1.5:
        volume_trend = "显著放量"
    elif volume_ratio > 1.2:
        volume_trend = "温和放量"
    elif volume_ratio < 0.5:
        volume_trend = "显著缩量"
    elif volume_ratio < 0.8:
        volume_trend = "温和缩量"
    else:
        volume_trend = "量能平稳"

    # 涨日量 vs 跌日量
    up_volumes = []
    down_volumes = []
    for d in recent:
        if d["close"] >= d["open"]:
            up_volumes.append(d["volume"])
        else:
            down_volumes.append(d["volume"])

    avg_up = sum(up_volumes) / len(up_volumes) if up_volumes else 0
    avg_down = sum(down_volumes) / len(down_volumes) if down_volumes else 0

    if avg_up > 0 and avg_down > 0:
        up_down_ratio = avg_up / avg_down
        if up_down_ratio > 1.3:
            vol_quality = "量价配合良好（上涨放量，下跌缩量）"
        elif up_down_ratio < 0.7:
            vol_quality = "量价背离（上涨缩量，下跌放量）"
        else:
            vol_quality = "量价中性"
    else:
        up_down_ratio = 0
        vol_quality = "无法判断（单边行情）"

    return {
        "avg_volume_20d": round(avg_volume, 0),
        "latest_volume": round(latest_volume, 0),
        "volume_ratio": round(volume_ratio, 2),
        "volume_trend": volume_trend,
        "up_days": len(up_volumes),
        "down_days": len(down_volumes),
        "avg_up_volume": round(avg_up, 0),
        "avg_down_volume": round(avg_down, 0),
        "up_down_ratio": round(up_down_ratio, 2),
        "vol_quality": vol_quality,
    }


# ============================================================
# 5. 筹码确定性打分 (v5.2 新增)
# ============================================================

def score_chip(chip_data, market="A"):
    """
    对 stock-data chip 返回的筹码数据做确定性打分。

    参数:
      chip_data - dict，来自 stock-data chip 的解析结果，
                  至少应包含以下字段:
                    profitPercent (获利比例, 0-100)
                    avgCost       (平均成本)
                    p70           (70%筹码集中价位区间宽度占比, 0-100)
                    p90           (90%筹码集中价位区间宽度占比, 0-100, 可选)
                    currentPrice  (当前价格, 用于与avgCost对比)
      market    - 市场类型: "A"(A股) / "HK"(港股) / "US"(美股)

    返回 dict:
      raw_score    - 原始得分
      effective_max - 有效满分（A股5分, 港美股3分）
      items        - 各子项详情
      missing      - 缺失项列表
      applicable   - 是否可用

    设计原则:
      - 港美股机构占比高，筹码理论有效性降低，满分降权为3分
      - 每个子项都有确定性规则，不依赖主观解释
      - 缺失字段 → 该子项不参与评分，分母同步剔除
    """
    # 满分基础
    full_score = 5 if market == "A" else 3
    # 子项权重：获利比例(40%), 筹码集中度(40%), 股价>平均成本(20%)
    w_profit = 0.4
    w_concentration = 0.4
    w_above_cost = 0.2

    items = []
    missing = []
    total_weight = 0.0
    weighted_score = 0.0

    if chip_data is None:
        return {
            "raw_score": 0,
            "effective_max": 0,
            "items": [],
            "missing": ["筹码数据（chip_data 为空）"],
            "applicable": False,
        }

    # ----- 子项1: 获利比例 -----
    profit_pct = chip_data.get("profitPercent")
    if profit_pct is not None:
        try:
            profit_pct = float(profit_pct)
            total_weight += w_profit
            if profit_pct >= 60:
                s = 1.0
            elif profit_pct >= 40:
                s = 0.5
            else:
                s = 0.0
            weighted_score += w_profit * s
            items.append({
                "name": "获利比例",
                "value": f"{profit_pct:.1f}%",
                "score_ratio": s,
                "detail": "≥60%满分, 40-60%半分, <40%零分",
            })
        except (ValueError, TypeError):
            missing.append("获利比例（解析失败）")
    else:
        missing.append("获利比例")

    # ----- 子项2: 70%筹码集中度 -----
    p70 = chip_data.get("p70")
    if p70 is not None:
        try:
            p70 = float(p70)
            total_weight += w_concentration
            if p70 < 15:
                s = 1.0
            elif p70 < 25:
                s = 0.5
            else:
                s = 0.0
            weighted_score += w_concentration * s
            items.append({
                "name": "70%筹码集中度",
                "value": f"{p70:.1f}%",
                "score_ratio": s,
                "detail": "<15%满分, 15-25%半分, ≥25%零分",
            })
        except (ValueError, TypeError):
            missing.append("70%筹码集中度（解析失败）")
    else:
        missing.append("70%筹码集中度")

    # ----- 子项3: 股价 > 平均成本 -----
    avg_cost = chip_data.get("avgCost")
    cur_price = chip_data.get("currentPrice")
    if avg_cost is not None and cur_price is not None:
        try:
            avg_cost = float(avg_cost)
            cur_price = float(cur_price)
            total_weight += w_above_cost
            s = 1.0 if cur_price > avg_cost else 0.0
            weighted_score += w_above_cost * s
            items.append({
                "name": "股价 > 平均成本",
                "value": f"价格={cur_price:.2f}, avgCost={avg_cost:.2f}",
                "score_ratio": s,
                "detail": "股价>平均成本满分, 否则零分",
            })
        except (ValueError, TypeError):
            missing.append("股价>平均成本（解析失败）")
    else:
        missing.append("股价>平均成本（缺字段）")

    # ----- 汇总 -----
    if total_weight > 0:
        raw_score = round(full_score * (weighted_score / total_weight), 1)
        effective_max = round(full_score * (total_weight / 1.0), 1)
    else:
        raw_score = 0
        effective_max = 0

    return {
        "raw_score": raw_score,
        "effective_max": effective_max,
        "items": items,
        "missing": missing,
        "applicable": total_weight > 0,
    }


# ============================================================
# 5b. 分值映射与置信等级 (v5.2 新增)
# ============================================================

def calculate_score_mapping(raw_score, effective_max, theoretical_max=None):
    """
    将原始得分映射到 100 分制。

    公式: mapped = raw_score / effective_max × 100
    当 effective_max == 0 时返回 None（无法评分）。

    参数:
      raw_score       - 实际得分
      effective_max   - 有效满分（剔除缺失项后）
      theoretical_max - 当前维度理论满分（如技术面不含筹码=55, 全框架=100）
                        不传时默认 = effective_max（覆盖率100%）

    返回 dict:
      mapped_score    - 映射后得分（保留1位小数）
      raw_score       - 原始得分
      effective_max   - 有效满分
      theoretical_max - 理论满分
      coverage_pct    - 分母覆盖率 (effective_max / theoretical_max × 100%)
    """
    if theoretical_max is None or theoretical_max <= 0:
        theoretical_max = effective_max  # 兜底：覆盖率100%

    if effective_max <= 0:
        return {
            "mapped_score": None,
            "raw_score": raw_score,
            "effective_max": effective_max,
            "theoretical_max": theoretical_max,
            "coverage_pct": 0.0,
        }
    mapped = round(raw_score / effective_max * 100, 1)
    coverage = round(effective_max / theoretical_max * 100, 1)
    return {
        "mapped_score": mapped,
        "raw_score": raw_score,
        "effective_max": effective_max,
        "theoretical_max": theoretical_max,
        "coverage_pct": coverage,
    }


def determine_confidence(data_completeness_level, coverage_pct):
    """
    联合 K 线完整性等级与分母覆盖率，判定置信等级。

    置信等级:
      高   - A/A- 且覆盖率 ≥ 90%
      中   - B 且覆盖率 ≥ 80%，或 A/A- 且覆盖率 70-89%
      低   - C/D 或覆盖率 60-79%
      极低 - E/F 或覆盖率 < 60%

    返回 str: "高" / "中" / "低" / "极低"
    """
    top_levels = {"A", "A-"}
    mid_levels = {"B"}

    if data_completeness_level in top_levels and coverage_pct >= 90:
        return "高"
    elif (data_completeness_level in mid_levels and coverage_pct >= 80) or \
         (data_completeness_level in top_levels and coverage_pct >= 70):
        return "中"
    elif coverage_pct >= 60:
        return "低"
    else:
        return "极低"




def assess_data_completeness(kline_count):
    """
    评估数据完整性等级，用于降级规则。
    v5.1: A-F六级分类; v5.2: 移除 score_cap 限制（改用分母剔除映射）
    v1.3.0: 移除废弃的 score_cap 字段

    返回: dict，含 level (A/B/C/D/E/F)、description
    """
    if kline_count >= 252:
        return {
            "level": "A",
            "description": f"数据完整（{kline_count}根K线，满足所有指标）",
        }
    elif kline_count >= 220:
        return {
            "level": "A-",
            "description": f"数据基本完整（{kline_count}根K线，52周高低点可能不完整）",
        }
    elif kline_count >= 200:
        return {
            "level": "B",
            "description": f"数据部分不足（{kline_count}根K线，MA200趋势判断不可用）",
        }
    elif kline_count >= 50:
        return {
            "level": "C",
            "description": f"数据不足（{kline_count}根K线，MA200不可用）",
        }
    elif kline_count >= 35:
        return {
            "level": "D",
            "description": f"数据严重不足（{kline_count}根K线，仅RSI可用）",
        }
    elif kline_count >= 15:
        return {
            "level": "E",
            "description": f"数据极度不足（{kline_count}根K线，仅RSI可用）",
        }
    elif kline_count > 2:
        return {
            "level": "F",
            "description": f"数据近乎无效（{kline_count}根K线，MA/RSI/MACD全部不可用）",
        }
    else:
        return {
            "level": "F",
            "description": f"数据无效（{kline_count}根K线），技术面不可用",
        }


# ============================================================
# 7. 主函数
# ============================================================

def main():
    """
    入口: 从 stdin 读取 JSON → 解析 K 线 → 计算所有指标 → 输出。
    v5.2: 新增有效满分/缺失项/置信等级/100分映射得分输出。
    v1.3.0: 支持 --chip 参数自动合并筹码分数;
            支持 --market 指定市场(A/HK/US);
            check_sepa 传入 kline_data 以使用 high/low;
            coverage_pct 按技术面理论满分计算(非写死100)。
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="米勒维尼技术指标计算脚本 v1.3.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--chip', help='筹码JSON文件路径（stock-data chip 输出）')
    parser.add_argument('--market', default='A', choices=['A', 'HK', 'US'],
                        help='市场类型: A(A股)/HK(港股)/US(美股), 默认A')
    args = parser.parse_args()

    # Windows 终端 UTF-8 兼容
    import io
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    # 读取 stdin
    try:
        raw_text = sys.stdin.read()
    except Exception as e:
        print(f"[错误] 读取 stdin 失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not raw_text.strip():
        print("[错误] stdin 为空，请通过管道传入 stock-data kline 的输出", file=sys.stderr)
        sys.exit(1)

    # 解析 K 线数据
    kline_data = parse_kline_json(raw_text)
    if not kline_data:
        print("[错误] 无法解析有效的 K 线数据", file=sys.stderr)
        sys.exit(1)

    kline_count = len(kline_data)
    closes = [d["close"] for d in kline_data]
    current_price = closes[-1]
    latest_date = kline_data[-1]["date"]

    # 数据完整性评级
    completeness = assess_data_completeness(kline_count)

    # 计算技术指标
    ma50 = calculate_ma(closes, 50)
    ma150 = calculate_ma(closes, 150)
    ma200 = calculate_ma(closes, 200)
    rsi = calculate_rsi(closes, 14)
    macd = calculate_macd(closes, 12, 26, 9)
    sepa = check_sepa(kline_data, current_price)  # v1.3.0: 传入 kline_data 以使用 high/low
    volume_analysis = analyze_volume(kline_data)

    # ========== 技术面评分汇总 ==========
    # 收集所有技术面子项的得分和满分
    all_missing = []  # 所有缺失项
    tech_raw = 0
    tech_max = 0

    # --- SEPA (40分) ---
    tech_raw += sepa["raw_score"]
    tech_max += sepa["effective_max"]
    all_missing.extend(sepa["missing_items"])

    # --- RSI (5分) ---
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
    if macd is not None:
        macd_max = 5
        sig = macd["signal_text"]
        if "多头（" in sig:          # 强多头: DIF>DEA 且柱状图为正
            macd_score = 5
        elif "减弱" in sig:          # 多头减弱 或 空头减弱
            macd_score = 3
        else:                        # 空头
            macd_score = 0
        tech_raw += macd_score
        tech_max += macd_max
    else:
        all_missing.append(f"MACD（需35根K线，当前{kline_count}根）")

    # --- 量价 (5分) ---
    if "error" not in volume_analysis:
        va = volume_analysis
        vol_raw = 0
        vol_max = 5
        # 涨跌日量比（3分）
        if va["up_down_ratio"] > 1.3:
            vol_raw += 3
        elif va["up_down_ratio"] > 1.0:
            vol_raw += 1
        # 量比（2分）
        if 0.8 <= va["volume_ratio"] <= 1.5:
            vol_raw += 2
        elif 0.5 <= va["volume_ratio"] <= 2.0:
            vol_raw += 1
        tech_raw += vol_raw
        tech_max += vol_max
    else:
        all_missing.append("量价分析（K线不足）")

    # --- 筹码 (5分A股/3分港美股) ---
    # v1.3.0: 支持 --chip 参数自动合并筹码分数
    chip_result = None
    if args.chip:
        try:
            with open(args.chip, 'r', encoding='utf-8') as f:
                chip_json = json.load(f)
            chip_result = score_chip(chip_json, market=args.market)
            if chip_result["applicable"]:
                tech_raw += chip_result["raw_score"]
                tech_max += chip_result["effective_max"]
            else:
                all_missing.extend(chip_result["missing"])
        except Exception as e:
            all_missing.append(f"筹码（读取 --chip 文件失败: {e}）")
    else:
        chip_note = "筹码评分需 --chip 参数或单独调用 score_chip()"

    # ========== 格式化输出 ==========
    print("=" * 60)
    print(f"技术指标计算结果 (calc_indicators v1.3.0)")
    print(f"日期: {latest_date}  |  收盘价: {current_price:.2f}")
    print(f"K线数量: {kline_count}  |  数据完整性: {completeness['level']}级")
    print(f"市场: {args.market}")
    print("=" * 60)

    # --- 均线 ---
    print("\n【均线 (MA)】")
    print(f"  MA50:  {f'{ma50:.2f}' if ma50 is not None else f'N/A（需50根，当前{kline_count}根）'}")
    print(f"  MA150: {f'{ma150:.2f}' if ma150 is not None else f'N/A（需150根，当前{kline_count}根）'}")
    print(f"  MA200: {f'{ma200:.2f}' if ma200 is not None else f'N/A（需200根，当前{kline_count}根）'}")

    # --- RSI ---
    print("\n【RSI(14)】")
    if rsi is not None:
        if rsi > 70:
            rsi_signal = "超买区间（>70）"
        elif rsi < 30:
            rsi_signal = "超卖区间（<30）"
        elif rsi > 50:
            rsi_signal = "偏强"
        else:
            rsi_signal = "偏弱"
        print(f"  RSI: {rsi}  —  {rsi_signal}")
        print(f"  评分: {rsi_score}/{rsi_max}分")
    else:
        print(f"  RSI: N/A（需15根K线，当前{kline_count}根）")

    # --- MACD ---
    print("\n【MACD(12,26,9)】")
    if macd is not None:
        print(f"  DIF:  {macd['dif']}")
        print(f"  DEA:  {macd['dea']}")
        print(f"  柱状图: {macd['histogram']}")
        print(f"  信号: {macd['signal_text']}")
        print(f"  评分: {macd_score}/{macd_max}分")
    else:
        print(f"  MACD: N/A（需35根K线，当前{kline_count}根）")

    # --- SEPA ---
    print("\n【SEPA趋势模板检查 (5项, v5.2)】")
    print(f"  通过: {sepa['passed_count']}/{sepa['total_valid']}（有效项）, 共{sepa['total']}项")
    print(f"  SEPA得分: {sepa['raw_score']}/{sepa['effective_max']}分")
    for item in sepa["items"]:
        if item["passed"] is True:
            status = "✅"
        elif item["passed"] is False:
            status = "❌"
        else:
            status = "⚠️"
        score_str = f" [{item['score']}/{item['max_score']}分]" if item["applicable"] else " [剔除]"
        print(f"  {status} {item['name']}: {item['detail']}{score_str}")

    # --- 量价 ---
    print("\n【量价分析】")
    if "error" in volume_analysis:
        print(f"  {volume_analysis['error']}")
    else:
        va = volume_analysis
        print(f"  近20日均量: {va['avg_volume_20d']:.0f}")
        print(f"  最新成交量: {va['latest_volume']:.0f}")
        print(f"  量比: {va['volume_ratio']:.2f}  —  {va['volume_trend']}")
        print(f"  涨日数/跌日数: {va['up_days']}/{va['down_days']}")
        print(f"  涨日均量/跌日均量: {va['avg_up_volume']:.0f}/{va['avg_down_volume']:.0f} (比值: {va['up_down_ratio']:.2f})")
        print(f"  量价质量: {va['vol_quality']}")
        print(f"  评分: {vol_raw}/{vol_max}分")

    # --- 筹码输出 ---
    print(f"\n【筹码结构】")
    if chip_result is not None and chip_result["applicable"]:
        print(f"  评分: {chip_result['raw_score']}/{chip_result['effective_max']}分 (市场: {args.market})")
        for item in chip_result["items"]:
            ratio_str = f"{'满分' if item['score_ratio'] == 1.0 else '半分' if item['score_ratio'] == 0.5 else '零分'}"
            print(f"  · {item['name']}: {item['value']} → {ratio_str}")
        if chip_result["missing"]:
            for m in chip_result["missing"]:
                print(f"  ⚠️ 缺失: {m}")
    elif chip_result is not None:
        print(f"  筹码不可用: {', '.join(chip_result['missing'])}")
    else:
        print(f"  {chip_note}")
        print(f"  用法: stock-data chip <code> > chip.json")
        print(f"        然后加 --chip chip.json --market {args.market}")

    # --- 数据完整性 ---
    print(f"\n【数据完整性】")
    print(f"  等级: {completeness['level']}级 — {completeness['description']}")

    # --- v5.2: 技术面汇总 ---
    # 技术面理论满分: SEPA(40) + RSI(5) + MACD(5) + 量价(5) + 筹码(5或3)
    # 含筹码时: A股=60, 港美股=58; 不含筹码=55
    if chip_result is not None and chip_result["applicable"]:
        tech_theoretical = 60 if args.market == "A" else 58
        chip_label = "含筹码"
    else:
        tech_theoretical = 55  # SEPA(40)+RSI(5)+MACD(5)+量价(5)
        chip_label = "不含筹码"

    mapping = calculate_score_mapping(tech_raw, tech_max, theoretical_max=tech_theoretical)
    confidence = determine_confidence(completeness["level"], mapping["coverage_pct"])

    print(f"\n{'=' * 60}")
    print(f"【技术面评分汇总 ({chip_label})】")
    print(f"  原始得分: {tech_raw} / 有效满分: {tech_max}")
    print(f"  理论满分: {tech_theoretical}（{chip_label}）")
    if mapping["mapped_score"] is not None:
        print(f"  映射得分: {mapping['mapped_score']}分 (映射到100分制)")
    else:
        print(f"  映射得分: 无法计算（有效满分为0）")
    print(f"  分母覆盖率: {mapping['coverage_pct']:.1f}%")
    print(f"  置信等级: {confidence}")

    if all_missing:
        print(f"\n【缺失项】({len(all_missing)}项)")
        for m in all_missing:
            print(f"  ⚠️ {m}")
    else:
        print(f"\n【缺失项】无")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    main()
