'use strict';
(() => {
    const key=draftStorageKey+':dialogue:v1';let session=null,index=0,review=false;
    const dialog=document.createElement('dialog');dialog.id='dialogue_dialog';
    dialog.innerHTML=`<header class="dialogue-header"><div><small>ComfyUI Batch Hub · ${taskQuality==='768p'?'832p成品':'480p测试'}</small><h2>批量文案编辑</h2></div><button id="dialogue_close" class="btn secondary small">保存并返回任务编写</button></header>
    <div class="dialogue-toolbar"><span id="dialogue_step">提取文案 → 分模板修改 → 核对并回填</span><span>修改只回填到编写区草稿</span></div>
    <div class="dialogue-layout"><aside><h3>模板列表</h3><input id="dialogue_search" placeholder="搜索模板或台词" aria-label="搜索模板或台词"><div id="dialogue_list"></div></aside>
    <section class="dialogue-main"><div id="dialogue_heading"></div><div id="dialogue_content"></div></section>
    <aside class="dialogue-source"><h3>原提示词定位</h3><p class="lab-muted">点击台词可定位。漏提取时，选中下方原文中的台词后手动添加。</p><textarea id="dialogue_source" readonly aria-label="原提示词定位"></textarea><button id="dialogue_manual" class="btn secondary small">提取选中的原文</button><p class="lab-muted">说话人可核对标注；只替换台词正文，保留原有角色设定、时间、动作及场景。</p><div id="dialogue_stats"></div></aside></div>
    <p id="dialogue_error" role="alert"></p><footer class="dialogue-footer"><span id="dialogue_summary"></span><div><button id="dialogue_back" class="btn secondary small" hidden>返回继续编辑</button><button id="dialogue_save" class="btn secondary small">保存编辑草稿</button><button id="dialogue_preview" class="btn">预览替换</button><button id="dialogue_apply" class="btn" hidden>确认回填到提示词</button></div></footer>`;
    document.body.append(dialog);
    const error=e=>{$('dialogue_error').textContent=e.message||String(e);};
    function save(){localStorage.setItem(key,JSON.stringify(session));}
    function guardedEdit(fn){try{$('dialogue_error').textContent='';fn();}catch(e){error(e);}}
    function changes(t){return t.entries.filter(e=>e.text!==e.original).length;}
    function title(t,i){return (i+1)+'. '+(t.row.title||'任务 '+(i+1)).replace(/^\d+[.．、)）]\s*/,'');}
    function summary(){
        const changed=session.templates.filter(t=>changes(t));
        $('dialogue_summary').textContent=`已修改 ${changed.reduce((n,t)=>n+changes(t),0)} 句 · 涉及 ${changed.length} 个模板 · 尚未回填`;
        $('dialogue_stats').textContent='当前模板：'+session.templates[index].entries.filter(e=>!e.confirmed).length+' 句待核对。未提取到的格式可手动选中原文添加。';
    }
    function list(){
        const query=$('dialogue_search').value.trim();
        $('dialogue_list').innerHTML=session.templates.map((t,i)=>{
            if(query&&!((t.row.title||'')+t.row.prompt).includes(query))return '';
            return `<button class="dialogue-template ${index===i?'active':''}" data-template="${i}"><strong>${esc(title(t,i))}</strong><small>${t.entries.length} 句 · 修改 ${changes(t)} 句 · 待核对 ${t.entries.filter(e=>!e.confirmed).length} 句</small></button>`;
        }).join('');
    }
    function diff(t,after,compact=false){
        if(compact)return t.entries.filter(e=>e.text!==e.original).map(e=>esc(e.time+' · '+e.speaker+'\n…'+t.row.prompt.slice(Math.max(0,e.start-65),e.start))+`<mark>${esc(after?e.text:e.original)}</mark>`+esc(t.row.prompt.slice(e.end,e.end+40))+'…').join('\n\n');
        let pos=0,html='';
        for(const e of [...t.entries].sort((a,b)=>a.start-b.start)){
            html+=esc(t.row.prompt.slice(pos,e.start));
            html+=e.text!==e.original?`<mark>${esc(after?e.text:e.original)}</mark>`:esc(e.original);pos=e.end;
        }
        return html+esc(t.row.prompt.slice(pos));
    }
    function render(){
        const t=session.templates[index];list();summary();
        $('dialogue_step').textContent=review?'③ 核对替换范围，确认后回填':'② 分模板修改台词，可随时保存草稿';
        $('dialogue_heading').innerHTML=`<h3>${esc(title(t,index))}</h3><p class="lab-muted">${esc(t.row.seconds)} 秒 · ${t.entries.length} 句台词 · 修改 ${changes(t)} 句</p>`;
        $('dialogue_source').value=t.row.prompt;
        $('dialogue_source').setSelectionRange(0,0);
        $('dialogue_manual').disabled=review;
        $('dialogue_back').hidden=!review;$('dialogue_apply').hidden=!review;$('dialogue_preview').hidden=review;
        if(review){
            $('dialogue_content').innerHTML=`<label class="dialogue-scope"><input type="checkbox" id="dialogue_selected" ${t.selected?'checked':''} ${changes(t)?'':'disabled'}> 回填此模板（${changes(t)}句修改）</label><div class="dialogue-diff"><div><h4>原提示词 · 修改位置</h4><pre>${diff(t,false,true)||'没有修改'}</pre></div><div><h4>回填后提示词 · 修改位置</h4><pre>${diff(t,true,true)||'没有修改'}</pre></div></div><details><summary>展开完整提示词对比</summary><div class="dialogue-diff"><pre>${diff(t,false)}</pre><pre>${diff(t,true)}</pre></div></details>`;
            $('dialogue_selected').onchange=e=>{t.selected=e.target.checked;guardedEdit(save);};
        }else{
            $('dialogue_content').innerHTML=t.entries.length?t.entries.map((e,i)=>`<article class="dialogue-entry" data-entry="${i}"><div class="dialogue-entry-meta"><strong>第 ${i+1} 句 · ${esc(e.time)}</strong><label>说话人（核对标注）<input class="dialogue-speaker" value="${esc(e.speaker)}" aria-label="第${i+1}句说话人"></label><label><input type="checkbox" class="dialogue-confirm" ${e.confirmed?'checked':''}> 已核对是此人的台词</label><small>${esc(e.note)}</small><button class="lab-link" data-ignore="${i}">忽略此段</button></div><div><label>原台词</label><p class="dialogue-original">${esc(e.original)}</p><button class="lab-link" data-locate="${i}">定位原文</button></div><div><label>修改后的台词</label><textarea class="dialogue-text" aria-label="第${i+1}句修改后的台词">${esc(e.text)}</textarea><small class="lab-muted">台词变长时，请核对口播时间</small><button class="lab-link" data-restore="${i}">恢复原句</button></div></article>`).join(''):'<p class="lab-empty">未自动识别到台词。请在右侧选中台词原文，点击“提取选中的原文”，再核对说话人。</p>';
        }
    }
    $('dialogue_open').onclick=()=>guardedEdit(()=>{
        const rows=getRows();if(!rows.some(r=>r.prompt.trim()))throw new Error('请先填写提示词或导入文档。');
        const sig=DialogueCore.signature(rows);let saved;
        try{saved=JSON.parse(localStorage.getItem(key)||'null');const pending=JSON.parse(localStorage.getItem(draftStorageKey)||'{}').dialogueSession;if(pending?.signature===sig&&saved?.signature!==sig)saved=pending;}catch(e){/* Old invalid session cannot replace the draft. */}
        if(saved?.version===1&&saved.signature===sig)session=saved;
        else{
            if(saved&&!confirm('提示词草稿已变化。重新提取会替换之前保存的文案编辑草稿，是否继续？'))return;
            session={version:1,signature:sig,templates:rows.map(row=>({row,entries:DialogueCore.extract(row.prompt),selected:true}))};
        }
        session.draftToken=localStorage.getItem(draftStorageKey);
        index=0;review=false;$('dialogue_search').value='';render();save();dialog.showModal();
    });
    $('dialogue_search').oninput=list;
    $('dialogue_list').onclick=e=>{const b=e.target.closest('[data-template]');if(b){index=Number(b.dataset.template);render();}};
    $('dialogue_content').oninput=e=>guardedEdit(()=>{
        const article=e.target.closest('[data-entry]');if(!article)return;
        const entry=session.templates[index].entries[Number(article.dataset.entry)];
        if(e.target.classList.contains('dialogue-text'))entry.text=e.target.value;
        if(e.target.classList.contains('dialogue-speaker')){entry.speaker=e.target.value;entry.confirmed=false;article.querySelector('.dialogue-confirm').checked=false;}
        if(e.target.classList.contains('dialogue-confirm')){
            if(!entry.speaker.trim()||entry.speaker==='待核对'){e.target.checked=false;throw new Error('请先填写或核对说话人。');}
            entry.confirmed=e.target.checked;
        }
        summary();list();save();
    });
    $('dialogue_content').onclick=e=>guardedEdit(()=>{
        const b=e.target.closest('button');if(!b)return;
        const t=session.templates[index];
        if(b.dataset.locate!==undefined){const item=t.entries[Number(b.dataset.locate)];const source=$('dialogue_source');source.focus();source.setSelectionRange(item.start,item.end);return;}
        if(b.dataset.restore!==undefined)t.entries[Number(b.dataset.restore)].text=t.entries[Number(b.dataset.restore)].original;
        if(b.dataset.ignore!==undefined)t.entries.splice(Number(b.dataset.ignore),1);
        save();render();
    });
    $('dialogue_manual').onclick=()=>guardedEdit(()=>{
        const t=session.templates[index],field=$('dialogue_source');let start=field.selectionStart,end=field.selectionEnd;
        while(start<end&&/\s/.test(t.row.prompt[start]))start++;
        while(end>start&&/\s/.test(t.row.prompt[end-1]))end--;
        if(start===end)throw new Error('请先在右侧原文中选中一段台词。');
        if(t.entries.some(e=>start<e.end&&end>e.start))throw new Error('这段原文与已提取台词重叠，请先忽略原有条目。');
        const original=t.row.prompt.slice(start,end);
        t.entries.push({start,end,original,text:original,speaker:'待核对',time:'手动提取',confirmed:false,note:'请核对说话人和台词范围'});
        t.entries.sort((a,b)=>a.start-b.start);save();render();
    });
    function close(){guardedEdit(()=>{save();dialog.close();});}
    $('dialogue_close').onclick=close;
    dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
    $('dialogue_save').onclick=()=>guardedEdit(()=>{save();$('dialogue_summary').textContent='编辑草稿已保存，可关闭后继续。';});
    $('dialogue_preview').onclick=()=>guardedEdit(()=>{
        if(DialogueCore.signature(getRows())!==session.signature)throw new Error('编写区原文已变化，请返回并重新提取。');
        session.templates.forEach(t=>DialogueCore.apply(t.row.prompt,t.entries));
        if(!session.templates.some(changes))throw new Error('还没有修改台词。');
        review=true;render();save();
    });
    $('dialogue_back').onclick=()=>{review=false;render();};
    function replaceDraft(rows,undo,pending=null){
        // One write contains both the new draft and rollback snapshot before UI mutation.
        localStorage.setItem(draftStorageKey,JSON.stringify({assets,rows,requestId:'',dialogueUndo:undo,dialogueSession:pending}));
        requestId='';$('draft_rows').replaceChildren();rows.forEach(addRow);refreshDraftCount();
    }
    $('dialogue_apply').onclick=()=>guardedEdit(()=>{
        if(localStorage.getItem(draftStorageKey)!==session.draftToken)throw new Error('草稿已在其他操作或页面中更新，请返回任务编写并刷新核对，暂未回填。');
        const before=getRows();if(DialogueCore.signature(before)!==session.signature)throw new Error('编写区原文已变化，请重新提取。');
        let count=0;
        const after=session.templates.map(t=>{if(!t.selected||!changes(t))return t.row;count+=changes(t);return {...t.row,prompt:DialogueCore.apply(t.row.prompt,t.entries)};});
        if(!count)throw new Error('请选择至少一个已修改的模板。');
        if(after.some(r=>r.prompt.length>50000))throw new Error('回填后单条提示词超过 50000 字，请缩短台词。');
        const remaining=session.templates.map((t,i)=>t.selected&&changes(t)?{row:after[i],entries:DialogueCore.extract(after[i].prompt),selected:true}:t);
        const pending=remaining.some(changes)?{version:1,signature:DialogueCore.signature(after),templates:remaining}:null;
        replaceDraft(after,{before,afterSignature:DialogueCore.signature(after)},pending);
        localStorage.removeItem(key);session=null;dialog.close();toast(`已回填 ${count} 句台词。可核对后加入队列，或撤销本次回填。`);
    });
    $('dialogue_undo').onclick=()=>{try{
        const saved=JSON.parse(localStorage.getItem(draftStorageKey)||'{}'),undo=saved.dialogueUndo;
        if(!undo)throw new Error('没有可以撤销的文案回填。');
        if(DialogueCore.signature(getRows())!==undo.afterSignature)throw new Error('回填后提示词或时长已被修改，为避免覆盖，请手动调整。');
        replaceDraft(undo.before,null);toast('已恢复回填前的提示词。');
    }catch(e){toast(e.message);}};
})();
