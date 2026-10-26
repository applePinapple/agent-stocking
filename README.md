# agent-stocking

严格执行明确股票策略的本地日线回测与审计项目，支持读取 CSV、通达信 `.day` 行情，以及经核对的截图观测。项目保存成交、拒单、每日权益、费用和输入哈希；不会将未确认规则、合成行情或未完成的评测当作正式市场收益。

策略资料见[策略1—21索引](docs/strategies/README.md)。通用单股回测、策略1截图回放、分批建仓和共享资金组合使用不同的执行模块，成交时点与资金规则以各策略的明确配置为准。项目不连接交易账户、不执行真实下单；尚未完整支持送转配股等公司行动，也不宣称与通达信公式完全兼容。

通达信新增公式见[MACDBS专家买卖点副图](docs/indicators/MACDBS/README.md)和[6套网络专家公式候选](docs/strategies/网络专家公式候选-20261025/README.md)。这些公式已在本机编译并保存，验证范围和未解决的边界差异见各自说明；不代表正式收益评测完成。文档中的`reports/macd-subchart-20261025/`及`reports/web-experts-20261025/`证据链接仅供本机查阅，原始导出、截图和生成报告不随仓库发布。

## 目录与文件用途

| 路径 | 功能 | 版本管理范围 |
| --- | --- | --- |
| `src/agent_stocking/` | 核心 Python 包：数据、配置、指标、各类执行器及报告 | 提交源码 |
| `scripts/` | 策略1证据组装、回放、独立流水审计命令 | 提交源码 |
| `configs/` | 合成演示配置与待填写的策略配置模板 | 提交模板；真实配置提交前另行审阅 |
| `data/sample/` | 合成行情、逐日信号格式示例 | 提交示例；不是市场数据 |
| `data/raw/` | 用户本机原始行情 | 默认忽略，仅保留 `.gitkeep` |
| `docs/` | 数据格式、策略模板、通达信快捷键与策略1详细说明 | 提交文档 |
| `docs/strategies/` | 策略1—21及新增网络专家候选的来源、买卖规则、公式源码、边界及适配状态 | 提交源码与文档；状态以各篇说明为准 |
| `docs/indicators/` | 通达信技术指标源码、参数、用法与验证范围；目前包含MACDBS副图 | 提交源码与文档；本机验证证据保留在`reports/` |
| `tests/` | 时序、资金、费用、许可、指标、分批、组合及报告测试 | 提交测试 |
| `reports/` | 本机原生评测、回放输出、截图、审计证据与研究材料 | 默认忽略；下述源码和公式白名单例外 |
| `reports/strategies3-21-watchlist-20210101-20261023/` | 策略3—21批次的输入核验、适配、运行与报告工具 | 仅提交经审阅的根目录 Python 源码 |
| `reports/strategies3-21-watchlist-20210101-20261023/native-evaluation/` | 通达信专家公式、诊断指标和版本核对材料 | 仅提交 `per-strategy/*.txt` 与 `CHECKALL*.txt` 公式源码 |
| `tmp/` | 临时下载、PDF处理运行库、实验中间文件 | 忽略，不提交 |
| `run.py` | 不安装包时，从仓库启动通用命令行 | 提交源码 |
| `pyproject.toml` | Python版本、包元数据及命令行入口 | 提交配置 |
| `AGENTS.md` | 项目协作与策略执行约束 | 提交规则 |
| `.gitignore` | 定义缓存、环境、原始数据和结果的忽略范围 | 提交配置 |
| `.gitattributes` | 保留源码原始字节，防止换行转换改变已绑定的SHA-256 | 提交配置 |
| `.git/` | 本地 Git 历史、分支及远端配置 | Git 自行管理，不作为项目文件提交 |

`reports/` 中的源码白名单是本项目历史布局的例外。行情、截图、JSON运行结果、PDF资料、原生 `.xls` 导出及认证资料仍保留在本机。批次工具依赖对应本机证据和配置，克隆仓库后不能直接复现已有历史结果；依赖关系、公式版本及使用边界见[批次源码说明](reports/strategies3-21-watchlist-20210101-20261023/SOURCE-CODE.md)。

## 核心模块

| 模块 | 职责 |
| --- | --- |
| `models.py`、`data.py`、`config.py` | 行情与交易数据类型、CSV/`.day`读取、日期/数值/许可检查、显式配置验证 |
| `indicators.py`、`strategies.py` | 基础 MA/REF/CROSS、逐日信号文件与合成均线演示；不是通达信完整解释器 |
| `engine.py`、`reporting.py` | 通用单股全仓回测、成交及拒单流水、每日权益、统计与来源哈希 |
| `strategy1.py` | 策略1机会与批次管理、理想收盘成交、封板顺延、显式现金分红及期末估值 |
| `scale_in.py` | 策略11/13分批状态、冻结预算、按实际成交推进档位、拒单及整组退出 |
| `portfolio.py` | 多股共享现金、持仓槽位、先卖后买、精确订单许可、期末处理与中断审计 |
| `portfolio_strategies.py` | 策略14累计RSI选择、策略20周度排序及已完成历史数据上下文 |
| `point_in_time.py` | 财务/股本事实的生效时间与公开时间控制、覆盖区间及来源哈希核验 |
| `cli.py`、`__main__.py` | 通用命令行参数解析与运行调度 |

分批和共享组合的核心实现已具备，部分本机能力验证、具体配置和正式执行仍待完成；代码测试通过不等于所有策略都已完成原生评测。公式源码也包含用于追溯的历史版本：策略6旧版曾出现容量或运行问题，v3仍待本机编译及运行验证。

## 安装、演示与测试

需要 Python 3.11 或更高版本。核心运行和测试仅使用标准库。从项目根目录执行：

```powershell
python run.py --help
python run.py demo
python -m unittest discover -s tests -t . -v
```

演示使用 `configs/demo.json` 和 `data/sample/demo.csv` 的合成行情。首次默认写入 `reports/demo`，后续运行使用新目录保留既有结果；演示收益只验证程序流程。

也可安装命令行入口：

```powershell
python -m pip install -e .
agent-stocking --help
agent-stocking demo
```

## 运行入口

### 通用单股回测

先按[策略确认模板](docs/strategy-template.md)明确规则，再按[数据格式](docs/data-format.md)提供行情、逐日信号、价格口径和买卖许可。复制 `configs/strategy.template.json`，填写资金、整手、费用等参数；模板为 `pending`，不会直接生成收益。

```powershell
python run.py validate-data --input data/raw/my-stock.csv --format csv --volume-unit shares
python run.py backtest --config configs/my-strategy.json --output reports/my-run
```

配置文件中的相对路径以配置文件所在目录为基准，例如 `configs` 中配置使用 `../data/raw/my-stock.csv`。命令行参数路径以当前工作目录为基准。

本地 `.day` 可转换成 CSV；输出路径必须不存在，转换后仍需核对证券、复权、公司行动、单位和成交许可：

```powershell
python run.py import-day --input D:\new_tdx\vipdoc\sh\lday\sh600000.day --output data/raw/sh600000.csv
```

通用引擎在收盘后形成信号，于下一根已有日线的开盘尝试执行，按配置整手全仓买入或全部卖出。结算等待按输入日线行数计数；失败订单取消并保留拒单原因；期末持仓按最后收盘估值。该执行模型不能直接替代策略1、分批或共享组合约定。当前通用引擎只接受明确的不复权、无待处理公司行动输入；滑点按当根完整高低价范围校验，属于需明确接受的成交近似。

### 策略1证据回放

| 命令 | 用途 |
| --- | --- |
| `python scripts/assemble_strategy1_local_replay.py --help` | 组装截图观测和证据，默认生成草稿；核验齐全后才能显式形成正式输入 |
| `python scripts/run_strategy1_replay.py --help` | 按明确观测、规则和哈希绑定证据回放策略1 |
| `python scripts/audit_strategy1_replay.py --help` | 独立复核已生成的成交、分红及权益流水，不重新运行策略 |

输入字段、稀疏筹码观测、现金分红、来源核验和完整命令见[策略1回放详细说明](docs/strategy1-replay.md)。该页保留历史会话记录，历史进度句不代表当前验收状态；正式运行仍须提供本机证据。

### 分批、共享组合及通达信批次工具

`scale_in.py`、`portfolio.py` 和相关选择器提供 Python 接口，未接入 `run.py backtest` 的通用配置。策略3—21批次根目录中的 `scale_in_native_runner.py`、`portfolio_native_runner.py` 等负责本机输入装配、预检和显式补充模拟；这些工具依赖未提交的行情、日历、事实包、许可及确认配置。

通达信原生评测导出、本地补充模型和合成测试分别记录。`pending` 配置不能输出正式收益；S20基准计算方式、S14历史股本口径、分批预算和组合仓位也不得静默替换。具体边界见对应[策略文档](docs/strategies/README.md)。

## 输出、数据与提交约定

通用回测输出 `summary.json`、`trades.csv`、`orders.csv`、`equity.csv` 和 `report.md`，保存完整配置、成交及拒单、每日权益和来源哈希。策略1另保留批次、现金分红和证据核验记录；共享回放支持在中断时保存已经提交的状态。

- 真实原始行情只读；保留证券、日期、复权、成交量单位、公司行动及来源。导入成功不代表数据已经满足严格回测要求。
- 现金和费用使用 `Decimal`；费率、最低费用、滑点和仓位来自明确配置，不作为统一市场默认值。
- 决策只读取当时已完成的数据与当时已公开的事实。不从 OHLCV 猜测成交许可；未来数据扰动、拒单、结算和缺失输入均纳入测试。
- 代码、测试、模板和文档可以提交；原始行情、生成收益、客户端截图、运行证据、认证信息和临时运行库默认留在本地。批次 Python 与公式源码按前述白名单单独管理。
- 现存股票名单、当前复权版本和后来消失的历史信号可能带来样本选择或删失偏差；相关记录随本机报告保存。

通达信操作前查阅[快捷键与本机实测记录](docs/tongdaxin-shortcuts.md)。官方资料、当前客户端观察和本地模型验证分别记录，不能相互替代。
