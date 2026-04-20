# nav_offroute

![前端查看器示例](assets/20260421-042616.jpg)

这个目录已经整理成 4 个主要模块：

- `nav_offroute.py` 和 `profiles/`：偏航算法脚本
- `suite/`：synthetic 数据生成脚本和生成结果
- `validation/`：验证脚本和验证结果
- `frontend/`：JSONL/GeoJSON 可视化查看器

## Quick Start

先在项目根目录激活虚拟环境：

```bash
source .venv/bin/activate
```

如果你只是想快速确认环境没问题，建议先跑一遍 `v4` 验证：

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl \
  --algorithm nav_offroute.py \
  --out validation/validation_v4.csv
```

## Directory Layout

- `nav_offroute.py`
  默认 spoken off-route 算法。
- `profiles/nav_offroute_v13.py`
  更低时延的 profile。
- `profiles/nav_offroute_v14.py`
  版本化备份，内容与默认脚本一致。
- `suite/`
  `v2 / v3 / v4` synthetic 数据生成脚本，以及对应 `jsonl / summary`。
- `validation/`
  `validate_offroute_suite.py` 和验证输出 CSV。
- `frontend/`
  基于 Vite 的本地查看器。
- `docs/`
  最终报告和长期架构设计。

## Module 1: Run The Algorithm

### 1. Run the default algorithm

```bash
python nav_offroute.py <route_geojson> <gps_geojson> [--csv debug.csv]
```

参数：

- `<route_geojson>`：路线 GeoJSON
- `<gps_geojson>`：定位点 GeoJSON
- `--csv`：可选，输出逐点调试 CSV

如果你有本地回放数据，可以用上面的形式直接跑，不要求文件一定来自当前仓库。

### 2. Run a profile algorithm

`profiles/` 里的脚本参数和默认算法一致。

低时延 profile：

```bash
python profiles/nav_offroute_v13.py <route_geojson> <gps_geojson> [--csv debug_v13.csv]
```

版本化备份：

```bash
python profiles/nav_offroute_v14.py <route_geojson> <gps_geojson> [--csv debug_v14.csv]
```

## Module 2: Generate Synthetic Suites

`suite/` 里有三层数据生成脚本：

- `synthetic_nav_cases_v2.py`
  更偏几何偏离语义
- `synthetic_nav_cases_v3_conservative.py`
  更保守的产品语义
- `synthetic_nav_cases_v4_extreme.py`
  最终默认语义，强调 spoken off-route 要谨慎

### 1. Generate v2

```bash
python suite/synthetic_nav_cases_v2.py \
  --out suite/synthetic_nav_suite_v2.jsonl \
  --summary suite/synthetic_nav_suite_v2_summary.json
```

### 2. Generate v3

```bash
python suite/synthetic_nav_cases_v3_conservative.py \
  --out suite/synthetic_nav_suite_v3_conservative.jsonl \
  --summary suite/synthetic_nav_suite_v3_conservative_summary.json
```

### 3. Generate v4

```bash
python suite/synthetic_nav_cases_v4_extreme.py \
  --out suite/synthetic_nav_suite_v4_extreme_6400.jsonl \
  --summary suite/synthetic_nav_suite_v4_extreme_6400_summary.json \
  --limit 6400
```

### 4. Common options

这三个脚本都支持：

- `--out`
- `--summary`
- `--seed`
- `--scale`
- `--limit`

例如只生成一个小样本：

```bash
python suite/synthetic_nav_cases_v4_extreme.py \
  --out suite/v4_smoke.jsonl \
  --summary suite/v4_smoke_summary.json \
  --limit 50
```

## Synthetic Case Categories

这些分类来自 synthetic suite 的 `meta.category` 字段，同时也会出现在 `case_id` 里。它们描述的是**样本语义**，不是算法跑完后的 `verdict`。

命名约定：

- `_off`：这个 case 的期望是“应该判偏航”。
- `_nooff`：这个 case 的期望是“不应该判偏航”。
- `_allowed_nooff`：几何上看起来有偏离，但产品语义上允许，不应该语音 reroute。
- `_ambiguous_nooff`：这是保守语义下的边界样本，默认不报偏航。
- `probe` / `混合`：这是刻意做出来的探针类样本，不同偏移幅度下可能得到不同期望。

### 转弯与路口

| 分类 | 期望 | 中文解释 | 出现于 |
| --- | --- | --- | --- |
| `right_angle_follow` | 不应报偏航 | 正常直角转弯，按规划完成 90 度转向 | v2 / v3 / v4 |
| `right_angle_missed_turn` | 应报偏航 | 该拐弯时没有拐，继续直行离开规划路线 | v2 / v3 / v4 |
| `right_angle_late_turn_parallel` | 混合 / 旧版过渡 | 晚拐，先冲过路口再进入与规划后续路段平行的路；旧版里既有 off 也有边界样本 | v2 / v3 / v4 |
| `right_angle_early_turn_parallel` | 混合 / 旧版过渡 | 早拐，提前转入与规划后续路段平行的路；旧版里既有 off 也有边界样本 | v2 / v3 / v4 |
| `right_angle_late_turn_parallel_ambiguous_nooff` | 不应报偏航 | 晚拐但仍贴近规划走廊，保守视为不报偏航 | v4 |
| `right_angle_early_turn_parallel_ambiguous_nooff` | 不应报偏航 | 早拐但仍贴近规划走廊，保守视为不报偏航 | v4 |
| `right_angle_late_parallel_ambiguous_nooff` | 不应报偏航 | 晚拐后保持近距离平行，v4 明确归为边界不报偏航 | v4 |
| `right_angle_early_parallel_ambiguous_nooff` | 不应报偏航 | 早拐后保持近距离平行，v4 明确归为边界不报偏航 | v4 |
| `right_angle_late_parallel_far_off` | 应报偏航 | 晚拐后离规划走廊较远且持续平行，应报偏航 | v4 |
| `right_angle_early_parallel_far_off` | 应报偏航 | 早拐后离规划走廊较远且持续平行，应报偏航 | v4 |
| `right_angle_corner_cut_allowed_nooff` | 不应报偏航 | 直角路口切角通过，但很快回到规划走廊，不应语音 reroute | v4 |
| `small_corner_cut_nooff` | 不应报偏航 | 小幅切弯 / 抄近角，属于可容忍偏差 | v2 / v3 / v4 |
| `stopped_at_turn_nooff` | 不应报偏航 | 在转角附近停车或缓行，航向和 GPS 不稳定，不应误报偏航 | v4 |

### 掉头与回头弯

| 分类 | 期望 | 中文解释 | 出现于 |
| --- | --- | --- | --- |
| `uturn_follow` | 不应报偏航 | 正常掉头 / 发卡弯，按规划完成 | v2 / v3 / v4 |
| `uturn_missed_continue_straight` | 应报偏航 | 需要掉头但继续直行 | v2 / v3 / v4 |
| `uturn_early` | 应报偏航 | 过早掉头，较早切到回程段 | v2 |
| `uturn_early_allowed_nooff` | 不应报偏航 | 早掉头但仍可理解为提前进入预期反向走廊，保守不报偏航 | v3 / v4 |
| `uturn_early_crossover_allowed_nooff` | 不应报偏航 | 早掉头或跨到对向走廊，但明显仍在朝预期对向线回归 | v4 |
| `uturn_early_far_ambiguous_nooff` | 不应报偏航 | 较早掉头且距离较远，但仍不足以确定偏航，保守 nooff | v4 |
| `uturn_too_early_far_off` | 应报偏航 | 掉头太早且离规划掉头点太远，难以合理回归，应报偏航 | v3 / v4 |

### Shortcut 与重回路线

| 分类 | 期望 | 中文解释 | 出现于 |
| --- | --- | --- | --- |
| `shortcut_block_straight` | 应报偏航 | 绕过街区的一整段近路后回到路线；在 v2 中仍按偏航处理 | v2 |
| `shortcut_diagonal` | 应报偏航 | 穿过 dogleg / 折线路段的对角近路；在 v2 中仍按偏航处理 | v2 |
| `shortcut_block_straight_allowed_nooff` | 不应报偏航 | 同街区直穿 shortcut，但在保守语义下允许 | v3 / v4 |
| `shortcut_diagonal_allowed_nooff` | 不应报偏航 | 对角穿越但仍很快重回规划走廊，保守允许 | v3 / v4 |
| `shortcut_rejoin_allowed_nooff` | 不应报偏航 | 走更短的本地路后重新并回规划路线，不应语音 reroute | v3 / v4 |
| `shortcut_wrong_corridor_off` | 应报偏航 | 虽然看起来像 shortcut，但实际进入了错误 / 对向走廊，短期内不可能合理回归 | v3 / v4 |

### 平行路、起终点与岔路

| 分类 | 期望 | 中文解释 | 出现于 |
| --- | --- | --- | --- |
| `parallel_measurement_bias_nooff` | 不应报偏航 | GPS 与路线整体平行偏移，更像地图 / GPS 对齐误差 | v2 / v3 / v4 |
| `parallel_road_after_branch_off` | 应报偏航 | 从支路分出去后沿平行道路持续行驶，应报偏航 | v2 / v3 / v4 |
| `parallel_same_shape_ambiguous_nooff` | 不应报偏航 | 轨迹与规划形状几乎相同，但整体平移到旁边一条平行道路，保守视为不报偏航 | v3 / v4 |
| `parallel_branch_sustained_far_off` | 应报偏航 | 分叉后进入较远平行支路并持续偏离，应报偏航 | v3 / v4 |
| `start_approach_extension_nooff` | 不应报偏航 | 从路线起点延长线接近起点，属于起步接入，不应报偏航 | v2 / v3 / v4 |
| `start_far_parallel_off` | 应报偏航 | 一开始就在较远平行路上，明显还没进入规划路线 | v2 / v3 / v4 |
| `start_far_parallel_ambiguous_nooff` | 不应报偏航 | 起步锁路前处于平行路 / 辅路附近，仍有可能接入，保守不报偏航 | v4 |
| `wrongway_from_start_off` | 应报偏航 | 从起点附近就朝错误方向离开 | v2 / v3 / v4 |
| `fork_correct_branch_follow` | 不应报偏航 | 岔路口走了正确分支 | v2 / v3 / v4 |
| `fork_wrong_branch_off` | 应报偏航 | 岔路口走错分支 | v2 / v3 / v4 |
| `gradual_lateral_drift_probe` | 混合 / 探针样本 | 逐渐横向漂移的探针样本；偏移小可能只是误差，偏移大则应报偏航 | v2 / v3 / v4 |
| `gradual_lateral_drift_ambiguous_nooff` | 不应报偏航 | 渐进横向漂移但幅度不足以确认偏航，保守不报偏航 | v3 / v4 |

### 回环、环岛与反向行驶

| 分类 | 期望 | 中文解释 | 出现于 |
| --- | --- | --- | --- |
| `close_parallel_loop_follow` | 不应报偏航 | 近距离回环 / 贴近未来路段的正常跟随，不应误判 | v2 / v3 / v4 |
| `loop_wrong_future_leg_off` | 应报偏航 | 提前跳到 loop 的错误未来路段，应报偏航 | v2 / v3 |
| `loop_skip_future_leg_ambiguous_nooff` | 不应报偏航 | 略过 loop 贴到未来腿，但无道路拓扑时很难确认 spoken off-route，保守不报偏航 | v4 |
| `loop_wrong_corridor_off` | 应报偏航 | 在回环场景里离开到错误 / 对向走廊，应报偏航 | v4 |
| `roundabout_follow` | 不应报偏航 | 正常通过环岛 | v2 / v3 / v4 |
| `roundabout_wrong_exit_off` | 应报偏航 | 环岛走错出口 | v2 / v3 / v4 |
| `reverse_along_route_off` | 应报偏航 | 沿规划路线反向行驶 | v2 / v3 / v4 |

### GPS 质量与传感器异常

| 分类 | 期望 | 中文解释 | 出现于 |
| --- | --- | --- | --- |
| `gps_unusable_spikes_nooff` | 不应报偏航 | 少量不可用的远距 GPS 尖刺，不应报偏航 | v2 / v3 / v4 |
| `gps_usable_lowtrust_spike_nooff` | 不应报偏航 | 单个可用但低可信的尖刺点，不应报偏航 | v2 / v3 / v4 |
| `bad_heading_onroute_nooff` | 不应报偏航 | 实际仍在路线上，但航向传感器偶发错误 | v2 / v3 / v4 |
| `building_canyon_vector_bias_nooff` | 不应报偏航 | 城市峡谷 / 多路径导致整体向量偏移，实际仍在规划线路附近 | v3 / v4 |
| `temporary_bias_episode_nooff` | 不应报偏航 | 一段时间出现一致性 GPS 偏移，之后恢复，不应报偏航 | v3 / v4 |
| `poor_gnss_large_error_nooff` | 不应报偏航 | GNSS 很差、误差很大，但缺乏独立证据，不应直接 spoken off-route | v3 / v4 |

## Module 3: Validate Synthetic Suites

验证脚本位置：

- `validation/validate_offroute_suite.py`

用法：

```bash
python validation/validate_offroute_suite.py \
  --suite <suite_jsonl> \
  --algorithm <python_algorithm_file> \
  --out <validation_csv>
```

### 1. Validate v2

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v2.jsonl \
  --algorithm nav_offroute.py \
  --out validation/validation_v2.csv
```

### 2. Validate v3

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v3_conservative.jsonl \
  --algorithm nav_offroute.py \
  --out validation/validation_v3.csv
```

### 3. Validate v4

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl \
  --algorithm nav_offroute.py \
  --out validation/validation_v4.csv
```

### 4. Validate with another profile

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl \
  --algorithm profiles/nav_offroute_v13.py \
  --out validation/validation_v4_v13.csv
```

验证输出 CSV 里会包含：

- `case_id`
- `category`
- `should_off_route`
- `true_off_idx`
- `latest_detect_idx`
- `first_suspect_idx`
- `first_off_idx`
- `verdict`

`verdict` 常见值：

- `PASS`
- `MISS`
- `LATE`
- `EARLY`
- `FP`

## Module 4: Frontend Viewer

前端目录：

- `frontend/`

第一次运行建议先装依赖：

```bash
cd frontend
npm install
```

开发模式：

```bash
cd frontend
npm run dev
```

预览构建：

```bash
cd frontend
npm run build
npm run preview
```

查看器默认不会自动读取任何 JSONL，需要手动上传。

### In the viewer

1. 打开页面
2. 上传一个 JSONL 文件，比如 `../suite/synthetic_nav_suite_v4_extreme_6400.jsonl`
3. 如果要叠加算法结果，再手动选择一个 Python 算法脚本
4. 点击页面里的“运行算法”

可选算法脚本示例：

- `../nav_offroute.py`
- `../profiles/nav_offroute_v13.py`
- `../profiles/nav_offroute_v14.py`

说明：

- 算法脚本没有默认值，必须手动选择
- 这个能力依赖本地 `npm run dev` 或 `npm run preview`
- 不支持直接双击静态 HTML 文件运行

## Docs

如果你要看设计和结果总结，优先看：

- `docs/offroute_final_report.md`
- `docs/offroute_architecture_design.md`

## Typical Workflows

### 1. Full regenerate + validate

```bash
source .venv/bin/activate
python suite/synthetic_nav_cases_v2.py --out suite/synthetic_nav_suite_v2.jsonl --summary suite/synthetic_nav_suite_v2_summary.json
python suite/synthetic_nav_cases_v3_conservative.py --out suite/synthetic_nav_suite_v3_conservative.jsonl --summary suite/synthetic_nav_suite_v3_conservative_summary.json
python suite/synthetic_nav_cases_v4_extreme.py --out suite/synthetic_nav_suite_v4_extreme_6400.jsonl --summary suite/synthetic_nav_suite_v4_extreme_6400_summary.json --limit 6400
python validation/validate_offroute_suite.py --suite suite/synthetic_nav_suite_v2.jsonl --algorithm nav_offroute.py --out validation/validation_v2.csv
python validation/validate_offroute_suite.py --suite suite/synthetic_nav_suite_v3_conservative.jsonl --algorithm nav_offroute.py --out validation/validation_v3.csv
python validation/validate_offroute_suite.py --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl --algorithm nav_offroute.py --out validation/validation_v4.csv
```

### 2. Inspect generated data in the viewer

```bash
cd frontend
npm run dev
```

然后手动打开 `suite/` 里的 JSONL，并按需选择算法脚本叠加结果。

## Notes

- `v4` 生成依赖 `suite/synthetic_nav_cases_v2.py` 和 `suite/synthetic_nav_cases_v3_conservative.py`，所以不要只单独移动 `v4` 脚本。
- synthetic suite 适合做稳定回归，本地回放数据更适合做内部产品直觉校验。
- 当前目录已经生成好了 `v2 / v3 / v4` 三套 suite 和三份验证结果，可直接复用，不一定每次都需要重跑。
