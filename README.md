# 米勒维尼投资分析框架 v5.0 (100分制)

基于马克·米勒维尼《股市魔法师》SEPA策略的中短线投资分析框架。

## 核心特性

- **100分制评分体系**：基本面(25) + 技术面(60) + 催化剂(15)
- **仓位建议**：A+(20%) / A(15%) / B(10%) / C(5%) / D(0%)
- **纯数据驱动**：零估算、零编造、所有指标Python脚本计算
- **美股代码格式支持**：自动适配不同接口的代码格式要求

## 依赖

- **stock-data v2.3.0+** (必需): 腾讯自选股数据源
- **Python 3.x** (必需): 用于运行 calc_indicators.py

## 安装

### 方式1：Git 克隆

```bash
cd ~/.openclaw/workspace/skills
git clone https://github.com/amebapu/stock-analysis-framework.git
cd stock-analysis-framework
```

### 方式2：手动下载

1. 下载本仓库 ZIP 文件
2. 解压到 `~/.openclaw/workspace/skills/stock-analysis-framework/`

## 使用方法

### 基本分析流程

```bash
# 1. 获取K线数据并计算技术指标
stock-data kline usTSLA day 200 qfq | python3 calc_indicators.py

# 2. 获取财务数据（美股注意代码格式）
stock-data finance TSLA.N income 4

# 3. 获取筹码分布
stock-data chip usTSLA

# 4. 获取新闻资讯
stock-data news usTSLA 1 10 2

# 5. 获取机构研报
stock-data report usTSLA 1 10 1
```

### 美股代码格式对照表

| 数据类型 | 正确格式 | 示例 |
|----------|----------|------|
| K线数据 | `usTSLA` | `stock-data kline usTSLA day 200 qfq` |
| 财务数据 | `TSLA.N` / `TSLA.O` | `stock-data finance TSLA.N income 4` |
| 实时行情 | `usTSLA` | `stock-data quote usTSLA` |
| 筹码分布 | `usTSLA` | `stock-data chip usTSLA` |
| 新闻研报 | `usTSLA` | `stock-data news usTSLA 1 10 2` |

**重要提示**：财务数据接口需要使用 `.N` (纽交所) 或 `.O` (纳斯达克) 后缀，而非 `us` 前缀。

## 评分体系

### 1. 基本面 (25分)

| 指标 | 标准 | 分值 |
|------|------|------|
| 净利润增长 | ≥25% | 10分 |
| 营收增长 | ≥20% | 5分 |
| ROE | ≥17% | 5分 |
| 经营现金流 | 为正 | 5分 |

**扣分项**：净利润下滑(-5)、营收下滑(-5)、连续亏损(-10)

### 2. 技术面 (60分)

| 指标 | 标准 | 分值 |
|------|------|------|
| SEPA模板 | 5项检查 | 40分 |
| RSI(14) | 40-70 | 5分 |
| MACD | 多头 | 5分 |
| 量价 | 量比>0.8 | 5分 |
| 筹码 | 获利比例 | 5分 |

**SEPA模板检查项**：
1. 股价 > MA50
2. MA50 > MA150 > MA200 (多头排列)
3. 股价 > MA200
4. 距52周高点 < 25%
5. 距52周低点 > 25%

### 3. 催化剂 (15分)

| 指标 | 标准 | 分值 |
|------|------|------|
| 新闻情绪 | 正面新闻 | 3分 |
| 机构评级 | 买入评级 | 3分 |
| 无重大利空 | 无负面新闻 | 3分 |
| 流动性 | 日成交>$50M | 3分 |
| 资金/业绩 | 业绩超预期 | 3分 |

## 评级与仓位

| 总分 | 等级 | 建议 | 仓位 |
|------|------|------|------|
| 90-100 | A+ | 强烈建议 | 20% |
| 80-89 | A | 可以建仓 | 15% |
| 70-79 | B | 小仓位试探 | 10% |
| 60-69 | C | 建议观望 | 5% |
| <60 | D | 明确回避 | 0% |

## 风控红线

触碰任一红线，直接排除或止损：
- 跌破MA200
- 买入后跌7-8%
- 连续2季度业绩下滑
- 重大负面新闻

## 数据来源

- **stock-data**: 腾讯自选股 (https://github.com/yourname/stock-data)
- **计算脚本**: Python calc_indicators.py (本仓库)

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v5.0 | 2026-03-09 | 初始发布，100分制评分体系 |

## 免责声明

NOT FINANCIAL ADVICE. 本分析框架基于公开数据和技术指标，不构成投资建议。投资有风险，决策需谨慎。

## License

MIT License
