// Real headless-Chrome UI smoke using synthetic SQLite database and random local ports.
// Never touches the production Excel app, its documents, or port 8765.
import assert from 'node:assert/strict';
import {spawn,execFileSync} from 'node:child_process';
import {mkdtempSync,rmSync} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import net from 'node:net';
import {fileURLToPath} from 'node:url';
const ROOT=dirname(dirname(fileURLToPath(import.meta.url)));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function freePort(){
  const s=net.createServer();
  await new Promise((ok,fail)=>s.once('error',fail).listen(0,'127.0.0.1',ok));
  const p=s.address().port;await new Promise(ok=>s.close(ok));return p;
}
async function until(fn,timeout=12000){
  const end=Date.now()+timeout;while(Date.now()<end){try{const a=await fn();if(a)return a}catch{}await sleep(125)}return false;
}
const profile=mkdtempSync(join(tmpdir(),'ContractsSQLiteBrowser-'));
const port=await freePort(),debug=await freePort();
const target='http://localhost:'+port+'/';
const python=process.env.PYTHON||'python';
const chromePath=process.env.CHROME_PATH||'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const app=spawn(python,[join(ROOT,'server.py')],{
  cwd:ROOT,env:{...process.env,CONTRACTS_SQLITE_LAB_TEST:'1',
    CONTRACTS_SQLITE_LAB_DB:join(profile,'test.sqlite'),CONTRACTS_SQLITE_LAB_PORT:String(port)},
  windowsHide:true,stdio:'ignore'
});
let chrome,ws,seq=0;const pending=new Map();let checks=0;
const check=(condition,msg)=>{assert.ok(condition,msg);checks++;console.log('PASS '+msg)};
async function cdp(method,params={}){
  return await new Promise((resolve,reject)=>{
    const id=++seq,t=setTimeout(()=>{pending.delete(id);reject(Error('CDP timeout: '+method))},9000);
    pending.set(id,{resolve:v=>{clearTimeout(t);resolve(v)},reject:e=>{clearTimeout(t);reject(e)}});
    ws.send(JSON.stringify({id,method,params}));
  });
}
async function evaluate(expression){
  const x=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(x.exceptionDetails)throw Error(JSON.stringify(x.exceptionDetails));
  return x.result?.value;
}
const set=(id,value)=>evaluate("(()=>{const x=document.getElementById("+JSON.stringify(id)+");x.value="+JSON.stringify(value)+";x.dispatchEvent(new Event('change',{bubbles:true}));return true})()");
try {
  check(await until(async()=>{const r=await fetch(target+'api/bootstrap');return r.ok}), 'Isolated SQLite server running on distinct test port');
  chrome=spawn(chromePath,['--headless=new','--no-first-run','--no-default-browser-check',
    '--disable-extensions','--disable-gpu','--no-sandbox','--no-proxy-server',
    '--remote-allow-origins=*','--remote-debugging-port='+debug,
    '--user-data-dir='+join(profile,'chrome'),target],
    {windowsHide:true,stdio:'ignore'});
  const tab=await until(async()=>{
    const r=await fetch('http://127.0.0.1:'+debug+'/json/list');const all=await r.json();
    return all.find(x=>x.type==='page'&&x.url.includes('localhost:'+port));
  },15000);
  check(tab,'Headless Chrome opened isolated laboratory');
  ws=new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res,rej)=>{ws.addEventListener('open',res,{once:true});ws.addEventListener('error',rej,{once:true})});
  ws.addEventListener('message',e=>{
    const msg=JSON.parse(e.data),job=pending.get(msg.id);if(!job)return;
    pending.delete(msg.id);msg.error?job.reject(Error(JSON.stringify(msg.error))):job.resolve(msg.result);
  });
  await cdp('Runtime.enable');
  check(await until(()=>evaluate("document.getElementById('health').textContent.includes('SQLite')")),'UI loaded SQLite bootstrap');
  check(await set('newProduct','Рутокен Lite 1010'),'Product input interactive');
  await evaluate("document.getElementById('addProduct').click()");
  check(await until(()=>evaluate("document.getElementById('product').options.length===1")),'UI adds synthetic product');
  await evaluate("document.getElementById('receipt').click()");
  check(await until(()=>evaluate("document.getElementById('stock').textContent.includes('10')")),'Receipt shown as physical stock');
  await evaluate("document.getElementById('save').click()");
  check(await until(()=>evaluate("document.getElementById('records').rows.length===1")),'Browser saved synthetic contract through API');
  await set('status','заключен');
  check(await evaluate("!!document.getElementById('contractDate').value"),'Conclusion date prefilled in browser');
  await evaluate("document.getElementById('save').click()");
  check(await until(()=>evaluate("document.getElementById('records').textContent.includes('заключен')")),'Conclusion persisted via SQLite API');
  check(await evaluate("document.getElementById('health').textContent.includes('контрактов: 1')"),'UI count reconciled');
  console.log('SQLITE_BROWSER_SMOKE '+checks+'/'+checks+' PASS');
}finally{
  try{ws?.close()}catch{}
  // On Windows Chrome headless spawns children; only terminate the test-owned PID tree.
  for(const proc of [chrome,app]){
    if(!proc?.pid)continue;
    if(process.platform==='win32'){
      try{execFileSync('taskkill.exe',['/PID',String(proc.pid),'/T','/F'],{stdio:'ignore',timeout:5000})}catch{}
    }
    try{proc.kill()}catch{}
  }
  await sleep(1000);
  let cleaned=false;
  for(let i=0;i<7;i++){
    try{rmSync(profile,{recursive:true,force:true,maxRetries:4,retryDelay:250});cleaned=true;break}
    catch(e){if(i===6)console.error('CLEANUP WARNING:',e.message);else await sleep(650)}
  }
  if(cleaned)console.log('PASS test-owned browser profile cleaned');
}
