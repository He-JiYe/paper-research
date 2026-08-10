/* Paper Research SPA — 前端渲染与交互逻辑
 *
 * 动静分离：静态元数据（arxiv 分类/评级标签/颜色/分区标题）来自 /api/static，
 * 动态论文数据来自 /api/papers?range=...（全部/今日/近7天/近30天），
 * 均以 JSON 返回，前端动态建 DOM，无服务端模板。
 *
 * 前端只做：筛选（评级 + 日期范围 + 客户端全文搜索）、标记、批量导入 Zotero、排序。
 * 抓取由 serve 内置调度器每天定时完成：前端 SSE 订阅 /api/fetch/events 即时感知
 * 新抓取并刷新（连接时单次检查 /api/fetch/status 补基线；60s 轮询兜底防断流）。
 *
 * XSS 防护：所有 innerHTML 拼接处的标题/作者/摘要/LLM 输出等不可信内容
 * 一律经 escapeHtml() 转义。
 */

/* ─── 全局状态 ───────────────────────────── */
var state = {
    meta: null,
    papers: null,
    _currentRemark: 'all',
    _currentRange: 'all',
    _currentQuery: '',
};

/* ─── 初始化 ─────────────────────────────── */

async function init() {
    try {
        /* 评级颜色单一来源：app-meta → CSS 变量（style.css 用 var(--remark-*)） */
        var results = await Promise.all([
            fetch('/api/static').then(function (r) { return r.json(); }),
            fetch('/api/papers').then(function (r) { return r.json(); }),
        ]);
        state.meta = results[0];
        state.papers = results[1];
        applyMetaColors(state.meta.remark_colors);

        renderAll(state.papers);
        renderMetaLabels();

        /* 恢复上次评级筛选 + 日期范围 + 排序 */
        var remark = null;
        try { remark = localStorage.getItem('pr-remark'); } catch (e) {}
        if (remark && remark !== 'all') {
            var fb = document.getElementById('filter-btn-' + remark);
            if (fb) {
                document.querySelectorAll('.filter-btn').forEach(function (b) { b.classList.remove('active'); });
                fb.classList.add('active');
                state._currentRemark = remark;
            }
        }
        var range = null;
        try { range = localStorage.getItem('pr-range'); } catch (e) {}
        if (range && range !== 'all') {
            var rb = document.querySelector('.range-btn[data-range="' + range + '"]');
            if (rb) {
                document.querySelectorAll('.range-btn').forEach(function (b) { b.classList.remove('active'); });
                rb.classList.add('active');
                state._currentRange = range;
                await loadPapers();  /* 数据按恢复的范围重拉，而非停留在"全部" */
            }
        }
        var saved = null;
        try { saved = localStorage.getItem('pr-sort'); } catch (e) {}
        if (saved && saved !== 'default') {
            var sel = document.getElementById('sort-select');
            if (sel) { sel.value = saved; applySort(saved); }
        }
        applyFilters();
        updateThemeIcon();
        startAutoRefresh();
    } catch (e) {
        var list = document.getElementById('paper-list');
        if (list) list.innerHTML = '<div class="empty-state"><p><span class="empty-icon">⚠️</span>加载失败: ' + escapeHtml(e.message) + '</p></div>';
    }
}

/* 评级颜色注入 CSS 变量（--remark-*），style.css 与徽章统一引用 */
function applyMetaColors(colors) {
    if (!colors) return;
    var root = document.documentElement.style;
    Object.keys(colors).forEach(function (k) {
        root.setProperty('--remark-' + k, colors[k]);
    });
}

/* 局部刷新：按当前日期范围重新拉取论文并重渲染 */
async function loadPapers() {
    try {
        var url = '/api/papers';
        if (state._currentRange && state._currentRange !== 'all') {
            url += '?range=' + encodeURIComponent(state._currentRange);
        }
        var res = await fetch(url);
        state.papers = await res.json();
        renderAll(state.papers);
        applyFilters();
        var saved = null;
        try { saved = localStorage.getItem('pr-sort'); } catch (e) {}
        if (saved && saved !== 'default') applySort(saved);
    } catch (e) {
        var list = document.getElementById('paper-list');
        if (list) list.innerHTML = '<div class="empty-state"><p><span class="empty-icon">⚠️</span>刷新失败: ' + escapeHtml(e.message) + '</p></div>';
    }
}

/* 自动刷新：SSE 订阅 /api/fetch/events 为主路径（抓取完成即刷新）；
   连接/重连时单次检查 /api/fetch/status 补基线；另设 60s 轮询兜底，
   SSE 被网络/代理阻断（onopen 不触发）时页面仍能感知新抓取。 */
var _lastFetchSuccess = null;
var _fetchCheckedOnce = false;
async function _checkFetchStatus() {
    try {
        var res = await fetch('/api/fetch/status');
        var d = await res.json();
        var ts = d && d.last_success;
        if (!_fetchCheckedOnce) { _fetchCheckedOnce = true; _lastFetchSuccess = ts; return; }
        if (ts && ts !== _lastFetchSuccess) {
            _lastFetchSuccess = ts;
            await loadPapers();
        }
    } catch (e) {}
}
function startAutoRefresh() {
    /* 主路径：SSE。onopen 时建基线 / 重连补查；onmessage 收到 fetch-done 即刷新。 */
    try {
        var es = new EventSource('/api/fetch/events');
        es.onopen = function () { _checkFetchStatus(); };
        es.onmessage = async function (e) {
            var evt;
            try { evt = JSON.parse(e.data); } catch (err) { return; }
            if (evt.type === 'fetch-done') {
                if (evt.status === 'success' && evt.last_success) _lastFetchSuccess = evt.last_success;
                await loadPapers();
            }
        };
    } catch (e) {}
    /* 兜底：60s 轮询 status（SSE 失败时的保底刷新通道；_fetchCheckedOnce 防重复基线） */
    setInterval(_checkFetchStatus, 60000);
}

/* ─── 渲染 ───────────────────────────────── */

/* 整体重渲染：头部统计 + 分组列表（init 与 loadPapers 共用，单一入口） */
function renderAll(papers) {
    renderHeader(papers);
    renderStats(papers.stats);
    renderSections(papers.sections);
}

function renderHeader(p) {
    var ut = document.getElementById('update-time-text');
    if (ut) ut.textContent = '更新于 ' + (p.update_time || '--');
    var tc = document.getElementById('total-count-text');
    if (tc) tc.textContent = '共 ' + ((p.stats && p.stats.total) || 0) + ' 篇';
    var uc = document.getElementById('unmarked-count');
    if (uc) uc.textContent = (p.stats && p.stats.unmarked) || 0;
}

function renderStats(stats) {
    var map = {
        'stat-important': stats && stats.important || 0,
        'stat-useful': stats && stats.useful || 0,
        'stat-browse': stats && stats.browse || 0,
        'stat-unmarked': stats && stats.unmarked || 0,
    };
    for (var id in map) {
        if (!Object.prototype.hasOwnProperty.call(map, id)) continue;
        var el = document.getElementById(id);
        if (el) el.textContent = map[id];
    }
}

/* 评分分段阈值（设计常量，集中命名便于调整） */
var SCORE_TIER_TOP = 0.8;
var SCORE_TIER_HIGH = 0.65;
var SCORE_TIER_MID = 0.45;
function scoreTier(s) {
    return s >= SCORE_TIER_TOP ? 'top' : s >= SCORE_TIER_HIGH ? 'high' : s >= SCORE_TIER_MID ? 'mid' : 'low';
}

function renderSections(sections) {
    var container = document.getElementById('paper-list');
    var labels = (state.meta && state.meta.section_labels) || {};
    var order = ['unmarked', 'marked', 'lurk'];
    var html = '';
    var hasAny = false;
    for (var i = 0; i < order.length; i++) {
        var sec = order[i];
        var papers = (sections && sections[sec]) || [];
        if (!papers.length) continue;
        hasAny = true;
        html += '<div class="section" id="section-' + sec + '">';
        html += '<div class="section-header" onclick="toggleSection(\'' + sec + '\')">';
        html += '<span class="section-title">(' + papers.length + ') ' + escapeHtml(labels[sec] || sec) + '</span>';
        html += '<span class="section-toggle">[-]</span>';
        html += '</div><div class="section-body" id="section-body-' + sec + '">';
        for (var j = 0; j < papers.length; j++) {
            html += buildPaperCard(papers[j]);
        }
        html += '</div></div>';
    }
    if (!hasAny) html = '<div class="empty-state"><p><span class="empty-icon">📭</span>当前范围暂无论文</p></div>';
    container.innerHTML = html;
    syncBatchChecks();
}

/* 重渲染后恢复勾选状态；卡片已消失（被标记/导入）的从选中集合移除 */
function syncBatchChecks() {
    Object.keys(_batchSelected).forEach(function (key) {
        var card = document.getElementById('card-' + key);
        var cb = card && card.querySelector('.card-check');
        if (cb) { cb.checked = true; }
        else { delete _batchSelected[key]; }
    });
    updateBatchBar();
}

/* 构建单篇论文卡片。不可信字段（标题/作者/摘要/LLM 输出/URL 等）一律转义 */
function buildPaperCard(p) {
    var meta = state.meta || {};
    var labels = meta.remark_labels || {};
    var colors = meta.remark_colors || {};
    var source = p.source || '';  // 后端 source 恒非空；空只是防御兜底，不静默当 arxiv
    var sourceId = p.source_id || '';
    var cardKey = source + ':' + sourceId;
    var remark = p.llm_remark || 'browse';
    var label = labels[remark] || remark;
    var color = colors[remark] || '#999';
    var score = parseFloat(p.llm_score) || 0;
    var tier = scoreTier(score);
    var userMark = p.user_mark || '';
    var markCls = userMark || 'unmarked';
    /* 标题链接：直连 p.url / p.pdf_url（空值时纯文本，不做死链） */
    var url = p.url || p.pdf_url || '';
    var titleHtml = url
        ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' + escapeHtml(p.title || '') + '</a>'
        : '<span>' + escapeHtml(p.title || '') + '</span>';
    var pct = Math.round(score * 100);
    var published = (p.published || '').slice(0, 10);  // YYYY-MM-DD
    var fetchDate = p.fetch_date || '';
    var abstract = (p.abstract || '').slice(0, 500);
    var suggested = p.suggested_short_title || '';

    var html = '';
    html += '<div class="paper-card state-' + markCls + '" id="card-' + escapeHtml(cardKey) + '"';
    html += ' data-remark="' + escapeHtml(remark) + '" data-mark="' + escapeHtml(userMark) + '"';
    html += ' data-score="' + escapeHtml(String(score)) + '" data-published="' + escapeHtml(published);
    html += '" data-fetch="' + escapeHtml(fetchDate) + '" data-suggest="' + escapeHtml(suggested) + '"';
    html += ' data-keyword="' + escapeHtml(p.keyword_match || '') + '">';

    /* 头行：title 左对齐，评分徽章 + 勾选框右对齐 */
    html += '<div class="card-header">';
    html += '<h3 class="card-title">' + titleHtml + '</h3>';
    html += '<div class="card-meta">';
    html += '<span class="remark-badge" style="background:' + escapeHtml(color) + '">' + escapeHtml(label) + '</span>';
    html += '<span class="score-badge tier-' + tier + '" title="LLM 相关性评分">';
    if (score >= 0.9) html += '<span class="score-flame">🔥</span>';
    html += '<span class="score-bar"><span class="score-bar-fill" style="width: ' + pct + '%"></span></span>';
    html += '<span class="score-num">' + score.toFixed(2) + '</span></span>';
    if (userMark) {
        var markText = userMark;
        if (userMark === 'lurk') markText = '⏳ 延后';
        else if (userMark === 'ignore') markText = '🗑️ 忽略';
        else if (userMark === 'imported') markText = '📥 已导入';
        html += '<span class="mark-badge mark-' + markCls + '">' + escapeHtml(markText) + '</span>';
    }
    html += '</div>';
    /* 批量导入勾选框（未标记 + 延后处理的论文可勾选导入 Zotero）：title 一行最右 */
    if (!userMark || userMark === 'lurk') {
        html += '<label class="card-check-wrap" title="选择以批量导入 Zotero"><input type="checkbox" class="card-check" data-source="' + escapeHtml(source) + '" data-id="' + escapeHtml(sourceId) + '" onchange="onCardSelect(this)"></label>';
    }
    html += '</div>';

    html += '<div class="card-body">';
    html += '<div class="card-meta-chips">';
    html += '<span class="meta-chip" title="' + escapeHtml(p.authors || '') + '"><span class="chip-label">作者</span>' + escapeHtml(p.authors || '') + '</span>';
    html += '<span class="meta-chip" title="' + escapeHtml(p.keyword_match || '') + '"><span class="chip-label">关键词</span>' + escapeHtml(p.keyword_match || '') + '</span>';
    html += '<span class="meta-chip"><span class="chip-label">发布于</span>' + escapeHtml(published) + '</span>';
    html += '<span class="meta-chip"><span class="chip-label">来源</span>' + escapeHtml(source + ' / ' + sourceId) + '</span>';
    html += '</div>';
    if (p.llm_summary) html += '<div class="card-ai-summary"><span class="ai-badge">✦ AI 审阅</span><div>' + escapeHtml(p.llm_summary) + '</div></div>';
    if (p.llm_reason) html += '<div class="card-ai-reason">' + escapeHtml(p.llm_reason) + '</div>';
    html += '<details class="card-abstract-details"><summary>查看原文摘要</summary>';
    html += '<p class="card-abstract">' + escapeHtml(abstract) + '</p></details>';
    html += '</div>';

    html += '<div class="card-footer">';
    html += '<div class="mark-buttons">';
    html += '<button class="btn-mark btn-ignore" onclick="markPaper(\'' + escapeJs(source) + '\',\'' + escapeJs(sourceId) + '\',\'ignore\')"' + (userMark === 'ignore' ? ' disabled' : '') + '>🗑️ 忽略</button>';
    html += '<button class="btn-mark btn-lurk" onclick="markPaper(\'' + escapeJs(source) + '\',\'' + escapeJs(sourceId) + '\',\'lurk\')"' + (userMark === 'lurk' ? ' disabled' : '') + '>⏳ 延后</button>';
    html += '<button class="btn-mark btn-pending" onclick="markPaper(\'' + escapeJs(source) + '\',\'' + escapeJs(sourceId) + '\',\'pending\')"' + (userMark ? '' : ' disabled') + '>⏳ 待审核</button>';
    html += '</div>';
    html += '<div class="mark-result" id="result-' + escapeHtml(cardKey) + '"></div>';
    html += '</div></div>';
    return html;
}

/* 评级标签/分区标题单一来源：app-meta（/api/static）渲染统计区与筛选按钮 */
function renderMetaLabels() {
    var meta = state.meta || {};
    var labels = meta.remark_labels || {};
    var set = function (id, v) { var el = document.getElementById(id); if (el && v) el.textContent = v; };
    set('stat-label-important', labels.important);
    set('stat-label-useful', labels.useful);
    set('stat-label-browse', labels.browse);
    set('filter-btn-important', labels.important);
    set('filter-btn-useful', labels.useful);
    set('filter-btn-browse', labels.browse);
    set('filter-btn-skip', labels.skip);
    var section = meta.section_labels || {};
    /* app-meta 的 unmarked 是纯文字；统计框保留"⏳"图标前缀，与其他统计项风格一致 */
    var unmarkedLabel = section.unmarked || '待审核';
    if (unmarkedLabel.indexOf('⏳') !== 0) unmarkedLabel = '⏳ ' + unmarkedLabel;
    set('stat-label-unmarked', unmarkedLabel);
}

/* ─── 主题切换 ───────────────────────────── */
function toggleTheme() {
    var html = document.documentElement;
    var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    try { localStorage.setItem('pr-theme', next); } catch (e) {}
    updateThemeIcon();
}
function updateThemeIcon() {
    var icon = document.getElementById('theme-icon');
    if (icon) {
        icon.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙';
    }
}

async function markPaper(source, sourceId, markType) {
    var cardKey = source + ':' + sourceId;
    var resultEl = document.getElementById('result-' + cardKey);
    if (resultEl) resultEl.textContent = '标记中...';
    try {
        var params = new URLSearchParams();
        params.append('source', source);
        params.append('source_id', sourceId);
        params.append('mark_type', markType);
        var res = await fetch('/mark', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: params.toString(),
        });
        if (res.ok) {
            await loadPapers();  /* 局部刷新，论文从当前分区移走 */
        } else {
            if (resultEl) { resultEl.textContent = '标记失败'; resultEl.style.color = '#e74c3c'; }
        }
    } catch (e) {
        if (resultEl) { resultEl.textContent = '网络错误'; resultEl.style.color = '#e74c3c'; }
    }
}

/* ─── 筛选（评级 + 日期范围）+ 搜索 + 排序 ───── */

function filterByRemark(remark, el) {
    document.querySelectorAll('.filter-btn').forEach(function (b) { b.classList.remove('active'); });
    el.classList.add('active');
    state._currentRemark = remark;
    try { localStorage.setItem('pr-remark', remark); } catch (e) {}
    applyFilters();
}

async function applyRange(range, el) {
    document.querySelectorAll('.range-btn').forEach(function (b) { b.classList.remove('active'); });
    el.classList.add('active');
    state._currentRange = range;
    try { localStorage.setItem('pr-range', range); } catch (e) {}
    await loadPapers();
}

function onSearchInput(val) {
    state._currentQuery = (val || '').trim().toLowerCase();
    var clearBtn = document.getElementById('search-clear');
    if (clearBtn) clearBtn.classList.toggle('visible', !!state._currentQuery);
    applyFilters();
}

function clearSearch() {
    var input = document.getElementById('paper-search');
    if (input) { input.value = ''; }
    onSearchInput('');
    if (input) input.focus();
}

function applyFilters() {
    document.querySelectorAll('.paper-card').forEach(function (card) {
        var show = true;
        if (state._currentRemark !== 'all') {
            show = card.getAttribute('data-remark') === state._currentRemark;
        }
        if (show && state._currentQuery) {
            show = card.textContent.toLowerCase().indexOf(state._currentQuery) !== -1;
        }
        card.style.display = show ? '' : 'none';
    });
    updateSectionCounts();
}

function applySort(mode) {
    if (!mode) return;  // 未选排序 → 保持 DOM 顺序（默认渲染）
    try { localStorage.setItem('pr-sort', mode); } catch (e) {}
    document.querySelectorAll('.section-body').forEach(function (body) {
        var cards = Array.prototype.slice.call(body.querySelectorAll('.paper-card'));
        cards.sort(function (a, b) {
            if (mode === 'score') {
                return (parseFloat(b.dataset.score) || 0) - (parseFloat(a.dataset.score) || 0);
            }
            if (mode === 'published') {
                return (b.dataset.published || '').localeCompare(a.dataset.published || '');
            }
            if (mode === 'fetch') {
                return (b.dataset.fetch || '').localeCompare(a.dataset.fetch || '');
            }
            return 0;
        });
        cards.forEach(function (c) { body.appendChild(c); });
    });
}

function updateSectionCounts() {
    ['unmarked', 'marked', 'lurk'].forEach(function (sec) {
        var body = document.getElementById('section-body-' + sec);
        if (!body) return;
        var cards = body.querySelectorAll('.paper-card');
        var shown = 0;
        cards.forEach(function (c) { if (c.style.display !== 'none') shown++; });
        var titleEl = document.querySelector('#section-' + sec + ' .section-title');
        if (titleEl) {
            var txt = titleEl.textContent;
            titleEl.textContent = '(' + shown + ')' + txt.replace(/^\(\d+\)/, '');
        }
    });
}

function toggleSection(name) {
    var body = document.getElementById('section-body-' + name);
    if (body) {
        body.classList.toggle('collapsed');
        var toggle = document.querySelector('#section-' + name + ' .section-toggle');
        if (toggle) toggle.textContent = body.classList.contains('collapsed') ? '[+]' : '[-]';
    }
}

/* ─── Zotero 导入 ──────────────────────────── */

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/* 转义用于内联 onclick 双引号属性内的 JS 单引号字符串，防注入：
   & → &amp; 避免实体二次解码改变值；" → &quot; 避免提前终止 HTML 属性；
   \ → \\\\ 与 ' → \\' 避免提前终止 JS 字符串。 */
function escapeJs(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'");
}

function showImportBanner() {
    document.getElementById('import-banner').style.display = 'flex';
    /* 重置标题/日志：第二次导入时不残留上一次的「导入完成」 */
    document.getElementById('import-banner-title').textContent = '⏳ 导入中';
    document.getElementById('import-banner-log').innerHTML = '';
}

/* ─── 批量导入 Zotero（卡片勾选 → 批量面板 → import-batch）────── */

var _batchSelected = {};  // key: "source:source_id"
var _collectionsCache = [];  // 最近一次拉取的 Zotero 分类列表（自动推荐分类用）

/* 按论文 keyword_match 推荐最接近的分类：优先 path 末段==关键词，其次 path 包含关键词 */
function suggestCollection(keyword, collections) {
    if (!keyword) return '';
    var kw = keyword.trim().toLowerCase();
    if (!kw) return '';
    for (var i = 0; i < collections.length; i++) {
        var path = (collections[i].path || '');
        if (path.split(' / ').pop().toLowerCase() === kw) return path;
    }
    for (var j = 0; j < collections.length; j++) {
        var p2 = (collections[j].path || '');
        if (p2.toLowerCase().indexOf(kw) !== -1) return p2;
    }
    return '';
}

function onCardSelect(cb) {
    var source = cb.getAttribute('data-source');
    var id = cb.getAttribute('data-id');
    var key = source + ':' + id;
    var card = cb.closest('.paper-card');
    var titleEl = card && card.querySelector('.card-title');
    var title = titleEl ? titleEl.textContent || '' : '';
    var suggest = card ? (card.getAttribute('data-suggest') || '') : '';
    var keyword = card ? (card.getAttribute('data-keyword') || '') : '';
    if (cb.checked) {
        _batchSelected[key] = {
            source: source,
            source_id: id,
            title: title,
            suggested_short_title: suggest,
            keyword_match: keyword,
        };
    } else {
        delete _batchSelected[key];
    }
    updateBatchBar();
}

function updateBatchBar() {
    var n = Object.keys(_batchSelected).length;
    var btn = document.getElementById('batch-import-btn');
    var count = document.getElementById('batch-selected-count');
    if (btn) btn.disabled = n === 0;
    if (count) count.textContent = '已选 ' + n + ' 篇';
}

async function openBatchImport() {
    var keys = Object.keys(_batchSelected);
    if (!keys.length) return;
    var panel = document.getElementById('zotero-batch-panel');
    panel.style.display = 'flex';
    /* 每次打开都恢复确认按钮：上次导入成功后 disabled 残留会导致按钮不可点 */
    document.getElementById('zb-confirm').disabled = false;
    document.getElementById('zb-confirm').textContent = '确认导入 ' + keys.length + ' 条';
    document.getElementById('zb-status').textContent = '';
    /* 加载分类下拉（datalist 与单篇导入共用），缓存供自动推荐分类 */
    try {
        var res = await fetch('/api/zotero/collections');
        var data = await res.json();
        _collectionsCache = data.collections || [];
        var dl = document.getElementById('zi-collections');
        dl.innerHTML = '<option value=""></option>' + _collectionsCache.map(function (c) {
            return '<option value="' + escapeHtml(c.path || c.name) + '"></option>';
        }).join('');
    } catch (e) {
        document.getElementById('zb-status').textContent = '加载分类失败: ' + e.message;
    }
    renderBatchItems();
}

function renderBatchItems() {
    var itemsEl = document.getElementById('zb-items');
    var keys = Object.keys(_batchSelected);
    var html = '';
    keys.forEach(function (key, i) {
        var it = _batchSelected[key];
        var suggestedColl = suggestCollection(it.keyword_match, _collectionsCache);
        html += '<div class="zb-item">';
        html += '<div class="zb-item-head">';
        /* 叉号：从选中列表移除该条，无需回列表重新找卡片取消勾选 */
        html += '<button type="button" class="zb-item-remove" title="移除该条" onclick="removeBatchItem(\'' + escapeJs(key) + '\')">✕</button>';
        html += '<div class="zb-item-title">[' + escapeHtml(it.source_id) + '] ' + escapeHtml(it.title || '') + '</div>';
        html += '</div>';
        html += '<div class="zb-item-row">';
        html += '<input type="text" class="form-input" id="zb-st-' + i + '" value="' + escapeHtml(it.suggested_short_title || '') + '" placeholder="短标题">';
        html += '<input type="text" class="form-input" list="zi-collections" id="zb-coll-' + i + '" value="' + escapeHtml(suggestedColl) + '" placeholder="分类（可选，支持 A / B / C）">';
        html += '</div></div>';
    });
    itemsEl.innerHTML = html;
}

/* 从批量导入选中列表移除一条，并同步取消对应卡片复选框 */
function removeBatchItem(key) {
    delete _batchSelected[key];
    var card = document.getElementById('card-' + key);
    var cb = card && card.querySelector('.card-check');
    if (cb) cb.checked = false;
    var keys = Object.keys(_batchSelected);
    document.getElementById('zb-confirm').textContent = '确认导入 ' + keys.length + ' 条';
    if (!keys.length) {
        closeBatchImport();  // 全部移除 → 关闭面板
    } else {
        renderBatchItems();  // 重渲染保持输入框索引与 keys 对齐
    }
    updateBatchBar();
}

async function confirmBatchImport() {
    var keys = Object.keys(_batchSelected);
    if (!keys.length) return;
    var btn = document.getElementById('zb-confirm');
    var statusEl = document.getElementById('zb-status');
    btn.disabled = true;
    statusEl.textContent = '提交导入任务...';
    var items = keys.map(function (key, i) {
        var it = _batchSelected[key];
        return {
            source: it.source,
            source_id: it.source_id,
            short_title: document.getElementById('zb-st-' + i).value.trim(),
            collection_key: document.getElementById('zb-coll-' + i).value.trim(),
        };
    });
    try {
        var res = await fetch('/api/zotero/import-batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({items: items}),
        });
        if (res.status === 409) { statusEl.textContent = '已有导入任务进行中，请等待完成后再试'; btn.disabled = false; return; }
        if (!res.ok) { statusEl.textContent = '导入提交失败'; btn.disabled = false; return; }
        closeBatchImport();
        _batchSelected = {};
        updateBatchBar();
        showImportBanner();
        pollImportStatus();
    } catch (e) {
        statusEl.textContent = '导入失败: ' + e.message;
        btn.disabled = false;
    }
}

function closeBatchImport() {
    document.getElementById('zotero-batch-panel').style.display = 'none';
}

function hideImportBanner() {
    document.getElementById('import-banner').style.display = 'none';
}

/* 导入状态：先查一次 → 进行中则 SSE 等完成信号（无高频轮询） */
async function pollImportStatus() {
    var title = document.getElementById('import-banner-title');
    var logEl = document.getElementById('import-banner-log');

    /* 1) 先查一次：任务可能已秒完成 */
    var job = await _fetchImportJob();
    if (!job) { title.textContent = '无导入任务'; return; }
    _renderImportLog(title, logEl, job);
    if (job.status === 'done' || job.status === 'error') { await loadPapers(); return; }

    /* 2) 进行中：SSE 等完成信号（断线自动重连一次） */
    var retriedSse = false;
    function watchSse() {
        var es;
        try { es = new EventSource('/api/import/events'); } catch (e) { return; }
        es.onmessage = async function (e) {
            var evt;
            try { evt = JSON.parse(e.data); } catch (err) { return; }
            var latest = await _fetchImportJob();
            if (latest) _renderImportLog(title, logEl, latest);
            /* 终态判定：事件类型为终态，或最新拉取的状态已是 done/error 时收尾刷新。
               后者覆盖「轮询→SSE 连接」间隙任务已完成的竞态（服务端只发一条 status
               兜底事件即关流，事件类型非终态，但状态已是终态） */
            var done = evt.type === 'import-done' || evt.type === 'timeout' || evt.type === 'error';
            if (!done && latest) done = (latest.status === 'done' || latest.status === 'error');
            if (done) {
                es.close();
                await loadPapers();  /* 刷新：已处理区出现该论文 */
            }
        };
        es.onerror = function () {
            try { es.close(); } catch (err) {}
            if (!retriedSse) { retriedSse = true; setTimeout(watchSse, 2000); }
        };
    }
    watchSse();
}

async function _fetchImportJob() {
    try {
        var res = await fetch('/api/zotero/import/status');
        var data = await res.json();
        return data.job || null;
    } catch (e) {
        return null;
    }
}

/* 手动打开导入记录框（隐藏后可重新打开，拉最新状态） */
async function openImportHistory() {
    showImportBanner();
    var title = document.getElementById('import-banner-title');
    var logEl = document.getElementById('import-banner-log');
    var job = await _fetchImportJob();
    if (job) {
        _renderImportLog(title, logEl, job);
    } else {
        title.textContent = '暂无导入记录';
        logEl.innerHTML = '<div class="log-info">（还没有导入记录）</div>';
    }
}

function _renderImportLog(title, logEl, job) {
    if (!job) { title.textContent = '导入状态不可用'; return; }
    var itemsStatus = job.items_status || {};
    var ids = Object.keys(itemsStatus);
    var html = '';
    if (ids.length) {
        ids.forEach(function (sid) {
            var st = itemsStatus[sid] || {};
            var icon = st.item === 'imported' ? '✅' : st.item === 'skipped' ? '⏭️' : st.item === 'failed' ? '❌' : '⏳';
            var txt = st.item === 'imported' ? '条目已导入' : st.item === 'skipped' ? '已在 Zotero，已标记处理' : st.item === 'failed' ? '导入失败' : '处理中';
            html += '<div class="log-row">' + icon + ' <span class="log-time">' + escapeHtml(sid) + '</span> ' + txt + '</div>';
        });
    }
    if (!html) html = '<div class="log-info">（无日志）</div>';
    logEl.innerHTML = html;
    logEl.scrollTop = logEl.scrollHeight;
    if (job.status === 'done') {
        title.textContent = '✅ 导入完成';
    } else if (job.status === 'error') {
        title.textContent = '❌ 导入失败: ' + escapeHtml(job.error || '');
    } else {
        title.textContent = '⏳ 导入中';
    }
}

/* 模态框点击遮罩关闭 */
document.querySelectorAll('.modal-overlay').forEach(function (overlay) {
    overlay.addEventListener('mousedown', function (e) {
        if (e.target === overlay) { overlay.style.display = 'none'; }
    });
});
/* ESC 关闭模态框 */
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay').forEach(function (o) { o.style.display = 'none'; });
    }
});

/* ─── 启动 ───────────────────────────────── */
init();
