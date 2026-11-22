"""向量校验与归一化工具。"""

import math


# 校验向量是否符合调用方指定的维度，返回 L2 归一化后的向量。
def normalize_vector(values: list[float], *, expected_dimensions: int) -> list[float]:
    # region 校验向量维度和元素，模长有限且非零时执行归一化
    if len(values) != expected_dimensions or any(
        not isinstance(value, (int, float)) or not math.isfinite(value) for value in values
    ):
        raise ValueError('无效向量')
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('无效向量模长')
    return [value / norm for value in values]
    # endregion
