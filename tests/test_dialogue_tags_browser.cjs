const {chromium}=require('C:/Users/sun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1920,height:1080}}),errors=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});
  await page.goto(process.env.DIALOGUE_TEST_URL||'http://127.0.0.1:19106/prompt-lab.html');
  const source=fs.readFileSync('C:/Users/sun/Desktop/模板汇总.txt','utf8').split(/\r?\n2\.高铁/)[0];
  await page.locator('#import_open').click();await page.locator('#import_text').fill(JSON.stringify([{prompt:source,seconds:12}]));await page.locator('#import_apply').click();
  const draftOriginal=await page.locator('.lab-draft textarea').first().inputValue();
  // Reproduce a persisted old editor session which found zero lines.
  await page.evaluate(()=>{
   const rows=getRows();localStorage.setItem(draftStorageKey+':dialogue:v1',JSON.stringify({version:1,signature:DialogueCore.signature(rows),templates:rows.map(row=>({row,entries:[],selected:true}))}));
  });
  await page.locator('#dialogue_open').click();assert.equal(await page.locator('.dialogue-text').count(),15);
  assert.equal(await page.locator('.dialogue-speaker').nth(3).inputValue(),'画外男声（S2）');
  await page.locator('.dialogue-text').nth(3).fill('我再想想预算。');await page.locator('#dialogue_save').click();
  await page.locator('#dialogue_close').click();await page.reload();await page.locator('#dialogue_open').click();
  assert.equal(await page.locator('.dialogue-text').nth(3).inputValue(),'我再想想预算。');
  assert.match(await page.locator('#dialogue_summary').innerText(),/已保存待回填 1 个模板 \/ 1 句/);
  await page.screenshot({path:'dialogue-tags-editor.png'});
  await page.locator('#dialogue_preview').click();await page.locator('#dialogue_apply').click();
  assert.equal(await page.locator('.lab-draft textarea').first().inputValue(),draftOriginal.replace('<d>[Chinese] 预算不够啊</d>','<d>[Chinese] 我再想想预算。</d>'));
  await page.locator('#dialogue_undo').click();assert.equal(await page.locator('.lab-draft textarea').first().inputValue(),draftOriginal);
  assert.deepEqual(errors,[]);assert.deepEqual(posts,[]);
  console.log('PASS browser: old zero-line session auto-upgrade, 15 editable lines, S2 save/reload/bulk apply/undo, exact preserved markup, no backend writes.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
