# 行情与信号数据格式

## CSV 行情

使用 UTF-8 CSV；字段名采用下列小写名称，日期使用 `YYYY-MM-DD`。

```csv
date,open,high,low,close,volume,amount,buy_allowed,sell_allowed
2025-01-02,10.00,10.20,9.90,10.10,120000,1206000.00,true,true
2025-01-03,10.15,10.30,10.00,10.20,150000,1525000.00,true,true
```

| 字段 | 含义 |
| --- | --- |
| `date` | 交易日期，按时间严格递增且不可重复 |
| `open`, `high`, `low`, `close` | 元为单位的日线价格；数值应有限、为正，满足高低价关系 |
| `volume` | 非负成交量；配置明确是 `shares` 还是 `lots` |
| `amount` | 可选，成交额，统一为元；缺失时留空 |
| `buy_allowed` | 该日是否允许按本次回测的开盘成交假设买入，`true` / `false` |
| `sell_allowed` | 该日是否允许按本次回测的开盘成交假设卖出，`true` / `false` |

真实数据回测必须为所有行情行明确提供两个许可字段。它们是本次成交模型的输入，需要你核实其依据；日线 OHLCV 无法证明某个限价队列中能够成交。程序不会仅凭一个固定涨跌幅推断所有板块、日期或证券的交易状态。

`shares` 表示股，`lots` 按每手 100 股转换，且 CSV 成交量要求为整数。成交量单位与执行配置中的 `lot_size` 是两个概念：前者解释行情字段，后者限制买入数量。`amount` 必须已归一化为元，不依据列名猜测万元或其他单位。

CSV 校验示例：

```powershell
python run.py validate-data --input data/raw/my-stock.csv --format csv --volume-unit shares
```

结构校验通过仅说明数据可被读取及满足基本约束；不代表来源、公司行动、许可字段或经济含义已经被程序验证。不得用未来值填补历史价格；缺失交易日应保留真实缺失，不补造可成交 K 线。

## 配置中的数据声明

`data` 必须记录 `path`、`format`、`symbol`、`volume_unit`、`source`、`price_adjustment`、`corporate_actions`。相对 `path` 以配置文件所在目录为基准。

- `format`：`csv` 或 `tdx_day`。
- `source`：真实数据为 `user_supplied`；`synthetic` 仅用于合成测试。
- 初版 `price_adjustment` 仅接受 `none`，`corporate_actions` 仅接受 `none`。

这两个 `none` 是对数据的明确声明，不是程序自动检测结果。若区间有分红、送转、配股等事件，需先实现相应现金、持仓和价格处理后再做正式回测。将有此类事件的数据标为 `none` 会使收益失真。前复权或后复权价格也不能直接当作真实现金成交价格。

## 通达信本地 `.day`

项目按常见的 32 字节、小端本地日线布局读取：

| 偏移 | 类型 | 内容 |
| --- | --- | --- |
| 0 | 32 位整数 | `YYYYMMDD` 日期 |
| 4、8、12、16 | 32 位整数 | 开、高、低、收，读取后除以 100 |
| 20 | 32 位浮点 | 成交额，按元读取 |
| 24 | 32 位整数 | 成交量，按股读取 |
| 28 | 4 字节 | 保留字段，不用于策略与成交判断 |

以上是本项目支持的常见文件布局，尚未取得官方公开二进制规范保证。`.day` 的 `volume_unit` 必须为 `shares`。首次导入应将若干日期的价格、量、额与通达信实际显示对照，确认适用证券和数据版本；不能从扩展名证明复权状态或完整性。文件不含本框架需要的逐日买卖许可字段，导出为 CSV 后需补充才能进入严格回测。

```powershell
python run.py import-day --input D:\new_tdx\vipdoc\sh\lday\sh600000.day --output data/raw/sh600000.csv
```

程序不会修改原始通达信文件。请保留原始文件，用报告中的 SHA-256 与配置核对本次实际输入。

## 收盘信号 CSV

`strategy.kind: signals` 时，`strategy.path` 指向信号 CSV，相对路径同样以配置文件所在目录为基准。

```csv
date,action,reason
2025-01-02,buy,按已确认规则触发买入
2025-01-08,sell,按已确认规则触发卖出
```

- `action` 仅取 `buy`、`sell`、`hold`，表示该日收盘后的决策。
- 某个行情日期没有信号行，等同 `hold`。
- 日期严格递增、不可重复，且必须出现在行情中；行情区间外的信号拒绝执行。
- 决策不在信号日成交，最早在下一根已有日线开盘执行；最终日期的信号可能无下一根行情可执行。
- `reason` 用于保留规则触发依据，不参与数值计算。

## 官方来源与单位核对

[通达信公式函数文档](https://help.tdx.com.cn/gspt/docs/markdown/redword/functionlist.html)中的 `VOL` 单位为手、`AMOUNT` 单位为元；[官方 K 线接口](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1ctuhthaq5qmg/mindoc-1h10g60jt68sc.html)的成交量为股或张、成交额为万元，且单独指定复权方式。不要将不同渠道的数据未经单位转换直接混用。
