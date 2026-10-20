(function(root){
    'use strict';
    const rolePattern=/((?:画外|镜头后)[\u4e00-\u9fff]{0,8}?(?:邻居|同事|朋友|女伴|闺蜜|顾客|店员|女声|男声|同学)|主角|旁白|女主角|男主角|女邻居|女同事|朋友|邻居|同事|女伴|闺蜜|店员|顾客|\b[AB]\b|S[12]|Subject\s*\d+|角色[一二三123])/i;
    const timePattern=/\d+(?:\.\d+)?\s*[—–~～\-至]\s*\d+(?:\.\d+)?\s*(?:秒|s\b)/i;
    function extract(text){
        const entries=[];let offset=0,time='未标注';
        for(const chunk of text.match(/[^\n]*\n|[^\n]+$/g)||[]){
            const line=chunk.replace(/\r?\n$/,'');
            const tm=line.match(timePattern);if(tm)time=tm[0];
            // A new non-time section must not inherit an unrelated action window.
            if(/^\s*【[^】]+】\s*$/.test(line)&&!tm)time='未标注';
            const quotes=[...line.matchAll(/“([^”\r\n]+)”|「([^」\r\n]+)」|"([^"\r\n]+)"/g)];
            function add(start,end,prefix,quoted){
                const role=prefix.match(rolePattern);
                const explicit=/(对白|台词|旁白|说|问|答|喊|S[12]\s*[:：]|Subject\s*\d+\s*[:：])/i.test(prefix)||!!role;
                const original=text.slice(start,end);
                if(!original.trim())return;
                entries.push({start,end,original,text:original,speaker:role?role[0]:'待核对',time,
                    confirmed:!!role&&explicit,uncertain:!role||!explicit,
                    note:!explicit&&quoted?'引号内容可能不是对白，请核对原文':''});
            }
            if(quotes.length){
                for(const q of quotes){
                    const start=offset+q.index+1;
                    const before=line.slice(0,q.index);
                    // Avoid attributing a second quote to an earlier speaker.
                    const previous=quotes[quotes.indexOf(q)-1];
                    const prefix=previous?before.slice(previous.index+previous[0].length):before;
                    add(start,start+q[0].length-2,prefix,true);
                }
            }else{
                const match=line.match(/^\s*\d+(?:\.\d+)?\s*[—–~～\-至]\s*\d+(?:\.\d+)?\s*(?:秒|s)\s*[｜|]\s*([^：:\r\n]{1,32})\s*[:：]\s*(.+)$/i) ||line.match(/^\s*(?:对白|台词|旁白)\s*[｜|：:]\s*([^：:\r\n]{1,32})\s*[:：]\s*(.+)$/)
                    ||line.match(/^\s*((?:主角|(?:画外|镜头后)[^：:\r\n]{1,14}|旁白|\b[AB]\b|S[12]|Subject\s*\d+))\s*[:：]\s*(.+)$/i);
                if(match){const value=match[match.length-1];const start=offset+line.lastIndexOf(value);add(start,start+value.length,line.slice(0,start-offset),false);}
            }
            offset+=chunk.length;
        }
        return entries;
    }
    function apply(source,entries){
        let end=0;
        const sorted=[...entries].sort((a,b)=>a.start-b.start);
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
    function signature(rows){return JSON.stringify(rows.map(r=>({title:r.title||'',prompt:r.prompt,seconds:String(r.seconds)})));}
    const api={extract,apply,signature};
    if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DialogueCore=api;
})(globalThis);
