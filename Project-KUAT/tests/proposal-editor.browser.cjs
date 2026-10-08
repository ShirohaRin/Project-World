// 在 Electron 的真实 Chromium DOM 中回归类型切换；使用独立的内存档案，不连接云端。
// 运行：electron tests/proposal-editor.browser.cjs
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const projectRoot = path.resolve(__dirname, '..');
const sourceRoot = process.argv.find(arg => arg.startsWith('--source-root='))?.slice(14) || projectRoot;
const output = path.join(projectRoot, 'release/QA');
fs.mkdirSync(output, {recursive: true});
const logFile = path.join(output, 'proposal-editor-test.log');
fs.writeFileSync(logFile, `开始界面回归测试：${new Date().toISOString()}\n`);
const report = (...items) => fs.appendFileSync(logFile, items.map(String).join(' ') + '\n');
setTimeout(() => {report('FAIL: 界面测试超时'); app.exit(1);}, 25000).unref();
app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'kuat-editor-test-')));
let window;
const evaluate = script => window.webContents.executeJavaScript(script);

// 用浏览器鼠标事件点击文字区域，才能覆盖 label 默认行为造成的选择回退。
async function click(selector) {
  await evaluate(`(() => {
    const node = document.querySelector(${JSON.stringify(selector)});
    if (!node) throw Error('找不到控件：' + ${JSON.stringify(selector)});
    node.scrollIntoView({block: 'center'});
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) throw Error('控件不可见');
    node.click();
  })(); void 0;`);
  await new Promise(resolve => setTimeout(resolve, 80));
}

async function state() {
  return evaluate(`(() => {
    const dialog = document.querySelector('#entry-editor');
    const form = dialog.querySelector('form');
    return {
      open: dialog.open,
      valid: form.checkValidity(),
      saveDisabled: form.querySelector('#entry-save').disabled,
      saveType: form.querySelector('#entry-save').type,
      kind: form.elements.proposalKind.value,
      collectionMode: dialog.classList.contains('proposal-collection-mode'),
      categoryHidden: form.querySelector('.proposal-category-field').hidden,
      categoryDisabled: form.elements.category.disabled,
      parentHidden: form.querySelector('.proposal-parent-field').hidden,
      parentDisabled: form.elements.parentCollection.disabled,
      visibility: form.elements.collectionVisibility.value,
      title: form.elements.title.value,
      formDataKind: new FormData(form).get('proposalKind'),
      summary: form.elements.summary.value,
      body: form.elements.body.value,
      parent: form.elements.parentCollection.value,
      previewKind: dialog.querySelector('.live-kind').textContent,
      trajectoryButtonHidden: form.querySelector('[data-editor-add-trajectory]').hidden,
      trajectoryButtonDisabled: form.querySelector('[data-editor-add-trajectory]').disabled,
      trajectoryPanelHidden: form.querySelector('[data-trajectory-panel]').hidden,
      trajectory: JSON.parse(form.elements.trajectoryJson.value || '[]'),
      errors: window.testErrors,
      entryError: form.querySelector('#entry-error').textContent,
      saved: window.testSaved
      ,editable: window.testEntries.editable()
      ,submitCount: window.testSubmitCount
    };
  })()`);
}

app.whenReady().then(async () => {
  report('Electron ready');
  window = new BrowserWindow({show: false, width: 1440, height: 960,
    webPreferences: {contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false}});
  window.webContents.on('console-message', (_event, _level, message, line, sourceId) => report(`renderer ${sourceId}:${line}`, message));
  window.webContents.on('render-process-gone', (_event, details) => report('renderer gone', JSON.stringify(details)));
  await window.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent('<!doctype html><html><body><main id="main"></main></body></html>'));
  report('HTML loaded');
  await window.webContents.insertCSS(fs.readFileSync(path.join(sourceRoot, 'src/style.css'), 'utf8'));
  report('CSS loaded');
  await evaluate(`
    window.testErrors = [];
    window.testSaved = null;
    window.testSubmitCount = 0;
    if (!window.crypto.randomUUID) Object.defineProperty(window.crypto, 'randomUUID', {value: () => 'test-' + Math.random().toString(16).slice(2), configurable: true});
    window.addEventListener('error', event => testErrors.push(event.message));
    window.addEventListener('unhandledrejection', event => testErrors.push(String(event.reason)));
    window.KUATEntrySeed = {schemaVersion: 1, storeRevision: 0, entries: [{
      id: 'test-parent', title: '测试根设定集', type: '设定集', proposalKind: '设定集',
      category: '', collectionVisibility: '展示', parentCollection: '',
      summary: '', body: '', notes: '', status: '草稿', visibility: '内部',
      aliases: [], tags: [], related: [], sources: []
    }]};
    window.ArchiveView = {markdown: text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;')};
    window.IDEAArchive = {documents: []};
    window.kuat = {
      loadEntries: async () => structuredClone(KUATEntrySeed),
      saveEntries: async next => {testSaved = structuredClone(next); return structuredClone(next);}
    };
  ;void 0;`);
  report('fixtures loaded');
  await evaluate(fs.readFileSync(path.join(sourceRoot, 'src/entries-model.js'), 'utf8') + '\nvoid 0;');
  report('model loaded');
  await evaluate(fs.readFileSync(path.join(sourceRoot, 'src/entries.js'), 'utf8') + '\nwindow.testEntries = EntriesView; void 0;');
  report('entries module loaded');
  await evaluate(`testEntries.install(() => {}, () => {});`);
  report('entries installed');
  await new Promise(resolve => setTimeout(resolve, 80));
  await evaluate(`
    if (!testEntries.editable()) throw Error('测试档案未载入');
    testEntries.create();
    const form = document.querySelector('#entry-form');
    for (const [key, value] of Object.entries({title:'类型切换回归样例', summary:'保留简介', body:'保留正文'})) {
      form.elements[key].value = value;
      form.elements[key].dispatchEvent(new Event('input', {bubbles: true}));
    }
    document.querySelector('#entry-body-editor').textContent = '保留正文';
    document.querySelector('#entry-body-editor').dispatchEvent(new Event('input', {bubbles: true}));
    document.querySelector('#entry-editor').addEventListener('submit', () => window.testSubmitCount++);
  `);
  report('new editor opened');

  await click('[data-proposal-kind="设定集"]');
  let current = await state();
  report('首次点击设定集：', JSON.stringify(current));
  console.log('首次点击设定集：', JSON.stringify(current));
  assert.deepEqual(current.errors, [], '切换期间不能出现运行时错误');
  assert.equal(current.kind, '设定集');
  assert.equal(current.collectionMode, true);
  assert.equal(current.categoryHidden && current.categoryDisabled, true);
  assert.equal(current.parentHidden || current.parentDisabled, false);
  assert.equal(current.previewKind, '设定集');

  await click('[data-collection-visibility="不展示"]');
  assert.equal((await state()).visibility, '不展示');
  await evaluate(`document.querySelector('[name="parentCollection"]').value = 'test-parent';`);
  await click('[data-proposal-kind="设定"]');
  current = await state();
  assert.equal(current.kind, '设定');
  assert.equal(current.collectionMode, false);
  assert.equal(current.categoryHidden || current.categoryDisabled, false);
  assert.equal(current.parentHidden && current.parentDisabled, true);
  await click('[data-proposal-kind="设定集"]');
  current = await state();
  assert.equal(current.title, '类型切换回归样例');
  assert.equal(current.summary, '保留简介');
  assert.equal(current.body, '保留正文');
  assert.equal(current.parent, 'test-parent');
  assert.equal(current.visibility, '不展示');

  // 再点击回到“设定”，验证双向切换和字段状态同步。
  await click('[data-proposal-kind="设定"]');
  assert.equal((await state()).kind, '设定');
  await click('[data-proposal-kind="设定集"]');
  await click('#entry-save');
  current = await state();
  assert.equal(current.open, false, '保存后应关闭对话框');
  const saved = current.saved.entries.find(entry => entry.title === '类型切换回归样例');
  assert.equal(saved.proposalKind, '设定集');
  assert.equal(saved.type, '设定集');
  assert.equal(saved.collectionVisibility, '不展示');
  assert.equal(saved.parentCollection, 'test-parent');
  assert.equal(saved.body, '保留正文');

  await evaluate(`testEntries.select(${JSON.stringify(saved.id)}); testEntries.edit();`);
  current = await state();
  assert.equal(current.kind, '设定集');
  assert.equal(current.collectionMode, true);
  assert.equal(current.parentHidden || current.parentDisabled, false);
  assert.equal(current.parent, 'test-parent');
  assert.equal(current.visibility, '不展示');
  await click('[data-proposal-kind="设定"]');
  report('重新打开后切换回设定：', JSON.stringify(await state()));
  await click('#entry-save');
  await new Promise(resolve => setTimeout(resolve, 120));
  current = await state();
  report('第二次保存后：', JSON.stringify(current));
  assert.equal(current.saved.entries.find(entry => entry.id === saved.id).proposalKind, '设定');
  assert.deepEqual(current.errors, []);

  // 新建设定时允许添加轨迹；切换为设定集后轨迹入口必须隐藏、禁用并在保存时清空。
  await evaluate(`testEntries.create(); document.querySelector('#entry-form').elements.title.value='轨迹限制样例';`);
  current = await state();
  assert.equal(current.trajectoryButtonHidden, false);
  assert.equal(current.trajectoryButtonDisabled, false);
  await click('[data-editor-add-trajectory]');
  current = await state();
  assert.equal(current.trajectoryPanelHidden, false);
  assert.equal(current.trajectory.length, 2);
  await click('[data-proposal-kind="设定集"]');
  current = await state();
  assert.equal(current.trajectoryButtonHidden, true);
  assert.equal(current.trajectoryButtonDisabled, true);
  assert.equal(current.trajectoryPanelHidden, true);
  await click('#entry-save');
  await new Promise(resolve => setTimeout(resolve, 120));
  current = await state();
  const trajectorySaved = current.saved.entries.find(entry => entry.title === '轨迹限制样例');
  assert.deepEqual(trajectorySaved.trajectory, []);
  // 再打开新表单，防止前一次表单的监听器持有旧 DOM。
  await evaluate(`testEntries.create();`);
  await click('[data-proposal-kind="设定集"]');
  current = await state();
  assert.equal(current.collectionMode, true);
  assert.deepEqual(current.errors, []);
  fs.writeFileSync(path.join(output, 'proposal-collection.png'), (await window.webContents.capturePage()).toPNG());
  report('PASS: 文字点击、双向切换、方向键、展示状态、内容保留、保存及重新打开全部通过。');
  console.log('PASS: 文字点击、双向切换、方向键、展示状态、内容保留、保存及重新打开全部通过。');
  app.exit(0);
}).catch(error => {report(error.stack); console.error(error); app.exit(1);});
