/*
 * 更新检查的纯数据逻辑。
 *
 * 这部分不依赖 Electron 或页面，主进程和渲染进程都可以复用，方便以后
 * 修改版本号规则时只改一处，也方便用 Node 直接做基本校验。
 */
(function exposeUpdateModel(root, factory) {
    const model = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = model;
    if (root) root.KUATUpdateModel = model;
})(typeof globalThis === 'undefined' ? this : globalThis, function createUpdateModel() {
    function parseVersion(value) {
        const match = String(value || '').trim().replace(/^v/i, '').match(/^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/);
        if (!match) throw new Error('版本号格式无效');
        return [Number(match[1]), Number(match[2]), Number(match[3])];
    }

    function compareVersion(left, right) {
        const a = parseVersion(left);
        const b = parseVersion(right);
        for (let index = 0; index < 3; index += 1) {
            if (a[index] !== b[index]) return a[index] > b[index] ? 1 : -1;
        }
        return 0;
    }

    function isNewer(candidate, current) {
        return compareVersion(candidate, current) > 0;
    }

    function validateManifest(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) {
            throw new Error('更新清单格式无效');
        }
        const version = String(value.version || '').trim().replace(/^v/i, '');
        parseVersion(version);
        const downloadUrl = String(value.downloadUrl || '').trim();
        const url = new URL(downloadUrl);
        if (url.protocol !== 'https:') throw new Error('更新地址必须使用 HTTPS');
        const sha256 = String(value.sha256 || '').trim().toLowerCase();
        if (!/^[a-f0-9]{64}$/.test(sha256)) throw new Error('更新文件缺少有效的 SHA-256 校验值');
        const notes = typeof value.notes === 'string' ? value.notes.trim() : '';
        const sizeBytes = Number.isSafeInteger(value.sizeBytes) && value.sizeBytes > 0 ? value.sizeBytes : null;
        return { version, downloadUrl, sha256, notes, sizeBytes };
    }

    return { parseVersion, compareVersion, isNewer, validateManifest };
});
