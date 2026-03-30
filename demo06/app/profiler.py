"""
profiler.py —— 问卷答案 → 用户旅行画像

将 16 道题的答案（每题 1-4 分）转换成：
  1. 8 个维度的分数 + 标签 + 自然语言描述
  2. 注入 LLM System Prompt 的完整画像文本
  3. 个性化人格标签（如"美食探索家 · 户外冒险者"）

计分规则：每道题 A=1 B=2 C=3 D=4，每个维度 = 两题之和（2~8）
分值解读：2~3 低，4~5 中，6~7 中高，8 极高
"""

from .schemas import DimensionScore, QuestionAnswer, UserProfile

_BUDGET_MAP = {"low": "节省型", "medium": "均衡型", "high": "体验型"}

_DIMENSION_META: dict[str, tuple[str, str]] = {
    "pace":          ("行程节奏", "🏃"),
    "food":          ("美食探索", "🍜"),
    "culture":       ("文化兴趣", "🏛"),
    "nature":        ("自然亲近", "🏔"),
    "photo":         ("拍照打卡", "📸"),
    "activity":      ("体力偏好", "⚡"),
    "accommodation": ("住宿品质", "🛏"),
    "social":        ("社交性格", "💬"),
}


def _label(score: int) -> str:
    if score <= 3: return "低"
    if score <= 5: return "中"
    if score <= 6: return "中高"
    return "高"


def _bucket(score: int) -> str:
    """把分数分成 low/mid/high 三档，用于索引描述文案"""
    if score <= 4: return "low"
    if score >= 6: return "high"
    return "mid"


def _dimension_description(key: str, score: int, budget: str) -> str:
    budget_label = _BUDGET_MAP.get(budget, "均衡型")
    b = _bucket(score)
    desc: dict[str, dict[str, str]] = {
        "pace": {
            "low": "慢游型：每天不超过 2 个核心景点，留充裕时间停留感受，不赶行程",
            "mid": "节奏均衡：每天 2~3 个景点，保持适当弹性",
            "high": "高效型：喜欢充实行程，每天可安排 4~5 个景点，效率优先",
        },
        "food": {
            "low": "饮食随意：不需要特意推荐排队名店，快速方便解决即可",
            "mid": "适度探店：推荐 1~2 家有代表性的地道餐厅即可",
            "high": "深度探店：愿意为地道美食排队等候，餐厅推荐需精心挑选知名本地店",
        },
        "culture": {
            "low": "文化兴趣较低：减少博物馆、展览等文化场所的安排",
            "mid": "适度文化：安排 1 个有代表性的文化景点",
            "high": "文化深度游：多安排博物馆、古迹、历史街区，可安排讲解服务",
        },
        "nature": {
            "low": "偏好轻松自然：避免长距离徒步，优先有交通接驳的自然景区",
            "mid": "适度户外：1~2 小时的步行或骑行可以接受",
            "high": "热爱户外：可安排徒步、骑行、爬山等高强度户外体验",
        },
        "photo": {
            "low": "不在乎出片：无需特意安排网红机位或最佳光线时段",
            "mid": "偶尔拍拍：推荐几个好看的地方，不用刻意安排",
            "high": "出片优先：推荐时注明最佳拍摄时间和角度，安排网红打卡地",
        },
        "activity": {
            "low": "体力偏低：避免爬升大、连续步行超过 1.5 小时的路线",
            "mid": "体力中等：适量步行没问题，避免高强度连续爬坡",
            "high": "体力充沛：可安排高强度徒步、骑行或水上活动项目",
        },
        "accommodation": {
            "low":  f"住宿从简：在{budget_label}预算内选最基础的选项，省下来留给体验消费",
            "mid":  f"住宿均衡：在{budget_label}预算内保障基本舒适度即可",
            "high": f"住宿优先：在{budget_label}预算内尽量升级住宿品质，好的休息是旅行保障",
        },
        "social": {
            "low": "偏内向：推荐安静人少的景点，避开极度拥挤的时段和嘈杂场所",
            "mid": "适度社交：冷热皆宜，有氛围感的地方最好",
            "high": "热爱交流：可推荐热闹的夜市、市集等人气聚集场所",
        },
    }
    return desc[key][b]


def _build_personality(dims: dict[str, int], budget: str) -> tuple[str, str]:
    """生成人格标签和概述句"""
    budget_label = _BUDGET_MAP.get(budget, "均衡型")
    tags: list[str] = []

    if dims["pace"] <= 3:
        tags.append("慢游达人")
    elif dims["pace"] >= 7:
        tags.append("效率旅行家")

    if dims["food"] >= 7:
        tags.append("美食探索家")

    if dims["nature"] >= 7:
        tags.append("户外冒险者")
    elif dims["culture"] >= 7:
        tags.append("文化深度游客")

    if dims["photo"] >= 7:
        tags.append("出片达人")
    if dims["activity"] >= 7 and "户外冒险者" not in tags:
        tags.append("运动达人")
    if dims["social"] >= 7:
        tags.append("社交达人")
    elif dims["social"] <= 3:
        tags.append("独处享受者")

    if not tags:
        tags.append("随性旅行者")

    label = " · ".join(tags[:2])

    pace_str = "偏爱慢节奏、不赶行程" if dims["pace"] <= 4 else "喜欢充实高效的行程安排"
    food_str = "，美食是旅行中不可缺少的仪式感" if dims["food"] >= 6 else "，饮食上随性不讲究"
    nature_str = "，大自然景色让你最放松" if dims["nature"] >= 6 else "，更享受城市和人文体验"
    desc = f"你{pace_str}{food_str}{nature_str}。预算档位是{budget_label}，AI 将在此范围内为你匹配最合适的住宿和餐厅。"

    return label, desc


def compute_user_profile(answers: QuestionAnswer) -> UserProfile:
    """将问卷答案转换成结构化的用户旅行画像"""
    raw: dict[str, int] = {
        "pace":          answers.q1  + answers.q2,
        "food":          answers.q3  + answers.q4,
        "culture":       answers.q5  + answers.q6,
        "nature":        answers.q7  + answers.q8,
        "photo":         answers.q9  + answers.q10,
        "activity":      answers.q11 + answers.q12,
        "accommodation": answers.q13 + answers.q14,
        "social":        answers.q15 + answers.q16,
    }

    dimensions: list[DimensionScore] = []
    for key, score in raw.items():
        name, icon = _DIMENSION_META[key]
        dimensions.append(DimensionScore(
            key=key,
            name=name,
            icon=icon,
            score=score,
            label=_label(score),
            description=_dimension_description(key, score, answers.budget_level),
        ))

    budget_label = _BUDGET_MAP.get(answers.budget_level, "均衡型")
    profile_lines = [d.description for d in dimensions]
    profile_lines.append(f"预算档位：{budget_label}，住宿和餐饮推荐均在此档位内匹配")
    profile_text = (
        "【用户旅行偏好画像】\n"
        + "\n".join(f"- {line}" for line in profile_lines)
        + "\n\n请在行程规划中严格参照以上画像，使景点密度、餐厅档次、住宿品质、"
          "体力强度和景点类型真正符合该用户的旅行风格。"
    )

    label, desc = _build_personality(raw, answers.budget_level)

    return UserProfile(
        dimensions=dimensions,
        profile_text=profile_text,
        personality_label=label,
        personality_desc=desc,
        budget_level=answers.budget_level,
    )
