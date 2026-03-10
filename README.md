# 米勒维尼投资分析框架 v6.0 (100分制 + 深度研究)

基于马克·米勒维尼《股市魔法师》SEPA策略的中短线投资分析框架，融合彼得林奇/巴菲特长线深度研究体系。

## 核心特性

- **双体系分析**: SEPA中短线评分(100分) + 深度研究长线分析(100分)
- **100分制评分体系**：基本面(25) + 技术面(60) + 催化剂(15)
- **深度研究10维度**: 商业理解/收入分解/行业背景/竞争格局/财务质量/风险下行/管理团队/牛熊情景/估值思考/长期论点
- **仓位建议**：A+(20%) / A(15%) / B(10%) / C(5%) / D(0%)
- **纯数据驱动**：零估算、零编造、所有指标Python脚本计算
- **双计算层**：calc_indicators.py（技术面）+ calc_fundamentals.py（基本面）
- **研究层**: web_search 补充行业/竞争/管理层等定性信息
- **252根K线优先**：满足52周高低点+MA200；不足时降级评分，缺项剔除分母，映射到100分
- **大盘环境必查**：标普500/上证指数/恒指趋势评估
- **美股代码格式支持**：自动适配不同接口的代码格式要求
- **分值映射机制**：缺项时同时剔除分子和分母，映射后总分公平可比
- **置信等级输出**：报告包含有效满分、缺失项、数据完整性与置信等级
- **联合解读矩阵**: SEPA + 深度研究双维度交叉判断

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

### 基本分析流程（Linux/macOS）

```bash
# 1. 大盘环境（必须获取）
stock-data kline usSPY day 252 qfq 2>/dev/null | sed '/^\[HTTP/d' | python3 calc_indicators.py

# 2. 获取K线数据并计算技术指标（必须252根）
stock-data kline usTSLA day 252 qfq 2>/dev/null | sed '/^\[HTTP/d' | python3 calc_indicators.py

# 3. 获取财务数据（美股注意代码格式）
stock-data finance TSLA.N income 4
stock-data finance TSLA.N cashflow 4  # 若失败则剔除该项分数

# 4. 获取筹码分布
stock-data chip usTSLA

# 5. 获取新闻资讯
stock-data news usTSLA 1 10 2

# 6. 获取机构研报
stock-data report usTSLA 1 10 1
```

### Windows PowerShell 用法

> v1.3.0 起，`calc_indicators.py` 内置 HTTP 日志过滤，Windows 无需 `sed` 和 `2>/dev/null`。

```powershell
# 1. 大盘环境
stock-data kline usSPY day 252 qfq | python calc_indicators.py

# 2. 个股技术面（不含筹码）
stock-data kline usTSLA day 252 qfq | python calc_indicators.py

# 3. 含筹码评分的完整技术面（先保存筹码数据，再管道合并）
stock-data chip usSNDK > sndk_chip.json
stock-data kline usSNDK day 252 qfq | python calc_indicators.py --chip sndk_chip.json --market US
```

### 命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--chip` | 筹码JSON文件路径（stock-data chip 输出） | `--chip sndk_chip.json` |
| `--market` | 市场类型: A(A股)/HK(港股)/US(美股)，默认A | `--market US` |

### 基本面评分（calc_fundamentals.py）

> v5.3 新增。基本面指标也纳入 Python 确定性计算，消除大模型手动解析 JSON 的幻觉风险。

```powershell
# === A股（管道最简单，推荐 lrb，含现金流） ===
stock-data finance sh600519 lrb | python calc_fundamentals.py --market A

# === A股（summary 模式，不含现金流） ===
stock-data finance sh600519 summary | python calc_fundamentals.py --market A

# === 美股（需要分别获取 income 和 balance） ===
stock-data finance AAPL.O income 4 > aapl_income.json
stock-data finance AAPL.O balance 2 > aapl_balance.json
python calc_fundamentals.py --market US --income aapl_income.json --balance aapl_balance.json

# === 港股（最完整：zhsy + zcfz + xjll） ===
stock-data finance hk00700 zhsy 4 > hk_zhsy.json
stock-data finance hk00700 zcfz 2 > hk_zcfz.json
stock-data finance hk00700 xjll 4 > hk_xjll.json
python calc_fundamentals.py --market HK --income hk_zhsy.json --balance hk_zcfz.json --cashflow hk_xjll.json
```

#### 基本面命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--market` | 市场类型: A/HK/US（必需） | `--market US` |
| `--income` | 利润表 JSON 文件（港美股必需） | `--income aapl_income.json` |
| `--balance` | 资产负债表 JSON 文件（用于自算 ROE） | `--balance aapl_balance.json` |
| `--cashflow` | 现金流量表 JSON 文件（港股可选） | `--cashflow hk_xjll.json` |

### 美股代码格式对照表

| 数据类型 | 正确格式 | 示例 |
|----------|----------|------|
| K线数据 | `usTSLA` | `stock-data kline usTSLA day 252 qfq` |
| 财务数据 | `TSLA.N` / `TSLA.O` | `stock-data finance TSLA.N income 4` |
| 实时行情 | `usTSLA` | `stock-data quote usTSLA` |
| 筹码分布 | `usTSLA` | `stock-data chip usTSLA` |
| 新闻研报 | `usTSLA` | `stock-data news usTSLA 1 10 2` |

**重要提示**：财务数据接口需要使用 `.N` (纽交所) 或 `.O` (纳斯达克) 后缀，而非 `us` 前缀。

## 评分体系

### SEPA评分（中短线决策，100分）

#### 1. 基本面 (25分)

| 指标 | 标准 | 分值 |
|------|------|------|
| 净利润增长 | ≥25% | 10分 |
| 营收增长 | ≥20% | 5分 |
| ROE | ≥17% | 5分 |
| 经营现金流 | 为正 | 5分 |

**扣分项**：净利润下滑(-5)、营收下滑(-5)、连续亏损(-10)

#### 2. 技术面 (60分)

| 指标 | 标准 | 分值 |
|------|------|------|
| SEPA模板 | 5项检查 | 40分 |
| RSI(14) | 40-70 | 5分 |
| MACD | 多头 | 5分 |
| 量价 | 量比>0.8 | 5分 |
| 筹码 | 获利比例 | 5分(A股)/3分(港美股) |

**SEPA模板检查项（每项8分）**：
1. 股价 > MA50
2. MA50 > MA150 > MA200 (多头排列)
3. 股价 > MA200
4. 距52周高点 < 25%
5. 距52周低点 > 25%

> v5.2变更：移除原"MA200走平或上升"项（与多头排列共线性高）；合并"MA50>MA150"和"MA150>MA200"为一项"多头排列"；新增"距52周低点>25%"作为独立检查项。

#### 3. 催化剂 (15分)

| 指标 | 标准 | 分值 |
|------|------|------|
| 新闻情绪 | 正面新闻 | 3分 |
| 机构评级 | 买入评级 | 3分 |
| 无重大利空 | 无负面新闻 | 3分 |
| 流动性 | 日成交>$50M | 3分 |
| 资金/业绩 | 业绩超预期 | 3分 |

### 深度研究评分（长线辅助，100分）

10个维度，每个10分，默认每次分析自动输出：

| # | 维度 | 满分 | 侧重 |
|---|------|------|------|
| 1 | 商业理解 | 10分 | 业务清晰度+客户粘性 |
| 2 | 收入分解 | 10分 | 收入多元化+增长质量 |
| 3 | 行业背景 | 10分 | 行业增速+趋势顺逆 |
| 4 | 竞争格局 | 10分 | 市场地位+护城河 |
| 5 | 财务质量 | 10分 | 多期趋势+盈利质量 |
| 6 | 风险下行 | 10分 | 风险可控度（越低越好→越高分） |
| 7 | 管理团队 | 10分 | 执行力+股东友好度 |
| 8 | 牛熊情景 | 10分 | 3-5年情景分析 |
| 9 | 估值思考 | 10分 | 估值合理度+安全边际 |
| 10 | 长期论点 | 10分 | 投资论点+错误信号 |

**深度研究评级**: ⭐(差) → ⭐⭐⭐⭐⭐(优秀)

## 评级与仓位

### SEPA评级

> **分值映射**：当某些评分项因数据不可获取而被剔除时，实际满分 < 100。映射公式：`映射后总分 = 原始得分 / 有效满分 × 100`。评级始终基于映射后的100分制。

| 映射后总分 | 等级 | 建议 | 仓位 |
|------|------|------|------|
| 90-100 | A+ | 强烈建议 | 20% |
| 80-89 | A | 可以建仓 | 15% |
| 70-79 | B | 小仓位试探 | 10% |
| 60-69 | C | 建议观望 | 5% |
| <60 | D | 明确回避 | 0% |

> 报告须同时输出：原始得分、有效满分、映射后总分、缺失项列表、K线完整性等级、置信等级。

## 风控红线

触碰任一红线，直接排除或止损：
- 跌破MA200
- 买入后跌7-8%
- 连续2季度业绩下滑
- 重大负面新闻

## 数据来源

- **stock-data**: 腾讯自选股 (https://github.com/yourname/stock-data)
- **技术面脚本**: Python calc_indicators.py (本仓库)
- **基本面脚本**: Python calc_fundamentals.py (本仓库)

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v5.0 | 2026-03-09 | 初始发布，100分制评分体系 |
| v5.1 | 2026-03-09 | 强制252根K线/大盘必查/ROE现金流处理/计算过程展示 |
| v5.2 | 2026-03-10 | SEPA改为5项(移除MA200走平,每项8分);新增确定性筹码打分score_chip();缺项映射(分子分母同时剔除→100分映射);报告输出有效满分/缺失项/置信等级;修正ROE/现金流跨市场链路;港美股筹码降权3分 |
| v5.2.1 | 2026-03-10 | calc_indicators v1.3.0: 修复coverage_pct分母写死100;52周高低点改用high/low;MACD评分补全3分档;新增--chip/--market参数;Windows兼容(自动过滤HTTP日志);移除废弃score_cap |
| v5.3 | 2026-03-10 | 新增calc_fundamentals.py v1.1.0基本面确定性计算(A股/港股/美股全支持); 3个Bug修复(stdin UTF-8/空行名/精确匹配优先); 双计算层架构 |
| v6.0 | 2026-03-10 | 新增10维度深度研究体系(100分); 5星评级; SEPA+深度研究联合解读矩阵; 三层数据架构(数据+计算+研究); 支持web_search补充 |

## 免责声明

NOT FINANCIAL ADVICE. 本分析框架基于公开数据和技术指标，不构成投资建议。投资有风险，决策需谨慎。

## License

MIT License
