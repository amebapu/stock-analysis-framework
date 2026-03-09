#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
技术指标计算脚本 - calc_indicators.py
版本: 1.1.0 (2026-03-09)

功能: 从 stdin 读取 stock-data kline 的 JSON 输出，
      精确计算 MA/RSI/MACD/SEPA/量价 等技术指标。
      v1.1.0: 更新数据完整性评级为A-F六级

用法:
  stock-data kline sh600519 day 252 qfq 2>/dev/null | sed '/^\[HTTP/d' | python calc_indicators.py
  stock-data kline usAAPL day 252 qfq 2>/dev/null | sed '/^\[HTTP/d' | python calc_indicators.py

依赖: 仅使用 Python 标准库（json/sys/math），无需 pip install
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

    返回: list[dict]，按日期从旧到新排序，每个 dict 包含:
          date, open, close, high, low, volume, amount
    """
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
# 3. SEPA 趋势模板检查 (6 项)
# ============================================================

def check_sepa(closes, current_price):
    """
    米勒维尼 SEPA 趋势模板 6 项检查。

    检查项:
      1. 股价 > MA50
      2. MA50 > MA150
      3. MA150 > MA200
      4. MA200 至少走平或上升（对比 20 日前）
      5. 股价 > MA200
      6. 股价处于 52 周高点的 75% 以上

    返回 dict，含 items (每项详情) 和 passed_count。
    """
    items = []
    ma50 = calculate_ma(closes, 50)
    ma150 = calculate_ma(closes, 150)
    ma200 = calculate_ma(closes, 200)

    # 第 1 项: 股价 > MA50
    if ma50 is not None:
        passed = current_price > ma50
        items.append({
            "name": "股价 > MA50",
            "passed": passed,
            "detail": f"股价={current_price:.2f}, MA50={ma50:.2f}",
        })
    else:
        items.append({
            "name": "股价 > MA50",
            "passed": None,
            "detail": f"N/A（需50根K线，当前{len(closes)}根）",
        })

    # 第 2 项: MA50 > MA150
    if ma50 is not None and ma150 is not None:
        passed = ma50 > ma150
        items.append({
            "name": "MA50 > MA150",
            "passed": passed,
            "detail": f"MA50={ma50:.2f}, MA150={ma150:.2f}",
        })
    else:
        items.append({
            "name": "MA50 > MA150",
            "passed": None,
            "detail": "N/A（数据不足）",
        })

    # 第 3 项: MA150 > MA200
    if ma150 is not None and ma200 is not None:
        passed = ma150 > ma200
        items.append({
            "name": "MA150 > MA200",
            "passed": passed,
            "detail": f"MA150={ma150:.2f}, MA200={ma200:.2f}",
        })
    else:
        items.append({
            "name": "MA150 > MA200",
            "passed": None,
            "detail": "N/A（数据不足）",
        })

    # 第 4 项: MA200 走平或上升
    if ma200 is not None and len(closes) >= 220:
        ma200_20d_ago = calculate_ma(closes[:-20], 200)
        if ma200_20d_ago is not None:
            passed = ma200 >= ma200_20d_ago
            items.append({
                "name": "MA200 走平或上升",
                "passed": passed,
                "detail": f"当前MA200={ma200:.2f}, 20日前MA200={ma200_20d_ago:.2f}",
            })
        else:
            items.append({
                "name": "MA200 走平或上升",
                "passed": None,
                "detail": "N/A（数据不足以对比20日前）",
            })
    else:
        items.append({
            "name": "MA200 走平或上升",
            "passed": None,
            "detail": f"N/A（需220根K线，当前{len(closes)}根）",
        })

    # 第 5 项: 股价 > MA200
    if ma200 is not None:
        passed = current_price > ma200
        items.append({
            "name": "股价 > MA200",
            "passed": passed,
            "detail": f"股价={current_price:.2f}, MA200={ma200:.2f}",
        })
    else:
        items.append({
            "name": "股价 > MA200",
            "passed": None,
            "detail": f"N/A（需200根K线，当前{len(closes)}根）",
        })

    # 第 6 项: 股价 >= 52 周高点的 75%
    if len(closes) >= 50:
        # 取最近 250 个交易日（约 52 周），不足则用全部
        period_data = closes[-250:] if len(closes) >= 250 else closes
        high_52w = max(period_data)
        threshold = high_52w * 0.75
        passed = current_price >= threshold
        items.append({
            "name": "股价 >= 52周高点的75%",
            "passed": passed,
            "detail": f"股价={current_price:.2f}, 52周高={high_52w:.2f}, 75%线={threshold:.2f}",
        })
    else:
        items.append({
            "name": "股价 >= 52周高点的75%",
            "passed": None,
            "detail": f"N/A（需至少50根K线，当前{len(closes)}根）",
        })

    # 统计
    passed_count = sum(1 for item in items if item["passed"] is True)
    total_valid = sum(1 for item in items if item["passed"] is not None)

    return {
        "items": items,
        "passed_count": passed_count,
        "total_valid": total_valid,
        "total": len(items),
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
# 5. 数据完整性评级
# ============================================================

def assess_data_completeness(kline_count):
    """
    评估数据完整性等级，用于美股降级规则。
    v5.1更新: 使用A-F六级分类，与SKILL.md保持一致

    返回: dict，含 level (A/B/C/D/E/F)、description、score_cap
    """
    if kline_count >= 252:
        return {
            "level": "A",
            "description": f"数据完整（{kline_count}根K线，满足所有指标）",
            "score_cap": 60,
        }
    elif kline_count >= 220:
        return {
            "level": "A-",
            "description": f"数据基本完整（{kline_count}根K线，52周高低点可能不完整）",
            "score_cap": 58,
        }
    elif kline_count >= 200:
        return {
            "level": "B",
            "description": f"数据部分不足（{kline_count}根K线，MA200趋势判断不可用）",
            "score_cap": 55,
        }
    elif kline_count >= 50:
        return {
            "level": "C",
            "description": f"数据不足（{kline_count}根K线，MA200不可用）",
            "score_cap": 45,
        }
    elif kline_count >= 35:
        return {
            "level": "D",
            "description": f"数据严重不足（{kline_count}根K线，仅RSI可用）",
            "score_cap": 30,
        }
    elif kline_count >= 15:
        return {
            "level": "E",
            "description": f"数据极度不足（{kline_count}根K线，仅RSI可用）",
            "score_cap": 15,
        }
    elif kline_count > 2:
        return {
            "level": "F",
            "description": f"数据近乎无效（{kline_count}根K线，MA/RSI/MACD全部不可用）",
            "score_cap": 5,
        }
    else:
        return {
            "level": "F",
            "description": f"数据无效（{kline_count}根K线），技术面不可用",
            "score_cap": 0,
        }


# ============================================================
# 6. 主函数
# ============================================================

def main():
    """
    入口: 从 stdin 读取 JSON → 解析 K 线 → 计算所有指标 → 输出
    """
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
    sepa = check_sepa(closes, current_price)
    volume_analysis = analyze_volume(kline_data)

    # 格式化输出
    print("=" * 60)
    print("技术指标计算结果")
    print(f"日期: {latest_date}  |  收盘价: {current_price:.2f}")
    print(f"K线数量: {kline_count}  |  数据完整性: {completeness['level']}级")
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
    else:
        print(f"  RSI: N/A（需15根K线，当前{kline_count}根）")

    # --- MACD ---
    print("\n【MACD(12,26,9)】")
    if macd is not None:
        print(f"  DIF:  {macd['dif']}")
        print(f"  DEA:  {macd['dea']}")
        print(f"  柱状图: {macd['histogram']}")
        print(f"  信号: {macd['signal_text']}")
    else:
        print(f"  MACD: N/A（需35根K线，当前{kline_count}根）")

    # --- SEPA ---
    print("\n【SEPA趋势模板检查】")
    print(f"  通过: {sepa['passed_count']}/{sepa['total_valid']}（有效项）, 共{sepa['total']}项")
    for item in sepa["items"]:
        status = "✅" if item["passed"] is True else ("❌" if item["passed"] is False else "⚠️")
        print(f"  {status} {item['name']}: {item['detail']}")

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

    # --- 数据完整性 ---
    print(f"\n【数据完整性】")
    print(f"  等级: {completeness['level']}级 — {completeness['description']}")
    print(f"  技术面评分上限: {completeness['score_cap']}分")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
