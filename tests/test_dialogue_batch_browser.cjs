const {chromium}=require('C:/Users/sun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1920,height:1080}});const errors=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});
  await page.goto('http://127.0.0.1:19106/prompt-lab.html');
  const rows=Array.from({length:10},(_,i)=>({prompt:`【0.0—3.0秒】\n动作：第${i+1}个场景，保持不变。\n对白｜主角：原文第${i+1}句。\n对白｜画外女邻居：公共台词保持不变。`,seconds:15}));
  await page.locator('#import_open').click();await page.locator('#import_text').fill(JSON.stringify(rows));await page.locator('#import_apply').click();
  await page.locator('#dialogue_open').click();
  for(let i=0;i<6;i++){
   await page.locator(`[data-template="${i}"]`).click();await page.locator('.dialogue-text').first().fill(`改好并保存的第${i+1}句。`);
   if(i===0){await page.locator('#dialogue_preview').click();assert.match(await page.locator('#dialogue_error').innerText(),/修改未保存/);}
   await page.locator('#dialogue_save').click();
   assert.match(await page.locator(`[data-template="${i}"] .dialogue-save-state`).innerText(),/已保存/);
  }
  assert.match(await page.locator('#dialogue_summary').innerText(),/已保存待回填 6 个模板 \/ 6 句/);
  await page.locator('#dialogue_close').click();
  assert.deepEqual(await page.locator('.lab-draft textarea').evaluateAll(es=>es.map(e=>e.value)),rows.map(r=>r.prompt));
  await page.reload();await page.locator('#dialogue_open').click();
  assert.equal(await page.locator('.dialogue-save-state').filter({hasText:'已保存'}).count(),6);
  // A later edit invalidates the saved mark and must be saved again.
  await page.locator('.dialogue-text').first().fill('最终确认的第1句。');await page.locator('#dialogue_preview').click();
  assert.match(await page.locator('#dialogue_error').innerText(),/修改未保存/);
  await page.evaluate(()=>{window.realSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(window.failSession&&k.endsWith(':dialogue:v1'))throw new DOMException('quota','QuotaExceededError');if(window.failDraft&&k.endsWith('promptLabDraft'))throw new DOMException('quota','QuotaExceededError');return window.realSet.call(this,k,v);};window.failSession=true;});
  await page.locator('#dialogue_save').click();assert.match(await page.locator('[data-template="0"] .dialogue-save-state').innerText(),/修改未保存/);
  await page.evaluate(()=>window.failSession=false);await page.locator('#dialogue_save').click();
  // Previewing an untouched seventh template still applies all six saved templates.
  await page.locator('[data-template="6"]').click();await page.locator('#dialogue_preview').click();
  assert.match(await page.locator('.dialogue-batch-scope').innerText(),/全部 6 个已保存模板，共 6 句/);
  assert.match(await page.locator('.dialogue-batch-scope').innerText(),/其余 4 个模板保持原样/);
  assert.equal(await page.locator('#dialogue_selected').count(),0);
  assert.match(await page.locator('#dialogue_apply').innerText(),/一键回填全部已保存修改（6个模板）/);
  await page.screenshot({path:'dialogue-batch-preview.png',fullPage:false});
  await page.evaluate(()=>window.failDraft=true);await page.locator('#dialogue_apply').click();
  assert.equal(await page.locator('#dialogue_dialog').isVisible(),true);
  assert.deepEqual(await page.locator('.lab-draft textarea').evaluateAll(es=>es.map(e=>e.value)),rows.map(r=>r.prompt));
  await page.evaluate(()=>window.failDraft=false);await page.locator('#dialogue_apply').click();
  const actual=await page.locator('.lab-draft textarea').evaluateAll(es=>es.map(e=>e.value));
  for(let i=0;i<10;i++)assert.equal(actual[i],i<6?rows[i].prompt.replace(`原文第${i+1}句。`,i===0?'最终确认的第1句。':`改好并保存的第${i+1}句。`):rows[i].prompt);
  await page.reload();await page.locator('#dialogue_undo').click();
  assert.deepEqual(await page.locator('.lab-draft textarea').evaluateAll(es=>es.map(e=>e.value)),rows.map(r=>r.prompt));
  assert.deepEqual(errors,[]);assert.deepEqual(posts,[]);
  console.log('PASS: 10 templates, six individually saved, reload recovery, unsaved-change blocking, failed-save rollback, all-six batch from untouched template, atomic quota failure, four untouched, batch undo, no backend writes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
