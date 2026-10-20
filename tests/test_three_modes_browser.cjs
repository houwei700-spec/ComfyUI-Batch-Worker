const {chromium}=require('C:/Users/sun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const b=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const p=await b.newPage({viewport:{width:1920,height:1080}});const errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  const base='http://127.0.0.1:19110/prompt-lab.html';
  await p.goto(base);await p.locator('[data-action="promote"][data-quality="single768p"]').waitFor();
  await p.locator('[data-action="promote"][data-quality="single768p"]').click();
  await p.locator('.promotion-note').filter({hasText:'已加入一彩768p'}).first().waitFor();
  await p.locator('[data-action="promote"][data-quality="768p"]').click();
  await p.locator('.promotion-note').filter({hasText:'已加入二彩928p'}).first().waitFor();
  const state=await (await p.request.get('http://127.0.0.1:19110/api/prompt_lab/state')).json();
  assert.equal(state.tasks.length,3);assert.ok(state.tasks.slice(1).every(t=>t.status==='PENDING'&&t.held_for_start));
  for(const [mode,label] of [['480p','480p测试'],['single768p','一彩768p'],['768p','二彩928p']]){
   await p.goto(base+(mode==='480p'?'':'?mode='+mode));
   await p.waitForFunction(()=>typeof tasks!=='undefined'&&tasks.length===1);
   assert.equal(await p.locator('[data-mode]').count(),3);
   if(mode!=='480p')assert.equal(await p.locator('#lab_page_title').innerText(),label);
   assert.ok((await p.locator('#lab_tasks').innerText()).includes(label));
   await p.locator('.lab-draft textarea').first().fill('对白｜主角：'+label+'独立草稿。');
   await p.locator('#dialogue_open').click();assert.ok((await p.locator('.dialogue-header').innerText()).includes(label));
   await p.locator('#dialogue_close').click();
   await p.screenshot({path:'mode-'+mode+'.png'});
  }
  for(const mode of ['480p','single768p','768p']){
   await p.goto(base+(mode==='480p'?'':'?mode='+mode));
   const expected={'480p':'480p测试','single768p':'一彩768p','768p':'二彩928p'}[mode];
   assert.equal(await p.locator('.lab-draft textarea').first().inputValue(),'对白｜主角：'+expected+'独立草稿。');
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: three isolated drafts, independent queue filtering, two promotion targets with held tasks, labels/editor, no JS errors; isolated server only.');
 }finally{await b.close()}
})().catch(e=>{console.error(e);process.exit(1)});
