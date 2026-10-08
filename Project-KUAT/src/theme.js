/* 主题只保存界面偏好，不写入世界、词条或其他创作档案。 */
(function installTheme() {
    const key = 'kuat-interface-theme-v1';
    let theme = 'light';
    try { theme = sessionStorage.getItem(key) === 'dark' ? 'dark' : 'light'; } catch {}

    function apply(next) {
        theme = next === 'dark' ? 'dark' : 'light';
        document.documentElement.dataset.theme = theme;
        const button = document.querySelector('#theme-toggle');
        if (button) {
            button.textContent = theme === 'dark' ? '☀ 浅色' : '☾ 深色';
            button.setAttribute('aria-label', theme === 'dark' ? '切换浅色主题' : '切换深色主题');
        }
        try { sessionStorage.setItem(key, theme); } catch {}
    }

    apply(theme);
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelector('#theme-toggle')?.addEventListener('click', () => apply(theme === 'dark' ? 'light' : 'dark'));
    });
    window.KUATTheme = { get: () => theme, set: apply };
})();
