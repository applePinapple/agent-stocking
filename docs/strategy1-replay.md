# 策略1回放详细说明

本页保留原根 README 中的策略1操作说明和截至2026-10-24的本机会话记录。文中的“尚未完成”等进度描述属于该历史记录，**不代表当前验收状态**；当前状态以用户最近验收及本地正式报告为准。

下文 `reports/...` 指本机证据与结果目录，这些行情、截图和生成文件不随代码提交。命令均从项目根目录执行；示例文件名不表示相应证据已准备完毕。公共项目入口与目录说明见[根 README](../README.md)。

## 策略1：截图复盘入口

策略说明按独立 Markdown 文件管理，[策略1](strategies/策略1.md)以用户确认后整理的干净正文为准。每次执行必须指定并在报告输出标的、请求起止日期和实际数据区间。

本次任务为绿的谐波（688017），请求区间为2023-01-01至2026-10-24，最新已收盘日2026-10-23。统一截图会话为 [`local-mode-20261024`](../reports/strategy1-688017-20230101-20261023/local-mode-20261024)。**2023-01-03至2026-10-23的920根不复权日线价格、26次当前可见火箭及其余894日的无火箭覆盖已经核对。**必要筹码数据仍在逐日采集和独立复核，尚未完成真实回放，完整收益尚未生成。进度见[执行进度](../reports/strategy1-688017-20230101-20261023/执行进度.md)。

旧420日和较早80日的截图继续保留，不混入当前P序列。同步对照已确认部分同日筹码值不同，例如2025-01-27旧图3.8%、当前本地模式76.1%，2025-02-05旧图93.5%、当前98.9%；这不证明仅由加载长度造成差异，也不表示所有旧值均错。新P均取当前完整本地历史状态。来源状态见[source epoch记录](../reports/strategy1-688017-20230101-20261023/source-epoch-status.json)。

小火箭源码加密，历史火箭可能因后续下跌而消失，目前只有当前可见历史图。用户已同意按现存火箭复盘；所有结果必须注明信号删失与幸存者偏差、可能偏乐观，不能称为当时全部可见信号的完整历史回测。价格、筹码获利比例、火箭及执行标记以本地通达信截图核对，不用外部行情替代。

已按用户授权仅下载688017上市以来日线，并保存本地日线模式、2000根上限及报表不复权设置；原生核验`LD=UD=1489`、`FQ=0`。通过分别指定F1显示截止日补拍历史价格报表，11页共946行，重叠26行OHLC一致，合并为920个唯一日期；火箭和P采集前已解除截止锁定，仍使用完整1489根。详见[价格覆盖](../reports/strategy1-688017-20230101-20261023/local-mode-20261024/derived/price-union-20261024/coverage.json)及[信号审计](../reports/strategy1-688017-20230101-20261023/local-mode-20261024/derived/rocket-daily-confirmed-audit.json)。辅助公式`WINNER(C)*100`实测与右侧原生获利比例不一致，已排除，不能用其批量值替代P。

当前流程使用专用组装器核验统一会话内的价格、火箭、独立复核P和执行字段证据，默认只生成采集草稿及状态，不计算收益：

```powershell
$localRun = "reports/strategy1-688017-20230101-20261023/local-mode-20261024"
python scripts/assemble_strategy1_local_replay.py --local-run "$localRun"
```

默认输出`strategy1-observations-draft.csv`、`assembled-evidence-manifest-draft.json`、逐日草稿证据JSON和`derived/collection-status.json`，指出下一必需P或实际执行字段缺口。草稿kind不能被正式回放器接受。完整安全追踪必须走完全部日线，并核验现金分红及公司行动覆盖声明，才可通过后面的`--finalize`生成正式输入。

旧文件`reports/strategy1-688017-20230101-20261023/assemble_observations.py`及其`derived/assembly-draft`属于历史完整P草稿流程，不再作为当前执行入口；旧420条资料和既有草稿不得作为正式输入。候选涨跌停算术不等于实际成交许可。

专用回放器的CSV有以下**11个必填列名**。默认完整P模式要求所有字段均有值；显式启用稀疏P模式后，仅`profit_pct`可留空，信号、价格、许可和来源仍不得缺省：

```csv
date,open,high,low,close,profit_pct,rocket,close_limit_up,open_buy_allowed,sell_allowed,source
```

- `date` 使用 `YYYY-MM-DD`，严格递增且不得重复；OHLC必须为有限正十进制数。已提供的`profit_pct`必须为0—100内的有限十进制数，`profit_pct=45`表示45%。
- `rocket`、`close_limit_up`、`open_buy_allowed`、`sell_allowed`必须明确填写`true`／`false`、`True`／`False`或`1`／`0`。不得手工把未复核的空值填成`false`，也不得默认成交许可为`true`。专用组装器只有在完整安全追踪证明执行字段未被决策读取时，才可写入明确标注的安全占位；占位不是市场事实，正式清单逐日记录依据并绑定CSV、引擎及证据哈希。卖出许可遵循本次理想收盘执行约定。
- 完整P模式的`source`可指向直接截图，或下述每日证据JSON；稀疏P模式必须使用每日证据JSON。所有路径均相对于主证据清单所在目录，不能为空、不能使用绝对路径或逃逸该目录。
- 回放器核验CSV、日期JSON及其引用原图的全链SHA-256，输出前再次检查输入、清单及全部来源文件是否变化。文件存在、哈希及人工声明日期一致，不等于已核验图片内文字；脚本不通过OCR推断数据日期或P。

**稀疏P需显式启用。**后面的回放命令已包含`--allow-sparse-profit`。CSV的P空白仅表示尚未提供，不会填成0、前值或任何阈值。引擎先按既有状态处理开盘顺延，再登记当天火箭；有持仓或有效观察／顺延机会时必须有当天P。只有空仓且无有效机会、当天没有新火箭时才允许缺省。必要P缺失会报出具体日期、持仓数量和机会编号并终止，**不会创建正式结果目录或输出收益**。全部日线仍保留，用于窗口计数、每日净值、年化和回撤。

三类数据可来自不同截图：价格来自报表页，火箭来自已逐根核对的历史图，P来自对应日期的筹码图。CSV的`source`例如`daily-evidence/2023-01-03.json`，文件结构如下；`sha256`占位内容须替换为对应原图实际的64位十六进制SHA-256：

```json
{
  "schema_version": 1,
  "kind": "tdx_daily_evidence",
  "date": "2023-01-03",
  "price": {"source": "price-pages/page-001.jpg", "sha256": "<价格原图SHA-256>", "date": "2023-01-03"},
  "rocket": {"source": "rocket-maps/map-001.jpg", "sha256": "<火箭原图SHA-256>", "date": "2023-01-03"},
  "profit": null
}
```

`price`、`rocket`必须有图；P有值时，`profit`也必须是含`source`、`sha256`、`date`的图像引用对象，不能为`null`。每个角色的日期必须与CSV行及JSON顶层日期一致，可附非空`locator`或`note`标注人工核对的表格行／K线位置。同一原图可被多个日期复用，但每个日期都必须明确映射；不能把未核对的火箭标成`false`。角色中的路径同样相对**主证据清单目录**，不是相对每日JSON目录。未知kind、重复键、角色缺失、日期错配、路径逃逸、缺图或SHA-256不符均拒绝执行。

**执行前必须核验复权与公司行动。**策略1支持通过`--cash-dividends`传入经核对的登记日、除息日、付款日、每股现金及来源原图哈希；登记收盘锁定权益、除息计应收、付款开盘前入现金，分红归属于原买入批次。金额不自动按10股换算、不推断税率。尚不支持送转、配股等其他事件；旧版通用引擎仍未核算公司行动。本次规则核验见 [`market-rules.md`](../reports/strategy1-688017-20230101-20261023/market-rules.md)。

待必要P采集和独立复核、实际读取的执行字段确认及公司行动核验完成后，显式生成正式输入，再运行专用回放器。以下分红事件及coverage文件名只是路径示例，**不表示已经生成或核验**；须先准备真实、已确认且带原图哈希的文件。coverage声明必须覆盖实际数据区间、绑定该分红事件JSON的SHA-256，并明确每股金额及事件日期已核对、分红事件完整、无其他未支持公司行动：

```powershell
$localRun = "reports/strategy1-688017-20230101-20261023/local-mode-20261024"
python scripts/assemble_strategy1_local_replay.py --local-run "$localRun" --finalize --cash-dividends "$localRun/derived/cash-dividends-verified.json" --corporate-actions-coverage "$localRun/derived/corporate-actions-coverage-verified.json"
python scripts/run_strategy1_replay.py --input "$localRun/strategy1-observations.csv" --output "$localRun/final-replay" --symbol 688017 --name 绿的谐波 --start 2023-01-01 --end 2026-10-24 --price-adjustment none --allow-sparse-profit --evidence-manifest "$localRun/assembled-evidence-manifest.json" --cash-dividends "$localRun/derived/cash-dividends-verified.json"
```

`--price-adjustment none`为必填项，用于明确已核对输入价格为不复权口径；该参数不会自动转换复权数据或处理公司行动。

只有`ready_for_formal_replay=true`才会生成`strategy1-observations.csv`及`assembled-evidence-manifest.json`；条件未满足时仍为草稿。正式回放必须传入清单绑定的同一份分红事件文件，输入、证据、日期全集或引擎变化都会被拒绝。上述命令是后续执行方式，当前真实回放尚未完成，不表示确认文件或真实结果已生成；输出目录必须不存在或为空。请求结束日为2026-10-24，报告另列实际末日2026-10-23。策略1正常按理想当日收盘价执行，收盘封涨停后按规则尝试后续开盘，每个火箭只使用一次预算；旧仓和当日新增股份分别核算。资金不足、退出无法成交等未约定分支会终止计算，不自行放宽规则。

本次用户指定初始资金100万元、每次买入含费用最多10万元；买卖佣金均为万分之1、每笔最低5元，其他费用本次按0元计算、滑点为0。这些是本次回放的明确约定，已写入专用执行器配置，**不是通用市场默认费率**。

策略1输出`trades.csv`、`orders.csv`、`daily_equity.csv`、`closed_batches.csv`、`cash_dividends.csv`、`source-verification.json`、`summary.json`及`report.md`，保留全部成交、订单事件、批次盈亏、期末估值、核心统计、完整配置和来源哈希；`cash_dividends.csv`独立记录登记、除息应收及现金到账。`source-verification.json`区分日期JSON、角色原图与旧直接截图，并记录角色、日期、引用次数及文件哈希。账户总收益率以初始100万元为分母；`closed_batches.csv`的`net_return`按各批含买入佣金成本计算，不能将批次收益率直接相加作为账户收益率。稀疏P模式下，`daily_equity.csv`未采P留空，摘要记录已采／缺省P行数及必要P缺失数；不补值、不删除空闲日线。覆盖报告只陈述已采集日期及行数，不能据此断言不存在漏采或停牌。

