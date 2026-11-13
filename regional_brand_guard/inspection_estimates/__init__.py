"""两阶段不等概率抽样下的在证批次总体合格率估计。

本包在既有逐案抽检/证书处理（regional_brand_guard.inspections、
regional_brand_guard.certificates）之上新增总体推断能力，不修改证书签发规则：

* 总体以 study.population_at 时点的在证框 frame 为准，抽样后停证不剔除批次；
* 入样概率为 plant_inclusion_probability * conditional_inclusion_probability，
  采用 Horvitz-Thompson / Hájek 估计并按框规模事后分层；
* 复检（supersedes）只替代同一 draw 的初检结论，不产生新样本；
* 概率缺失、整层无有效检测、非应答偏向高风险层时输出不可估标记与敏感性界限，
  不回退为简单比例；
* 证据链接 sample_plan 输入版本与证书库版本，后补检测产生可辨认的新版本。
"""
from __future__ import annotations

from regional_brand_guard.inspection_estimates.estimator import (
    EstimationError,
    build_estimate,
)

__all__ = ["EstimationError", "build_estimate"]
