const assert=require('node:assert/strict');
const fs=require('node:fs');
const core=require('../web/js/dialogue-core-tags-20261005.js');
const source=fs.readFileSync('C:/Users/sun/Desktop/模板汇总.txt','utf8');
const first=source.split(/\r?\n2\.高铁/)[0];
const found=core.extract(first);
assert.equal(found.length,15);assert.equal(found.filter(e=>e.speaker==='画外男声（S2）').length,4);
assert.equal(found.filter(e=>e.speaker==='Subject 1').length,11);
assert.ok(found.every(e=>e.confirmed&&e.time!=='未标注'));
assert.equal(core.apply(first,found),first);
for(let i=0;i<found.length;i++){
 const changed=found.map(e=>({...e}));changed[i].text='单句测试😀';
 assert.equal(core.apply(first,changed),first.slice(0,found[i].start)+'单句测试😀'+first.slice(found[i].end));
 assert.ok(!found[i].original.includes('[Chinese]'));
}
const second=source.split(/\r?\n2\.高铁/)[1].split(/\r?\n3\.地铁/)[0];
const chinese=core.extract(second);assert.equal(chinese.length,4);
assert.deepEqual(chinese.map(e=>e.speaker),['画外旅客','主角','画外旅客','主角']);
assert.ok(chinese.every(e=>e.confirmed));assert.equal(chinese[0].time,'0.10–2.90秒');
const mixed='<Subject 2> is the complete upper‑body outerwear garment from image.\r\n00:00.000 - 00:02.000: <Subject 1> says: <d>[Chinese] 相同台词 </d>. S2 says: <d>[Chinese] 相同台词</d>.\r\n00:02.000 - 00:03.000: <Subject 2> says: <d>[English] garment</d>.\r\n00:03.000 - 00:04.000: <Subject 1> says:\r\n<d>\r\n[Chinese] 多行😀\r\n正文\r\n</d>\r\n<d>[Chinese] </d>';
const multi=core.extract(mixed);assert.equal(multi.length,4);
assert.equal(multi[1].speaker,'S2');assert.equal(multi[1].time,multi[0].time);
assert.equal(multi[2].confirmed,false);assert.equal(multi[3].original,'多行😀\r\n正文');
assert.equal(core.apply(mixed,multi),mixed);
assert.equal(core.extract('<d>[Chinese] “你好”</d>').length,1);
assert.equal(core.extract('<d>[Chinese] 谁说的</d>')[0].confirmed,false);
assert.equal(core.extract('00:00.0 - 00:01.0: <Subject 1> looks at the off-screen male voice (S2), then speaks: <d>[Chinese] 我说的</d>')[0].speaker,'Subject 1');
// Old empty sessions are re-extracted. Saved edits and overlapping manual ranges survive.
const manual={...found[3],text:'已保存男声修改',time:'手动提取'};
const unsaved={...found[0],text:'尚未保存修改'};
const session={version:1,templates:[{row:{prompt:first},entries:[]},{row:{prompt:first},entries:[manual],savedSignature:JSON.stringify([manual])},{row:{prompt:first},entries:[unsaved]}]};
core.upgradeSession(session);
assert.equal(session.templates[0].entries.length,15);
const saved=session.templates[1];assert.equal(saved.savedSignature,JSON.stringify(saved.entries));assert.ok(saved.entries.some(e=>e.text===manual.text));
assert.equal(session.templates[2].savedSignature,undefined);assert.ok(session.templates[2].entries.some(e=>e.text===unsaved.text));
assert.equal(core.apply(first,saved.entries),first.slice(0,manual.start)+manual.text+first.slice(manual.end));
const once=JSON.stringify(session);core.upgradeSession(session);assert.equal(JSON.stringify(session),once);
console.log('PASS: real tagged template 15 lines (11 Subject 1 / 4 offscreen S2), Chinese 4 lines, exact replacement, namespace separation, multiline/empty/duplicate tags, old draft migration and saved state.');
