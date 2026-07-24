"""网络层：基于 arxiv 库的 Arxiv API 查询 + PDF 下载

使用 ``arxiv`` Python 库（v4+）替代自定义 HTTP 客户端和 XML 解析。

职责：
- Arxiv API：多关键词并发查询、版本检测、搜索、按 ID 获取
- PDF 下载：从 arxiv.org 下载论文 PDF
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import arxiv
import httpx
from arxiv import SortCriterion, SortOrder

from src.config import KeywordEntry

# ─── 结果转换 ──────────────────────────────────────────────


def _result_to_paper(result: arxiv.Result, keyword: str = "") -> dict:
    """将 arxiv.Result 转换为项目统一的 paper dict 格式。

    Args:
        result: arxiv 搜索结果条目
        keyword: 匹配的关键词

    Returns:
        标准化的论文字典
    """
    raw_id = result.entry_id
    arxiv_id_match = re.search(r"(\d{4}\.\d{4,5})", raw_id)
    arxiv_id = arxiv_id_match.group(1) if arxiv_id_match else ""

    version_match = re.search(r"v(\d+)$", raw_id)
    version = int(version_match.group(1)) if version_match else 1

    authors_str = ", ".join(a.name for a in result.authors)
    published_str = result.published.strftime("%Y-%m-%d") if result.published else ""

    return {
        "arxiv_id": arxiv_id,
        "version": version,
        "title": result.title.strip().replace("\n", " "),
        "authors": authors_str,
        "abstract": result.summary.strip().replace("\n", " "),
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "primary_category": result.primary_category,
        "categories": ", ".join(result.categories),
        "published": published_str,
        "arxiv_updated": result.updated.isoformat() if result.updated else "",
        "keyword_match": keyword,
    }


# ─── 查询构建 ──────────────────────────────────────────────


def build_query(keyword: str, arxiv_cats: list[str] | None = None) -> str:
    """构建 Arxiv API 查询字符串。

    arxiv_cats 与 keyword 做 AND 组合（限定分类，不参与 OR 扩展）。

    Note:
        arxiv 库的 Search 使用标准 Arxiv API 查询语法，
        分类用 ``cat:XXX`` 前缀，关键词用 ``all:XXX`` 前缀。
    """
    cat_part = ""
    if arxiv_cats:
        cat_part = "(" + "+OR+".join(f"cat:{c}" for c in arxiv_cats) + ")+AND+"
    return f"{cat_part}all:{keyword}"


# ─── 关键词查询 ────────────────────────────────────────────


async def fetch_keyword(
    kw: KeywordEntry,
    max_results: int = 50,
    lookback_days: int = 7,
    historical: bool = False,
    skip_ids: set[str] | None = None,
) -> list[dict]:
    """异步查询单个关键词（使用 ``arxiv`` 库）。

    Args:
        kw: KeywordEntry 对象
        max_results: 最大结果数
        lookback_days: 回溯天数（增量模式）
        historical: True=全量不限制时间, False=限制时间窗口
        skip_ids: 需要跳过的 arxiv_id 集合

    Returns:
        论文列表（已去重、过滤）
    """
    query = build_query(kw.keyword, kw.arxiv_cats)

    if historical:
        sort_by = SortCriterion.Relevance
        sort_order = SortOrder.Descending
    else:
        sort_by = SortCriterion.LastUpdatedDate
        sort_order = SortOrder.Descending

    def _fetch() -> list[arxiv.Result]:
        """同步封装：在 executor 中运行。"""
        client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
        search = arxiv.Search(
            query=query,
            max_results=max_results * 2,  # 多取一些应对 skip_ids 过滤
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return list(client.results(search))

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _fetch)

    papers = []
    seen_ids: set[str] = set()

    for r in results:
        paper = _result_to_paper(r, keyword=kw.keyword)
        aid = paper["arxiv_id"]

        if aid in seen_ids:
            continue
        if skip_ids and aid in skip_ids:
            continue

        if not historical:
            cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()[:10]
            if paper["published"] < cutoff:
                continue

        seen_ids.add(aid)
        papers.append(paper)

        if historical and len(papers) >= max_results:
            break

    if not historical:
        papers.sort(key=lambda p: relevance_sort_key(p, kw.keyword), reverse=True)

    return papers[:max_results]


async def fetch_all(
    keywords: list[KeywordEntry],
    settings,
    max_results: int = 50,
    historical: bool = False,
    skip_ids: set[str] | None = None,
) -> tuple[list[dict], int, int]:
    """
    并发查询所有活跃关键词。

    Args:
        keywords: KeywordEntry 列表
        settings: AppConfig 对象
        max_results: 每个关键词的最大结果数
        historical: True=全量, False=增量
        skip_ids: 需要跳过的 arxiv_id 集合

    Returns:
        (去重后的论文列表, 总篇数, 重复篇数)
    """
    fetch_cfg = settings.fetch
    max_conns = fetch_cfg.max_concurrent_requests
    lookback_days = fetch_cfg.lookback_days

    active_kws = [kw for kw in keywords if kw.active]

    if not active_kws:
        print("  [!] No active keywords")
        return [], 0, 0

    # 并发限制
    sem = asyncio.Semaphore(max_conns)

    async def _limited_fetch(kw: KeywordEntry) -> tuple[str, list[dict] | Exception]:
        async with sem:
            try:
                result = await fetch_keyword(
                    kw, max_results, lookback_days, historical=historical, skip_ids=skip_ids
                )
                return kw.keyword, result
            except Exception as e:
                return kw.keyword, e

    results = await asyncio.gather(*[_limited_fetch(kw) for kw in active_kws])

    all_papers = []
    for kw_name, result in results:
        if isinstance(result, Exception):
            print(f"  ⚠️ 关键词 [{kw_name}] 抓取失败: {result}")
            continue
        print(f"  ✅ [{kw_name}]: {len(result)} 篇")
        all_papers.extend(result)

    # 多关键词去重
    seen = set()
    unique = []
    for p in all_papers:
        if p["arxiv_id"] in seen:
            continue
        seen.add(p["arxiv_id"])
        unique.append(p)

    duplicate_count = len(all_papers) - len(unique)
    if duplicate_count:
        print(f"  🔗 去重 {duplicate_count} 篇: {len(all_papers)} → {len(unique)}")

    return unique, len(all_papers), duplicate_count


# ─── 搜索（无日期限制）─────────────────────────────────────


async def search_arxiv(
    query: str,
    max_results: int = 20,
    categories: list[str] | None = None,
) -> list[dict]:
    """搜索 Arxiv（无日期限制），支持 ti:/au:/all: 等前缀。

    Args:
        query: 搜索关键词
        max_results: 最大结果数
        categories: 可选分类过滤列表

    Returns:
        论文列表
    """
    cat_part = ""
    if categories:
        cat_part = "+AND+(" + "+OR+".join(f"cat:{c}" for c in categories) + ")"

    search_query = (
        f"all:{query}{cat_part}"
        if not any(query.startswith(p) for p in ("ti:", "au:", "all:", "cat:"))
        else f"{query}{cat_part}"
    )

    def _search() -> list[arxiv.Result]:
        client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
        search = arxiv.Search(
            query=search_query,
            max_results=min(max_results, 100),
            sort_by=SortCriterion.Relevance,
            sort_order=SortOrder.Descending,
        )
        return list(client.results(search))

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _search)

    return [_result_to_paper(r, keyword=query) for r in results]


# ─── 按 ID 获取 ────────────────────────────────────────────


async def fetch_by_ids(arxiv_ids: list[str]) -> list[dict]:
    """通过 arxiv_id 列表精确获取论文元数据。

    使用 arxiv 库的 id_list 参数。

    Args:
        arxiv_ids: Arxiv ID 列表

    Returns:
        论文列表
    """
    # arxiv 库的 Search 支持 id_list 参数
    def _fetch() -> list[arxiv.Result]:
        client = arxiv.Client(page_size=100, delay_seconds=3, num_retries=3)
        search = arxiv.Search(id_list=arxiv_ids)
        return list(client.results(search))

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _fetch)

    return [_result_to_paper(r) for r in results]


# ─── PDF 下载 ──────────────────────────────────────────────


def download_pdf(arxiv_id: str, dest_dir: str | Path, version: int = 0) -> Path:
    """从 arxiv.org 下载论文 PDF 到本地（同步阻塞，异步上下文请用 to_thread）。

    Args:
        arxiv_id: Arxiv ID（如 "2501.12345"）
        dest_dir: 目标目录
        version: 版本号（>0 时文件名带 vN 后缀）

    Returns:
        下载后的本地文件路径

    Raises:
        ValueError: arxiv_id 格式非法
        RuntimeError: 下载失败或内容不是 PDF
    """
    if not re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", arxiv_id):
        raise ValueError(f"Invalid arxiv_id: {arxiv_id}")

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    base_id = arxiv_id.split("v")[0]
    suffix = f"v{version}" if version > 0 else ""
    pdf_path = dest / f"{base_id}{suffix}.pdf"

    with httpx.Client(follow_redirects=True, timeout=120) as client:
        resp = client.get(f"https://arxiv.org/pdf/{arxiv_id}")
        if resp.status_code != 200:
            raise RuntimeError(f"PDF 下载失败: HTTP {resp.status_code}")
        content = resp.content

    # arXiv 出错时可能返回 HTML 错误页，用魔数校验 PDF
    if not content.startswith(b"%PDF"):
        raise RuntimeError(f"下载内容不是 PDF（{len(content)} bytes）: {arxiv_id}")

    pdf_path.write_bytes(content)
    return pdf_path


# ─── 工具函数 ──────────────────────────────────────────────


def relevance_sort_key(paper: dict, keyword: str) -> float:
    """计算论文与关键词的简单相关性得分（用于排序）。

    对 title 和 abstract 分别与 keyword 做 token 重叠匹配，
    标题匹配权重为摘要的 2 倍。纯文本处理，无需外部依赖。
    """
    if not keyword:
        return 0.0

    kw_tokens = set(re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", keyword.lower()))
    if not kw_tokens:
        return 0.0

    title_tokens = set(
        re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", paper.get("title", "").lower())
    )
    abstract_tokens = set(
        re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", paper.get("abstract", "").lower())
    )

    overlap_title = len(kw_tokens & title_tokens)
    overlap_abstract = len(kw_tokens & abstract_tokens)
    n = len(kw_tokens)

    if n == 0:
        return 0.0

    # 标题匹配权重 ×2，摘要匹配权重 ×1
    score = (overlap_title * 2 + overlap_abstract) / (n * 3)
    return round(min(1.0, score), 4)
