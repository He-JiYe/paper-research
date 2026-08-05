/* Paper Research SPA — 前端渲染与交互逻辑
 *
 * 动静分离：静态元数据（arxiv 分类/评级标签/颜色/分区标题）来自 /api/static，
 * 动态论文数据来自 /api/papers，均以 JSON 返回，前端动态建 DOM，无服务端模板。
 *
 * XSS 防护：移除 Jinja2 autoescape 后，所有 innerHTML 拼接处的
 * 标题/作者/摘要/LLM 输出等不可信内容一律经 escapeHtml() 转义。
 */

/* ─── 全局状态 ───────────────────────────── */
var state = {
    meta: null,
    papers: null,
    _currentRemark: 'all',
    _currentQuery: '',
    _zoteroArxivId: '',
};

/* ─── 初始化 ─────────────────────────────── */

async function init() {
    try {
        var results = await Promise.all([
            fetch('/api/static').then(function (r) { return r.json(); }),
            fetch('/api/papers').then(function (r) { return r.json(); }),
        ]);
        state.meta = results[0];
        state.papers = results[1];
        renderHeader(state.papers);
        renderStats(state.papers.stats);
        renderSections(state.papers.sections);
        renderCatCheckboxes(state.meta.arxiv_cats);
        initFetchConfig(state.papers.fetch_config);
        applyFilters();
        /* 恢复上次排序选择 */
        var saved = null;
        try { saved = localStorage.getItem('pr-sort'); } catch (e) {}
        if (saved && saved !== 'default') {
            var sel = document.getElementById('sort-select');
            if (sel) { sel.value = saved; applySort(saved); }
        }
        updateThemeIcon();
    } catch (e) {
        var list = document.getElementById('paper-list');
        if (list) list.innerHTML = '<div class="empty-state"><p>加载失败: ' + escapeHtml(e.message) + '</p></div>';
    }
}

/* 局部刷新：重新拉取论文数据并重渲染（不再整页 reload） */
async function loadPapers() {
    try {
        var res = await fetch('/api/papers');
        state.papers = await res.json();
        renderHeader(state.papers);
        renderStats(state.papers.stats);
        renderSections(state.papers.sections);
        applyFilters();
        var saved = null;
        try { saved = localStorage.getItem('pr-sort'); } catch (e) {}
        if (saved && saved !== 'default') applySort(saved);
    } catch (e) {
        var list = document.getElementById('paper-list');
        if (list) list.innerHTML = '<div class="empty-state"><p>刷新失败: ' + escapeHtml(e.message) + '</p></div>';
    }
}

/* ─── 渲染 ───────────────────────────────── */

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

function scoreTier(s) { return s >= 0.8 ? 'top' : s >= 0.65 ? 'high' : s >= 0.45 ? 'mid' : 'low'; }

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
    if (!hasAny) html = '<div class="empty-state"><p>📭 暂无待审核论文</p></div>';
    container.innerHTML = html;
}

/* 构建单篇论文卡片。不可信字段（标题/作者/摘要/LLM 输出/URL 等）一律转义 */
function buildPaperCard(p) {
    var meta = state.meta || {};
    var labels = meta.remark_labels || {};
    var colors = meta.remark_colors || {};
    var arxivId = p.arxiv_id || '';
    var remark = p.llm_remark || 'browse';
    var label = labels[remark] || remark;
    var color = colors[remark] || '#999';
    var score = parseFloat(p.llm_score) || 0;
    var tier = scoreTier(score);
    var userMark = p.user_mark || '';
    var markCls = userMark.replace(/_/g, '-') || 'unmarked';
    var url = p.url || ('https://arxiv.org/abs/' + arxivId);
    var pct = Math.round(score * 100);
    var published = p.published || '';
    var fetchDate = p.fetch_date || '';
    var abstract = (p.abstract || '').slice(0, 500);

    var html = '';
    html += '<div class="paper-card state-' + markCls + '" id="card-' + escapeHtml(arxivId) + '"';
    html += ' data-remark="' + escapeHtml(remark) + '" data-mark="' + escapeHtml(userMark) + '"';
    html += ' data-score="' + escapeHtml(String(score)) + '" data-published="' + escapeHtml(published);
    html += '" data-fetch="' + escapeHtml(fetchDate) + '">';

    html += '<div class="card-header">';
    html += '<h3 class="card-title"><a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">';
    html += '<span class="arxiv-id">[' + escapeHtml(arxivId) + ']</span> ' + escapeHtml(p.title || '') + '</a></h3>';
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
    html += '</div></div>';

    html += '<div class="card-body">';
    html += '<div class="card-authors"><strong>Authors:</strong> ' + escapeHtml(p.authors || '') + '</div>';
    html += '<div class="card-keyword"><strong>Keyword:</strong> ' + escapeHtml(p.keyword_match || '') + '</div>';
    html += '<div class="card-published"><strong>Published:</strong> ' + escapeHtml(published) + '</div>';
    if (p.llm_summary) html += '<div class="card-ai-summary"><strong>AI Summary:</strong> ' + escapeHtml(p.llm_summary) + '</div>';
    if (p.llm_reason) html += '<div class="card-ai-reason"><strong>Reason:</strong> ' + escapeHtml(p.llm_reason) + '</div>';
    html += '<details class="card-abstract-details"><summary>查看原文摘要</summary>';
    html += '<p class="card-abstract">' + escapeHtml(abstract) + '</p></details>';
    html += '</div>';

    html += '<div class="card-footer">';
    if (!userMark) {
        var suggested = p.suggested_short_title || '';
        html += '<div class="card-short-title"><input type="text" id="st-' + escapeHtml(arxivId) + '"';
        html += ' value="' + escapeHtml(suggested) + '" placeholder="短标题（导入 Zotero 时使用）" class="short-title-input"></div>';
    }
    html += '<div class="mark-buttons">';
    html += '<button class="btn-mark btn-ignore" onclick="markPaper(\'' + escapeHtml(arxivId) + '\', \'ignore\')"' + (userMark === 'ignore' ? ' disabled' : '') + '>🗑️ 忽略</button>';
    html += '<button class="btn-mark btn-lurk" onclick="markPaper(\'' + escapeHtml(arxivId) + '\', \'lurk\')"' + (userMark === 'lurk' ? ' disabled' : '') + '>⏳ 延后</button>';
    html += '<button class="btn-mark btn-pending" onclick="markPaper(\'' + escapeHtml(arxivId) + '\', \'pending\')"' + (userMark ? '' : ' disabled') + '>⏳ 待审核</button>';
    html += '<button class="btn-mark btn-zotero" onclick="showZoteroImport(\'' + escapeHtml(arxivId) + '\')">📚 Zotero</button>';
    html += '</div>';
    html += '<div class="mark-result" id="result-' + escapeHtml(arxivId) + '"></div>';
    html += '</div></div>';
    return html;
}

function renderCatCheckboxes(cats) {
    var box = document.getElementById('cat-checkboxes');
    if (!box || !cats) return;
    var html = '';
    for (var i = 0; i < cats.length; i++) {
        html += '<label><input type="checkbox" name="arxiv_cats" value="' + escapeHtml(cats[i]) + '"> ' + escapeHtml(cats[i]) + '</label>';
    }
    box.innerHTML = html;
}

function initFetchConfig(fc) {
    if (!fc) return;
    var setVal = function (id, v) { var el = document.getElementById(id); if (el && v != null) el.value = v; };
    var setText = function (id, v) { var el = document.getElementById(id); if (el && v != null) el.textContent = v; };
    setVal('fetch-max', fc.max_results);
    setVal('kw-target-new', fc.max_results);
    setVal('kw-lookback-days', fc.lookback_days);
    setText('cfg-lb-days', fc.lookback_days);
    setText('fetch-lb-days', fc.lookback_days);
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

/* SSE */
function waitFetchResult(callback) {
    var evtSource = new EventSource('/api/fetch-status-stream');
    evtSource.onmessage = function (event) {
        evtSource.close();
        callback(JSON.parse(event.data));
    };
    evtSource.onerror = function () { evtSource.close(); };
}

function showFetchModal() { document.getElementById('fetch-modal').style.display = ''; }
function closeFetchModal() { document.getElementById('fetch-modal').style.display = 'none'; }

async function doRefresh() {
    await loadPapers();
}

async function doPush() {
    var btn = event && event.target;
    if (btn) { btn.disabled = true; btn.textContent = '推送中...'; }
    try {
        await fetch('/api/push', {method: 'POST'});
        alert('推送完成，请查看邮箱');
    } catch (e) {
        alert('推送失败: ' + e.message);
    }
    if (btn) { btn.disabled = false; btn.textContent = '📧 推送'; }
}

async function markPaper(arxivId, markType) {
    var resultEl = document.getElementById('result-' + arxivId);
    if (resultEl) resultEl.textContent = '标记中...';
    try {
        var params = new URLSearchParams();
        params.append('arxiv_id', arxivId);
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

/* ─── 筛选 + 搜索 + 排序 ───────────────────── */

function filterByRemark(remark, el) {
    document.querySelectorAll('.filter-btn').forEach(function (b) { b.classList.remove('active'); });
    (el || event.target).classList.add('active');
    state._currentRemark = remark;
    applyFilters();
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
        if (state._currentRemark === 'unmarked') {
            show = !card.getAttribute('data-mark');
        } else if (state._currentRemark !== 'all') {
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
    try { localStorage.setItem('pr-sort', mode); } catch (e) {}
    if (mode === 'default') return;
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

async function showZoteroImport(arxivId) {
    state._zoteroArxivId = arxivId;
    var panel = document.getElementById('zotero-import-panel');
    panel.style.display = 'flex';
    document.getElementById('zi-title').value =
        (document.getElementById('st-' + arxivId) || {}).value || '';
    document.getElementById('zi-status').textContent = '加载分类中...';
    try {
        var res = await fetch('/api/zotero/collections');
        var data = await res.json();
        var sel = document.getElementById('zi-collection');
        sel.innerHTML = '<option value="">-- 不指定分类 --</option>' +
            (data.collections || []).map(function (c) {
                return '<option value="' + escapeHtml(c.key) + '">' + escapeHtml(c.path || c.name) + '</option>';
            }).join('');
        document.getElementById('zi-status').textContent = '';
    } catch (e) {
        document.getElementById('zi-status').textContent = '加载分类失败: ' + e.message;
    }
}

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function closeZoteroImport() {
    document.getElementById('zotero-import-panel').style.display = 'none';
}

async function confirmZoteroImport() {
    var statusEl = document.getElementById('zi-status');
    statusEl.textContent = '导入中...';
    try {
        var params = new URLSearchParams();
        params.append('arxiv_id', state._zoteroArxivId);
        params.append('collection_key', document.getElementById('zi-collection').value);
        params.append('short_title', document.getElementById('zi-title').value.trim());
        var res = await fetch('/api/zotero/import', {method: 'POST', body: params});
        if (!res.ok) { statusEl.textContent = '导入失败'; return; }
        var result = await res.json();
        statusEl.textContent = result.pdf_attached
            ? '✅ 已导入 Zotero（含 PDF 附件），刷新中...'
            : '✅ 已导入 Zotero（PDF 附件未上传），刷新中...';
        closeZoteroImport();
        await loadPapers();  /* 局部刷新：已处理区出现该论文 */
    } catch (e) {
        statusEl.textContent = '导入失败: ' + e.message;
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

/* ─── Tab 切换 ───────────────────────────── */
function switchFetchTab(tab) {
    document.querySelectorAll('.ftab').forEach(function (btn) { btn.classList.remove('active'); });
    var activeBtn = document.getElementById('ftab-' + tab);
    if (activeBtn) { activeBtn.classList.add('active'); }
    document.getElementById('fpanel-keyword').style.display = tab === 'keyword' ? '' : 'none';
    document.getElementById('fpanel-search').style.display = tab === 'search' ? '' : 'none';
    document.getElementById('fpanel-keywords').style.display = tab === 'keywords' ? '' : 'none';
    if (tab === 'keywords') { loadKeywords(); }
}

/* ─── 关键词抓取 Tab ─────────────────────── */
function toggleFetchModeHint() {
    var mode = document.querySelector('input[name="fetch-mode"]:checked');
    var lbSpan = document.getElementById('fetch-lb-days');
    var lbDays = lbSpan ? lbSpan.textContent : (state.papers && state.papers.fetch_config && state.papers.fetch_config.lookback_days);
    lbSpan.textContent = mode && mode.value === 'incremental' ? lbDays : '全量';
}

async function doFetch() {
    var btn = event ? event.target : arguments.callee;
    btn.disabled = true;
    btn.textContent = '抓取中...';
    var resultEl = document.getElementById('fetch-result');
    resultEl.textContent = '开始抓取...';
    var defaultMax = (state.papers && state.papers.fetch_config && state.papers.fetch_config.max_results) || 20;
    try {
        var params = new URLSearchParams();
        var kw = document.getElementById('fetch-keyword').value.trim();
        if (kw) params.append('keyword', kw);
        params.append('max_results', document.getElementById('fetch-max').value || defaultMax);
        var mode = document.querySelector('input[name="fetch-mode"]:checked');
        params.append('mode', mode ? mode.value : 'incremental');
        var res = await fetch('/api/fetch', {method: 'POST', body: params});
        if (!res.ok) { resultEl.textContent = '请求失败'; btn.disabled = false; btn.textContent = '开始抓取'; return; }
        resultEl.textContent = '等待结果...';
        waitFetchResult(function (data) {
            resultEl.textContent = '抓取完成: 获取 ' + (data.fetched || 0) + ' 篇, 新增 ' + (data.new || 0) + ' 篇';
            btn.disabled = false;
            btn.textContent = '开始抓取';
            loadPapers();
        });
    } catch (e) {
        resultEl.textContent = '网络错误: ' + e.message;
        btn.disabled = false;
        btn.textContent = '开始抓取';
    }
}

/* ─── 搜索 Tab ───────────────────────────── */
var _searchQuery = '';

async function doSearch() {
    var query = document.getElementById('search-query').value.trim();
    if (!query) return;
    _searchQuery = query;
    var resultsEl = document.getElementById('search-results');
    resultsEl.innerHTML = '<div class="search-empty">搜索中...</div>';
    document.getElementById('import-btn').disabled = true;
    document.getElementById('import-btn').textContent = '导入选中 (0 篇)';
    var defaultMax = (state.papers && state.papers.fetch_config && state.papers.fetch_config.max_results) || 20;
    try {
        var params = new URLSearchParams();
        params.append('query', query);
        params.append('max_results', document.getElementById('fetch-max').value || defaultMax);
        var res = await fetch('/api/search-preview', {method: 'POST', body: params});
        var data = await res.json();
        var papers = data.papers || [];
        if (!papers.length) {
            resultsEl.innerHTML = '<div class="search-empty">未找到结果</div>';
            return;
        }
        var html = '';
        papers.forEach(function (p, i) {
            var status = '';
            if (p._in_zotero) status = '<span class="status-ok">✓ 已在 Zotero</span>';
            else if (p._in_pending) status = '<span class="status-pending">⏳ 已在待审阅</span>';
            else status = '<span class="status-new">● 新</span>';
            html += '<div class="search-item" onclick="toggleSearchItem(this)">';
            html += '<div class="search-item-row">';
            html += '<input type="checkbox" class="search-check" value="' + escapeHtml(p.arxiv_id) + '" data-title="' + escapeHtml(p.title || '') + '">';
            html += '<div style="flex:1;">';
            html += '<div class="search-item-title"><a href="https://arxiv.org/abs/' + escapeHtml(p.arxiv_id) + '" target="_blank">[' + escapeHtml(p.arxiv_id) + '] ' + escapeHtml(p.title || '') + '</a></div>';
            html += '<div class="search-item-authors">' + escapeHtml((p.authors || '').substring(0, 120)) + '</div>';
            html += '<div class="search-item-status">' + status + '</div>';
            html += '</div></div></div>';
        });
        resultsEl.innerHTML = html;
        document.getElementById('import-btn').disabled = false;
        updateImportCount();
    } catch (e) {
        resultsEl.innerHTML = '<div class="search-empty search-error">搜索失败: ' + escapeHtml(e.message) + '</div>';
    }
}

function toggleSearchItem(el) {
    var cb = el.querySelector('.search-check');
    if (cb) { cb.checked = !cb.checked; }
    updateImportCount();
}

function updateImportCount() {
    var checked = document.querySelectorAll('.search-check:checked').length;
    var btn = document.getElementById('import-btn');
    btn.textContent = '导入选中 (' + checked + ' 篇)';
    btn.disabled = checked === 0;
}

async function doImport() {
    var checked = document.querySelectorAll('.search-check:checked');
    if (!checked.length) return;
    var arxivIds = Array.from(checked).map(function (cb) { return cb.value; });
    var btn = document.getElementById('import-btn');
    btn.disabled = true;
    btn.textContent = '导入中...';
    var statusEl = document.getElementById('import-status');
    statusEl.textContent = '导入中...';
    try {
        var res = await fetch('/api/search-import', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({arxiv_ids: arxivIds}),
        });
        var data = await res.json();
        statusEl.textContent = '导入完成: 新增 ' + (data.imported || 0) + ' 篇';
        btn.textContent = '导入选中 (0 篇)';
        await doSearch();  /* 刷新搜索结果 */
        await loadPapers();
    } catch (e) {
        statusEl.textContent = '导入失败: ' + e.message;
        btn.disabled = false;
        btn.textContent = '导入选中 (' + checked.length + ' 篇)';
    }
}

/* ─── 关键词设置 Tab ─────────────────────── */
var _kwData = [];

async function loadKeywords() {
    var listEl = document.getElementById('kw-list');
    listEl.innerHTML = '<div class="search-empty">加载中...</div>';
    try {
        var res = await fetch('/api/keywords');
        if (!res.ok) { listEl.innerHTML = '<div class="search-empty search-error">加载失败</div>'; return; }
        var data = await res.json();
        _kwData = data.keywords || [];
        renderKeywords();
        _applyFetchConfig(data.fetch_config);
    } catch (e) {
        listEl.innerHTML = '<div class="search-empty search-error">网络错误</div>';
    }
}

/* 同步 fetch 配置到 UI（loadKeywords / reloadConfig 共用） */
function _applyFetchConfig(fc) {
    if (!fc) return;
    if (fc.max_results) {
        document.getElementById('kw-target-new').value = fc.max_results;
        document.getElementById('fetch-max').value = fc.max_results;
    }
    if (fc.lookback_days) {
        document.getElementById('kw-lookback-days').value = fc.lookback_days;
        document.getElementById('cfg-lb-days').textContent = fc.lookback_days;
        document.getElementById('fetch-lb-days').textContent = fc.lookback_days;
    }
}

function renderKeywords() {
    var listEl = document.getElementById('kw-list');
    if (!_kwData.length) {
        listEl.innerHTML = '<div class="search-empty">暂无关键词</div>';
        return;
    }
    var html = '';
    _kwData.forEach(function (kw, i) {
        var cats = (kw.arxiv_cats || []).join(', ');
        html += '<div class="kw-item">';
        html += '<div><span class="kw-item-keyword">' + escapeHtml(kw.keyword) + '</span>';
        if (cats) html += '<span class="kw-item-cats">' + escapeHtml(cats) + '</span>';
        html += '</div>';
        html += '<button onclick="removeKeyword(' + i + ')" class="kw-remove" title="删除">×</button>';
        html += '</div>';
    });
    listEl.innerHTML = html;
}

function addKeywordItem() {
    var kwInput = document.getElementById('kw-new-keyword');
    var catInput = document.getElementById('kw-new-cat');
    var keyword = kwInput.value.trim();
    if (!keyword) { document.getElementById('kw-result').textContent = '请输入关键词'; return; }
    var cats = catInput.value.trim();
    var entry = {keyword: keyword, active: true};
    if (cats) {
        entry.arxiv_cats = cats.split(',').map(function (c) { return c.trim(); }).filter(Boolean);
    }
    _kwData.push(entry);
    renderKeywords();
    kwInput.value = '';
    catInput.value = '';
    document.getElementById('kw-result').textContent = '已添加: ' + keyword;
}

function removeKeyword(index) {
    _kwData.splice(index, 1);
    renderKeywords();
}

async function saveAllSettings() {
    var resultEl = document.getElementById('kw-result');
    resultEl.textContent = '保存中...';
    try {
        var payload = {
            keywords: _kwData,
            fetch_config: {
                max_results: parseInt(document.getElementById('kw-target-new').value, 10),
                lookback_days: parseInt(document.getElementById('kw-lookback-days').value, 10),
            },
        };
        var res = await fetch('/api/keywords', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        if (!res.ok) { resultEl.textContent = '保存失败'; return; }
        var data = await res.json();
        resultEl.textContent = '✅ 配置已保存';
        _applyFetchConfig(payload.fetch_config);
    } catch (e) {
        resultEl.textContent = '保存失败: ' + e.message;
    }
}

async function reloadConfig() {
    var resultEl = document.getElementById('kw-result');
    resultEl.textContent = '重载中...';
    try {
        var res = await fetch('/api/keywords');
        var data = await res.json();
        _kwData = data.keywords || [];
        renderKeywords();
        _applyFetchConfig(data.fetch_config);
        resultEl.textContent = '✅ 配置已重载 (' + _kwData.length + ' 个关键词)';
    } catch (e) {
        resultEl.textContent = '重载失败: ' + e.message;
    }
}

async function fetchByConfig() {
    var btn = event ? event.target : arguments.callee;
    btn.disabled = true;
    btn.textContent = '抓取中...';
    var resultEl = document.getElementById('kw-result');
    resultEl.textContent = '开始按设置抓取...';
    var defaultMax = (state.papers && state.papers.fetch_config && state.papers.fetch_config.max_results) || 20;
    try {
        var params = new URLSearchParams();
        params.append('max_results', document.getElementById('kw-target-new').value || defaultMax);
        var mode = document.querySelector('input[name="config-fetch-mode"]:checked');
        params.append('mode', mode ? mode.value : 'incremental');
        var res = await fetch('/api/keyword-fetch', {method: 'POST', body: params});
        if (!res.ok) { resultEl.textContent = '请求失败'; btn.disabled = false; btn.textContent = '按设置抓取'; return; }
        resultEl.textContent = '等待结果...';
        waitFetchResult(function (data) {
            resultEl.textContent = '抓取完成: 获取 ' + (data.fetched || 0) + ' 篇, 新增 ' + (data.new || 0) + ' 篇';
            btn.disabled = false;
            btn.textContent = '按设置抓取';
            loadPapers();
        });
    } catch (e) {
        resultEl.textContent = '网络错误: ' + e.message;
        btn.disabled = false;
        btn.textContent = '按设置抓取';
    }
}

/* ─── 启动 ───────────────────────────────── */
init();
