# 策略5：Lambert CCI +100 趋势

- `status: pending`：资料候选，CCI 周期与成交约定尚待确认。
- 类型：趋势追随；原始方法源于商品，来源亦讨论股票与 ETF。
- 来源：[StockCharts ChartSchool：CCI Correction](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/cci-correction)，Strategy 开头记载的 Lambert 原始规则；公式参考 [StockCharts 官方 CCI 指标说明](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/commodity-channel-index-cci)。
- 访问日期：2026-10-24。

## 买点与卖点

**买点：**CCI 向上突破 +100 时建立多仓。

**卖点：**已有多仓在 CCI 回落至 +100 以下时平仓。多仓退出阈值是 +100，不能擅自改成零或 -100。原文也描述低于 -100 的空头交易；本条只摘录其明确的多仓进出部分，是否另纳入空头须独立确认。本条不是该页面后续的“周线方向＋日线回调”变体。[规则来源](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/cci-correction)

## 参数与指标边界

该段原始交易规则未固定 CCI 周期和 K 线周期，不自行补定。指标说明以 20 周期、典型价 `(最高+最低+收盘)/3` 和常数 0.015 举例；该示例不构成用户选定的策略参数。周期、起始预热、平均绝对偏差为零、缺失数据及恰等于 +100 的交叉处理均须确认。

## 回测前待确认

标的、区间、价格复权与公司行动、成交时点、仓位与重复信号、结算与整手、交易许可及受限后的重试、费用、滑点、期末估值均待确认。不得继承策略1资金、理想同日收盘及封板顺延约定。

仅使用当时已完成历史数据；实现后验证未来数据变化不改写历史信号，并单独记录现存历史信号的删失偏差和证券名单的幸存者偏差。通达信公式需另行逐值对照，本文未实现或回测。
