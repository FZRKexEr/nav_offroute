# 偏航判断长期架构设计

## 1. 目标

偏航判断不要是一个单点算法，而应该是一套可扩展的在线决策系统：

```text
GPS/IMU/Map Input
  -> Projection Engine
  -> Feature Extractors
  -> Hypothesis Scorers
  -> Decision Policy
  -> Product Action Layer
```

## 2. 模块职责

### Projection Engine

负责候选投影，不负责判偏航。

输出：

```text
best_projection
local_window_candidates
global_candidates
future_leg_candidates
corrected_by_bias_candidates
```

### Feature Extractors

只产特征：

```text
raw_dist, residual, signed_dist, endpoint_gap
s, ds, speed, heading_diff
progress_plausible, endpoint_stuck
bias_conf, vector_bias_conf
shortcut_chord_dist, shortcut_target_s
low_trust, duplicate_time, gps_jump
```

### Hypothesis Scorers

每个 hypothesis 维护自己的分数：

```text
route_score
vector_bias_score
poor_gnss_score
shortcut_score
true_offroute_score
```

### Decision Policy

只在最后做产品判断：

```text
silent_suspect = true_offroute_score high OR projection ambiguity high
spoken_offroute = true_offroute_score very high AND physical evidence gate
```

## 3. Case 驱动开发闭环

```text
线上发现问题
  -> 抽取 route + gps + debug metrics
  -> 加入 JSONL case bank
  -> 标注 category + should_off_route + latest_detect_idx
  -> CI 跑所有算法 profile
  -> 新算法必须通过 regression gates
```

建议 regression gates：

```text
真实 case 不允许退化
nooff FP 率不能恶化超过阈值
核心 off case LATE/MISS 不能恶化超过阈值
按 category 做门禁，而不是只看总通过率
```

## 4. 产品 Action Layer

```text
ON_ROUTE:
    正常导航

DEGRADED_GPS:
    保持路线，不播偏航；必要时提示“GPS 信号弱”

POSSIBLE_BIAS:
    使用偏移补偿后的 snapped location；不播

POSSIBLE_SHORTCUT:
    不播；可静默请求替代路线

SUSPECT_OFF_ROUTE:
    静默 reroute / alternative；不打断用户

SPOKEN_OFF_ROUTE:
    播报或 UI 明确切换到新路线
```

## 5. 为什么架构上一定要分 silent 和 spoken

用户不关心系统内部有没有重新算路线。用户关心的是：

```text
不要错误播报
不要突然把路线线乱跳
真正走错时别拖太久
```

因此 reroute 可以更积极，spoken 必须更保守。

## 6. 可加入但当前没有的数据

如果后续能拿到这些数据，偏航判断会明显提升：

```text
道路候选集合
路网拓扑和转向限制
道路等级/主辅路/高架层级
route maneuver metadata
alternative route
定位源类型和系统 accuracy
IMU yaw rate / gyro / accelerometer
车机/手机是否在隧道/室内/停车场
历史城市峡谷热区
```
