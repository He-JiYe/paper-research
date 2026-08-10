"""LLM prompt 模板：论文初筛提示词 + few-shot 加载。

提示词用 ``str.format`` 的 ``{name}`` 占位符（``{keyword}`` / ``{title}`` /
``{abstract}`` / ``{categories}`` / ``{examples}``）；JSON 示例中的花括号以
``{{ }}`` 转义。

few-shot 示例按关键词存放在项目根 ``examples/{keyword}-few-shot.txt`` 文件
（src 之外），评分时按关键词加载对应示例文件注入 prompt；文件缺失时自动
创建空文件供后续手工补充。
"""

import logging

from src.paths import ROOT_DIR

logger = logging.getLogger(__name__)

# few-shot 示例根目录（每个关键词一个 ``{keyword}-few-shot.txt`` 文件）
EXAMPLES_DIR = ROOT_DIR / "examples"

# 论文初筛 prompt：对标题+摘要进行 LLM 评级（important/useful/browse/skip）、
# 相关性评分（0-1）并生成中文摘要。{examples} 由评分方按关键词注入 few-shot 示例。
SUMMARIZE_PROMPT = """你是一位计算机科学博士生,正在筛选最新学术论文.请根据以下论文的标题、摘要、来源与更新时间,给出你的判断.

搜索关键词:{keyword}
来源:{source}
更新时间:{updated}
标题:{title}
摘要:{abstract}
分类:{categories}

请用中文回复,严格遵循以下 JSON 格式,不要添加任何其他文字:
```json
{{
  "summary": "用2-3句中文概括本文的核心技术贡献和方法",
  "remark": "useful",
  "reason": "用1句话说明选择该等级的理由",
  "score_distribution": {{"0": 概率0, "1": 概率1, "2": 概率2, "3": 概率3, "4": 概率4, "5": 概率5}}
}}
```

评级标准（remark 字段必须严格为以下英文之一,禁止输出等级描述、中文说明或其它文字）:
- "important": 范式突破,理论创新,或可能产生重大影响的 work
- "useful": 有实用价值,solid engineering,可复用的 trick 或方法
- "browse": 有一定参考价值但非核心关注方向,可快速浏览
- "skip": 增量式工作,无明显贡献,或与研究方向无关

评分标准（请结合「搜索关键词」判断相关性,输出各分数的概率分布）:
- 5分: 研究搜索关键词的核心方向,并做出范式突破、理论创新,或可能产生重大影响
- 4分: 直接研究搜索关键词的核心方向,标题/摘要命中关键词
- 3分: 属于该方向的平均水平工作,有一定相关性
- 2分: 弱相关,仅边缘触及该方向
- 1分: 其他学科的工作,仅引用了该方向的基础方法
- 0分: 完全不相关,与研究方向无关

{examples}
请为每个分数给出合理的概率(0.0-1.0),所有概率之和应为1.0。score_distribution 的 key 必须为字符串形式的 "0"、"1"、"2"、"3"、"4"、"5"。最终得分由系统根据概率分布的期望值自动计算。
"""


def load_examples(keyword: str) -> str:
    """按关键词加载 few-shot 示例（``examples/{keyword}-few-shot.txt``）。

    文件不存在时创建空文件并提示，方便后续手工补充样例。

    Args:
        keyword: 搜索关键词（用于定位 ``examples/{keyword}-few-shot.txt``）。

    Returns:
        示例文件内容；关键词为空或文件为空时返回空字符串。
    """
    if not keyword:
        return ""
    path = EXAMPLES_DIR / f"{keyword}-few-shot.txt"
    try:
        if not path.exists():
            EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
            path.touch()
            logger.warning(
                "未找到关键词「%s」的 few-shot 示例，已创建空文件 %s，请补充样例", keyword, path
            )
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        # 只读/不可写环境（如部署安装目录）：示例文件不可读写时返回空继续评分，
        # 不因写盘/读盘失败中断整个评分流程。
        logger.warning("few-shot 示例文件不可读写 %s：%s", path, e)
        return ""


def build_summarize_prompt(
    *,
    keyword: str,
    title: str,
    abstract: str,
    categories: str,
    source: str = "",
    updated: str = "",
) -> str:
    """构建完整的论文初筛 prompt（含按关键词加载的 few-shot 示例）。

    Args:
        keyword: 搜索关键词（用于相关性判断 + 定位示例文件夹）
        title: 论文标题
        abstract: 论文摘要
        categories: 论文分类
        source: 数据源名（如 arxiv，供 LLM 判断来源类型）
        updated: 论文最近更新时间（YYYY-MM-DD 或 ISO，供 LLM 判断时效性）

    Returns:
        可直接发给 LLM 的完整 prompt 字符串。
    """
    examples = load_examples(keyword)
    examples_block = (
        f"\n\n以下是关键词「{keyword}」的评分示例，请参考判断尺度：\n{examples}\n"
        if examples
        else ""
    )
    return SUMMARIZE_PROMPT.format(
        keyword=keyword or "未指定",
        source=source or "未指定",
        updated=(updated or "未知")[:10],  # ISO 时间戳截取日期，保持 prompt 简洁
        title=title,
        abstract=abstract,
        categories=categories or "未指定",
        examples=examples_block,
    )
