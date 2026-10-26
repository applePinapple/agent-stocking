# MACDBS：MACD专家买卖点副图

根据2026-10-25在本机通达信金融终端V7.71读取的内置MACD专家系统制作。公式名为`MACDBS`，描述为“MACD专家买卖点”，应建立在**技术指标公式**分类，画线方法选择**副图**。原专家源码的读取由界面操作记录提供，未用网上常见MACD交易规则代替。

当前状态：副图已在本机测试通过、保存并加载；红买绿卖标记已实际确认。原生日线信号与独立参考共3296项比较，3295项一致，保留1个参考初始化边界差异。指标计算和CROSS表达式与本机专家逐句相同；未直接导出专家系统信号，因此该差异不表示副图与专家不同。本指标用于显示专家条件，没有执行评测或生成收益。[编译通过证据](../../../reports/macd-subchart-20261025/004-MACDBS-compile-passed/state.json)、[保存证据](../../../reports/macd-subchart-20261025/005-MACDBS-saved/state.json)、[完整验证记录](../../../reports/macd-subchart-20261025/verification.md)。

## 文件

本页链接的`reports/macd-subchart-20261025/`为本机验证证据，不随Git仓库提交；克隆仓库后这些证据链接不可用。仓库保留以下公式源码、用法和验证范围说明。

- [formula.txt](formula.txt)：UTF-8/LF副图正文，包含中文买卖标记。
- [source-expert.txt](source-expert.txt)：本机专家计算与两条输出的逐句转录，ASCII/LF；不含编辑器自带的提示注释。
- [usage.txt](usage.txt)：已在本机保存的中文用法注释，换机重建指标时可一并录入。

## 参数

参数名称、顺序及范围与本机MACD专家一致。创建指标时需要在参数表中录入，正文不硬编码缺省参数。本机副图编辑器已对齐三个参数的最小值、最大值和缺省值；该技术指标编辑器没有步长列，表中的步长记录原专家参数。

| 参数 | 最小 | 最大 | 缺省 | 步长 |
| --- | --- | --- | --- | --- |
| SHORT | 2 | 200 | 12 | 4 |
| LONG | 10 | 200 | 26 | 5 |
| M | 2 | 200 | 9 | 1 |

对照专家图和副图时，两边的证券、周期、复权、加载历史长度及上述参数应一致。通达信技术指标MACD常见第三参数名为MID，本副图沿用实际专家的`M`。

## 买卖规则与显示

计算逐句沿用本机专家：`DIFF=EMA(CLOSE,SHORT)-EMA(CLOSE,LONG)`，`DEA=EMA(DIFF,M)`，`MACD=2*(DIFF-DEA)`。

| 输出 | 专家对应输出 | 条件 |
| --- | --- | --- |
| BSIG | ENTERLONG | `CROSS(MACD,0)` |
| SSIG | EXITLONG | `CROSS(0,MACD)` |

BSIG和SSIG以`NODRAW`保留为可导出的原始布尔输出。没有加入FILTER、TFILTER、持仓过滤、额外预热、周期限制或止盈止损条件，不使用SYSTEM引用新增依赖。CROSS在相等、起始及缺失数据处的行为交由同一客户端实现；实际对照须保留这些边界，不能用另一套金死叉约定替换。

副图显示白色DIFF、黄色DEA、零轴、正红负绿MACD柱。买点用图标1和红色“买”文字，卖点用图标2和绿色“卖”文字；本机已经确认红色上箭头和绿色下箭头的实际外观。[图形证据](../../../reports/macd-subchart-20261025/007-MACDBS-chart-visible/state.json)、[用法注释保存证据](../../../reports/macd-subchart-20261025/008-MACDBS-usage-updated/state.json)。图形位置以当根DIFF/DEA为基础，`GAP=MAX(ABS(DIFF),ABS(DEA))*0.12+0.01`仅用于分开箭头与文字，不参与买卖条件。未使用股票价格LOW/HIGH作为副图坐标。

## 语义资料与验证边界

- [通达信官方函数表](https://help.tdx.com.cn/gspt/docs/markdown/redword/functionlist.html)：EMA、CROSS、STICKLINE、DRAWICON、DRAWTEXT等定义；2026-10-25查阅。绘图函数根据布尔条件在指定序列坐标绘制，图标编号范围1至51。
- [通达信公式编辑器说明](https://help.tdx.com.cn/gspt/docs/markdown/tdxgs-1d1k7b2r1ihug/tdxgs-gsbj/tdxgs-bjqqsm.html)：指标画线方法可选副图。
- [通达信专家系统说明](https://help.tdx.com.cn/gspt/docs/markdown/tdxgs-gsxt/tdxgs-1d1kngsmd4kd8.html)：在K线图用Ctrl+E调用专家指示。

上述资料查阅与本机实测分别记录。本副图的信号计算只使用当根与先前价格。独立Python参考模型通过48项合成数据前缀截断/未来后缀扰动检查、12项零轴相等和缺失边界自检；这是参考模型实验，没有改写本机行情验证未来数据，不声称已经完全兼容。[自检记录](../../../reports/macd-subchart-20261025/reference-self-checks.json)

项目规定的`python -m unittest discover -s tests -t . -v`已于2026-10-25运行，199项测试全部通过。原生导出鸣志电器603728日线1648行，2020-01-02至2026-10-23；参数12/26/9。校验器使用未舍入的EMA参考值生成信号，不从两位小数的MACD导出值反推交叉。

主参考采用“前期小于等于零、当期严格大于零”判定买入，出现2020-01-06一处初始化差异（原生BSIG=0、参考=1）；其余3295项布尔一致。数值4列共6592项在原定两位显示精度容差0.0051内一致。主报告保留`differences_require_review`，不声称完全兼容。[主对照](../../../reports/macd-subchart-20261025/native-comparison.json)

另作“前期严格小于零/大于零”的有界参考调查，3296项布尔全部匹配；这是单证券样本对初始化假设的观察，未替换主基线，也未修改已保存副图或专家条件。[边界调查](../../../reports/macd-subchart-20261025/boundary-investigation.json)

当前未收盘K线的条件可能随行情变化；已存在的历史专家箭头还可能受到信号过滤或加载起点影响，不能把箭头显示直接等同于真实成交。

## 源码哈希

校验对象为本目录原始字节。本机编辑器可能采用不同换行或编码，因此不以编辑器显示推断其内部字节哈希。

| 文件 | 字节数 | SHA-256 |
| --- | --- | --- |
| formula.txt | 474 | `f386bfdc12f1c7acc10cf70c89f8f0c361644f3900276701b68eecd73d32273e` |
| source-expert.txt | 127 | `4c3d8ef2cecc7a8f4759de0d296adb53a27a0ce6bc0142b53747d042a8575bef` |
| usage.txt | 469 | `bd65ef338c22e73c637b2848c6edbd5768eac2b6f2e1efb5b8c9606ee9edd4e9` |
