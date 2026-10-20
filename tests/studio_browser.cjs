const {chromium}=require('C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {spawn}=require('child_process');
const assert=require('assert/strict');
const fs=require('fs');
const path=require('path');
const output='C:/Users/Administrator/.codex/visualizations/2026/09/17/01a0adcf-abbd-70b3-85df-638f34f67eb6';
(async()=>{
 const server=spawn('python',['tests/ui_preview_server.py'],{cwd:path.resolve(__dirname,'..'),windowsHide:true,stdio:'ignore'});
 let browser;
 try{
  for(let n=0;n<50;n++){try{await fetch('http://127.0.0.1:19099/api/tasks');break}catch{await new Promise(r=>setTimeout(r,100));}}
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1586,height:992}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const posts=[];
  await page.route('**/api/**',async route=>{if(route.request().method()==='POST'){posts.push({url:route.request().url(),body:route.request().postDataJSON()});await route.fulfill({json:{success:true,tasks:[],count:1,cleared:50}});}else await route.continue();});
  await page.goto('http://127.0.0.1:19099/');
  await page.waitForTimeout(2000);
  console.log('Initial',await page.locator('#queue_count').textContent(),errors,await page.locator('.toast-container').textContent());
  await page.screenshot({path:output+'/studio-debug.png',fullPage:true});
  await page.waitForFunction(()=>document.querySelectorAll('#task_tbody .task-assets').length===50);
  assert.equal(await page.locator('#stat_total').textContent(),'50');
  await page.locator('.btn-action-play').first().click();
  await page.waitForFunction(()=>document.getElementById('player_element').readyState>=2);
  assert.equal(await page.locator('#player_element').evaluate(e=>getComputedStyle(e).objectFit),'contain');
  await page.locator('#video_toggle').click();
  await page.screenshot({path:output+'/studio-main.png',fullPage:true});
  await page.locator('.task-asset').first().click();await page.locator('#image_modal').waitFor({state:'visible'});await page.keyboard.press('Escape');
  assert.equal(await page.locator('#image_modal').isVisible(),false);
  for (const viewport of [{width:1920,height:1080},{width:1366,height:768},{width:390,height:844},{width:900,height:600}]) {
   await page.setViewportSize(viewport);
   await page.evaluate(()=>previewSelectedAsset('scenes','scene_select'));
   await page.waitForFunction(()=>document.getElementById('image_modal_img').naturalWidth>0);
   await page.waitForTimeout(280);
   const geometry=await page.locator('#image_modal').evaluate(el=>{
    const frame=el.querySelector('.image-modal-content').getBoundingClientRect(),box=el.querySelector('.image-modal-box').getBoundingClientRect();
    return {ratio:frame.width/frame.height,contained:box.top>=0&&box.bottom<=innerHeight&&box.left>=0&&box.right<=innerWidth,fit:getComputedStyle(el.querySelector('img')).objectFit};
   });
   assert(Math.abs(geometry.ratio-9/16)<.002);assert(geometry.contained);assert.equal(geometry.fit,'contain');
   if(viewport.width===1366)await page.screenshot({path:output+'/studio-scene-portrait.png'});
   await page.keyboard.press('Escape');
  }
  await page.setViewportSize({width:1586,height:992});
  for(const category of ['models','apparels']){
   await page.evaluate(c=>previewSelectedAsset(c,c==='models'?'model_select':'apparel_select'),category);
   assert.equal(await page.locator('#image_modal').evaluate(el=>el.classList.contains('scene-preview')),false);
   await page.keyboard.press('Escape');
  }
  await page.getByRole('button',{name:'▦ 批量排单'}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.garment-card').length>0);
  await page.getByRole('button',{name:'全选待制作',exact:true}).click();
  const selected=await page.locator('.garment-checkbox:checked').count();assert(selected>0);
  assert(await page.locator('.garment-card').first().evaluate(el=>{const card=el.getBoundingClientRect(),label=el.querySelector('.garment-info-bar').getBoundingClientRect();return label.bottom<=card.bottom;}),'garment labels must fit inside cards');
  assert.equal(await page.locator('#matrix_selected_count').textContent(),String(selected));
  await page.screenshot({path:output+'/studio-batch.png',fullPage:true});
  await page.getByRole('button',{name:'清空选择',exact:true}).click();assert.equal(await page.locator('.garment-checkbox:checked').count(),0);
  await page.locator('.garment-checkbox').first().check();
  await page.locator('#btn_batch_submit').click();assert(posts.some(x=>x.url.endsWith('/api/batch_add_tasks')&&x.body.apparels.length===1));
  await page.getByRole('button',{name:'端口管理',exact:true}).click();await page.keyboard.press('Escape');
  for(const width of [1920,1366,1100,1024,390]){
   await page.setViewportSize({width,height:900});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
   await page.getByRole('button',{name:'▦ 批量排单'}).click();await page.locator('#batch_matrix_modal').waitFor({state:'visible'});
   assert(await page.locator('#btn_batch_submit').evaluate(el=>{const r=el.getBoundingClientRect();return r.bottom<=innerHeight&&r.right<=innerWidth;}),`footer offscreen ${width}`);
   await page.keyboard.press('Escape');
  }
  assert.deepEqual(errors,[]);console.log('PASS: real assets, 50 tasks, video playback, images, batch selection/payload, dialogs and responsive 1920/1586/1366/1100/1024/390');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1});
