# 技术指标定义（已定，2026-10-03）

所有指标只使用 Tiingo 同一次抓取的日线复权字段 `adjHigh`、`adjLow`、
`adjClose`、`adjVolume`。一根指一个实际交易日；日期缺口不补零、不插值。
窗口包含计算日。数据不足时该指标值为 `null`，附 `insufficient_history`
及“需要 N 根，实际 M 根”；其他指标仍计算。价格无变化使 RSI 未定义时，
值为 `null`，原因 `no_price_change`。零分母也返回 `null` 并说明。

| 指标 | 定义 | 最早有效根数 |
|---|---|---:|
| SMA(20/50/200) | 最近 n 根 `adjClose` 的算术平均 | n |
| EMA(12/26) | α=2/(n+1)；前 n 根收盘价的 SMA 作种子，之后逐根按 α×当前价+(1−α)×前 EMA 更新 | n |
| RSI(14) | 前 14 个收盘价变化量的涨跌幅分别求简单平均，再按 `(前值×13+当前值)/14` 作 Wilder 平滑；`avg_loss=0, avg_gain>0` 为 100；两者皆 0 则无定义 | 15 |
| MACD(12,26,9) | 线=EMA12−EMA26；信号线对有效 MACD 线做 EMA9，前 9 个有效值的 SMA 作种子；柱=线−信号线 | 线 26，信号/柱 34 |
| Bollinger(20,2) | 中轨=SMA20；总体标准差除以 20；上下轨=中轨±2×标准差 | 20 |
| ATR(14) | 从第二根开始，TR=`max(high−low, abs(high−前收), abs(low−前收))`；前 14 个 TR 的平均作种子，之后 Wilder 平滑 | 15 |
| 成交量 | `average_volume_20`=最近 20 根 `adjVolume` 平均，`volume_ratio_20`=当日 `adjVolume` / 该平均 | 20 |
| 区间位置(20/60/252) | 最近 n 根 `adjHigh` 最高值和 `adjLow` 最低值；距高/低百分比=`(当日 adjClose / 该水平−1)×100` | n |
| 相对 SPY 强弱(21/63/126/252) | 标的与 SPY 按日期内连接；最近 n 根对齐后的涨幅之差，单位百分点；涨幅=`末根 adjClose / 首根 adjClose−1` | n 根对齐数据 |

标的与 SPY 截至 `as_of` 的最新交易日不同，则整个工具结果为 `unavailable`。
区间高低仅是机械历史区间，不是价格预测。所有递归状态只依赖当前及更早数据。
