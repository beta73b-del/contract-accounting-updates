// Аналитика цен победителей: только наблюдения за последние 6 календарных месяцев.
export const COMPETITOR_MODELS = Object.freeze({
  lite:'Рутокен Lite 1010',
  ecp:'Рутокен ЭЦП 3120'
});

// Stable legacy keys and normalized free-text product groups. Never combine different models.
const PRODUCT_ALIASES = new Map([
  ['рутокен lite 1010','lite'], ['рутокен 1010','lite'],
  ['рутокен эцп 3120','ecp'], ['рутокен эцп 3.0 3120','ecp'], ['рутокен 3120','ecp']
]);
export function normalizeProductName(value) {
  if(typeof value!=='string' || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(value)) return '';
  const name=value.trim().replace(/\s+/gu,' ').normalize('NFC');
  return [...name].length<=200 ? name : '';
}
export function productIdentity(value,entries=[]) {
  const name=normalizeProductName(value);
  if(!name) throw new Error('Укажите наименование товара (1–200 символов)');
  const key=name.toLowerCase();
  const model=PRODUCT_ALIASES.get(key) || 'custom:'+key;
  return {model,productName:COMPETITOR_MODELS[model] || entries.find(e=>e.model===model && e.productName)?.productName || name};
}
export function productLabel(row) {
  return Object.hasOwn(COMPETITOR_MODELS,row.model) ? COMPETITOR_MODELS[row.model] : row.productName || String(row.model||'').replace(/^custom:/,'');
}
export function productGroups(entries) {
  const groups=new Map();
  for(const row of entries) {
    if(typeof row.model!=='string' || !row.model) continue;
    if(!groups.has(row.model)) groups.set(row.model,{model:row.model,label:productLabel(row),count:0});
    groups.get(row.model).count++;
  }
  const rank=model=>model==='lite'?0:model==='ecp'?1:2;
  return [...groups.values()].sort((a,b)=>rank(a.model)-rank(b.model) || a.label.localeCompare(b.label,'ru',{numeric:true}) || a.model.localeCompare(b.model));
}

export function sixMonthStart(today) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(today))) throw new Error('Дата должна быть в формате YYYY-MM-DD');
  const [year,month,day]=today.split('-').map(Number);
  const checked=new Date(Date.UTC(year,month-1,day));
  if(checked.getUTCFullYear()!==year || checked.getUTCMonth()+1!==month || checked.getUTCDate()!==day) {
    throw new Error('Некорректная календарная дата');
  }
  const target=new Date(Date.UTC(year,month-1-5,1));
  return target.toISOString().slice(0,10);
}
export function currentPrices(entries, model, today) {
  const since=sixMonthStart(today);
  return entries.filter(e=>
    e.model===model && e.date>=since && e.date<=today &&
    Number.isSafeInteger(e.priceKopecks) && e.priceKopecks>0
  ).sort((a,b)=>b.date.localeCompare(a.date) || String(b.created||'').localeCompare(String(a.created||'')));
}
export function formatKopecks(cents) {
  if(!Number.isSafeInteger(cents) || cents<0) return '—';
  const rub=Math.floor(cents/100), fractional=String(cents%100).padStart(2,'0');
  return new Intl.NumberFormat('ru-RU').format(rub)+','+fractional+' ₽';
}
export function formatMedian(items) {
  if(!items.length) return '—';
  const sorted=items.map(x=>x.priceKopecks).sort((a,b)=>a-b);
  const mid=Math.floor(sorted.length/2);
  const twice=sorted.length%2 ? sorted[mid]*2 : sorted[mid-1]+sorted[mid];
  if(!Number.isSafeInteger(twice) || twice<0) throw new Error('Переполнение суммы цен');
  const whole=Math.floor(twice/2);
  if(twice%2===0) return formatKopecks(whole);
  // Медиана может оказаться точно посередине между соседними копейками.
  return formatKopecks(whole).replace(' ₽', '5 ₽');
}
export function modelMetrics(entries,model,today){
  const recent=currentPrices(entries,model,today);
  return {
    entries:recent,
    min:recent.length ? formatKopecks(Math.min(...recent.map(x=>x.priceKopecks))) : '—',
    median:formatMedian(recent)
  };
}
export function winnerIdentity(entry) {
  const inn=String(entry.inn||'').replace(/\s/g,'');
  if(inn) return 'inn:'+inn;
  const name=String(entry.winner||'').trim().replace(/\s+/g,' ').toLocaleLowerCase('ru-RU');
  return 'name:'+name;
}
export function winnerHistory(entries,identity,today) {
  const since=sixMonthStart(today);
  return entries.filter(e=>winnerIdentity(e)===identity)
    .sort((a,b)=>b.date.localeCompare(a.date) || String(b.created||'').localeCompare(String(a.created||'')))
    .map(e=>({...e,archived:e.date<since||e.date>today}));
}
