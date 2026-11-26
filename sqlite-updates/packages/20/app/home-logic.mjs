import {canonicalStatus, isActiveRecord, moneyToKopecks, todayInMoscow} from './logic.mjs?v=20261114-home1';

export const isCompleteContract = r => isActiveRecord(r) && r.check === 'OK' && canonicalStatus(r.status) !== 'формирование';
export const dayDifference = (date, today) =>
  /^\d{4}-\d{2}-\d{2}$/.test(date||'') ?
    Math.round((Date.parse(date+'T00:00:00Z')-Date.parse(today+'T00:00:00Z'))/86400000) : null;
export function currentAction(record, today=todayInMoscow()) {
  if(!isCompleteContract(record))return null;
  const status=canonicalStatus(record.status);
  const actions={
    'подписать до':{owner:'supplier',action:'Подписать контракт',date:record.signBy||'',kind:'sign'},
    'на подписи':{owner:'customer',action:'Ожидаем подпись заказчика',date:record.signBy||'',kind:'sign'},
    'заключен':{owner:'supplier',action:'Отправить товар',date:record.fulfillBy||'',kind:'delivery'},
    'Вручен':{owner:'customer',action:'Ожидаем оплату',date:record.paymentDue||'',kind:'payment'}
  };
  const action=actions[status];
  if(!action)return null;
  const days=dayDifference(action.date,today);
  return {...action,record,days,urgency:days===null?'none':days<0?'overdue':days<=1?'critical':days<=3?'soon':'normal'};
}
export function allCurrentActions(records,today=todayInMoscow()){
  const ids=new Set();
  return (records||[]).flatMap(r=>{
    if(!r?.id||ids.has(r.id))return [];
    ids.add(r.id);
    const item=currentAction(r,today);
    return item?[item]:[];
  }).sort((a,b)=>(a.days??Infinity)-(b.days??Infinity)||
    String(b.record.created||'').localeCompare(String(a.record.created||'')));
}
export const supplierActions=(records,today)=>allCurrentActions(records,today).filter(x=>x.owner==='supplier');
export const customerActions=(records,today)=>allCurrentActions(records,today).filter(x=>x.owner==='customer');
// Один формат для списка всех действий и срочных уведомлений.
export function customerActionLabel(item){
  const days=item.days;
  if(item.date&&(!/^\d{4}-\d{2}-\d{2}$/.test(String(item.date))||!Number.isFinite(days)||new Date(Date.parse(item.date+'T00:00:00Z')).toISOString().slice(0,10)!==item.date))return item.action+' · Некорректный срок';
  const date=item.date?String(item.date).split('-').reverse().join('.'):'';
  const due=days===null||!Number.isFinite(days)?'':days<0?' · Просрочено':days===0?' · Сегодня':days===1?' · Завтра':' · Через '+days+' дн.';
  return item.action+(date?' — '+date+due:' · Срок не указан');
}
// Колокольчик: только события заказчика с датой в ближайшие 3 дня или просроченные.
export const urgentCustomerActions=(records,today)=>customerActions(records,today).filter(x=>x.days!==null&&x.days<=3);
export const unpaidContracts=(records)=>{const ids=new Set();return records.filter(r=>{if(!isCompleteContract(r)||canonicalStatus(r.status)!=='Вручен'||!r.id||ids.has(r.id))return false;ids.add(r.id);return true})};
export function unpaidTotalKopecks(records){
  return unpaidContracts(records).reduce((sum,r)=>{
    try{return sum+moneyToKopecks(r.amount)}catch{return sum}
  },0);
}
// The Home table is a small, searchable view of the registry, not just a queue
// of actionable contracts. Drafts/forming contracts must appear under "all"
// without being falsely counted as actionable or as unpaid.
export function homeContractRows(records,{today=todayInMoscow(),filter='active',query='',limit=5}={}){
  // The home view is an unfinished-contract worklist across all periods.
  // Do not mutate the registry: completed records remain available there and in finance.
  if(filter==='active')records=(records||[]).filter(r=>canonicalStatus(r?.status)!=='Оплачен');
  const actions=allCurrentActions(records,today);
  const ids=new Set();
  let rows;
  if(filter==='supplier')rows=actions.filter(item=>item.owner==='supplier');
  else if(filter==='customer')rows=actions.filter(item=>item.owner==='customer');
  else if(filter==='unpaid')rows=actions.filter(item=>canonicalStatus(item.record.status)==='Вручен');
  else if(filter==='all'||filter==='active'){
    const actionIds=new Set(actions.map(item=>item.record.id));
    const other=(records||[]).filter(r=>r?.id&&isActiveRecord(r)&&!actionIds.has(r.id)).map(record=>{
      const status=canonicalStatus(record.status);
      const draft=record.check!=='OK'||status==='формирование';
      const date=draft ? (status==='формирование'||status==='подписать до'||status==='на подписи'?record.signBy||'':'') : '';
      return {
        record,owner:status==='Оплачен'&&!draft?'done':'draft',
        action:draft?(record.check!=='OK'?'Черновик':'Формирование'):(status==='Оплачен'?'Оплачен':status||'Статус не указан'),
        date,kind:draft?'draft':status==='Оплачен'?'paid':'other',
        days:null,urgency:'none'
      };
    }).sort((a,b)=>(a.owner==='done')-(b.owner==='done')||
      String(b.record.created||'').localeCompare(String(a.record.created||'')));
    rows=[...actions,...other].sort((a,b)=>
      String(b.record.created||'').localeCompare(String(a.record.created||'')));
  }else return [];
  const term=String(query||'').trim().toLocaleLowerCase('ru-RU');
  return rows.filter(({record})=>{
    if(ids.has(record.id))return false;
    ids.add(record.id);
    return !term||[record.customer,record.contractNumber,record.platform,record.amount]
      .some(value=>String(value??'').toLocaleLowerCase('ru-RU').includes(term));
  }).slice(0,Math.max(0,Math.trunc(Number(limit)||0)));
}
export function activeNotes(notes){return (notes||[]).filter(n=>!n.done).sort((a,b)=>{
  const da=a.dueDate||'9999-12-31',db=b.dueDate||'9999-12-31';
  return da.localeCompare(db)||String(a.createdAt||'').localeCompare(String(b.createdAt||''));
});}
export function calendarEvents(records,notes,year,month,today=todayInMoscow()){
  const key=String(year)+'-'+String(month).padStart(2,'0');
  const result=new Map();
  for(const item of allCurrentActions(records,today)){
    if(!item.date?.startsWith(key))continue;
    const bucket=result.get(item.date)||[];
    bucket.push({type:'contract',label:item.record.customer||'Контракт',detail:item.action,record:item.record,urgency:item.urgency});
    result.set(item.date,bucket);
  }
  for(const item of activeNotes(notes)){
    if(!item.dueDate?.startsWith(key))continue;
    const bucket=result.get(item.dueDate)||[];
    bucket.push({type:'note',label:item.text,detail:'Заметка',note:item,urgency:'none'});
    result.set(item.dueDate,bucket);
  }
  return result;
}
