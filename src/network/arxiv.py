"""网络层：Arxiv API 查询 + PDF 下载

职责：
- Arxiv API：多关键词并发查询、Atom XML 解析、版本检测、搜索、按 ID 获取
- PDF 下载：从 arxiv.org 下载论文 PDF
- 不包含任何业务逻辑或 LLM 调用
"""

import asyncio
import re
from datetime import UTC
from pathlib import Path
from xml.etree import ElementTree

import httpx

ARXIV_API_BASE = "https://export.arxiv.org/api/query"
DEFAULT_USER_AGENT = "PaperResearch/1.0"


# ─── 辅助 ──────────────────────────────────────────────────


def _get_user_agent(settings) -> str:
    """从 settings 或环境变量读取 User-Agent"""
    if hasattr(settings, "arxiv") and hasattr(settings.arxiv, "user_agent"):
        return settings.arxiv.user_agent
    return DEFAULT_USER_AGENT


# ─── Atom XML 解析 ─────────────────────────────────────────


def _parse_atom(xml_text: str, keyword: str = "") -> list[dict]:
    """
    解析 Arxiv API 返回的 Atom XML。

    Arxiv API 返回结构:
      <feed>
        <entry>
          <id>http://arxiv.org/abs/2401.00001v2</id>
          <title>Paper Title</title>
          <summary>Abstract text...</summary>
          <author><name>Author Name</name></author>
          <published>2024-01-01T00:00:00Z</published>
          <updated>2024-01-05T00:00:00Z</updated>
          <arxiv:primary_category scheme="..." term="cs.AI"/>
          <category scheme="..." term="cs.AI"/>
          <category scheme="..." term="cs.CL"/>
        </entry>
      </feed>
    """
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
        "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    }

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        print(f"  ⚠️ XML 解析失败: {e}")
        return []

    papers = []
    for entry in root.findall("atom:entry", ns):
        id_el = entry.find("atom:id", ns)
        if id_el is None:
            continue
        raw_id = id_el.text or ""
        arxiv_id_match = re.search(r"(\d{4}\.\d{4,5})", raw_id)
        if not arxiv_id_match:
            continue
        arxiv_id = arxiv_id_match.group(1)

        version_match = re.search(r"v(\d+)$", raw_id)
        version = int(version_match.group(1)) if version_match else 1

        title_el = entry.find("atom:title", ns)
        title = (
            (title_el.text or "").strip().replace("\n", " ")
            if title_el is not None
            else ""
        )

        summary_el = entry.find("atom:summary", ns)
        abstract = (
            (summary_el.text or "").strip().replace("\n", " ")
            if summary_el is not None
            else ""
        )

        authors = []
        for author_el in entry.findall("atom:author", ns):
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())
        authors_str = ", ".join(authors)

        published_el = entry.find("atom:published", ns)
        published = (published_el.text or "")[:10] if published_el is not None else ""

        updated_el = entry.find("atom:updated", ns)
        arxiv_updated = (updated_el.text or "") if updated_el is not None else ""

        primary_cat = ""
        primary_el = entry.find("arxiv:primary_category", ns)
        if primary_el is not None:
            primary_cat = primary_el.get("term", "")

        categories = []
        for cat_el in entry.findall("atom:category", ns):
            cat_term = cat_el.get("term", "")
            if cat_term:
                categories.append(cat_term)
        categories_str = ", ".join(categories)

        url = f"https://arxiv.org/abs/{arxiv_id}"

        papers.append(
            {
                "arxiv_id": arxiv_id,
                "version": version,
                "title": title,
                "authors": authors_str,
                "abstract": abstract,
                "url": url,
                "primary_category": primary_cat,
                "categories": categories_str,
                "published": published,
                "arxiv_updated": arxiv_updated,
                "keyword_match": keyword,
            }
        )

    return papers


# ─── 关键词查询 ────────────────────────────────────────────


async def fetch_keyword(
    client: httpx.AsyncClient,
    keyword: str,
    arxiv_cats: list[str] | None = None,
    max_results: int = 50,
    lookback_days: int = 30,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """异步查询单个关键词，支持分类筛选和日期范围。

    Args:
        client: httpx 客户端
        keyword: 搜索关键词
        arxiv_cats: Arxiv 分类列表
        max_results: 最大结果数
        lookback_days: 回溯天数（date_from/date_to 未指定时使用）
        date_from: 起始日期 (YYYYMMDD)，覆盖 lookback_days
        date_to: 结束日期 (YYYYMMDD)，默认当天
    """
    if date_from is None:
        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        since = now - timedelta(days=lookback_days)
        date_from = since.strftime("%Y%m%d")
        date_to = now.strftime("%Y%m%d")
    elif date_to is None:
        from datetime import datetime

        date_to = datetime.now(UTC).strftime("%Y%m%d")

    date_filter = f"+AND+submittedDate:[{date_from}+TO+{date_to}]"

    if arxiv_cats and len(arxiv_cats) > 0:
        cat_part = "+OR+".join(f"cat:{c}" for c in arxiv_cats)
        query = f"({cat_part})+AND+all:{keyword}{date_filter}"
    else:
        query = f"all:{keyword}{date_filter}"

    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    response = await client.get(ARXIV_API_BASE, params=params, timeout=30)
    response.raise_for_status()

    papers = _parse_atom(response.text, keyword=keyword)
    return papers


async def fetch_all(
    keywords: list[dict],
    settings,
    per_keyword_max_results: dict[str, int] | None = None,
    per_keyword_date_from: dict[str, str] | None = None,
    per_keyword_date_to: dict[str, str] | None = None,
) -> tuple[list[dict], int, int]:
    """
    并发查询所有活跃关键词。

    Args:
        keywords: 关键词列表，每项含 keyword, arxiv_cat(可选), active
        settings: AppConfig 对象
        per_keyword_max_results: 可选 keyword->max_results 覆盖字典
        per_keyword_date_from: 可选 keyword->date_from 覆盖（YYYYMMDD）
        per_keyword_date_to: 可选 keyword->date_to 覆盖（YYYYMMDD）

    Returns:
        (去重后的论文列表, 总篇数, 重复篇数)
    """
    arxiv_cfg = settings.arxiv
    max_results = arxiv_cfg.target_new_per_keyword * 2  # 默认每关键词抓取量
    max_conns = arxiv_cfg.max_concurrent_requests
    lookback_days = arxiv_cfg.lookback_days
    user_agent = _get_user_agent(settings)

    active_kws = [kw for kw in keywords if kw.get("active", True)]

    if not active_kws:
        print("  [!] No active keywords")
        return [], 0, 0

    async with httpx.AsyncClient(
        headers={"User-Agent": user_agent},
        limits=httpx.Limits(max_connections=max_conns),
        follow_redirects=True,
        timeout=30,
    ) as client:
        tasks = []
        for kw in active_kws:
            kw_name = kw["keyword"]
            if per_keyword_max_results and kw_name in per_keyword_max_results:
                kw_max = per_keyword_max_results[kw_name]
            else:
                kw_max = kw.get("max_results", max_results)
            kw_cats = kw.get("arxiv_cats") or (
                [kw["arxiv_cat"]] if kw.get("arxiv_cat") else None
            )
            kw_date_from = (
                per_keyword_date_from.get(kw_name) if per_keyword_date_from else None
            )
            kw_date_to = (
                per_keyword_date_to.get(kw_name) if per_keyword_date_to else None
            )
            tasks.append(
                fetch_keyword(
                    client,
                    kw_name,
                    kw_cats,
                    kw_max,
                    lookback_days,
                    date_from=kw_date_from,
                    date_to=kw_date_to,
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_papers = []
    for i, result in enumerate(results):
        kw = active_kws[i]
        if isinstance(result, Exception):
            err_msg = str(result) or f"{type(result).__name__} (no detail)"
            print(f"  ⚠️ 关键词 [{kw['keyword']}] 抓取失败: {err_msg}")
            continue
        print(f"  ✅ [{kw['keyword']}]: {len(result)} 篇")
        all_papers.extend(result)

    # 去重（同一篇论文可能命中多个关键词）
    seen = set()
    unique = []
    for p in all_papers:
        if p["arxiv_id"] not in seen:
            seen.add(p["arxiv_id"])
            unique.append(p)

    duplicate_count = len(all_papers) - len(unique)
    if duplicate_count:
        print(
            f"  🔗 去重: {len(all_papers)} → {len(unique)} (去重 {duplicate_count} 篇)"
        )

    return unique, len(all_papers), duplicate_count


# ─── 搜索（无日期限制）─────────────────────────────────────


async def search_arxiv(
    query: str,
    max_results: int = 20,
    categories: list[str] | None = None,
) -> list[dict]:
    """
    搜索 Arxiv（无日期限制），支持 ti:/au:/all: 等前缀。
    不写入数据库，仅用于前端搜索预览。
    """
    cat_part = ""
    if categories and len(categories) > 0:
        cat_part = "+AND+(" + "+OR+".join(f"cat:{c}" for c in categories) + ")"

    search_query = (
        f"all:{query}{cat_part}"
        if not any(query.startswith(p) for p in ("ti:", "au:", "all:", "cat:"))
        else f"{query}{cat_part}"
    )

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": min(max_results, 100),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(ARXIV_API_BASE, params=params)
        response.raise_for_status()
        papers = _parse_atom(response.text, keyword=query)

    return papers


# ─── 按 ID 获取 ────────────────────────────────────────────


async def fetch_by_ids(arxiv_ids: list[str]) -> list[dict]:
    """通过 arxiv_id 列表精确获取论文元数据（使用 id_list 参数）。"""
    ids_str = ",".join(arxiv_ids)
    params = {"id_list": ids_str}

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(ARXIV_API_BASE, params=params)
        response.raise_for_status()
        papers = _parse_atom(response.text, keyword="")

    return papers


# ─── PDF 下载 ──────────────────────────────────────────────


async def download_pdf(arxiv_id: str, output_dir: Path) -> Path:
    """下载论文 PDF。

    Args:
        arxiv_id: ArXiv ID
        output_dir: 输出根目录（output/）

    Returns:
        PDF 文件路径
    """
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
    pdf_dir = output_dir / "notes" / arxiv_id
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / "paper.pdf"

    # 如果已存在且大小合理，跳过下载
    if pdf_path.exists() and pdf_path.stat().st_size > 10000:
        return pdf_path

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        response = await client.get(pdf_url)
        response.raise_for_status()
        with open(pdf_path, "wb") as f:
            f.write(response.content)

    file_size_kb = pdf_path.stat().st_size / 1024
    print(f"    📥 PDF 下载完成: {file_size_kb:.0f} KB")
    return pdf_path
