# 业务演练数据口径

frame 一行一在证批次，为抽样总体；plant_inclusion_probability 乘 conditional_inclusion_probability 是两阶段联合入样概率。tests.attempt 为该次 draw 内检测次数，supersedes 表示替代初检结论。后停证不追溯删掉 population_at 时点的批次。

各 ID 均为本地合成标识，不包含真实人员或生产数据。时刻使用带时区 ISO 8601；as_of 是分析可知信息的截止时刻，不是业务发生时间上界。晚到事实允许保留在输入中，但只能在 available_at 到达之后采用。source_versions 标识输入版本；baseline_case_id 关联同库既有演示运行。空值表示未知，不应未经说明转成零。
