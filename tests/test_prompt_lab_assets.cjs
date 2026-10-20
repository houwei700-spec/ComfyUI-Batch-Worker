// Offline regression: exercise production asset handlers without submitting jobs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/js/prompt-lab.js'), 'utf8');
class Option {
    constructor(text, value) { this.text = text; this.value = value; this.dataset = {}; }
    remove() { this.parent.options = this.parent.options.filter(o => o !== this); }
}
class Element {
    constructor() { this.options = []; this.value = ''; this.files = []; }
    add(option) { option.parent = this; this.options.push(option); }
    replaceChildren(...options) { this.options = []; options.forEach(o => this.add(o)); }
    removeAttribute(name) { delete this[name]; }
}
async function main() {
    const elements = {};
    const $ = id => elements[id] ||= new Element();
    const assets = {model:null,apparel:null,scene:{file:'old.png',name:'吧台.png'}};
    const assetRevision = {model:0,apparel:0,scene:0};
    const saved = [];
    let finishUpload;
    const api = async action => action === 'library'
        ? {models:['model.jpg'],apparels:['coat.png'],scenes:['library-scene.png']}
        : new Promise(resolve => { finishUpload = resolve; });
    const ctx = vm.createContext({$,assets,assetRevision,Option,UPLOADED_ASSET:'__uploaded_asset__',
        libraryCategory:{model:'models',apparel:'apparels',scene:'scenes'},api,
        assetUrl:a=>a.file,requestId:'previous',uploading:0,
        saveDraft:()=>saved.push(JSON.parse(JSON.stringify(assets))),toast:()=>{},guarded:()=>{},
        prepareUpload:async file=>({blob:file,optimized:false}),
        FileReader:class { readAsDataURL() {this.result='data:image/png;base64,YQ==';this.onload();} }
    });
    vm.runInContext(source.slice(source.indexOf('function showAsset('), source.indexOf("$('add_row').onclick")), ctx);
    await ctx.loadLibrary();
    assert.equal($('scene_saved').value,'__uploaded_asset__');
    assert.equal($('scene_saved').options.at(-1).text,'已上传：吧台.png');
    assert.equal($('scene_image').hidden,false);
    // Explicit none clears both preview and persisted draft.
    $('scene_saved').value='';
    $('scene_saved').onchange({target:$('scene_saved')});
    assert.equal(assets.scene,null);
    assert.equal($('scene_image').hidden,true);
    assert.equal($('scene_image').src,undefined);
    assert.equal(saved.at(-1).scene,null);
    // System library -> none has the same semantics.
    $('scene_saved').value='library-scene.png';
    $('scene_saved').onchange({target:$('scene_saved')});
    assert.equal(assets.scene.source,'library');
    await ctx.loadLibrary();
    assert.equal($('scene_saved').value,'library-scene.png');
    $('clear_scene').onclick();
    assert.equal(assets.scene,null);
    // An upload completing after a clear cannot restore the old selection.
    $('scene_file').files=[{name:'late.png'}];
    const pending=$('scene_file').onchange({target:$('scene_file')});
    while(!finishUpload)await new Promise(resolve=>setImmediate(resolve));
    $('clear_scene').onclick();
    finishUpload({asset:{file:'late.png',name:'late.png'}});
    await pending;
    assert.equal(assets.scene,null);
    assert.equal($('scene_saved').value,'');
    assert.equal(ctx.uploading,0);
    // Library refresh and restored drafts preserve a visibly named upload.
    for(const role of ['model','apparel','scene']) {
        assets[role]={file:role+'.png',name:role+'.png'};
        ctx.showAsset(role);
        await ctx.loadLibrary();
        assert.equal($(role+'_saved').value,'__uploaded_asset__');
        assert.equal($(role+'_saved').options.filter(o=>o.dataset.currentAsset).length,1);
    }
    // Exercise the real enqueue handler with a captured request, never a server.
    let body;
    Object.assign(ctx,{guarded:(_button,operation)=>operation(),getRows:()=>[{prompt:'test',seconds:12}],
        crypto:{randomUUID:()=> 'test-request'},taskQuality:'480p',addRow:()=>{},refresh:async()=>{},
        api:async(action,payload)=>{assert.equal(action,'enqueue');body=JSON.parse(JSON.stringify(payload));return {created_ids:['offline']};}});
    vm.runInContext(source.slice(source.indexOf("$('enqueue').onclick"),source.indexOf("$('lab_start').onclick")),ctx);
    for(const quality of ['480p','768p']) {
        ctx.taskQuality=quality;
        assets.scene={file:'stale.png',name:'stale.png'};
        $('scene_saved').value='';
        await $('enqueue').onclick();
        assert.equal(body.assets.scene,null);
        assert.equal(body.quality,quality);
        assert.equal(body.assets.model.file,'model.png');
    }
    console.log('PASS: uploaded selection, clear, library refresh, upload race, 480p/768p enqueue without scene');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
