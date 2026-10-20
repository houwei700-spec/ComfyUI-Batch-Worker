(function(root) {
    'use strict';
    function duration(prompt) {
        const lines=prompt.split('\n');
        const first=lines.find(line=>line.trim()&&!/^[【#]/.test(line.trim()))||'';
        const candidates=[];
        // Only opening specification prose or explicitly named total durations;
        // never derive duration from dialogue or shot timestamps.
        for(const line of lines) {
            if(line!==first&&!/(?:视频时长|总时长|成片时长|视频长度)\s*[:：为是]?/.test(line))continue;
            const pattern=line===first?/(\d+(?:\.\d+)?)\s*(秒|seconds?\b|s\b)/gi:/(?:视频时长|总时长|成片时长|视频长度)\s*[:：为是]?\s*(\d+(?:\.\d+)?)\s*(秒|seconds?\b|s\b)/gi;
            for(const match of line.matchAll(pattern)) {
                const before=line.slice(Math.max(0,match.index-12),match.index);
                const after=line.slice(match.index+match[0].length,match.index+match[0].length+6);
                if(/(?:[\d.\-—–~～至到]|\d[:：])\s*$/.test(before)||/(?:前|约|大约|约为|停留|等待|停顿|用时|最后)\s*$/.test(before))continue;
                candidates.push({value:Number(match[1]),upper:/^\s*(以内|以下|左右)/.test(after)});
            }
        }
        const unique=[...new Set(candidates.map(c=>c.value))];
        if(!unique.length)return {seconds:null,reason:'未找到明确总时长，请填写或使用统一默认时长。'};
        if(unique.length>1)return {seconds:null,reason:'总时长存在冲突：'+unique.join(' / ')+' 秒，请核对。'};
        if(!validSeconds(unique[0]))return {seconds:null,reason:'文档时长 '+unique[0]+' 秒超出 0.2–30 秒或不是 0.1 秒步长，请调整。'};
        return {seconds:unique[0],reason:candidates.some(c=>c.upper)?'文档写的是时长范围；建议 '+unique[0]+' 秒，请核对。':''};
    }
    function validSeconds(value) {return Number.isFinite(value)&&value>=.2&&value<=30&&Math.abs(value*10-Math.round(value*10))<.00001;}
    function parse(text) {
        if(typeof text!=='string'||!text.trim())throw new Error('文档为空。');
        if(text.includes('\u0000'))throw new Error('无法读取文本编码，请另存为 UTF-8 文本。');
        const normalized=text.replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n');
        const lines=normalized.split('\n');
        const blocks=[];let open=null,title='',body=[],headingIndex=-1;
        for(let i=0;i<lines.length;i++) {
            const line=lines[i], fence=line.match(/^\s{0,3}(`{3,}|~{3,})([^`]*)$/);
            if(open) {
                if(fence&&fence[1][0]===open[0]&&fence[1].length>=open.length&&!fence[2].trim()) {
                    const prompt=body.join('\n').replace(/^\n|\n$/g,'');
                    if(!prompt.trim())throw new Error((title||'第 '+(blocks.length+1)+' 条')+' 的提示词为空。');
                    blocks.push({title:title||'提示词 '+(blocks.length+1),prompt,headingIndex});
                    open=null;body=[];
                } else body.push(line);
            } else if(fence) {
                if(!['','text','txt','plaintext','prompt'].includes(fence[2].trim().toLowerCase()))throw new Error('发现非提示词代码块（'+fence[2].trim()+'），请仅保留 text 提示词块。');
                open=fence[1];
            } else {
                const heading=line.match(/^#{1,6}\s+(.+?)\s*#*\s*$/);
                if(heading){title=heading[1];headingIndex=i;}
            }
        }
        if(open)throw new Error('文档末尾有未闭合的提示词代码块，请补齐结束标记。');
        const warnings=[];
        if(!blocks.length) {
            const markers=[];
            lines.forEach((line,i)=>{const m=line.match(/^(?:#{1,6}\s+)?((?:任务|场景|提示词)\s*\d+\s*[:：、.．)）\-]?.*|\d+\s*[.．、)）]\s*\S.*)$/);if(m)markers.push({i,title:m[1]});});
            if(!markers.length)throw new Error('无法确定任务边界。请为每条使用“## 1. 标题”或“任务1：标题”，或把每条提示词放进独立 text 代码块。');
            markers.forEach((marker,index)=>{
                const prompt=lines.slice(marker.i+1,markers[index+1]?.i??lines.length).join('\n').trim();
                if(!prompt)throw new Error(marker.title+' 的提示词为空。');
                blocks.push({title:marker.title,prompt});
            });
            warnings.push('按编号标题拆分，请在预览中核对每条内容边界。');
        } else {
            const headings=lines.map((line,i)=>({line,i})).filter(({line})=>/^##\s+(?:\d+[.．、)）]|(?:任务|场景|提示词)\s*\d+)/.test(line));
            for(const h of headings)if(!blocks.some(b=>b.headingIndex===h.i))throw new Error(h.line+' 下没有完整提示词代码块，请补齐后导入。');
            if(new Set(blocks.map(b=>b.headingIndex)).size!==blocks.length)warnings.push('同一标题下有多个代码块，每个代码块将作为一条任务，请核对。');
        }
        if(blocks.length>500)throw new Error('识别到 '+blocks.length+' 条，每批最多 500 条，请拆分文档。');
        const declared=normalized.match(/^#\s*(\d+)\s*(?:个|条)/m);
        if(declared&&Number(declared[1])!==blocks.length)throw new Error('标题写有 '+declared[1]+' 条，但实际识别到 '+blocks.length+' 条，请检查缺失或多余代码块。');
        return {rows:blocks.map(({title,prompt})=>({title,prompt,...duration(prompt)})),warnings};
    }
    const api={parse,duration,validSeconds};
    if(typeof module!=='undefined'&&module.exports)module.exports=api;
    else root.PromptDocument=api;
})(typeof globalThis==='undefined'?this:globalThis);
