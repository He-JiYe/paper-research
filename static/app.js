/**
 * Paper Research — 交互式 HTML Summary 前端逻辑
 * 主要 JS 逻辑已内联在 generator.py 生成的 HTML 中。
 * 此文件用于 server.py 静态托管时的扩展功能。
 */

// 键盘快捷键
document.addEventListener('keydown', (e) => {
    // 仅在没有输入焦点时响应
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    switch (e.key) {
        case '1':
            // 按 1 筛选全部
            document.querySelectorAll('.filter-btn')[0]?.click();
            break;
        case '2':
            // 按 2 筛选重要
            document.querySelectorAll('.filter-btn')[1]?.click();
            break;
        case '3':
            // 按 3 筛选值得关注
            document.querySelectorAll('.filter-btn')[2]?.click();
            break;
        case '4':
            // 按 4 筛选可浏览
            document.querySelectorAll('.filter-btn')[3]?.click();
            break;
        case '5':
            // 按 5 筛选待审核
            document.querySelectorAll('.filter-btn')[4]?.click();
            break;
    }
});
