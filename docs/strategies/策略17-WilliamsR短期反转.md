---
status: pending
research_date: 2026-10-24
---

# 策略17：Williams %R(2) 短期反转

仅整理网上已发表版本，尚未确认用于本项目回测；不产出正式收益。共同约定见 [目录说明](README.md)。

## 来源与适用范围

- 发布者：QuantifiedStrategies.com。
- 规则原文：[Williams %R Trading Strategy – Williams Percent Range vs RSI](https://quantifiedstrategies.substack.com/p/williams-r-trading-strategy-williams-203)，2024-08-21。
- 股票版本核对：[We Tested Williams %R on All Nasdaq-100 Stocks](https://quantifiedstrategies.substack.com/p/we-tested-williams-r-on-all-nasdaq)，2026-10-01，The Williams %R Strategy We Tested。
- 查阅日期：2026-10-24；两篇公开正文均已打开，阈值与退出条件一致。
- 原文对象：SPY，以及作者研究时的纳斯达克 100 成分股；周期：日线。这里收录固定 2 日参数版本，不按股票另行寻优。

## 买点规则

当日收盘 `Williams %R(2) < -90` 时买入；采用数值范围 `[-100,0]` 的 %R。来源主要版本按信号日收盘买入，另测试过次日开盘买入；本条保留主要版本，两者不能混算。

## 卖点规则

持有多仓后，以下条件任一成立即退出：

- 当日收盘价严格高于前一交易日最高价。
- 当日收盘 `Williams %R(2) > -30`。

等于 -90、-30 或前日最高价，均不满足相应严格不等式。

## 来源边界与待确认

未增加 200 日均线、固定止损、固定持仓天数。回测前仍须确认 %R 的两日窗口边界、区间最高等于最低时的处理、退出成交时点、加仓及仓位；其他执行项见目录说明。同日收盘成交须单独确认，不能因策略1采用该约定就自动沿用。

股票研究使用的是研究时的成分股，存在成分选择与幸存者偏差；本项目不复制该股票排名或收益。实施时只用已完成 K 线，并验证未来数据变化不改变历史结果。

## 本地通达信已运行公式包

已按实际本机运行源码逐字节保存至[对应策略目录](策略17-WilliamsR短期反转/通达信/README.md)，包含公式、评测参数及来源哈希。已运行，用户接受现状；原生汇总及6股1304条流水保留，688322缺172条明细，未称完整流水核验。
