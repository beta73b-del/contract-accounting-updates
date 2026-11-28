import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {PLATFORMS, validateRecord, fieldToInput} from './app/logic.mjs';
const src=fs.readFileSync(new URL('./app/app.mjs',import.meta.url),'utf8');
const html=fs.readFileSync(new URL('./app/index.html',import.meta.url),'utf8');
test('editable platform control linked to original form',()=>{
 assert.match(html,/<input type="text" id="platform" name="platform" list="contractPlatformList" maxlength="120"/);
 assert.match(html,/<datalist id="contractPlatformList"><\/datalist>/);
 assert.doesNotMatch(html,/<select id="platform"/);
 assert.match(src,/options\(el\('contractPlatformList'\),PLATFORMS\)/);
});
test('all existing list choices populated by production options helper',()=>{
 const nodes=[]; const list={replaceChildren(){nodes.length=0},append(x){nodes.push(x)}};
 const context=vm.createContext({document:{createElement:()=>({dataset:{}})},PLATFORMS,list});
 const helper=src.slice(src.indexOf('function options('),src.indexOf('function availableMonths('));
 vm.runInContext(helper+'\noptions(list,PLATFORMS);',context);
 assert.deepEqual(nodes.map(x=>x.value),PLATFORMS);assert.equal(nodes.length,12);
});
test('custom, existing and historical platform values validate and round trip',()=>{
 for(const platform of ['РТС','ЭТП Тест-030','П'.repeat(120),'<script>текст</script>']){
  assert.equal(validateRecord({platform}).platform,undefined);assert.equal(fieldToInput({platform}).platform,platform);
 }
 assert.ok(validateRecord({platform:'П'.repeat(121)}).platform);assert.ok(validateRecord({platform:'   '}).platform);
});
