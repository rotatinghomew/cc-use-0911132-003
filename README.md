# 风险抽检下的区域品牌总体合格率推断

项目使用 Python 3.11 标准库与 SQLite，现有逐案处理入口为 `regional_brand_guard.certification.cli`。保留原有处理器、持久化与测试；分析业务使用本地合成资料，不连接生产系统。无需 pip 安装、下载模型或启动额外服务。

## 现有程序

在仓库根目录运行 `bash scripts/demo.sh`，依次完成现有产品回归、分析输入核对、语法构建和真实业务入口调用。所有运行文件位于临时目录，结束后清理；运行失败立即退出，不使用预存输出代替执行。

单独测试：`python3 -m unittest discover -s tests/certification_cycles -p 'test_*.py' -v`。

输入检查：`python3 -m unittest discover -s tests -p test_analysis_inputs.py -v`。

构建检查：`python3 -m compileall -q regional_brand_guard tests`。

业务调用：`python3 -m regional_brand_guard.certification.cli fixtures/demo.json --database /tmp/regional_brand_guard-demo.sqlite3`。

## 分析资料与联调

`fixtures/analysis/input.json` 保存业务演练数据，字段解释在 `fixtures/analysis/fields.md`。以下已核对的数据形态用于分析回归：empty_stratum、retest_chain、unequal_probability。现有输入测试检查这些形态是否真实存在，不代替分析方法或数值正确性的验证。

分析入口接通后执行 `bash scripts/feature_demo.sh`：先重跑现有产品，再从干净数据库计算分析、同输入重放、核对输出结构并打印完整 JSON。尚未接通时该命令会报模块不存在，不会用静态样例冒充成功。分析 ID 为 `estimate_id`，其余输出包括 `population, estimates, design_diagnostics, sensitivity, comparison`。方法正确性还需覆盖业务说明中的反例，结构核验不能代替统计验证。

## 总体合格率推断（inspection_estimates）

入口（不改动既有证书签发规则与处理器）：

```
python3 -m regional_brand_guard.inspection_estimates \
  --input <inspection_json> --database <certificate_db> --output <estimate_json>
  [--predecessor-input <predecessor_json>]
```

口径与方法：

- 总体固定为 `study.population_at` 时点在证框（`certificate_start <= population_at < certificate_end`）；抽样后停证只在证据中登记，不把批次从季度总体删除。
- 两阶段联合入样概率 `pi = plant_inclusion_probability * conditional_inclusion_probability`；层内用 Horvitz-Thompson/Hájek 比率估计，总体按框规模事后分层。输入未提供二阶联合概率，方差采用泊松（有放回）线性化近似并在 `design_diagnostics` 明示，不做简单随机抽样假设；区间为 logit 变换 95% 区间，全合格/全失败层退化为 rule of three 经验界限。
- 复检按 `supersedes` 折叠为同一 draw 的唯一最终结论，不新增样本；晚于 `as_of` 的检测、抽检与停证事实不采用并在诊断中列名。
- 非应答：主估计声明层内 MAR 假设，另按 `study.nonresponse_ranges` 给出非应答合格假设 q 的敏感性；高风险层非应答率偏高时给出风险提示。整层无有效检测时该层标记 `not_estimable`、总体只给 `[0,1]` 无信息包络；入样概率缺失的单元排除出加权点估计并计入包络，绝不静默改算简单比例。
- `comparison` 区分产品表现、产品构成（事后分层份额变化）与标准口径；标准集合变化时禁止总体两数直接比较，只允许共同标准子总体结论，变化还需通过 95% 显著性与 2 个百分点重要性阈值才允许写入质量提升/下降。
- 证据与版本：`evidence` 链接 `source_versions` 与同库证书运行（run_id、revision、sample_plan 证据哈希、签发决策）；输入指纹变化即产生新版本，同输入重放复用同一 `estimate_id`。

补充演练场景由 `python3 scripts/build_analysis_scenarios.py` 生成到 `fixtures/analysis_scenarios/`：raw_drop（原始送检合格率下降而总体未显著下降）、standard_change（标准变严与可比子总体）、missing_probability（概率缺失保护）。新增统计测试：`python3 -m unittest tests.test_inspection_estimates -v`。

## 离线环境

使用 Python 3.11.2 的 Linux 环境即可复现。容器环境预置解释器后，以只读方式挂载仓库，将文件复制到容器临时目录再运行脚本；运行时关闭网络。基础测试和小规模本地数据适合在 90 秒内展示，正式展示命令为 `bash scripts/feature_demo.sh`。不要在展示脚本里安装依赖或等待外部服务。
