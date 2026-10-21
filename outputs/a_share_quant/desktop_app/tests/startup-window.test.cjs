const test = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const {createDesktopWindows} = require('../modules/windows.cjs');
function fixture() {
 const created=[];let finishLoad;
 class Window extends EventEmitter {
  constructor(options){super();this.options=options;this.destroyed=false;this.visible=options.show;this.webContents=new EventEmitter();this.webContents.stop=()=>{};this.webContents.setWindowOpenHandler=()=>{};created.push(this);}
  loadFile(file){this.file=file;return Promise.resolve();}
  loadURL(url){this.url=url;return new Promise(resolve=>{finishLoad=resolve;});}
  isDestroyed(){return this.destroyed;}
  show(){this.visible=true;}
  getBounds(){return {x:20,y:30,width:1440,height:960};}
  setBounds(bounds){this.bounds=bounds;}
  close(){if(this.destroyed)return;this.destroyed=true;this.emit('closed');}
 }
 const runtime=createDesktopWindows({app:{getPath:()=>'.',quit:()=>{}},BrowserWindow:Window,shell:{openExternal:async()=>{}},Menu:{setApplicationMenu:()=>{}},desktopRoot:'.'});
 return {runtime,created,finish:()=>finishLoad()};
}
test('startup renderer stays alive until both workbench load and first paint finish',async()=>{
 const {runtime,created,finish}=fixture();await runtime.createWindow();
 const [workbench,startup]=created;
 assert.equal(workbench.visible,false);assert.equal(startup.visible,true);assert.match(startup.file,/startup.html/);
 const opening=runtime.openWorkbench();finish();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(workbench.visible,false);assert.equal(startup.destroyed,false);
 workbench.emit('ready-to-show');await opening;
 assert.equal(workbench.visible,true);assert.equal(startup.destroyed,true);assert.deepEqual(workbench.bounds,{x:20,y:30,width:1440,height:960});
 assert.equal(workbench.listenerCount('ready-to-show'),0);
});
test('early first paint alone does not reveal a still-loading workbench',async()=>{
 const {runtime,created,finish}=fixture();await runtime.createWindow();const opening=runtime.openWorkbench();
 created[0].emit('ready-to-show');assert.equal(created[0].visible,false);finish();await opening;assert.equal(created[0].visible,true);
});
test('closing startup cancels its hidden workbench without leaking windows',async()=>{
 const {runtime,created}=fixture();await runtime.createWindow();created[1].close();assert.equal(created[0].destroyed,true);assert.equal(runtime.current(),null);
});
