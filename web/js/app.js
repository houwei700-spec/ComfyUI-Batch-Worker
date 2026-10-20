// ComfyUI Batch Hub Frontend Logic (极速流畅高并发与全功能自愈增强版)
let bootstrapData = {};
let currentPlayingFile = '';
let isBatchRunning = false;
let lastTasksFingerprint = '';
let lastBatchFingerprint = '';
let lastClusterStatusKey = '';
let matrixSubmitting = false;
let matrixRequestVersion = 0;
let singleSubmitting = false;

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function encodeFile(value) {
    return encodeURIComponent(value).replace(/'/g, '%27');
}

// ==========================================
// 琥珀微光 Toast 消息中心
// ==========================================
function showToast(message, type = 'info', duration = 3200) {
    let container = document.getElementById('toast_container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast_container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('toast-fadeout');
        setTimeout(() => toast.remove(), 260);
    }, duration);
}

// 快速拷贝文本进剪贴板并提示
function copyText(text, successMsg) {
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => {
            if (successMsg) showToast(successMsg, 'success');
        }).catch(() => fallbackCopy(text, successMsg));
    } else {
        fallbackCopy(text, successMsg);
    }
}

function fallbackCopy(text, successMsg) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
        document.execCommand('copy');
        if (successMsg) showToast(successMsg, 'success');
    } catch (err) {}
    ta.remove();
}

function copyOutputPath() {
    copyText('E:\\ComfyUI-Batch-Worker\\output', '📋 成品存储目录路径已复制到剪贴板！');
}

// ==========================================
// 系统初始化
// ==========================================
async function init() {
    setupPreviewControls();
    try {
        const res = await fetch('/api/bootstrap');
        bootstrapData = await res.json();

        // 1. 初始化模板下拉框
        const tplSelect = document.getElementById('template_select');
        tplSelect.innerHTML = '';
        (bootstrapData.templates || []).forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.filename;
            opt.textContent = t.name + (t.has_scene ? ' (含场景)' : ' (无场景)');
            tplSelect.appendChild(opt);
        });

        // 2. 填充模特、服装、场景下拉框
        populateSelect('model_select', bootstrapData.models || []);
        populateSelect('apparel_select', bootstrapData.apparels || []);
        populateSelect('scene_select', bootstrapData.scenes || []);

        // 3. 渲染首帧与各功能状态
        onTemplateChange();
        renderTasks(bootstrapData.tasks || []);
        checkClusterStatus();
        refreshBatchStatus();

        // 4. 后台静默全量预加载所有缩略图至浏览器内存 (全部加起来仅 ~570KB)
        preloadAllThumbnails(bootstrapData);

        // 5. 智能守护定时器 (无缝 diff 比对，杜绝抖动与卡顿)
        setInterval(checkClusterStatus, 4000);
        setInterval(refreshTasks, 2000);
        setInterval(refreshBatchStatus, 2000);
    } catch (err) {
        console.error("系统初始化异常:", err);
        showToast("⚠️ 系统初始化连接异常，请检查服务状态", "warning");
    }
}

function preloadAllThumbnails(data) {
    const urls = [];
    if (data.models) data.models.forEach(m => urls.push(`/api/asset/thumb/models/${encodeURIComponent(m)}?v=20260917`));
    if (data.apparels) data.apparels.forEach(a => urls.push(`/api/asset/thumb/apparels/${encodeURIComponent(a)}?v=20260917`));
    if (data.scenes) data.scenes.forEach(s => urls.push(`/api/asset/thumb/scenes/${encodeURIComponent(s)}?v=20260917`));

    const preloadWorker = () => {
        urls.forEach(url => {
            const img = new Image();
            img.src = url;
        });
    };

    if (window.requestIdleCallback) {
        requestIdleCallback(preloadWorker);
    } else {
        setTimeout(preloadWorker, 100);
    }
}

function populateSelect(selectId, items) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    sel.innerHTML = '';
    items.forEach(item => {
        const opt = document.createElement('option');
        opt.value = item;
        opt.textContent = assetName(item);
        sel.appendChild(opt);
    });
}

function onTemplateChange() {
    const tplSelect = document.getElementById('template_select');
    if (!tplSelect) return;
    const currentFilename = tplSelect.value;
    const tpl = (bootstrapData.templates || []).find(t => t.filename === currentFilename);
    if (!tpl) return;

    const sceneGroup = document.getElementById('scene_group');
    if (tpl.has_scene) {
        sceneGroup.classList.remove('scene-hidden');
        if (tpl.default_scene) {
            const sceneSelect = document.getElementById('scene_select');
            for (let opt of sceneSelect.options) {
                if (opt.value === tpl.default_scene || opt.value.includes(tpl.default_scene)) {
                    sceneSelect.value = opt.value;
                    break;
                }
            }
        }
        updatePreview('scenes', document.getElementById('scene_select').value, 'scene_preview');
    } else {
        sceneGroup.classList.add('scene-hidden');
    }

    updatePreview('models', document.getElementById('model_select').value, 'model_preview');
    updatePreview('apparels', document.getElementById('apparel_select').value, 'apparel_preview');
}

// 毫秒级缩略图极速显示
function updatePreview(category, filename, imgId) {
    const img = document.getElementById(imgId);
    if (!img) return;
    if (filename) {
        const thumbUrl = `/api/asset/thumb/${category}/${encodeURIComponent(filename)}?v=20260917`;
        if (img.dataset.loadedUrl !== thumbUrl) {
            img.dataset.loadedUrl = thumbUrl;
            img.style.opacity = '0.35';
            img.src = thumbUrl;
            img.onload = () => {
                img.style.display = 'block';
                img.style.opacity = '1';
            };
        }
    } else {
        img.style.display = 'none';
        img.dataset.loadedUrl = '';
    }
}

function assetName(value) { return String(value || '').replace(/\.[^/.]+$/, ''); }
function previewSelectedAsset(category, selectId) {
    const value = document.getElementById(selectId).value;
    if (value) openImageModal(category, encodeFile(value));
}
function renderTasks(tasks) {
    bootstrapData.tasks = tasks;
    const tbody = document.getElementById('task_tbody');
    document.getElementById('queue_count').textContent = `${tasks.length} 个任务`;
    const counts = {total:tasks.length,pending:0,running:0,done:0,failed:0};
    const labels = {PENDING:'排队中',QUEUED:'已认领',RUNNING:'生成中',DOWNLOADING:'下载中',DONE:'已完成',FAILED:'失败',SUBMITTING:'提交中',REVIEW:'待核对'};
    const asset = (category, filename) => filename ? `<button class="task-asset" onclick="openImageModal('${category}','${encodeFile(filename)}')" title="${escapeHtml(assetName(filename))}"><img src="/api/asset/thumb/${category}/${encodeFile(filename)}?v=20260917" alt="${escapeHtml(assetName(filename))}" loading="lazy"><span>${escapeHtml(assetName(filename))}</span></button>` : '<div class="task-asset"><span class="no-scene">⌁</span><span>无场景</span></div>';
    tbody.innerHTML = tasks.map(t => {
        const st = (t.status || 'PENDING').toUpperCase();
        if (st==='PENDING') counts.pending++;
        else if (['QUEUED','SUBMITTING','RUNNING','DOWNLOADING'].includes(st)) counts.running++;
        else if (st==='DONE') counts.done++;
        else if (['FAILED','REVIEW'].includes(st)) counts.failed++;
        const id=encodeFile(t.task_id), file=encodeFile(t.output_file || '');
        let actions='';
        if(st==='DONE' && t.output_file) actions+=`<button class="btn-action-play" onclick="openVideoModal('${file}',decodeURIComponent('${id}'))">播放</button><button onclick="locateFile('${file}')">定位</button>`;
        if(['DONE','FAILED'].includes(st)) actions+=`<button onclick="regenerateTask(decodeURIComponent('${id}'))">重做</button>`;
        if(st==='REVIEW') actions+=`<button onclick="regenerateTask(decodeURIComponent('${id}'),true)">确认重排</button>`;
        if(['REVIEW','FAILED'].includes(st)&&(t.prompt_id||t.client_id)) actions+=`<button onclick="receiveExistingTask(decodeURIComponent('${id}'))">检查回传</button>`;
        if(!['QUEUED','SUBMITTING','RUNNING','DOWNLOADING','REVIEW'].includes(st)) actions+=`<button onclick="deleteTask(decodeURIComponent('${id}'))">删除</button>`;
        const tpl=(bootstrapData.templates || []).find(x=>x.filename===t.template);
        return `<tr data-task-id="${escapeHtml(t.task_id)}" class="${String(t.task_id)===String(currentPlayingTaskId) ? 'is-playing':''}"><td>${escapeHtml(t.task_id)}</td><td><div class="task-assets">${asset('models',t.model)}${asset('apparels',t.apparel)}${asset('scenes',t.scene)}</div></td><td class="workflow-name">${escapeHtml(tpl?.name || assetName(t.template))}</td><td><span class="status-badge status-${st.toLowerCase()}">${labels[st] || escapeHtml(st)}</span>${t.error?`<small style="display:block;max-width:180px;overflow-wrap:anywhere" title="${escapeHtml(t.error)}">${escapeHtml(t.error.slice(0,100))}</small>`:""}</td><td><span class="node-tag">${escapeHtml(t.worker_node || '—')}</span></td><td><div class="task-actions">${actions || '—'}</div></td></tr>`;
    }).join('') || '<tr><td colspan="6"><div class="empty-state">队列暂时为空<br><br>选择左侧素材，开始创建任务</div></td></tr>';
    for(const [key,value] of Object.entries(counts)) { const el=document.getElementById('stat_'+key); if(el) el.textContent=value; }
    const retry=document.getElementById('preview_retry');
    if(retry) retry.hidden=!tasks.some(t=>String(t.task_id)===String(currentPlayingTaskId) && ['DONE','FAILED'].includes(t.status));
}

// 守护式无感刷新
async function refreshTasks() {
    try {
        const res = await fetch('/api/tasks');
        const tasks = await res.json();
        const fingerprint = JSON.stringify(tasks);
        if (fingerprint !== lastTasksFingerprint) {
            lastTasksFingerprint = fingerprint;
            renderTasks(tasks);
        }
    } catch (e) {}
}

async function refreshBatchStatus() {
    try {
        const res = await fetch('/api/batch_status');
        const data = await res.json();
        const fingerprint = JSON.stringify(data);
        if (fingerprint === lastBatchFingerprint) return;
        lastBatchFingerprint = fingerprint;

        isBatchRunning = data.is_running;
        const banner = document.getElementById('active_banner');
        const stopBtn = document.getElementById('btn_stop_batch');
        const launchBtn = document.getElementById('btn_launch_batch');

        if (isBatchRunning) {
            banner.style.display = 'flex';
            stopBtn.disabled = false;
            launchBtn.disabled = true;

            let activeWorkerDescs = [];
            for (let sid in data.workers) {
                let w = data.workers[sid];
                if (w.state === 'BUSY' && w.task_id) {
                    activeWorkerDescs.push(`任务 [${w.task_id}] (${w.name})`);
                }
            }

            document.getElementById('banner_text').textContent = 
                `🚀 多机并行负载均衡渲染中 (进行中: ${data.running} | 排队中: ${data.pending} | 已完成: ${data.done})`;
            document.getElementById('banner_node_info').textContent = 
                activeWorkerDescs.length > 0 ? `当前各端口并行认领: ${activeWorkerDescs.join(' ｜ ')}` : '正在为各空闲端口分配任务...';
        } else {
            banner.style.display = 'none';
            stopBtn.disabled = true;
            launchBtn.disabled = false;
        }
    } catch (e) {}
}

// 🚀 新增/发送任务 (全面增强交互反馈与异常捕获)
async function submitTask() {
    if (singleSubmitting) return;
    const tplSelect = document.getElementById('template_select');
    const modelSelect = document.getElementById('model_select');
    const apparelSelect = document.getElementById('apparel_select');
    const sceneSelect = document.getElementById('scene_select');

    const tplFilename = tplSelect ? tplSelect.value : '';
    const tpl = (bootstrapData.templates || []).find(t => t.filename === tplFilename);

    const payload = {
        template: tplFilename,
        model: modelSelect ? modelSelect.value : '',
        apparel: apparelSelect ? apparelSelect.value : '',
        scene: (tpl && tpl.has_scene && sceneSelect) ? sceneSelect.value : ''
    };

    if (!payload.model || !payload.apparel) {
        showToast('⚠️ 请先在左侧选择模特图与服装图！', 'warning');
        return;
    }

    singleSubmitting = true;
    const addButton = document.getElementById('btn_add_task');
    if (addButton) { addButton.disabled = true; addButton.textContent = '正在加入…'; }
    try {
        const res = await fetch('/api/add_task', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            lastTasksFingerprint = JSON.stringify(data.tasks);
            renderTasks(data.tasks);
            showToast(`✅ 任务已成功加入今晚渲染队列！(当前排队: ${data.tasks.length} 项)`, 'success');
        } else {
            showToast('❌ 加入队列失败: ' + (data.message || '未知异常'), 'error');
        }
    } catch (err) {
        showToast('❌ 请求失败: ' + err.message, 'error');
    } finally {
        singleSubmitting = false;
        if (addButton) { addButton.disabled = false; addButton.textContent = '＋ 加入生产队列'; }
    }
}

async function deleteTask(taskId) {
    try {
        const res = await fetch(`/api/delete_task?id=${taskId}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            lastTasksFingerprint = JSON.stringify(data.tasks);
            renderTasks(data.tasks);
            showToast(`🗑️ 任务 [${taskId}] 已从队列移除`, 'info');
        } else {
            showToast(data.message || '删除任务失败', 'warning');
        }
    } catch (e) {
        showToast('❌ 删除任务失败', 'error');
    }
}

async function retryFailedTasks() {
    try {
        const res = await fetch('/api/reset_failed', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            lastTasksFingerprint = JSON.stringify(data.tasks);
            renderTasks(data.tasks);
            showToast('🔄 已重置所有失败/中断任务为排队中，可重新开始渲染！', 'success');
        }
    } catch (e) {
        showToast('❌ 重置失败', 'error');
    }
}

let clearingTaskRecords = false;
async function clearTaskRecords() {
    if (clearingTaskRecords) return;
    clearingTaskRecords = true;
    const button = document.getElementById('btn_clear_records');
    if (button) button.disabled = true;
    try {
        const res = await fetch('/api/clear_task_records', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.message || '清空记录失败');
        lastTasksFingerprint = JSON.stringify(data.tasks);
        renderTasks(data.tasks);
        showToast(data.cleared ? `已清空 ${data.cleared} 条记录，视频文件已保留` : '暂无已完成或失败的记录可清空', 'info');
        await refreshBatchStatus();
    } catch (err) {
        showToast('❌ ' + err.message, 'error');
    } finally {
        clearingTaskRecords = false;
        if (button) button.disabled = false;
    }
}

// 打开成品输出目录 (自动进入当天批次，双通道保障：原生唤起 + 路径自动复制)
async function openOutputFolder() {
    try {
        const res = await fetch('/api/open_output', { method: 'POST' });
        const data = await res.json();
        const openedPath = data.path ? data.path.replace(/\//g, '\\') : 'E:\\ComfyUI-Batch-Worker\\output';
        copyText(openedPath, null);
        showToast(`📂 已打开成品目录: ${openedPath} (路径已复制)`, 'info', 4000);
    } catch (e) {
        copyText('E:\\ComfyUI-Batch-Worker\\output', null);
        showToast('📂 已唤起资源管理器打开目录 (路径已自动复制)', 'info', 4000);
    }
}

// 在文件夹中高亮定位文件 (支持多级目录智能穿透)
async function locateFile(filename) {
    const rawFn = decodeURIComponent(filename);
    const fullLocalPath = `E:\\ComfyUI-Batch-Worker\\output\\${rawFn.replace(/\//g, '\\')}`;
    const baseName = rawFn.split('/').pop();
    try {
        copyText(fullLocalPath, null);
        showToast(`📁 正在定位: ${baseName} (路径已复制)`, 'info', 4000);
        await fetch(`/api/locate_file?filename=${filename}`, { method: 'POST' });
    } catch (e) {
        showToast('❌ 定位异常，路径已自动复制', 'warning');
    }
}

let currentPlayingTaskId = '';

function openVideoModal(encodedFilename, taskId = '') {
    currentPlayingFile = encodedFilename;
    currentPlayingTaskId = taskId;
    const fn = decodeURIComponent(encodedFilename);
    document.getElementById('modal_filename').textContent = fn.split('/').pop();
    const task=(bootstrapData.tasks || []).find(t=>String(t.task_id)===String(taskId));
    document.getElementById('preview_details').textContent=task ? [assetName(task.model),assetName(task.apparel)].join(' · ') : fn;
    document.getElementById('preview_retry').hidden=!taskId;
    document.getElementById('preview_locate').disabled=false;
    document.getElementById('video_controls').hidden=false;
    document.getElementById('portrait_frame').classList.add('has-video');
    const player = document.getElementById('player_element');
    document.getElementById('player_src').src = `/api/video_stream?filename=${encodedFilename}`;
    player.load();
    player.play().catch(() => {});
    document.querySelectorAll('#task_tbody tr').forEach(row=>row.classList.toggle('is-playing',row.dataset.taskId===String(taskId)));
    if(window.innerWidth<=1050) document.getElementById('video_modal').scrollIntoView({behavior:'smooth',block:'start'});
}
function togglePreviewPlayback(){const p=document.getElementById('player_element');if(p.paused)p.play().catch(()=>{});else p.pause();}
function seekPreview(value){const p=document.getElementById('player_element');if(Number.isFinite(p.duration))p.currentTime=p.duration*Number(value)/100;}
function togglePreviewMute(){const p=document.getElementById('player_element');p.muted=!p.muted;document.getElementById('video_mute').textContent=p.muted?'♩':'♫';document.getElementById('video_mute').setAttribute('aria-label',p.muted?'取消静音':'静音');}
function fullscreenPreview(){const p=document.getElementById('player_element');if(p.requestFullscreen)p.requestFullscreen().catch(()=>showToast('无法进入全屏','warning'));else if(p.webkitEnterFullscreen)p.webkitEnterFullscreen();}
function setupPreviewControls(){
    const p=document.getElementById('player_element');
    const time=n=>{n=Number.isFinite(n)?Math.floor(n):0;return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');};
    const update=()=>{document.getElementById('video_time').textContent=time(p.currentTime)+' / '+time(p.duration);document.getElementById('video_seek').value=p.duration?p.currentTime/p.duration*100:0;const b=document.getElementById('video_toggle');b.textContent=p.paused?'▶':'Ⅱ';b.setAttribute('aria-label',p.paused?'播放':'暂停');};
    ['loadedmetadata','timeupdate','play','pause','emptied'].forEach(e=>p.addEventListener(e,update));
    p.addEventListener('error',()=>showToast('视频暂时无法播放，请检查文件是否存在','warning'));
    document.addEventListener('fullscreenchange',()=>{p.controls=document.fullscreenElement===p;});
}

async function regenerateCurrentVideo() {
    if (!currentPlayingTaskId) return;
    const tid = currentPlayingTaskId;
    closeVideoModal();
    await regenerateTask(tid);
}

// 🔄 单任务一键重新生成（无论成功还是失败均可重做）
async function regenerateTask(taskId, review=false) {
    if (!confirm(review ? `任务 [${taskId}] 的执行结果尚未确认。请先核对 ComfyUI 队列及成品；再次生成可能重复。确认已核对并重排？` : `确定要重新生成任务 [${taskId}] 吗？`)) return;
    try {
        showToast(`🔄 正在重新调度任务 [${taskId}]...`, 'info');
        const res = await fetch('/api/task/regenerate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: taskId, confirmed: review })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`✅ 任务 [${taskId}] 已加入重做队列；调度未启动时，请点击“启动渲染”。`, 'success');
            await refreshTasks();
            await refreshBatchStatus();
        } else {
            showToast(`❌ 重新生成失败: ${data.message || '未知错误'}`, 'error');
        }
    } catch (e) {
        showToast(`❌ 网络异常: ${e.message}`, 'error');
    }
}

function closeVideoModal() {
    const player = document.getElementById('player_element');
    player.pause();

}

function locateCurrentVideo() {
    if (currentPlayingFile) {
        locateFile(currentPlayingFile);
    }
}

// 0 阻塞集群状态检查 (秒回)
async function checkClusterStatus() {
    try {
        const res = await fetch('/api/servers');
        const data = await res.json();
        const servers = data.servers || [];
        const select=document.getElementById('batch_server');
        const enabled=servers.filter(s=>s.enabled!==false);
        const optionsKey=JSON.stringify(enabled.map(s=>[s.id,s.name]));
        if(select&&select.dataset.key!==optionsKey){
            const old=select.dataset.key?select.value:(data.active_server_id||'all');
            select.replaceChildren(new Option('全部启用端口（并行）','all'));
            enabled.forEach(s=>select.add(new Option('仅 '+s.name,s.id)));
            select.value=enabled.some(s=>s.id===old)?old:'all';select.dataset.key=optionsKey;
        }
        const enabledServers = servers.filter(s => s.enabled !== false);
        const onlineCount = enabledServers.filter(s => s.status && s.status.online).length;

        const currentKey = `${onlineCount}/${enabledServers.length}`;
        if (currentKey === lastClusterStatusKey) return;
        lastClusterStatusKey = currentKey;

        const el = document.getElementById('server_status');
        el.textContent = `${onlineCount} / ${enabledServers.length} 节点在线`;
    } catch (e) {}
}

// 🚀 启动多端口动态并行渲染
async function launchBatchRunner() {
    try {
        showToast('🚀 正在启动多端口并行调度集群...', 'info');
        const res = await fetch('/api/start_batch', { method: 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({server_id:document.getElementById('batch_server').value}) });
        const data = await res.json();
        if (data.success) {
            showToast('✅ 算力集群已成功启动并开始并行认领任务！', 'success');
        } else {
            showToast('⚠️ ' + (data.message || '启动未能执行'), 'warning');
        }
        await refreshBatchStatus();
        await refreshTasks();
    } catch (e) {
        showToast('❌ 启动集群发生异常: ' + e.message, 'error');
    }
}

// ⏹️ 优雅停止调度
async function stopBatchRunner() {
    if (confirm('确定要停止多端口调度吗？各端口将在完成当前单次渲染后安全退出，不会损坏任务数据。')) {
        try {
            showToast('⏹️ 已向算力集群下达优雅停止指令，当前任务完成后退出', 'info');
            await fetch('/api/stop_batch', { method: 'POST' });
            await refreshBatchStatus();
        } catch (e) {
            showToast('❌ 停止指令下发失败', 'error');
        }
    }
}

// ==========================================
// 端口管理交互
// ==========================================
async function openServerModal() {
    showDialog('server_modal');
    await loadAndRenderServers();
}

function closeServerModal() {
    hideDialog('server_modal');
    checkClusterStatus();
}

async function loadAndRenderServers() {
    const container = document.getElementById('server_list_container');
    container.innerHTML = '<div style="text-align:center; padding: 20px; color: var(--text-muted);">正在读取各端口实时连通性与负载...</div>';
    
    try {
        const res = await fetch('/api/servers');
        const data = await res.json();
        renderServerList(data.servers, data.active_server_id);
    } catch (e) {
        container.innerHTML = '<div style="color:var(--danger); padding:20px;">获取端口列表失败</div>';
    }
}

function renderServerList(servers, activeId) {
    const container = document.getElementById('server_list_container');
    container.innerHTML = '';

    (servers || []).forEach(s => {
        const isActive = (s.id === activeId);
        const isEnabled = s.enabled !== false;
        const status = s.status || {};
        const isOnline = status.online;
        const worker = s.worker_status || {};

        let workerBadge = '';
        if (!isEnabled) {
            workerBadge = '<span class="worker-live-badge badge-disabled">⚪ 已停用</span>';
        } else if (worker.state === 'BUSY') {
            workerBadge = `<span class="worker-live-badge badge-busy">⚡ 正在处理: 任务 [${worker.task_id}]</span>`;
        } else if (isOnline) {
            workerBadge = '<span class="worker-live-badge badge-idle">🔵 空闲待命中</span>';
        }

        const div = document.createElement('div');
        div.className = `server-item ${isActive ? 'active-server' : ''}`;
        
        div.innerHTML = `
            <div class="server-info-left">
                <input type="radio" name="active_server_radio" class="server-radio" 
                    ${isActive ? 'checked' : ''} 
                    onclick="setActiveServer('${s.id}')"
                    title="设为主控默认端口">
                <div>
                    <div class="server-title">
                        <span>${escapeHtml(s.name)}</span>
                        ${isActive ? '<span class="active-tag">主端口</span>' : ''}
                        <span class="server-status-pill ${isOnline ? 'pill-online' : 'pill-offline'}">
                            ${isOnline ? `🟢 在线 (${status.latency_ms}ms)` : '🔴 离线'}
                        </span>
                        ${workerBadge}
                    </div>
                    <div class="server-url">${escapeHtml(s.url)} · ${status.gpu_info || ''} ${isOnline ? `(队列: ${status.queue_running} 运行 / ${status.queue_pending} 排队)` : ''}</div>
                </div>
            </div>
            <div class="server-actions-right">
                <button class="btn-toggle-enable ${isEnabled ? 'btn-enabled' : 'btn-disabled'}" onclick="toggleServerEnable('${s.id}', ${!isEnabled})">
                    ${isEnabled ? '🟢 已启用 (参与调度)' : '⚪ 已停用 (不接单)'}
                </button>
                <button class="btn-del" onclick="deleteServer('${s.id}')">删除</button>
            </div>
        `;
        container.appendChild(div);
    });
}

async function setActiveServer(serverId) {
    const res = await fetch('/api/server/set_active', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: serverId })
    });
    const data = await res.json();
    if (data.success) {
        showToast('✅ 默认主端口已切换', 'success');
        await loadAndRenderServers();
        checkClusterStatus();
    }
}

async function toggleServerEnable(serverId, newEnabled) {
    const res = await fetch('/api/server/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: serverId, enabled: newEnabled })
    });
    const data = await res.json();
    if (data.success) {
        showToast(newEnabled ? '🟢 端口已启用，调度启动后参与渲染' : '⚪ 端口已停用', 'info');
        await loadAndRenderServers();
        checkClusterStatus();
        refreshBatchStatus();
        refreshTasks();
    }
}

async function addNewServer() {
    const name = document.getElementById('new_srv_name').value.trim();
    const url = document.getElementById('new_srv_url').value.trim();
    if (!url) {
        showToast('⚠️ 请输入算力端口的 URL 地址！', 'warning');
        return;
    }

    const res = await fetch('/api/server/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, url })
    });
    const data = await res.json();
    if (data.success) {
        showToast('✅ 算力端口添加成功！', 'success');
        document.getElementById('new_srv_name').value = '';
        document.getElementById('new_srv_url').value = '';
        await loadAndRenderServers();
        checkClusterStatus();
    } else {
        showToast('❌ ' + (data.message || '添加失败'), 'error');
    }
}

async function deleteServer(serverId) {
    if (!confirm('确定要移除此算力端口吗？')) return;

    const res = await fetch('/api/server/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: serverId })
    });
    const data = await res.json();
    if (data.success) {
        showToast('🗑️ 算力端口已删除', 'info');
        await loadAndRenderServers();
        checkClusterStatus();
    } else {
        showToast('❌ ' + (data.message || '删除失败'), 'error');
    }
}


// ==========================================
// 主流 Lightbox 素材大图浮层灯箱预览
// ==========================================
function openImageModal(category, encodedFilename) {
    const fn = decodeURIComponent(encodedFilename);
    const modal = document.getElementById('image_modal');
    modal.classList.toggle('scene-preview', category === 'scenes');
    const img = document.getElementById('image_modal_img');
    const badge = document.getElementById('image_modal_badge');
    const title = document.getElementById('image_modal_filename');
    const origBtn = document.getElementById('image_modal_orig_btn');

    let badgeText = '📁 素材图';
    if (category === 'models') badgeText = '👤 人物模特图';
    else if (category === 'apparels') badgeText = '👗 穿搭服装图';
    else if (category === 'scenes') badgeText = '🏞️ 场景首帧图';

    badge.textContent = badgeText;
    title.textContent = fn;

    // 原图完整显示；场景使用单独的 9:16 预览框。
    const origUrl = `/api/asset/${category}/${encodedFilename}`;
    
    img.src = origUrl;
    origBtn.href = origUrl;
    showDialog('image_modal');
}

function closeImageModal(e) {
    if (!e || e.target.id === 'image_modal' || e.target.classList.contains('modal-close')) {
        const modal = document.getElementById('image_modal');
        if (modal) hideDialog('image_modal');
        const img = document.getElementById('image_modal_img');
        if (img) img.src = '';
    }
}

const dialogStack=[];
const dialogFocus=new Map();
function showDialog(id){
    const el=document.getElementById(id);if(!el)return;
    if(!dialogStack.includes(id)){dialogFocus.set(id,document.activeElement);dialogStack.push(id);}
    el.style.display='flex';document.body.style.overflow='hidden';
    el.querySelector('[role="dialog"]')?.focus();
}
function hideDialog(id){
    const el=document.getElementById(id);if(el)el.style.display='none';
    const index=dialogStack.indexOf(id);if(index>=0)dialogStack.splice(index,1);
    if(document.body)document.body.style.overflow=dialogStack.length?'hidden':'';
    dialogFocus.get(id)?.focus?.();dialogFocus.delete(id);
}
window.addEventListener('keydown',e=>{
    const id=dialogStack[dialogStack.length-1];if(!id)return;
    if(e.key==='Escape'){e.preventDefault();hideDialog(id);return;}
    if(e.key==='Tab'){
        const items=Array.from(document.getElementById(id).querySelectorAll('button:not(:disabled),select,input:not(:disabled),a[href],[tabindex="0"]')).filter(el=>el.getClientRects().length);
        if(!items.length){e.preventDefault();return;}
        const first=items[0],last=items[items.length-1];
        if(e.shiftKey && (!items.includes(document.activeElement)||document.activeElement===first)){e.preventDefault();last.focus();}
        else if(!e.shiftKey && (!items.includes(document.activeElement)||document.activeElement===last)){e.preventDefault();first.focus();}
    }
});

// ==========================================================================
// 🎨 矩阵批量排单工作台 (Batch Production Studio) 前端逻辑
// ==========================================================================
let matrixSelectedApparels = new Set();
let matrixFilter = 'all'; // 'all' | 'pending' | 'done'
let matrixItems = [];
let matrixFolders = [];
let matrixSelectedFolder = '';

// 打开矩阵工作台大弹窗
async function openBatchMatrixModal() {
    const modal = document.getElementById('batch_matrix_modal');
    if (!modal) return;

    // 1. 同步左侧单项选择器的当前值
    const mainTplSelect = document.getElementById('template_select');
    const mainModelSelect = document.getElementById('model_select');
    const mainSceneSelect = document.getElementById('scene_select');

    // 填充模特锁定选择器
    const mSelect = document.getElementById('matrix_model_select');
    if (mSelect && bootstrapData.models) {
        mSelect.innerHTML = bootstrapData.models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(assetName(m))}</option>`).join('');
        if (mainModelSelect && mainModelSelect.value) {
            mSelect.value = mainModelSelect.value;
        }
        updateMatrixModelPreview(mSelect.value);
    }

    // 填充模板锁定选择器
    const tSelect = document.getElementById('matrix_template_select');
    if (tSelect && bootstrapData.templates) {
        tSelect.innerHTML = bootstrapData.templates.map(t => `<option value="${escapeHtml(t.filename)}">${escapeHtml(t.name || assetName(t.filename))}</option>`).join('');
        if (mainTplSelect && mainTplSelect.value) {
            tSelect.value = mainTplSelect.value;
        }
        updateMatrixTemplatePreview(tSelect.value);
    }

    // 填充场景锁定选择器
    const sSelect = document.getElementById('matrix_scene_select');
    if (sSelect && bootstrapData.scenes) {
        sSelect.innerHTML = bootstrapData.scenes.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(assetName(s))}</option>`).join('');
        if (mainSceneSelect && mainSceneSelect.value) {
            sSelect.value = mainSceneSelect.value;
        }
        updateMatrixScenePreview(sSelect.value);
    }

    showDialog('batch_matrix_modal');
    await loadMatrixOutputFolders();

    // 4. 获取对比状态并渲染网格
    await fetchAndRenderMatrixStatus();
}

function closeBatchMatrixModal() {
    const modal = document.getElementById('batch_matrix_modal');
    if (modal) hideDialog('batch_matrix_modal');
}

function closeBatchMatrixModalOnOverlay(e) {
    if (e.target && e.target.id === 'batch_matrix_modal') {
        closeBatchMatrixModal();
    }
}

// 加载产出目录文件夹选项
async function loadMatrixOutputFolders() {
    try {
        const res = await fetch('/api/output_folders');
        const data = await res.json();
        matrixFolders = data.folders || [];

        const fSelect = document.getElementById('matrix_folder_select');
        if (!fSelect) return;

        fSelect.innerHTML = matrixFolders.map(f => `<option value="${escapeHtml(f.folder)}">${escapeHtml(f.label)}</option>`).join('');

        if (!matrixSelectedFolder) {
            // 优先选择“昨天”的文件夹，其次选择第 1 个文件夹
            const yesterdayItem = matrixFolders.find(f => f.label.includes('昨天'));
            if (yesterdayItem) {
                matrixSelectedFolder = yesterdayItem.folder;
            } else if (matrixFolders.length > 0) {
                matrixSelectedFolder = matrixFolders[0].folder;
            }
        }
        fSelect.value = matrixSelectedFolder;
    } catch (err) {
        console.error('加载产出文件夹失败:', err);
    }
}

// 切换对照基准批次文件夹
function onMatrixFolderChange() {
    const fSelect = document.getElementById('matrix_folder_select');
    if (fSelect) {
        matrixSelectedFolder = fSelect.value;
        fetchAndRenderMatrixStatus();
    }
}

// 模特变更联动
function onMatrixModelChange() {
    const mSelect = document.getElementById('matrix_model_select');
    if (mSelect) {
        updateMatrixModelPreview(mSelect.value);
        fetchAndRenderMatrixStatus();
    }
}

function updateMatrixModelPreview(val) {
    const img = document.getElementById('matrix_model_preview');
    if (img && val) {
        img.src = `/api/asset/thumb/models/${encodeURIComponent(val)}?v=20260917`;
    }
    const statPill = document.getElementById('stat_model_pill');
    if (statPill && val) {
        statPill.textContent = val.replace(/\.[^/.]+$/, "");
    }
}

// 模板变更联动
function onMatrixTemplateChange() {
    const tSelect = document.getElementById('matrix_template_select');
    if (tSelect) {
        updateMatrixTemplatePreview(tSelect.value);
        fetchAndRenderMatrixStatus();
    }
}

function updateMatrixTemplatePreview(val) {
    const nameEl = document.getElementById('matrix_tpl_name');
    if (nameEl) nameEl.textContent = val.replace(/\.json$/i, "");

    const tpl = (bootstrapData.templates || []).find(t => t.filename === val);
    const sceneCard = document.getElementById('matrix_scene_card');
    const scenePill = document.getElementById('stat_scene_pill');
    if (sceneCard) {
        const hasScene = tpl ? tpl.has_scene : true;
        sceneCard.style.display = hasScene ? 'flex' : 'none';
        if (scenePill) scenePill.style.display = hasScene ? 'inline-block' : 'none';
        const formula=document.getElementById('scene_formula');if(formula)formula.hidden=!hasScene;
    }

    const tplPill = document.getElementById('stat_tpl_pill');
    if (tplPill && val) {
        tplPill.textContent = val.replace(/\.json$/i, "");
    }
}

// 场景变更联动
function onMatrixSceneChange() {
    const sSelect = document.getElementById('matrix_scene_select');
    if (sSelect) {
        updateMatrixScenePreview(sSelect.value);
        fetchAndRenderMatrixStatus();
    }
}

function updateMatrixScenePreview(val) {
    const img = document.getElementById('matrix_scene_preview');
    if (img && val) {
        img.src = `/api/asset/thumb/scenes/${encodeURIComponent(val)}?v=20260917`;
    }
    const scenePill = document.getElementById('stat_scene_pill');
    if (scenePill && val) {
        scenePill.textContent = val.replace(/\.[^/.]+$/, "");
    }
}

// 请求并比对服装状态
async function fetchAndRenderMatrixStatus() {
    const requestVersion = ++matrixRequestVersion;
    const statBadge = document.getElementById('matrix_folder_stat');
    if (statBadge) statBadge.textContent = '⏳ 正在极速比对中...';

    const mSelect = document.getElementById('matrix_model_select');
    const curModel = mSelect ? mSelect.value : '';
    const template = document.getElementById('matrix_template_select')?.value || '';
    const tpl = (bootstrapData.templates || []).find(t => t.filename === template);
    const scene = tpl?.has_scene ? (document.getElementById('matrix_scene_select')?.value || '') : '';

    try {
        const url = '/api/apparel_matrix_status?' + new URLSearchParams({folder: matrixSelectedFolder, model: curModel, template, scene});
        const res = await fetch(url);
        const data = await res.json();
        if (requestVersion !== matrixRequestVersion) return;

        matrixItems = data.items || [];
        const summary = data.summary || { total: matrixItems.length, done: 0, unproduced: matrixItems.length, queued: 0 };

        // 更新徽章统计
        if (statBadge) {
            statBadge.textContent = `${summary.done} 款已制作 · ${summary.unproduced} 款待制作 · ${summary.queued || 0} 款排队中`;
        }

        // 更新 Tab 计数
        const cntAll = document.getElementById('cnt_all');
        const cntPending = document.getElementById('cnt_pending');
        const cntDone = document.getElementById('cnt_done');
        if (cntAll) cntAll.textContent = summary.total;
        if (cntPending) cntPending.textContent = summary.unproduced;
        if (cntDone) cntDone.textContent = summary.done;

        // 渲染服装看板
        renderMatrixGrid();
        updateMatrixFooterStats();
    } catch (err) {
        if (requestVersion !== matrixRequestVersion) return;
        console.error('获取服装矩阵状态失败:', err);
        if (statBadge) statBadge.textContent = '❌ 比对失败';
    }
}

// 切换过滤 Tab
function setMatrixFilter(filter) {
    matrixFilter = filter;
    ['all', 'pending', 'done'].forEach(f => {
        const tab = document.getElementById(`tab_filter_${f}`);
        if (tab) {
            if (f === filter) tab.classList.add('active');
            else tab.classList.remove('active');
        }
    });
    renderMatrixGrid();
}

// 渲染服装多选看板
function renderMatrixGrid() {
    const container=document.getElementById('garment_grid_container');if(!container)return;
    const visibleItems=matrixItems.filter(i=>matrixFilter==='all'||(matrixFilter==='pending'?i.status==='UNPRODUCED':i.status==='DONE'));
    container.innerHTML=visibleItems.map(item=>{
        const selected=matrixSelectedApparels.has(item.filename), id=encodeFile(item.filename);
        const status=item.status==='DONE'?'done':item.status==='QUEUED'?'queued':'pending';
        const label={done:'已制作',queued:'排队中',pending:'待制作'}[status];
        const preview=item.status==='DONE'&&item.output_video?`<div class="garment-play-overlay"><button class="btn-play-preview" onclick="playMatrixDoneVideo(event,'${encodeFile(item.output_video)}','${encodeFile(item.task_id || '')}')">预览成片</button></div>`:'';
        return `<div class="garment-card ${selected?'selected':''}" id="garment_card_${id}" onclick="toggleMatrixItemSelection(decodeURIComponent('${id}'))"><div class="garment-checkbox-wrap" onclick="event.stopPropagation()"><input type="checkbox" class="garment-checkbox" aria-label="选择 ${escapeHtml(item.sku)}" ${selected?'checked':''} onchange="toggleMatrixItemSelection(decodeURIComponent('${id}'))"></div><div class="garment-thumb-wrap"><img class="garment-thumb" src="/api/asset/thumb/apparels/${id}?v=20260917" alt="${escapeHtml(item.sku)}" loading="lazy">${preview}</div><div class="garment-info-bar"><span class="garment-sku" title="${escapeHtml(item.filename)}">${escapeHtml(item.sku)}</span><span class="garment-status-badge status-badge-${status}">${label}</span></div></div>`;
    }).join('') || '<div class="empty-state">暂无符合条件的服装款式</div>';
}

// 切换单张服装勾选
function toggleMatrixItemSelection(filename) {
    if (matrixSelectedApparels.has(filename)) {
        matrixSelectedApparels.delete(filename);
    } else {
        matrixSelectedApparels.add(filename);
    }

    // 局部更新卡片样式和 Checkbox
    const item = matrixItems.find(i => i.filename === filename);
    if (item) {
        const card = document.getElementById(`garment_card_${encodeFile(item.filename)}`);
        if (card) {
            const cb = card.querySelector('.garment-checkbox');
            const isSelected = matrixSelectedApparels.has(filename);
            if (isSelected) {
                card.classList.add('selected');
                if (cb) cb.checked = true;
            } else {
                card.classList.remove('selected');
                if (cb) cb.checked = false;
            }
        }
    }

    updateMatrixFooterStats();
}

// 核心快捷功能：一键全选所有待制作款式
function selectAllPendingApparels() {
    const pendingItems = matrixItems.filter(i => i.status === 'UNPRODUCED');
    if (pendingItems.length === 0) {
        showToast('ℹ️ 当前对照批次中没有待制作的款式（全部已出片或正在排队）', 'info');
        return;
    }

    for (const item of pendingItems) {
        matrixSelectedApparels.add(item.filename);
    }

    renderMatrixGrid();
    updateMatrixFooterStats();
    showToast(`✅ 已一键勾选当前批次待制作的 ${pendingItems.length} 款服装！`, 'success');
}

// 反选当前看板中显示的款式
function invertMatrixSelection() {
    let targetItems = matrixItems;
    if (matrixFilter === 'pending') {
        targetItems = matrixItems.filter(i => i.status === 'UNPRODUCED');
    } else if (matrixFilter === 'done') {
        targetItems = matrixItems.filter(i => i.status === 'DONE');
    }

    for (const item of targetItems) {
        if (matrixSelectedApparels.has(item.filename)) {
            matrixSelectedApparels.delete(item.filename);
        } else {
            matrixSelectedApparels.add(item.filename);
        }
    }

    renderMatrixGrid();
    updateMatrixFooterStats();
}

// 清空全部勾选
function clearMatrixSelection() {
    matrixSelectedApparels.clear();
    renderMatrixGrid();
    updateMatrixFooterStats();
    showToast('已清空全部服装勾选', 'info');
}

// 更新底部结算栏统计与提交按钮状态
function updateMatrixFooterStats() {
    const count = matrixSelectedApparels.size;
    const selectedCount=document.getElementById('matrix_selected_count');if(selectedCount)selectedCount.textContent=count;
    const apparelPill = document.getElementById('stat_apparel_pill');
    const totalEl = document.getElementById('stat_total_tasks');
    const submitCntEl = document.getElementById('btn_submit_count');
    const submitBtn = document.getElementById('btn_batch_submit');

    if (apparelPill) apparelPill.textContent = `${count} 件服装`;
    if (totalEl) totalEl.textContent = count;
    if (submitCntEl) submitCntEl.textContent = count;

    if (submitBtn) {
        submitBtn.disabled = count === 0 || matrixSubmitting;
    }
}

// 预览已制作成片视频
function playMatrixDoneVideo(e, encodedVideoPath, encodedTaskId) {
    if (e) e.stopPropagation();
    closeBatchMatrixModal();
    openVideoModal(encodedVideoPath, decodeURIComponent(encodedTaskId || ''));
}

// ⚡ 批量下发矩阵任务入库开跑
async function submitBatchMatrixTasks() {
    if (matrixSubmitting) return;
    const count = matrixSelectedApparels.size;
    if (count === 0) {
        showToast('⚠️ 请先勾选至少一件服装款式！', 'warning');
        return;
    }

    const tSelect = document.getElementById('matrix_template_select');
    const mSelect = document.getElementById('matrix_model_select');
    const sSelect = document.getElementById('matrix_scene_select');

    const template = tSelect ? tSelect.value : '';
    const model = mSelect ? mSelect.value : '';
    const tpl = (bootstrapData.templates || []).find(t => t.filename === template);
    const scene = (tpl && tpl.has_scene && sSelect) ? sSelect.value : '';

    if (!template || !model) {
        showToast('⚠️ 请确保已锁定模板与模特！', 'warning');
        return;
    }

    const payload = {
        template: template,
        model: model,
        scene: scene,
        apparels: Array.from(matrixSelectedApparels)
    };

    const submitBtn = document.getElementById('btn_batch_submit');
    matrixSubmitting = true;
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `<span>⏳ 正在批量生成 ${count} 个任务并入库...</span>`;
    }

    try {
        const res = await fetch('/api/batch_add_tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success) {
            lastTasksFingerprint = JSON.stringify(data.tasks);
            renderTasks(data.tasks);
            showToast(`🚀 成功批量下发 ${data.count} 个排单任务！调度未启动时，请点击“启动渲染”`, 'success', 4500);

            // 清空多选并关闭弹窗
            matrixSelectedApparels.clear();
            closeBatchMatrixModal();
        } else {
            showToast('❌ 批量下发失败: ' + (data.message || '未知错误'), 'error');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = `<span>立即下发 <span id="btn_submit_count">${count}</span> 个任务</span>`;
            }
        }
    } catch (err) {
        showToast('❌ 请求异常: ' + err.message, 'error');
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = `<span>立即下发 <span id="btn_submit_count">${count}</span> 个任务</span>`;
        }
    } finally {
        matrixSubmitting = false;
        if (submitBtn) submitBtn.innerHTML = '<span>立即下发 <span id="btn_submit_count">0</span> 个任务</span>';
        updateMatrixFooterStats();
    }
}

window.onload = init;


async function receiveExistingTask(taskId) {
    try {
        const response=await fetch('/api/task/receive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:taskId})});
        const data=await response.json();
        showToast(data.success?'正在检查原任务并接收视频，不会重新生成。':data.message,data.success?'info':'warning');
        await refreshTasks();
    } catch(e){showToast('检查回传失败：'+e.message,'error');}
}
function fillLocalServer() {
    document.getElementById('new_srv_name').value='本机 ComfyUI';
    document.getElementById('new_srv_url').value='http://127.0.0.1:8188';
}
