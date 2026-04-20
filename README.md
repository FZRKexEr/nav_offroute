# Commercial Off-route Detector Artifacts

这个目录已经整理成 4 个主要模块：

- `nav_offroute_commercial.py` 和 `profiles/`：偏航算法脚本
- `suite/`：synthetic 数据生成脚本和生成结果
- `validation/`：验证脚本和验证结果
- `frontend/`：JSONL/GeoJSON 可视化查看器

说明：

- 原始真实用户 case 已从当前仓库移除，不再随仓库分发。
- 当前 README 只描述公开可运行的 synthetic / validation / frontend 工作流。

## Quick Start

先在项目根目录激活虚拟环境：

```bash
cd /Users/xinpeng/Downloads/commercial_offroute_final_artifacts
source .venv/bin/activate
```

如果你只是想快速确认环境没问题，建议先跑一遍 `v4` 验证：

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl \
  --algorithm nav_offroute_commercial.py \
  --out validation/validation_v4_commercial.csv
```

## Directory Layout

- `nav_offroute_commercial.py`
  默认商用 spoken off-route 算法。
- `profiles/nav_offroute_commercial_v13.py`
  更低时延的 profile。
- `profiles/nav_offroute_commercial_v14.py`
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

### 1. Run the default commercial algorithm

```bash
python nav_offroute_commercial.py <route_geojson> <gps_geojson> [--csv debug.csv]
```

参数：

- `<route_geojson>`：路线 GeoJSON
- `<gps_geojson>`：定位点 GeoJSON
- `--csv`：可选，输出逐点调试 CSV

如果你有本地私有 case，可以用上面的形式直接跑，不要求文件一定来自当前仓库。

### 2. Run a profile algorithm

`profiles/` 里的脚本参数和默认算法一致。

低时延 profile：

```bash
python profiles/nav_offroute_commercial_v13.py <route_geojson> <gps_geojson> [--csv debug_v13.csv]
```

版本化备份：

```bash
python profiles/nav_offroute_commercial_v14.py <route_geojson> <gps_geojson> [--csv debug_v14.csv]
```

## Module 2: Generate Synthetic Suites

`suite/` 里有三层数据生成脚本：

- `synthetic_nav_cases_v2.py`
  更偏几何偏离语义
- `synthetic_nav_cases_v3_conservative.py`
  更保守的产品语义
- `synthetic_nav_cases_v4_extreme.py`
  最终商用语义，强调 spoken off-route 要谨慎

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
  --algorithm nav_offroute_commercial.py \
  --out validation/validation_v2_commercial.csv
```

### 2. Validate v3

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v3_conservative.jsonl \
  --algorithm nav_offroute_commercial.py \
  --out validation/validation_v3_commercial.csv
```

### 3. Validate v4

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl \
  --algorithm nav_offroute_commercial.py \
  --out validation/validation_v4_commercial.csv
```

### 4. Validate with another profile

```bash
python validation/validate_offroute_suite.py \
  --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl \
  --algorithm profiles/nav_offroute_commercial_v13.py \
  --out validation/validation_v4_commercial_v13.csv
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

- `../nav_offroute_commercial.py`
- `../profiles/nav_offroute_commercial_v13.py`
- `../profiles/nav_offroute_commercial_v14.py`

说明：

- 算法脚本没有默认值，必须手动选择
- 这个能力依赖本地 `npm run dev` 或 `npm run preview`
- 不支持直接双击静态 HTML 文件运行

## Docs

如果你要看设计和结果总结，优先看：

- [`docs/commercial_offroute_final_report.md`](/Users/xinpeng/Downloads/commercial_offroute_final_artifacts/docs/commercial_offroute_final_report.md)
- [`docs/offroute_architecture_design.md`](/Users/xinpeng/Downloads/commercial_offroute_final_artifacts/docs/offroute_architecture_design.md)

## Typical Workflows

### 1. Full regenerate + validate

```bash
source .venv/bin/activate
python suite/synthetic_nav_cases_v2.py --out suite/synthetic_nav_suite_v2.jsonl --summary suite/synthetic_nav_suite_v2_summary.json
python suite/synthetic_nav_cases_v3_conservative.py --out suite/synthetic_nav_suite_v3_conservative.jsonl --summary suite/synthetic_nav_suite_v3_conservative_summary.json
python suite/synthetic_nav_cases_v4_extreme.py --out suite/synthetic_nav_suite_v4_extreme_6400.jsonl --summary suite/synthetic_nav_suite_v4_extreme_6400_summary.json --limit 6400
python validation/validate_offroute_suite.py --suite suite/synthetic_nav_suite_v2.jsonl --algorithm nav_offroute_commercial.py --out validation/validation_v2_commercial.csv
python validation/validate_offroute_suite.py --suite suite/synthetic_nav_suite_v3_conservative.jsonl --algorithm nav_offroute_commercial.py --out validation/validation_v3_commercial.csv
python validation/validate_offroute_suite.py --suite suite/synthetic_nav_suite_v4_extreme_6400.jsonl --algorithm nav_offroute_commercial.py --out validation/validation_v4_commercial.csv
```

### 2. Inspect generated data in the viewer

```bash
cd frontend
npm run dev
```

然后手动打开 `suite/` 里的 JSONL，并按需选择算法脚本叠加结果。

## Notes

- `v4` 生成依赖 `suite/synthetic_nav_cases_v2.py` 和 `suite/synthetic_nav_cases_v3_conservative.py`，所以不要只单独移动 `v4` 脚本。
- synthetic suite 适合做稳定回归，私有真实 case 更适合做内部产品直觉校验。
- 当前目录已经生成好了 `v2 / v3 / v4` 三套 suite 和三份 commercial 验证结果，可直接复用，不一定每次都需要重跑。
