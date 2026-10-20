(function(root){
    'use strict';
    const active=new Set(['UPLOADING','SUBMITTING','RUNNING','DOWNLOADING']);
    function select(tasks,filter='',query='',page=1,size=10) {
        const needle=String(query).trim().toLocaleLowerCase();
        const counts={all:tasks.length,PENDING:0,ACTIVE:0,DONE:0,FAILED:0,REVIEW:0};
        for(const task of tasks){if(active.has(task.status))counts.ACTIVE++;else if(task.status in counts)counts[task.status]++;}
        const matches=tasks.filter(task=>(!filter||(filter==='ACTIVE'?active.has(task.status):task.status===filter))&&(!needle||[task.task_id,task.prompt,task.worker_node].some(value=>String(value||'').toLocaleLowerCase().includes(needle))));
        const pages=Math.max(1,Math.ceil(matches.length/size));
        const current=Math.min(pages,Math.max(1,Number(page)||1));
        return {rows:matches.slice((current-1)*size,current*size),total:matches.length,pages,page:current,counts};
    }
    const api={select};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.QueueView=api;
})(typeof globalThis==='undefined'?this:globalThis);
