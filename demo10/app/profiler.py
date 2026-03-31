"""
profiler.py —— 问卷答案 → 用户画像 prompt 片段（demo10 新增）

核心思路：
  每个问卷选项都直接绑定了一个 prompt 片段（见 questionnaire.py）。
  compute_profile() 将用户的选择拼装成一段自然语言描述，
  直接插入 planner_node 的 system prompt 里，无需经过 LLM 再次解读。

  这比"打分→标签映射"更可靠：
    - 旧方式：答案 → 数值分 → 分段 → 标签（多步骤，信息丢失）
    - 新方式：答案 → 直接取出对应的 prompt 文本（零损耗）

返回格式示例：
  "【用户画像】
    - 出行节奏：用户喜欢深度游，每天安排 3~4 个景点
    - 体验深度：用户对历史文化有深度兴趣，行程中加入讲解建议
    - 社交风格：独自旅行或情侣出游，推荐有氛围感的餐厅
    - 消费风格：用户偏好中等档次餐厅，人均 80~150 元
    - 体力水平：用户体力中等，可安排轻度徒步路线"
"""

from .questionnaire import DIMENSION_LABELS, QUESTIONS


def _build_option_map() -> dict[str, dict]:
    """
    构建 option_id → {prompt, dimension} 映射，避免每次计算时遍历问题列表。
    """
    mapping: dict[str, dict] = {}
    for q in QUESTIONS:
        for opt in q["options"]:
            mapping[opt["id"]] = {
                "prompt":    opt["prompt"],
                "dimension": q["dimension"],
                "question":  q["id"],
            }
    return mapping


_OPTION_MAP = _build_option_map()


def compute_profile(answers: list[str]) -> str:
    """
    将选项 ID 列表转换为用户画像描述字符串。

    Args:
        answers: 选项 ID 列表，例如 ["Q1A", "Q3B", "Q5A", "Q7B", "Q9B", "Q10B"]
                 可以是部分回答（用户跳过的题目自动忽略）

    Returns:
        可直接插入 planner prompt 的多行字符串；答案为空时返回空字符串。
    """
    if not answers:
        return ""

    # 按维度分组：每个维度取最后一个有效答案（允许用户多次选择）
    dimension_lines: dict[str, str] = {}
    for opt_id in answers:
        opt_id = opt_id.strip()
        info = _OPTION_MAP.get(opt_id)
        if not info:
            continue
        dim   = info["dimension"]
        label = DIMENSION_LABELS.get(dim, dim)
        dimension_lines[dim] = f"  - {label}：{info['prompt']}"

    if not dimension_lines:
        return ""

    # 按维度顺序输出（保持 questionnaire.py 中的定义顺序）
    ordered_dims = ["pace", "depth", "social", "budget", "physical"]
    lines = ["【用户画像（问卷生成）】"]
    for dim in ordered_dims:
        if dim in dimension_lines:
            lines.append(dimension_lines[dim])

    return "\n".join(lines)


def get_questionnaire_for_api() -> list[dict]:
    """
    返回前端展示用的问卷数据（去掉 prompt 字段，不暴露给客户端）。
    """
    result = []
    for q in QUESTIONS:
        result.append({
            "id":        q["id"],
            "dimension": q["dimension"],
            "dim_label": DIMENSION_LABELS.get(q["dimension"], q["dimension"]),
            "text":      q["text"],
            "options": [
                {"id": opt["id"], "label": opt["label"]}
                for opt in q["options"]
            ],
        })
    return result
