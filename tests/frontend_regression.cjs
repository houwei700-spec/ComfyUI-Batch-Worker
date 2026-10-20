// 离线执行真实前端函数，验证提交按钮、重复提交和异步响应顺序。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
function el(id) {
    if (!elements.has(id)) elements.set(id, {value: '', style: {}, textContent: '', innerHTML: '', disabled: false});
    return elements.get(id);
}
const context = vm.createContext({
    console, URLSearchParams, encodeURIComponent, decodeURIComponent,
    document: {getElementById: el, querySelector: () => null},
    window: {addEventListener() {}}, setTimeout() {},
    fetch: async () => ({json: async () => ({success: true, count: 1, tasks: []})})
});
vm.runInContext(fs.readFileSync('web/js/app.js', 'utf8'), context);
vm.runInContext(`
showToast = () => {};
renderTasks = () => {};
renderMatrixGrid = () => {};
bootstrapData = {templates: [{filename: 'tpl.json', has_scene: false}]};
matrixSelectedApparels.add('sku.jpg');
`, context);
el('matrix_template_select').value = 'tpl.json';
el('matrix_model_select').value = 'model.jpg';

(async () => {
    let resolveFirst, requests = 0;
    context.fetch = () => { requests++; return new Promise(r => {resolveFirst = r;}); };
    const submitting = vm.runInContext('submitBatchMatrixTasks()', context);
    await vm.runInContext('submitBatchMatrixTasks()', context);
    assert.equal(requests, 1, 'repeated clicks must not submit twice');
    resolveFirst({json: async () => ({success: true, count: 1, tasks: []})});
    await submitting;
    assert.match(el('btn_batch_submit').innerHTML, /btn_submit_count/);
    assert.doesNotMatch(el('btn_batch_submit').innerHTML, /正在/);
    assert.equal(el('btn_batch_submit').disabled, true);
    vm.runInContext("matrixSelectedApparels.add('next.png'); updateMatrixFooterStats()", context);
    assert.equal(el('btn_batch_submit').disabled, false);

    const resolves = [];
    const urls = [];
    context.fetch = url => { urls.push(url); return new Promise(r => resolves.push(r)); };
    const older = vm.runInContext('fetchAndRenderMatrixStatus()', context);
    el('matrix_model_select').value = 'other.jpg';
    const newer = vm.runInContext('fetchAndRenderMatrixStatus()', context);
    resolves[1]({json: async () => ({items: [{filename: 'new.jpg'}]})});
    await newer;
    resolves[0]({json: async () => ({items: [{filename: 'old.jpg'}]})});
    await older;
    assert.equal(vm.runInContext('matrixItems[0].filename', context), 'new.jpg');
    assert.match(urls[1], /model=other.jpg&template=tpl.json&scene=/);

    const encoded = encodeURIComponent('date/model/sku/100% & result.mp4');
    let played;
    context.openVideoModal = (file, task) => {played = [file, task];};
    context.playMatrixDoneVideo(null, encoded, '001');
    assert.deepEqual(played, [encoded, '001']);
    assert.notEqual(vm.runInContext("encodeFile('sku.jpg')", context), vm.runInContext("encodeFile('sku.png')", context));
    vm.runInContext('refreshBatchStatus = async () => {}', context);
    let finishClear, clearRequests = 0;
    context.fetch = url => {
        assert.equal(url, '/api/clear_task_records');
        clearRequests++;
        return new Promise(resolve => {finishClear = resolve;});
    };
    const clearing = vm.runInContext('clearTaskRecords()', context);
    await vm.runInContext('clearTaskRecords()', context);
    assert.equal(clearRequests, 1);
    assert.equal(el('btn_clear_records').disabled, true);
    finishClear({ok: true, json: async () => ({success: true, cleared: 2, tasks: []})});
    await clearing;
    assert.equal(el('btn_clear_records').disabled, false);
    assert.equal(vm.runInContext('lastTasksFingerprint', context), '[]');
    context.fetch = async () => {throw new Error('offline');};
    await vm.runInContext('clearTaskRecords()', context);
    assert.equal(el('btn_clear_records').disabled, false);
    console.log('PASS: clear records refresh, duplicate-click protection and error recovery');
    console.log('PASS: duplicate submit, button recovery, stale matrix response, combination query, encoded playback, unique image keys');
})().catch(error => {console.error(error); process.exitCode = 1;});
