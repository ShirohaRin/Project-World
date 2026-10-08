const test = require('node:test');
const assert = require('node:assert/strict');
const { compareVersion, isNewer, validateManifest } = require('../src/update-model.js');

test('更新版本按语义版本号比较', () => {
    assert.equal(compareVersion('v1.2.0', '1.1.9'), 1);
    assert.equal(compareVersion('1.2.0', '1.2.0'), 0);
    assert.equal(compareVersion('1.1.9', '1.2.0'), -1);
    assert.equal(isNewer('1.3.0', '1.2.9'), true);
    assert.equal(isNewer('1.2.9', '1.3.0'), false);
});

test('更新清单只接受 HTTPS、版本和 SHA-256', () => {
    const manifest = validateManifest({
        version: 'v1.3.0',
        notes: '修复编辑器问题',
        downloadUrl: 'https://shiroha-rin.world/kuat-api/update/KUAT-Setup.exe',
        sha256: 'A'.repeat(64),
        sizeBytes: 123,
    });
    assert.deepEqual(manifest, {
        version: '1.3.0',
        notes: '修复编辑器问题',
        downloadUrl: 'https://shiroha-rin.world/kuat-api/update/KUAT-Setup.exe',
        sha256: 'a'.repeat(64),
        sizeBytes: 123,
    });
    assert.throws(() => validateManifest({ ...manifest, downloadUrl: 'http://example.com/update.exe' }), /HTTPS/);
    assert.throws(() => validateManifest({ ...manifest, sha256: 'bad' }), /SHA-256/);
});
