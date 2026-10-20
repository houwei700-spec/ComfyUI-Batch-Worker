(function(root){
    'use strict';
    const parserVersion=2;
    const rolePattern=/((?:画外|镜头后)[\u4e00-\u9fff]{0,8}?(?:邻居|同事|朋友|女伴|闺蜜|顾客|店员|女声|男声|同学|旅客)|女主角|男主角|主角|旁白|女邻居|女同事|朋友|邻居|同事|女伴|闺蜜|店员|顾客|\b[AB]\b|\bS\d+\b|角色[一二三123])/i;
    const timeSource='(?:\\d{1,2}:){1,2}\\d{2}(?:\\.\\d+)?\\s*[—–‑−~～\\-至]\\s*(?:\\d{1,2}:){1,2}\\d{2}(?:\\.\\d+)?|\\d+(?:\\.\\d+)?\\s*[—–‑−~～\\-至]\\s*\\d+(?:\\.\\d+)?\\s*(?:秒|s\\b)';
    const timePattern=new RegExp(timeSource,'i');
    function speaker(prefix,tagged,nonPeople){
        if(tagged){
            // Speaker S2 and reference Subject 2 are independent identifiers.
            const voice=prefix.match(/(?:^|[.!?:：]\s*)(?:an?\s+|the\s+)?off[\s\-‑–]*screen\s+(male|female)\s+voice(?:\s*\((S\d+)\))?/i);
            if(voice)return {speaker:(voice[1].toLowerCase()==='male'?'画外男声':'画外女声')+(voice[2]?'（'+voice[2].toUpperCase()+'）':''),confirmed:true};
            const subjects=[...prefix.matchAll(/<Subject\s+(\d+)>/gi)];
            const explicit=prefix.match(/<Subject\s+(\d+)>\s+(?:says?|asks?|replies|speaks?|responds?|whispers?|shouts?|delivers)\b/i);
            const subject=explicit||subjects[0];
            if(subject){
                const id=subject[1],certain=!nonPeople.has(id)&&(!!explicit||new Set(subjects.map(m=>m[1])).size===1);
                return {speaker:'Subject '+id,confirmed:certain};
            }
        }
        const role=prefix.match(rolePattern);
        return {speaker:role?role[0]:'待核对',confirmed:!!role};
    }
    function extract(text){
        const entries=[],tags=[...text.matchAll(/<d\s*>([\s\S]*?)<\/d\s*>/gi)],nonPeople=new Set();
        for(const m of text.matchAll(/^\s*<Subject\s+(\d+)>\s+is\s+([^\r\n]+)/gmi)){
            if(/^(?:the\s+|a\s+|an\s+)?(?:complete\s+|universal\s+)?(?:upper[\s‑-]body\s+|lower[\s‑-]body\s+)?(?:outerwear\s+)?(?:garment|apparel|clothing|jacket|dress|coat)\b/i.test(m[2]))nonPeople.add(m[1]);
        }
        function add(start,end,prefix,time,tagged=false,quoted=false){
            const original=text.slice(start,end);if(!original.trim())return;
            const who=speaker(prefix,tagged,nonPeople);
            entries.push({start,end,original,text:original,...who,time,uncertain:!who.confirmed,
                note:who.confirmed?'':tagged?'已识别台词正文，请核对说话人':quoted?'引号内容可能不是对白，请核对原文':'请核对说话人'});
        }
        let previousTagEnd=0,tagTime='未标注';
        for(const tag of tags){
            const openLength=tag[0].indexOf('>')+1,body=tag[1];
            // Only the speech is editable; keep language markers and spacing intact.
            const leading=body.match(/^\s*(?:\[[^\]\r\n]+\]\s*)?/)[0].length;
            const start=tag.index+openLength+leading,end=tag.index+openLength+body.trimEnd().length;
            const before=text.slice(previousTagEnd,tag.index);
            const windows=[...before.matchAll(new RegExp(timeSource,'gi'))],window=windows[windows.length-1];
            const context=window?before.slice(window.index+window[0].length):before.split(/\r?\n/).slice(-3).join('\n');
            tagTime=window?window[0]:before.includes('\n')?'未标注':tagTime;
            add(start,end,context,tagTime,true);
            previousTagEnd=tag.index+tag[0].length;
        }
        let offset=0,time='未标注',pending='',definitions=false;
        for(const chunk of text.match(/[^\n]*\n|[^\n]+$/g)||[]){
            const line=chunk.replace(/\r?\n$/,'');
            const tm=line.match(timePattern);if(tm){time=tm[0];definitions=false;}
            if(/^\s*(?:人物与场景|人物与服装|角色设定|人物设定|subject_definitions|retention_analysis)\s*[:：]?\s*$/i.test(line))definitions=true;
            if(/^\s*【[^】]+】\s*$/.test(line)&&!tm)time='未标注';
            const overlaps=tags.some(t=>offset<t.index+t[0].length&&offset+chunk.length>t.index);
            if(!overlaps&&!definitions){
                const quotes=[...line.matchAll(/“([^”\r\n]+)”|「([^」\r\n]+)」|"([^"\r\n]+)"/g)];
                for(let i=0;i<quotes.length;i++){
                    const q=quotes[i],before=line.slice(i?quotes[i-1].index+quotes[i-1][0].length:0,q.index);
                    const prefix=before.trim()?before:pending;
                    const start=offset+q.index+1;add(start,start+q[0].length-2,prefix,time,false,true);
                }
                if(!quotes.length){
                    const match=line.match(/^\s*\d+(?:\.\d+)?\s*[—–‑~～\-至]\s*\d+(?:\.\d+)?\s*(?:秒|s)\s*[｜|]\s*([^：:\r\n]{1,32})\s*[:：]\s*(.+)$/i)
                        ||line.match(/^\s*(?:对白|台词|旁白)\s*[｜|：:]\s*([^：:\r\n]{1,32})\s*[:：]\s*(.+)$/)
                        ||line.match(/^\s*((?:主角|(?:画外|镜头后)[^：:\r\n]{1,14}|旁白|\b[AB]\b|S\d+))\s*[:：]\s*(.+)$/i);
                    if(match&&!/(?:语气|神态|动作|外貌|发型|身材比例|位于镜头|不露脸)/.test(line)){
                        const value=match[match.length-1],start=offset+line.lastIndexOf(value);add(start,start+value.length,line.slice(0,start-offset),time);
                    }
                }
            }
            if(line.trim())pending=/(对白|台词|旁白).*[:：]\s*$/.test(line)&&!overlaps&&!definitions?line:'';
            offset+=chunk.length;
        }
        return entries.sort((a,b)=>a.start-b.start);
    }
    function apply(source,entries){
        let end=0;const sorted=[...entries].sort((a,b)=>a.start-b.start);
        for(const e of sorted){
            if(!Number.isInteger(e.start)||!Number.isInteger(e.end)||e.start<end||e.end<=e.start||e.end>source.length||source.slice(e.start,e.end)!==e.original)
                throw new Error('原文定位已变化或有重叠，请重新提取并核对。');
            if(e.text!==e.original&&(!e.confirmed||!e.text.trim()))throw new Error('请核对说话人和修改后的台词，内容不能为空。');
            end=e.end;
        }
        let result=source;
        for(const e of sorted.reverse())if(e.text!==e.original)result=result.slice(0,e.start)+e.text+result.slice(e.end);
        return result;
    }
    function upgradeSession(session){
        if(session.parserVersion===parserVersion)return session;
        for(const t of session.templates){
            const wasSaved=t.savedSignature===JSON.stringify(t.entries);
            const fresh=extract(t.row.prompt);
            // Never discard edits or manually selected spans when upgrading old drafts.
            const keep=t.entries.filter(e=>e.text!==e.original||e.time==='手动提取');
            const merged=fresh.filter(e=>!keep.some(old=>e.start<old.end&&e.end>old.start));
            t.entries=[...merged,...keep].sort((a,b)=>a.start-b.start);
            if(wasSaved)t.savedSignature=JSON.stringify(t.entries);
        }
        session.parserVersion=parserVersion;return session;
    }
    function signature(rows){return JSON.stringify(rows.map(r=>({title:r.title||'',prompt:r.prompt,seconds:String(r.seconds)})));}
    const api={extract,apply,signature,upgradeSession,parserVersion};
    if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DialogueCore=api;
})(globalThis);
