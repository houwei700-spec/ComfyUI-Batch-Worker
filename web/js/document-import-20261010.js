'use strict';
let documentPending=null, documentLoading=false;
const documentHistoryKey=draftStorageKey+'DocumentHistory';
function documentMessage(message,error=false) {
    $('document_feedback').hidden=false;
    $('document_feedback').textContent=message;
    $('document_feedback').classList.toggle('document-warning',error);
}
async function readPromptDocument(bytes,name) {
    if(documentLoading)return;
    documentLoading=true;$('document_choose').disabled=true;
    try {
        if(!/\.(md|txt)$/i.test(name))throw new Error('请选择 .md 或 .txt 文档。');
        if(bytes.byteLength>2*1024*1024)throw new Error('文档超过 2 MB，请拆分后导入。');
        let text;
        try {text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch(e){throw new Error('文档不是 UTF-8 编码，请另存为 UTF-8 后导入。');}
        const result=PromptDocument.parse(text);
        const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text.replace(/\r\n?/g,'\n')));
        const hash=[...new Uint8Array(digest)].map(n=>n.toString(16).padStart(2,'0')).join('');
        let history=[];
        try {history=JSON.parse(localStorage.getItem(documentHistoryKey)||'[]');if(!Array.isArray(history))history=[];}catch(e){history=[];}
        const existing=new Set(getRows().map(r=>r.prompt.trim()).filter(Boolean)),incoming=new Set();
        let duplicates=0;
        result.rows.forEach(row=>{const prompt=row.prompt.trim();if(existing.has(prompt)||incoming.has(prompt))duplicates++;incoming.add(prompt);});
        const duplicate=history.includes(hash)||duplicates>0;
        documentPending={...result,name,hash,history,duplicate};
        $('document_summary').textContent=name+' · 已识别 '+result.rows.length+' 条 · '+qualityLabel+' · 按原文顺序追加';
        $('document_warnings').textContent=result.warnings.join('\n');$('document_warnings').hidden=!result.warnings.length;
        $('document_duplicate').hidden=!duplicate;$('document_duplicate_ok').checked=false;
        $('document_error').textContent='';$('document_rows').replaceChildren();
        result.rows.forEach((row,index)=>{
            const item=document.createElement('article');item.className='document-item';
            const bar=document.createElement('div');bar.className='document-item-bar';
            const title=document.createElement('strong');title.textContent=(index+1)+'. '+row.title.replace(/^\d+[.．、)）]\s*/,'');
            const label=document.createElement('label');label.textContent='视频时长（秒） ';
            const input=document.createElement('input');input.type='number';input.min='.2';input.max='30';input.step='.1';input.value=row.seconds??'';input.setAttribute('aria-label','导入任务 '+(index+1)+' 时长');
            label.append(input);bar.append(title,label);item.append(bar);
            const note=document.createElement('p');note.textContent=row.reason||'已识别总时长';note.className=row.reason?'document-warning':'lab-muted';item.append(note);
            const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
            summary.textContent='查看完整提示词 · '+row.prompt.length+' 字符';pre.textContent=row.prompt;details.append(summary,pre);item.append(details);
            $('document_rows').append(item);
        });
        $('document_dialog').showModal();
    } catch(e) {documentMessage(e.message,true);} finally {documentLoading=false;$('document_choose').disabled=false;}
}
async function selectDocument(file) {
    if(!file)return;
    if(file.size>2*1024*1024){documentMessage('文档超过 2 MB，请拆分后导入。',true);return;}
    try {await readPromptDocument(await file.arrayBuffer(),file.name);}catch(e){documentMessage('读取文档失败：'+e.message,true);}
}
$('document_choose').onclick=()=>$('document_file').click();
$('document_file').onchange=async event=>{await selectDocument(event.target.files[0]);event.target.value='';};
const drop=$('document_drop');
drop.addEventListener('dragover',event=>{event.preventDefault();drop.classList.add('dragover');});
drop.addEventListener('dragleave',()=>drop.classList.remove('dragover'));
drop.addEventListener('drop',async event=>{event.preventDefault();drop.classList.remove('dragover');if(event.dataTransfer.files.length!==1){documentMessage('请一次拖入一个文档。',true);return;}await selectDocument(event.dataTransfer.files[0]);});
$('document_cancel').onclick=()=>$('document_dialog').close();
$('document_dialog').addEventListener('close',()=>{documentPending=null;});
$('document_fill').onclick=()=>{
    const value=Number($('document_default').value);
    if(!PromptDocument.validSeconds(value)){$('document_error').textContent='默认时长应为 0.2–30 秒，步长 0.1 秒。';return;}
    for(const input of $('document_rows').querySelectorAll('input'))if(!input.value)input.value=value;
    $('document_error').textContent='';
};
$('document_apply').onclick=()=>{
    if(!documentPending)return;
    const data=documentPending;
    if(data.duplicate&&!$('document_duplicate_ok').checked){$('document_error').textContent='检测到重复导入。请核对后勾选“仍要再次追加”，或取消。';return;}
    const values=[...$('document_rows').querySelectorAll('input')].map(input=>Number(input.value));
    const invalid=values.findIndex(value=>!PromptDocument.validSeconds(value));
    if(invalid>=0){$('document_error').textContent='请核对第 '+(invalid+1)+' 条时长（0.2–30 秒，步长 0.1 秒）。';return;}
    const existing=getRows().filter(row=>row.prompt.trim());
    if(existing.length+data.rows.length>500){$('document_error').textContent='追加后超过 500 条，请分批导入。';return;}
    // Persist the complete new draft before changing the editor; failure leaves it intact.
    const added=data.rows.map((row,i)=>({title:row.title,prompt:row.prompt,seconds:values[i]}));
    try {localStorage.setItem(draftStorageKey,JSON.stringify({assets,rows:[...existing,...added],requestId:''}));}
    catch(e){$('document_error').textContent='浏览器草稿空间不足，尚未追加。请减少本批条数。';return;}
    requestId='';
    for(const row of [...$('draft_rows').children])if(!row.querySelector('textarea').value.trim())row.remove();
    added.forEach(addRow);
    try{localStorage.setItem(documentHistoryKey,JSON.stringify([...new Set([...data.history,data.hash])].slice(-100)));}catch(e){/* Draft is already saved; content comparison still detects duplicates. */}
    $('document_dialog').close();
    documentMessage('已从「'+data.name+'」追加 '+added.length+' 条任务到编写区。请核对素材后加入队列。');
};
