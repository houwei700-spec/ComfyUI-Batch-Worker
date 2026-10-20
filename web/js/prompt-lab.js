'use strict';
const $ = id => document.getElementById(id);
const taskQuality = new URLSearchParams(location.search).get('mode') === '768p' ? '768p' : '480p';
const draftStorageKey = taskQuality === '768p' ? 'promptLabDraft768p' : 'promptLabDraft';
if(taskQuality === '768p') {
    document.title='768p 成品生成 · ComfyUI Batch Hub';
    $('lab_page_title').textContent='768p 成品生成';
    $('lab_mode_link').href='/prompt-lab.html';
    $('lab_mode_link').textContent='返回 480p 测试';
    $('editor_heading').textContent='编写成品任务';
    $('editor_hint').textContent='重新填写每条成品视频的提示词和时长，独立选择参考素材。';
    $('enqueue').textContent='加入成品队列';
    $('queue_heading').firstChild.textContent='768p 成品队列 ';
    $('queue_hint').textContent='成品任务使用独立的 768p 工作流；测试记录和视频保持原样。';
    $('empty_queue').textContent='还没有成品任务。上传素材，填写提示词后加入队列。';
}
const assets = {model: null, apparel: null, scene: null};
const statuses = {PENDING:'待启动',UPLOADING:'上传素材',SUBMITTING:'提交中',RUNNING:'生成中',DOWNLOADING:'下载中',DONE:'已完成',FAILED:'失败',REVIEW:'待核对'};
let tasks = [], selectedId = '', fingerprint = '', uploading = 0, requestId = '', toastTimer;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const assetUrl = a => a.source==='library'
    ? '/api/asset/thumb/'+encodeURIComponent(a.category)+'/'+encodeURIComponent(a.file)
    : '/api/prompt_lab/asset/' + encodeURIComponent(a.file);
const libraryCategory = {model:'models',apparel:'apparels',scene:'scenes'};
const UPLOADED_ASSET = '__uploaded_asset__';
const assetRevision = {model:0,apparel:0,scene:0};
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const OPTIMIZED_TARGET_BYTES = 19 * 1024 * 1024;
const formatMB = bytes => (bytes / 1024 / 1024).toFixed(1) + ' MB';
async function prepareUpload(file) {
    if(file.size <= MAX_UPLOAD_BYTES) return {blob:file,optimized:false};
    let image;
    const canvas=document.createElement('canvas');
    try {
        image=await createImageBitmap(file);
        if(!image.width||!image.height)throw new Error('无法读取图片尺寸');
        const scale=Math.min(1,8192/Math.max(image.width,image.height),Math.sqrt(32000000/(image.width*image.height)));
        let width=Math.max(1,Math.floor(image.width*scale));
        let height=Math.max(1,Math.floor(image.height*scale));
        let quality=.94;
        for(let attempt=0;attempt<8;attempt++) {
            canvas.width=width;canvas.height=height;
            const context=canvas.getContext('2d');
            if(!context)throw new Error('浏览器无法处理这张图片');
            context.imageSmoothingQuality='high';
            context.drawImage(image,0,0,width,height);
            const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/webp',quality));
            if(!blob||blob.type!=='image/webp')throw new Error('浏览器不支持 WEBP 图片优化');
            if(blob.size<=OPTIMIZED_TARGET_BYTES)return {blob,optimized:true};
            if(attempt%2===0)quality=.82;
            else {width=Math.max(1,Math.floor(width*.85));height=Math.max(1,Math.floor(height*.85));quality=.9;}
        }
        throw new Error('优化后仍超过 20 MB，请换一张较小的图片');
    } catch(e) {
        throw new Error('大图自动优化失败：'+e.message);
    } finally {
        image?.close();
        canvas.width=canvas.height=1;
    }
}
function toast(text) { $('lab_toast').textContent=text; $('lab_toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('lab_toast').hidden=true,6500); }
async function api(action, body) {
    const response=await fetch('/api/prompt_lab/'+action,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json(); if(!response.ok||!data.success) throw new Error(data.message||'请求失败'); return data;
}
async function guarded(button, operation) { button.disabled=true; try { await operation(); } catch(e) { toast(e.message); } finally { button.disabled=false; if(button.id==='lab_start') { try { await refresh(); } catch(e) { /* Polling reports connection errors. */ } } } }
function saveDraft() {
    try { localStorage.setItem(draftStorageKey,JSON.stringify({assets,rows:getRows(),requestId})); } catch(e) { toast('草稿无法保存到浏览器，请勿关闭页面：'+e.message); }
}
function getRows() { return [...document.querySelectorAll('.lab-draft')].map(row=>({prompt:row.querySelector('textarea').value,seconds:row.querySelector('input').value})); }
function refreshDraftCount() { [...document.querySelectorAll('.lab-draft strong')].forEach((el,i)=>el.textContent='任务 '+(i+1)); $('draft_count').textContent=getRows().length+' 条待加入'; }
function addRow(value={prompt:'',seconds:15}) {
    const row=document.createElement('article'); row.className='lab-draft';
    row.innerHTML='<div class="lab-draft-bar"><strong></strong><label>视频时长（秒） <input type="number" min="0.2" max="30" step="0.1" aria-label="视频时长（秒）"></label><button type="button" aria-label="移除这一条">移除</button></div><textarea aria-label="视频提示词" placeholder="输入本条视频的提示词，例如人物动作、场景、镜头运动和画面要求…"></textarea>';
    row.querySelector('textarea').value=value.prompt; row.querySelector('input').value=value.seconds;
    row.addEventListener('input',()=>{requestId='';saveDraft();});
    row.querySelector('button').onclick=()=>{row.remove();if(!getRows().length)addRow();refreshDraftCount();requestId='';saveDraft();};
    $('draft_rows').append(row);refreshDraftCount();
}
function showAsset(role) {
    const a=assets[role], image=$(role+'_image'), select=$(role+'_saved');
    for(const option of [...select.options])if(option.dataset.currentAsset)option.remove();
    let selected='';
    if(a) {
        selected=a.source==='library'?a.file:UPLOADED_ASSET;
        if(![...select.options].some(option=>option.value===selected)) {
            const option=new Option((a.source==='library'?'系统素材：':'已上传：')+a.name,selected);
            option.dataset.currentAsset='true';select.add(option);
        }
    }
    image.hidden=!a;
    if(a)image.src=assetUrl(a);else image.removeAttribute('src');
    $(role+'_name').textContent=a?a.name:(role==='scene'?'不使用场景图':'尚未选择');
    select.value=selected;
    if(role==='apparel')$('clear_apparel').hidden=!a;
    if(role==='scene')$('clear_scene').hidden=!a;
}
function selectAsset(role, asset) {
    assetRevision[role]++;
    assets[role]=asset;
    $(role+'_file').value='';
    showAsset(role);requestId='';saveDraft();
}
async function loadLibrary() {
    const data=await api('library');
    for(const role of Object.keys(assets)) {
        const select=$(role+'_saved'), category=libraryCategory[role];
        const placeholder=role==='scene'?'不使用场景图':'从系统'+(role==='model'?'模特':'服装')+'中选择…';
        select.replaceChildren(new Option(placeholder,''));
        for(const name of data[category]||[])select.add(new Option(name,name));
        showAsset(role);
    }
}
$('refresh_library').onclick=()=>guarded($('refresh_library'),async()=>{await loadLibrary();toast('系统素材列表已更新。');});
for (const role of Object.keys(assets)) {
    $(role+'_saved').onchange=event=>{
        const name=event.target.value;
        if(name===UPLOADED_ASSET)return;
        selectAsset(role,name?{source:'library',category:libraryCategory[role],file:name,name}:null);
    };
    $(role+'_file').onchange=async event=>{
        const file=event.target.files[0];if(!file)return;
        const revision=++assetRevision[role];
        uploading++;event.target.disabled=true;
        try {
            $(role+'_name').textContent='正在处理 '+file.name+'…';
            const prepared=await prepareUpload(file);
            const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.onerror=()=>reject(new Error('读取图片失败'));r.readAsDataURL(prepared.blob);});
            const name=prepared.optimized?file.name+'（自动优化）':file.name;
            const result=await api('upload',{name,data});
            if(revision!==assetRevision[role])return;
            assets[role]=result.asset;showAsset(role);requestId='';saveDraft();
            if(prepared.optimized)toast(`已自动优化 ${formatMB(file.size)} → ${formatMB(prepared.blob.size)}；电脑上的原图未修改。`);
        } catch(e){if(revision===assetRevision[role]){showAsset(role);toast(e.message);}} finally{uploading--;event.target.disabled=false;event.target.value='';}
    };
}
$('clear_scene').onclick=()=>selectAsset('scene',null);
$('clear_apparel').onclick=()=>selectAsset('apparel',null);
$('add_row').onclick=()=>{addRow();requestId='';saveDraft();$('draft_rows').scrollTop=$('draft_rows').scrollHeight;};
$('enqueue').onclick=()=>guarded($('enqueue'),async()=>{
    if(uploading)throw new Error('请等待图片上传完成');
    // Honor an explicit empty selection even if browser form restoration changed it.
    if($('scene_saved').value==='')selectAsset('scene',null);
    if(!assets.model||!assets.apparel)throw new Error('请先选择或上传人物头像和服装');
    const rows=getRows();if(rows.length>500)throw new Error('每次最多加入 500 条，请分批追加');
    for(const [i,row] of rows.entries()){
        const n=Number(row.seconds);
        if(!row.prompt.trim())throw new Error('请填写任务 '+(i+1)+' 的提示词');
        if(!Number.isFinite(n)||n<.2||n>30||Math.abs(n*10-Math.round(n*10))>.00001)throw new Error('任务 '+(i+1)+' 的时长应为 0.2–30 秒，步长 0.1 秒');
    }
    requestId ||= crypto.randomUUID();saveDraft();
    const result=await api('enqueue',{request_id:requestId,quality:taskQuality,assets,tasks:rows});
    requestId='';$('draft_rows').replaceChildren();addRow();saveDraft();await refresh();toast('已加入 '+result.created_ids.length+' 条 '+taskQuality+' '+(taskQuality==='768p'?'成品':'测试')+'任务；点击启动队列后执行。');
});
$('lab_start').onclick=()=>guarded($('lab_start'),async()=>{await api('start',{server_id:$('lab_server').value,quality:taskQuality});await refresh();});
$('lab_pause').onclick=()=>guarded($('lab_pause'),async()=>{await api('pause',{});await refresh();toast('已暂停领取；当前任务继续生成和回传。');});
function renderTasks() {
    const html=JSON.stringify(tasks);if(html===fingerprint)return;fingerprint=html;
    $('task_count').textContent=tasks.length;$('empty_queue').hidden=tasks.length>0;
    $('lab_tasks').innerHTML=tasks.map(t=>`<tr data-id="${esc(t.task_id)}" class="${selectedId===t.task_id?'selected':''}"><td>${esc(t.task_id)}<br><small>${esc(t.quality||'480p')}</small></td><td><div class="prompt-summary">${esc(t.prompt)}</div></td><td>${esc(t.seconds)} 秒</td><td>${statuses[t.status]||esc(t.status)}</td><td><div class="row-actions"><button data-action="view">${t.output_file?'详情 / 播放':'详情'}</button>${(t.prompt_id||t.client_id)&&['FAILED','REVIEW'].includes(t.status)?'<button data-action="receive">检查回传</button>':''}${['DONE','FAILED','REVIEW'].includes(t.status)?'<button data-action="retry">重新排队</button>':''}${['PENDING','DONE','FAILED'].includes(t.status)?'<button data-action="delete">删除记录</button>':''}</div></td></tr>`).join('');
    if(selectedId)showDetail(selectedId);
}
function showDetail(id) {
    selectedId=id;const task=tasks.find(t=>t.task_id===id);if(!task)return;
    document.querySelectorAll('#lab_tasks tr').forEach(row=>row.classList.toggle('selected',row.dataset.id===id));
    $('detail_title').textContent=task.task_id;
    $('detail_meta').textContent=`${task.quality||'480p'} · ${task.seconds} 秒 · ${statuses[task.status]}${task.worker_node?' · '+task.worker_node:''}${task.prompt_id?' · 云端编号 '+task.prompt_id:''}`;
    $('detail_prompt').textContent=task.prompt;$('detail_error').textContent=task.error||'';
    $('detail_assets').replaceChildren();
    for(const role of ['model','apparel','scene']) {const a=task.assets[role];if(a){const img=document.createElement('img');img.src=assetUrl(a);img.alt=a.name;img.title=a.name;$('detail_assets').append(img);}}
    const video=$('lab_video');const path=task.output_file||'';
    if(video.dataset.file!==path){video.pause();video.dataset.file=path;if(path)video.src='/api/video_stream?filename='+encodeURIComponent(path);else{video.removeAttribute('src');video.load();}}
    video.hidden=!path;$('lab_video_empty').hidden=!!path;$('locate_video').disabled=!path;
}
$('lab_tasks').onclick=event=>{
    const button=event.target.closest('button[data-action]');if(!button)return;
    const id=button.closest('tr').dataset.id;const action=button.dataset.action;
    if(action==='view'){showDetail(id);return;}
    guarded(button,async()=>{
        const task=tasks.find(t=>t.task_id===id);
        if(action==='delete'&&!confirm('删除这条'+(taskQuality==='768p'?'成品':'测试')+'记录？已生成的视频仍保留。'))return;
        let confirmed=false;
        if(action==='retry'&&task.status==='REVIEW') {confirmed=confirm('这条任务的云端状态尚未确认。请先核对云端队列和成品；重新排队可能再次生成。确认已核对并仍要重新排队？');if(!confirmed)return;}
        await api('task',{task_id:id,action,confirmed});await refresh();
    });
};
$('locate_video').onclick=()=>guarded($('locate_video'),async()=>{const t=tasks.find(t=>t.task_id===selectedId);if(!t?.output_file)return;const r=await fetch('/api/locate_file?filename='+encodeURIComponent(t.output_file),{method:'POST'});const d=await r.json();if(!d.success)throw new Error(d.message||'定位失败');});
async function refresh() {
    const data=await api('state');tasks=data.tasks.filter(t=>(t.quality||'480p')===taskQuality);renderTasks();
    const workers=Object.values(data.workers||{});
    const active=data.tasks.filter(t=>['UPLOADING','SUBMITTING','RUNNING','DOWNLOADING'].includes(t.status)).length;
    $('lab_status').textContent=data.starting?'正在检查各端口，暂未领取任务':data.enabled?`多端口并行中 · ${active} 条正在执行或回传`:data.busy?'已暂停领取 · 当前任务仍在生成或回传':'已暂停 · 等待手动启动';
    $('lab_notice').hidden=!data.notice;$('lab_notice').textContent=data.notice||'';
    $('lab_pause').disabled=!data.enabled&&!data.starting;
    $('lab_start').disabled=data.busy;
    const workerLabels={CHECKING:'检查中',WAITING:'等待端口空闲',IDLE:'等待任务',STOPPED:'已停止领取',DISABLED:'已停用',ERROR:'异常，已停止领取',UNAVAILABLE:'本次不可用',...statuses};
    $('lab_workers').innerHTML=workers.map(w=>`<div class="lab-worker"><strong>${esc(w.name)}</strong><span>${esc(workerLabels[w.state]||w.state)}${w.task_id?' · '+esc(w.task_id):''}</span>${w.error?`<small>${esc(w.error)}</small>`:''}</div>`).join('');
    const select=$('lab_server'); const key=JSON.stringify(data.servers);
    if(select.dataset.key!==key){const old=select.dataset.key?select.value:(data.preferred_server_id||'all');select.replaceChildren();const all=document.createElement('option');all.value='all';all.textContent=`全部启用端口 · ${data.servers.length} 个并行`;select.append(all);for(const s of data.servers){const o=document.createElement('option');o.value=s.id;o.textContent='仅 '+s.name;select.append(o);}if(data.servers.some(s=>s.id===old))select.value=old;select.dataset.key=key;}
}
$('import_open').onclick=()=>{$('import_error').textContent='';$('import_dialog').showModal();};
$('import_apply').onclick=()=>{try{const rows=JSON.parse($('import_text').value);if(!Array.isArray(rows)||!rows.length||rows.length+getRows().filter(r=>r.prompt.trim()).length>500)throw new Error('请提供任务数组，总数不超过 500 条');for(const r of rows){if(typeof r.prompt!=='string'||!r.prompt.trim()||!Number.isFinite(Number(r.seconds))||Number(r.seconds)<.2||Number(r.seconds)>30)throw new Error('每项必须有 prompt 和 0.2–30 秒的 seconds');}if(getRows().length===1&&!getRows()[0].prompt.trim())$('draft_rows').replaceChildren();rows.forEach(addRow);requestId='';saveDraft();$('import_dialog').close();}catch(e){$('import_error').textContent=e.message;}};
try{const saved=JSON.parse(localStorage.getItem(draftStorageKey)||'null');if(saved){Object.assign(assets,saved.assets);for(const role of Object.keys(assets))showAsset(role);(saved.rows||[]).forEach(addRow);requestId=saved.requestId||'';}}catch(e){toast('本地草稿无法读取，请重新填写。');}
if(!getRows().length)addRow();
loadLibrary().catch(e=>toast('读取系统素材失败：'+e.message));
async function poll(){try{await refresh();}catch(e){$('lab_status').textContent='连接失败：'+e.message;}finally{setTimeout(poll,2500);}}
poll();
