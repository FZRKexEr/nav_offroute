# 商用导航偏航判断：极致保守版迭代报告

## 0. 结论

这一轮我把目标从“尽快抓偏航”重构成“只有高置信才对用户播偏航”。最终推荐默认使用：

- `nav_offroute_commercial.py`：最终默认版，等价于 `commercial_v14`。
- `nav_offroute_commercial_v13.py`：较低延迟、略高误报的 profile，可用于内部 silent reroute 或灰度比较。

最终策略不是一个单一 rule，而是在线多假设竞争：

```text
H0: 仍在规划路线，只是普通 GPS/map/lane 偏移
H1: GPS 存在稳定向量漂移，例如城市峡谷/高架/建筑物旁
H2: 用户走了局部捷径、提前掉头、抄近路，但仍有较大概率回归规划路线
H3: 用户真实进入错误走廊，需要重算路线
```

真正对用户播报 `OFF_ROUTE` 只发生在 H3 置信足够高，并且有物理证据：端点越界、持续远离、错过 maneuver 后 route progress 卡住、进入错误走廊、方向/进度明显不合理等。

## 1. 和前两轮相比，最重要的变化

### 1.1 拆分 silent suspect 和 spoken off-route

`SUSPECT` 是内部状态，允许导航系统静默准备重算、拉取 alternative、或更新 shadow route。

`OFF_ROUTE` 是用户可感知状态，才适合播报“已偏航 / 重新规划路线”。

这能同时满足：

```text
低延迟准备重算
高置信才打扰用户
```

### 1.2 不再把“离路线远”直接等价为偏航

GPS 漂移经常是相关的、方向稳定的向量偏移，不是 iid 随机噪声。算法现在维护二维向量 bias：

```text
bias_vec = observed_gps_xy - compatible_route_xy
bias_vec_conf = 该向量解释历史轨迹的置信度
```

如果减去这个向量后，轨迹形状、heading、progress 都能解释，就压制偏航证据。

### 1.3 提前掉头 / 捷径 / 抄近路变成“可解释假设”

只用当前点和历史点，不看未来 GPS，但看规划路线前方几何：

```text
当前点是否靠近 route ahead 的未来 leg？
当前点是否靠近 prev_s -> future_s 的 chord？
route ahead 是否存在明显绕行/掉头/转弯结构？
当前 heading 是否支持这个 shortcut/chord？
```

如果可以解释为 shortcut/rejoin，就不立即播偏航，只进入弱 suspect 或继续 on-route。

### 1.4 给 spoken off-route 加物理证据门槛

上一版只要 score 累积超过阈值就可能 OFF_ROUTE。最终版要求：

```text
score 足够高
AND
存在物理证据
```

物理证据包括：

- `endpoint_gap` 明显越过转向端点；
- 距离大到不太可能是 GPS 漂移；
- residual 大且伴随 heading divergence / stalled projection；
- 明显反向/倒退并远离路线。

这一步是减少城市峡谷、平行同形道路、临时弱定位误报的关键。

## 2. v4 极端数据集

生成脚本：`suite/synthetic_nav_cases_v4_extreme.py`

数据集：`synthetic_nav_suite_v4_extreme_6400.jsonl`

总计：

| 指标 | 数值 |
|---|---:|
| case 总数 | 6400 |
| 期望不偏航 | 4726 |
| 期望偏航 | 1674 |
| category 数量 | 47 |

覆盖的主要场景：

| 大类 | 例子 |
|---|---|
| 直角转弯 | 正常转弯、错过转弯、切角、提前转、延后转、转弯处停车 |
| 掉头 | 正常掉头、提前掉头允许、过马路到对向允许、错过掉头继续直行、过早且远离 |
| 捷径 | block shortcut、diagonal shortcut、shortcut rejoin、wrong corridor |
| GPS 漂移 | building canyon vector bias、temporary bias episode、parallel measurement bias |
| 平行路 | same-shape ambiguous、branch sustained far、parallel road after branch |
| loop/future leg | close parallel loop follow、skip future leg ambiguous、wrong corridor |
| 起点问题 | 起点延长线接入、起点远平行、起点逆行 |
| 低质量定位 | unusable spike、low-trust usable spike、poor GNSS large error、bad heading |
| roundabout/fork | 正确出口、错误出口、正确分叉、错误分叉 |

v4 的一个重要语义变化：很多过去标成 off 的 case 被改成 ambiguous/nooff。原因是只给 route LineString + GPS，不给路网拓扑时，提前掉头、同形平行道路、局部捷径、城市峡谷大偏移不应该轻易播偏航。

## 3. 验证结果

### 3.1 v3 全量保守语义数据集，1200 case

| 算法 | PASS | FP | MISS | LATE | EARLY | 通过率 | nooff FP 率 | off MISS+LATE 率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| balanced_v6 | 832 | 335 | 16 | 11 | 6 | 69.33% | 43.56% | 6.26% |
| conservative_v8 | 992 | 96 | 93 | 19 | 0 | 82.67% | 12.48% | 25.99% |
| commercial_v13 | 1016 | 29 | 116 | 36 | 3 | 84.67% | 3.77% | 35.27% |
| commercial_v14 default | 1000 | 21 | 129 | 47 | 3 | 83.33% | 2.73% | 40.84% |

解释：

- `commercial_v14` 的通过率不是最高，但误报最少，符合“商用播报谨慎”的目标。
- `commercial_v13` 可以作为低延迟 profile：更早报一部分真实偏航，但 nooff FP 率更高。

### 3.2 v4 极端数据集 sample 1000

本环境长时间 Python 进程会中断，因此 v4 全量设计为分片验证。本轮已验证前 1000 个 case，并保留分片验证脚本，可在 CI 或本地跑全量 6400。

| 算法 | PASS | FP | MISS | LATE | EARLY | 通过率 | nooff FP 率 | off MISS+LATE 率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| commercial_v14 default | 829 | 103 | 37 | 31 | 0 | 82.90% | 13.96% | 25.95% |

v4 比 v3 更极端，包含大量“几何上像偏航，但产品上应谨慎不播”的 nooff case，所以 nooff FP 更难降。

### 3.3 私有真实 case 回归

私有真实用户 case 已从当前仓库移除，不再随仓库分发。

内部回归结论保留为高层结论：

- 商用默认版在私有真实 case 上能维持既有回归表现；
- 相比更早期算法，spoken off-route 播报整体更谨慎；
- 更早阶段的信号可以通过 `SUSPECT` 提供给 silent reroute。

## 4. 当前算法的在线 update 逻辑

每个 GPS 点调用一次：

```python
detector = OffRouteDetector(route)
out = detector.update(gps_point)

out.state      # ON_ROUTE / SUSPECT / OFF_ROUTE
out.off_route  # 是否达到 spoken off-route
out.metrics    # debug 特征
```

算法只使用当前点和历史状态；不会读取未来 GPS 点。

核心步骤：

```text
1. 生成带时序约束的 route projection 候选
2. 计算 raw distance / residual / endpoint_gap / route progress / heading diff
3. 评估 H0: 普通路线匹配 + lateral bias
4. 评估 H1: 二维 GPS vector bias
5. 评估 H2: shortcut / early uturn / future-leg rejoin
6. 评估 H3: true off-route evidence
7. 更新 score
8. score >= silent threshold -> SUSPECT
9. score >= spoken threshold AND physical evidence -> OFF_ROUTE
```

## 5. 产品策略建议

### 5.1 不要把“重算路线”和“播偏航”绑定死

建议分成三层：

```text
Projection / Matching 层：给出当前最可信 route position 和多假设分数
Silent Reroute 层：SUSPECT 后可以静默请求 reroute / alternatives
User Messaging 层：只有 OFF_ROUTE 才播报或明显改变 UI 文案
```

用户体感里，最烦的是误播；后台多做一次静默重算不是问题。

### 5.2 推荐状态机

```text
ON_ROUTE
  -> DEGRADED_GPS            # 定位不可信，但不判偏航
  -> POSSIBLE_BIAS           # 轨迹像路线，只是整体偏移
  -> POSSIBLE_SHORTCUT       # 可能抄近路/提前掉头
  -> SUSPECT_OFF_ROUTE       # 内部准备重算，不播
  -> SPOKEN_OFF_ROUTE        # 高置信偏航
  -> RECOVERED               # 回到路线或切到新路线
```

### 5.3 对外接口建议

不要只返回 bool。建议返回：

```go
type OffRouteDecision struct {
    State              string  // ON_ROUTE, SUSPECT, OFF_ROUTE...
    SpokenOffRoute     bool
    SilentRerouteHint  bool
    ProjectionS        float64
    RawDistanceM       float64
    ResidualM          float64
    EndpointGapM       float64
    HeadingDiffDeg     float64
    RouteConfidence    float64
    VectorBiasScore    float64
    ShortcutScore      float64
    TrueOffrouteScore  float64
    Reasons            []string
}
```

这样产品和服务端可以独立调策略。

## 6. 架构建议：让后续补 case 的成本最低

### 6.1 Case registry

所有 case 统一 JSONL：

```json
{
  "meta": {
    "case_id": "...",
    "category": "...",
    "should_off_route": false,
    "true_off_idx": null,
    "latest_detect_idx": null,
    "note": "..."
  },
  "route_geojson": {...},
  "gps_geojson": {...}
}
```

新增 case 不需要改验证器，也不需要改算法。

### 6.2 Feature extractors 插件化

建议拆成：

```text
ProjectionFeatureExtractor
GpsQualityFeatureExtractor
ProgressFeatureExtractor
HeadingFeatureExtractor
BiasFeatureExtractor
ShortcutFeatureExtractor
ManeuverFeatureExtractor
```

每个 extractor 只产特征，不直接决定偏航。

### 6.3 Hypothesis scorer 插件化

```text
RouteMatchHypothesis
VectorBiasHypothesis
PoorGnssHypothesis
ShortcutRejoinHypothesis
TrueOffRouteHypothesis
```

新增一个“隧道/高架/停车场/轮渡/室内出库”等场景，只需要新增 hypothesis 或调 prior，不要改一坨 if-else。

### 6.4 Decision policy 单独配置

```text
silent_threshold
spoken_threshold
physical_evidence_gate
city_profile
road_class_profile
navigation_mode_profile
```

同一套特征可以服务不同策略：

- 驾车：最保守；
- 骑行/步行：允许更大路线自由度；
- 高速：错出口应更敏感；
- 城市峡谷：提高 GPS bias prior。

## 7. 概率化是否有搞头

有，而且我认为是最终方向。

不一定一开始就训练端到端模型。更稳的做法是：

```text
先用现在这套规则产生可解释特征和多假设 score；
再用真实线上样本训练一个 calibrated model：
P(true_offroute | features, context)
```

候选模型：

- Logistic Regression：可解释、方便校准；
- GBDT / XGBoost / LightGBM：适合 tabular feature，工程性强；
- HMM / particle filter：如果有路网候选道路；
- 小型序列模型：只有在日志量足够且可解释性要求降低时考虑。

建议输出的不是一个二分类，而是：

```text
P(on_route_with_bias)
P(poor_gnss)
P(shortcut_rejoin)
P(true_offroute)
```

最终产品策略可以选择：

```text
spoken_off_route = P(true_offroute) > 0.97
                   AND physical_evidence_gate
                   AND P(poor_gnss) < 0.2
                   AND P(shortcut_rejoin) < 0.2
```

## 8. 只给 LineString + GPS 的不可判定边界

这些场景无法靠单条 route line 完全区分：

```text
稳定平行同形道路 vs GPS 整体偏移
提前掉头合法过马路 vs 错误道路
捷径可回归 vs 非法穿越/错误走廊
高架上下层/辅路主路平行
路网临时封闭导致真实路线不可走
```

没有路网拓扑时，正确产品策略应该偏向“不播”，最多进入 SUSPECT 并静默重算。

## 9. 下一步真实商用落地优先级

1. 引入路网候选道路和 maneuver metadata。只要知道附近有哪些可行道路、是否允许掉头/穿越/转向，很多 ambiguous case 会从不可判定变成可判定。
2. 线上记录每次 `SUSPECT` 和 `OFF_ROUTE` 前后 30 秒特征，建立真实 case bank。
3. 用人工/半自动方式给真实 case 标注：误报、漏报、合理不播、应该静默重算、应该播报。
4. 用当前特征训练校准模型，但保留 deterministic safety gates。
5. 按城市/道路类型/设备定位质量做 profile。
6. 将 Python 原型移植到 Go 前，先冻结 feature schema 和 case suite；否则 Go 版会频繁返工。

## 10. 文件清单

- `nav_offroute_commercial.py`：最终默认算法，commercial_v14。
- `nav_offroute_commercial_v13.py`：低延迟 profile。
- `suite/synthetic_nav_cases_v4_extreme.py`：v4 数据生成器。
- `synthetic_nav_suite_v4_extreme_6400.jsonl`：6400 case 数据集。
- `validation/validate_offroute_suite.py`：全量验证器。
- `validation_v3_commercial_v14.csv`：v3 全量验证。
- `validation_v4_commercial_v14_sample1000.csv`：v4 sample 验证。
- `commercial_validation_summary.csv`：汇总对比。
