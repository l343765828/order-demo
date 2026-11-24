"""向量校验与归一化工具。"""

import math


# 校验向量是否符合调用方指定的维度，返回 L2 归一化后的向量。
def normalize_vector(values: list[float], *, expected_dimensions: int) -> list[float]:

    # region 校验向量维度和元素，模长有限且非零时执行归一化
    if (
        # 输入向量的维度与调用方要求不符。
        len(values) != expected_dimensions
        # 输入向量含有非数值或非有限元素，无法归一化。
        or any(
            (
                # 向量元素不是支持的数值类型。
                not isinstance(value, (int, float))
                # 向量元素是无穷大或 NaN。
                or not math.isfinite(value)
            )
            for value in values
        )
    ):
        raise ValueError('无效向量')
    norm = math.sqrt(sum(value * value for value in values))
    if (
        # 向量模长不是有限数值。
        not math.isfinite(norm)
        # 向量模长为零，无法用作归一化的除数。
        or norm == 0
    ):
        raise ValueError('无效向量模长')
    return [value / norm for value in values]
    # endregion
