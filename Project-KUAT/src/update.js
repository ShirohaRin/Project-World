/* 软件更新窗口：检查、下载、校验和安装都由 Electron 主进程执行。 */
(function setupUpdater() {
    const updateApi = window.kuatUpdater;
    if (!updateApi) return;

    const checkButton = document.querySelector('#check-update');
    const dialog = document.querySelector('#update-dialog');
    const closeButton = document.querySelector('#update-close');
    const currentLabel = document.querySelector('#update-current');
    const available = document.querySelector('#update-available');
    const versionLabel = document.querySelector('#update-version');
    const notesLabel = document.querySelector('#update-notes');
    const statusLabel = document.querySelector('#update-status');
    const downloadButton = document.querySelector('#update-download');
    const installButton = document.querySelector('#update-install');
    const appVersionLabel = document.querySelector('.version');
    let latestManifest = null;

    function setStatus(message, error = false) {
        statusLabel.textContent = message;
        statusLabel.dataset.error = error ? 'true' : 'false';
    }

    function resetDialog(currentVersion) {
        latestManifest = null;
        currentLabel.textContent = `当前版本 v${currentVersion}`;
        available.hidden = true;
        downloadButton.hidden = true;
        installButton.hidden = true;
        setStatus('点击“检查更新”获取最新版本。');
    }

    async function loadCurrentVersion() {
        try {
            const result = await updateApi.version();
            const version = result.version || '未知';
            if (appVersionLabel) appVersionLabel.textContent = `v${version}`;
            resetDialog(version);
            return version;
        } catch (error) {
            resetDialog('未知');
            return '未知';
        }
    }

    async function checkForUpdate(showDialog = true) {
        if (showDialog && !dialog.open) dialog.showModal();
        checkButton.disabled = true;
        setStatus('正在检查更新…');
        try {
            const result = await updateApi.check();
            currentLabel.textContent = `当前版本 v${result.currentVersion}`;
            if (!result.available) {
                latestManifest = null;
                available.hidden = true;
                downloadButton.hidden = true;
                installButton.hidden = true;
                setStatus('当前已经是最新版本。');
                return;
            }
            latestManifest = result;
            versionLabel.textContent = `v${result.version}`;
            notesLabel.textContent = result.notes || '本次更新没有附加说明。';
            available.hidden = false;
            downloadButton.hidden = false;
            installButton.hidden = true;
            setStatus('发现新版本，确认后下载更新文件。');
        } catch (error) {
            latestManifest = null;
            available.hidden = true;
            downloadButton.hidden = true;
            installButton.hidden = true;
            setStatus(error.message || '检查更新失败，请稍后重试。', true);
        } finally {
            checkButton.disabled = false;
        }
    }

    checkButton.addEventListener('click', () => checkForUpdate(true));
    closeButton.addEventListener('click', () => dialog.close());
    downloadButton.addEventListener('click', async () => {
        if (!latestManifest) return;
        downloadButton.disabled = true;
        setStatus('正在下载并校验更新文件，请稍候…');
        try {
            await updateApi.download(latestManifest);
            downloadButton.hidden = true;
            installButton.hidden = false;
            setStatus('更新文件已校验通过，可以安装。');
        } catch (error) {
            setStatus(error.message || '下载更新失败，请稍后重试。', true);
        } finally {
            downloadButton.disabled = false;
        }
    });
    installButton.addEventListener('click', async () => {
        installButton.disabled = true;
        setStatus('正在启动安装程序，软件即将重启…');
        try {
            await updateApi.install();
        } catch (error) {
            installButton.disabled = false;
            setStatus(error.message || '安装更新失败，请稍后重试。', true);
        }
    });

    loadCurrentVersion();
})();
