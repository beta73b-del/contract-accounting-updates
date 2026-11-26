import {PROFILE_SECTIONS,copyProfileValue} from './profile.mjs?v=1';
import {renderMonthlyFinanceChart} from './finance-chart.mjs?v=20261121-finance-overhead1';
import {calculateFinancialSummary,formatMoney,moneyToKopecks,MONTHS,displayProduct,visibleStockItems,canonicalStatus,todayInMoscow} from './logic.mjs?v=20261121-finance-overhead1';
import {supplierActions,customerActions,currentAction,customerActionLabel,unpaidContracts,unpaidTotalKopecks,homeContractRows,activeNotes,calendarEvents,dayDifference} from './home-logic.mjs?v=build020-customer1';

const el=id=>document.getElementById(id);
const svg=paths=>'<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'+paths+'</svg>';
const icons={
 clipboard:svg('<rect x="5" y="4" width="14" height="18" rx="2"/><path d="M9 4h6V2H9v2ZM9 11h6M9 15h4M16 18l4-4 2 2-4 4-3 1z"/>'),
 handshake:svg('<path d="m3 10 4-5 4 2 4-2 6 5-3 6-5 4-3-1-4-3z"/><path d="m9 11 4 4 3 2M11 7l-3 4M16 10l-4-3"/>'),
 coins:svg('<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v5c0 4 16 4 16 0V6M4 11v5c0 4 16 4 16 0v-5"/>'),
 file:svg('<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M9 7h6M9 11h6M9 15h5"/>'),
 note:svg('<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>'),
 package:svg('<path d="m3 7 9-4 9 4-9 4-9-4ZM3 7v10l9 4 9-4V7M12 11v10"/>'),
 id:svg('<rect x="2" y="4" width="20" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M5 16c.5-2 6.5-2 7 0M15 9h4M15 13h4"/>'),
 chart:svg('<path d="M4 20V11h3v9M10 20V7h3v13M16 20V3h3v17M2 21h20"/>'),
 copy:svg('<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h2"/>')
};
const number=new Intl.NumberFormat('ru-RU');
const fmtDate=date=>date&&/^\d{4}-\d{2}-\d{2}$/.test(date)?date.split('-').reverse().join('.'):'—';
const allProfileFields=PROFILE_SECTIONS.flatMap(x=>x.fields);
const getProfile=k=>allProfileFields.find(([label])=>label===k)?.[1]??'';
const quickFields=[
 ['ИП',getProfile('Полное наименование').replace(/^Индивидуальный предприниматель\s+/, '')],
 ['ОГРНИП',getProfile('ОГРНИП')],['Расчётный счёт',getProfile('Расчётный счёт')],
 ['Корр. счёт',getProfile('Корреспондентский счёт')],['БИК',getProfile('БИК')]
];
const make=(tag,cls='',txt)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(txt!==undefined)n.textContent=txt;return n;};
function copyButton(label,value,toast){
 const b=make('button','home-copy');b.type='button';b.innerHTML=icons.copy;b.title='Копировать только значение';b.setAttribute('aria-label','Скопировать: '+label);
 b.addEventListener('click',async()=>{try{await copyProfileValue(value);toast('Скопировано: '+label)}catch(e){toast('Не удалось скопировать: '+e.message,true)}});
 return b;
}
function drawQuick(toast){
 const root=el('homeProfileRows');root.replaceChildren();
 for(const [label,value] of quickFields){
   const row=make('dl','home-profile-row');row.append(make('dt','',label),make('dd','',value),copyButton(label,value,toast));root.append(row);
 }
}
function drawProfile(toast){
 const root=el('homeProfileModalDetails');root.replaceChildren();
 for(const section of PROFILE_SECTIONS){
   const block=make('section','home-profile-modal-section');block.append(make('h3','',section.title));
   for(const [label,value] of section.fields){
     const row=make('dl','home-profile-modal-row');row.append(make('dt','',label),make('dd','',value),copyButton(label,value,toast));block.append(row);
   }
   root.append(block);
 }
}

export function renderCustomerActionPreview(root,items,label=customerActionLabel,emptyText='Актуальных действий нет'){
 root.replaceChildren();
 if(!items.length){root.append(make('p','home-customer-preview-empty',emptyText));return;}
 const list=make('ul','home-customer-preview-list');
 for(const item of items){
   const row=make('li');
   row.append(make('strong','',item.record.customer||'Без названия заказчика'));
   if(item.record.contractNumber)row.append(make('span','home-customer-preview-number','№ '+item.record.contractNumber));
   row.append(make('span','home-customer-preview-action',label(item)));
   list.append(row);
 }
 root.append(list);
}

export function paymentPreviewLabel(item){
 let amount;
 try{amount=formatMoney(moneyToKopecks(item.record.amount));}catch{amount='Сумма не указана';}
 return amount+' · '+customerActionLabel(item);
}

export function initHome({state,api,toast,openForm,switchView,demo,headerPanel}){
 let notes={revision:0,notes:[],error:''};
 let showAllNotes=false,editingNote=null,busy=false,focusBeforeModal=null;
 let calYear=0,calMonth=0,calSelected='',query='';
 const today=()=>todayInMoscow();
 function setNotes(payload){notes={revision:Number(payload?.revision)||0,notes:Array.isArray(payload?.notes)?payload.notes:[],error:payload?.error||''};if(notes.error)toast('Заметки: '+notes.error,true)}
 function renderMetrics(){
   const mine=supplierActions(state.records,today());
   el('homeSupplierCount').textContent=String(mine.length);
   renderCustomerActionPreview(el('homeSupplierPreview'),mine);
   const actions=customerActions(state.records,today());
   el('homeCustomerCount').textContent=String(actions.length);
   renderCustomerActionPreview(el('homeCustomerPreview'),actions);
   el('homeUnpaidValue').textContent=formatMoney(unpaidTotalKopecks(state.records));
   const waiting=unpaidContracts(state.records);
   const n=waiting.length;
   const waitingActions=waiting.map(record=>currentAction(record,today()));
   renderCustomerActionPreview(el('homeUnpaidPreview'),waitingActions,paymentPreviewLabel,'Нет контрактов, ожидающих оплаты');
   el('homeUnpaidCount').textContent=n+' '+(n===1?'контракт':n>=2&&n<=4?'контракта':'контрактов')+' к получению';
 }
 function contractOpen(record){switchView('contracts');openForm(record)}
 function renderContracts(){
   const target=el('homeContractsBody');target.replaceChildren();
   const rows=homeContractRows(state.records,{today:today(),query,limit:Math.max(1,state.records.length)});
   if(!rows.length){const label=query?'Незавершённых контрактов по вашему запросу нет.':'Нет незавершённых контрактов.';const row=make('tr','home-empty'),cell=make('td','home-empty-state',label);cell.colSpan=4;row.append(cell);target.append(row);return}
   for(const item of rows){
     const r=item.record,row=make('tr');row.dataset.urgency=item.urgency;row.dataset.recordId=r.id;
     row.tabIndex=0;row.setAttribute('role','link');row.title='Открыть карточку контракта';
     row.addEventListener('click',e=>{if(!e.target.closest('button'))contractOpen(r)});
     row.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();contractOpen(r)}});
     const name=make('button','home-contract-name',r.customer||'Без названия');name.type='button';name.addEventListener('click',()=>contractOpen(r));
     name.append(make('small','',[(r.contractNumber?'№ '+r.contractNumber:'Контракт без номера'),r.platform||''].filter(Boolean).join(' · ')));
     const nameCell=make('td');nameCell.append(name);
     const status=canonicalStatus(r.status)||'—';
     const actionClass=status==='Оплачен'?'done':r.check!=='OK'?'draft':'';
     const statusCell=make('td');statusCell.append(make('span','home-action-pill '+actionClass,status));
     const product=displayProduct(r.product||'')||r.product||'Товар не указан';
     const qty=Number(r.quantity);
     const qtyLabel=Number.isFinite(qty)&&qty>0?number.format(qty)+' шт.':'Количество не указано';
     const productCell=make('td','home-product-cell',product);
     productCell.append(make('small','',qtyLabel));
     let amount='—';try{amount=formatMoney(moneyToKopecks(r.amount))}catch{}
     row.append(nameCell,productCell,statusCell,make('td','home-contract-sum',amount));target.append(row);
   }
 }
 function dateClass(date){const d=dayDifference(date,today());return d===null?'none':d<=1?'critical':''}
 function editNote(note){if(!note)return;editingNote=note;focusBeforeModal=document.activeElement;el('homeNoteEditText').value=note.text;el('homeNoteEditDue').value=note.dueDate||'';el('homeNoteEditTime').value=note.dueTime||'';el('homeNoteModal').hidden=false;el('homeNoteEditText').focus()}
 function closeNote(){el('homeNoteModal').hidden=true;editingNote=null;focusBeforeModal?.focus?.()}
 async function mutate(action,data={}){
   if(busy)return false;
   if(notes.error){toast('Заметки заблокированы: '+notes.error,true);return false}
   busy=true;
   try{
     if(demo){
       const rows=notes.notes.map(x=>({...x})),now=new Date().toISOString();
       if(action==='create')rows.push({id:'demo-'+Date.now(),text:data.text,dueDate:data.dueDate||'',dueTime:data.dueTime||'',done:false,createdAt:now,updatedAt:now});
       else{const index=rows.findIndex(x=>x.id===data.id);if(index<0)throw Error('Заметка не найдена');
         if(action==='delete'||action==='complete')rows.splice(index,1);
         else if(action==='update')Object.assign(rows[index],{text:data.text,dueDate:data.dueDate,dueTime:data.dueTime||''});
       }
       setNotes({revision:notes.revision+1,notes:rows});
     }else{
       const res=await api('/api/notes',{revision:notes.revision,action,...data});
       setNotes(res.notes);if(res.cloudStatus?.startsWith('ОШИБКА'))toast('Заметки сохранены, но синхронизация требует проверки',true);
     }
     renderNotes();renderCalendar();return true;
   }catch(e){
     toast('Не удалось сохранить заметку: '+e.message,true);
     if(e.message.includes('изменены'))try{setNotes(await fetch('/api/notes',{cache:'no-store'}).then(x=>x.json()));renderNotes()}catch{}
     return false;
   }finally{busy=false}
 }
 function renderNotes(){
   const root=el('homeNotesList');root.replaceChildren();
   const items=activeNotes(notes.notes);
   el('homeNotesCount').textContent='('+items.length+')';
   document.querySelector('.home-top-notes')?.classList.toggle('notes-empty',items.length===0);
   const toggle=el('homeAllNotes');
   toggle.hidden=items.length<=5;
   toggle.textContent=showAllNotes?'Свернуть':'Показать все ('+items.length+')';
   if(notes.error){root.append(make('p','home-empty-state','Заметки недоступны: '+notes.error));return}
   if(!items.length){return}
   for(const n of (showAllNotes?items:items.slice(0,5))){
     const row=make('div','home-note-row'),done=make('button','home-note-complete'),label=make('span','home-note-text',n.text);
     done.type='button';done.title='Отметить выполненной и удалить';
     done.setAttribute('aria-label','Выполнено, удалить заметку: '+n.text);
     done.addEventListener('click',async()=>{if(await mutate('complete',{id:n.id}))toast('Заметка удалена')});
     label.title=n.text;
     row.append(done,label);
     if(n.dueDate){
       const when=fmtDate(n.dueDate)+(n.dueTime?' · '+n.dueTime+' МСК':'');
       const date=make('span','home-note-date',when);
       date.title='Дата и время заметки. Уведомление не настроено.';
       row.append(date);
     }
     const edit=make('button','home-note-edit','✎');edit.type='button';
     edit.title='Изменить заметку';edit.setAttribute('aria-label','Изменить заметку: '+n.text);
     edit.addEventListener('click',()=>editNote(n));row.append(edit);root.append(row);
   }
 }
 function renderStock(){
   const root=el('homeStockRows');root.replaceChildren();
   const items=visibleStockItems(state.stock?.items||[],state.stock?.events||[]).filter(x=>!x.deleted);
   const nonempty=items.filter(x=>Number(x.quantity||0)>0||Number(x.reserved||0)>0||Number(x.shortage||0)>0);
   if(!nonempty.length)root.append(make('p','home-empty-state','Складских остатков пока нет.'));
   for(const item of nonempty.slice(0,3)){
     const quantity=Number(item.quantity||0),reserve=Number(item.reserved||0),available=Number(item.available??(quantity-reserve));
     const row=make('div','home-stock-row'+(available<0?' negative':''));
     const name=make('span','home-stock-row-name',displayProduct(item.product));
     name.append(make('small','','Остаток: '+number.format(quantity)+' · Резерв: '+number.format(reserve)));
     row.append(name,make('strong','',number.format(available)+' шт.'));root.append(row);
   }
   if(nonempty.length>3)root.append(make('small','','Ещё позиций: '+(nonempty.length-3)));
   const lacking=items.filter(x=>Number(x.shortage||0)>0||Number(x.available||0)<0);
   const alert=el('homeStockShortage');alert.hidden=lacking.length===0;
   if(lacking.length)alert.textContent='Дефицит: '+lacking.length+' поз. · К закупке ›';
 }
 function renderFinance(){
   const [year,month]=today().slice(0,7).split('-').map(Number);
   el('homeFinanceMonth').textContent=MONTHS[month-1]+' '+year+' · Текущий месяц';
   // Same six series, colors and monthly values as the chart in Financial Results.
   // This compact view always selects the current month in Moscow.
   const months=calculateFinancialSummary(state.records,year,state.finance).months;
   const values=months[month-1];
   renderMonthlyFinanceChart(el('homeFinanceBars'),months,year,month);
   const pct=values.contract&&values.profitAfter!==null?values.profitAfter/values.contract*100:0;
   const label=el('homeProfitability');label.classList.toggle('loss',pct<0);
   label.textContent=values.contract&&values.profitAfter!==null?'Рентабельность к сумме: '+pct.toLocaleString('ru-RU',{minimumFractionDigits:1,maximumFractionDigits:1})+' %':'Рентабельность: —';
 }
 function renderDemoSummary(){
   const panel=el('demoContractSummary');if(!panel)return;
   panel.hidden=false;
   const [year,month]=today().slice(0,7).split('-').map(Number);
   const monthly=calculateFinancialSummary(state.records,year,state.finance).months[month-1];
   const pct=monthly.contract&&monthly.profitAfter!==null?monthly.profitAfter/monthly.contract*100:null;
   const root=el('demoContractSummaryRows');root.replaceChildren();
   const metrics=make('div','demo-summary-metrics');
   for(const [label,value] of [['Контрактов',String(monthly.count)],['Сумма',formatMoney(monthly.contract)]]){
     const tile=make('div','demo-summary-tile');tile.append(make('small','',label),make('strong','',value));metrics.append(tile);
   }
   root.append(metrics);
   const profitability=make('div','demo-summary-profitability');
   profitability.append(make('span','','Рентабельность'),make('strong','',pct===null?'—':pct.toLocaleString('ru-RU',{maximumFractionDigits:1,minimumFractionDigits:1})+' %'));
   root.append(profitability);
   for(const [label,amount] of [['Общие расходы',monthly.common],['Чистая прибыль',monthly.profitAfter]]){
     const line=make('div','demo-summary-row');line.append(make('span','',label),make('strong','',amount===null?'—':formatMoney(amount)));root.append(line);
   }
   root.append(make('div','demo-summary-stock-title','Склад на '+fmtDate(today())));
   const items=visibleStockItems(state.stock?.items||[],state.stock?.events||[]).filter(x=>!x.deleted);
   if(!items.length)root.append(make('div','demo-summary-row','Нет складских позиций'));
   for(const item of items){
     const quantity=Number(item.quantity||0),reserved=Number(item.reserved||0);
     const available=Number(item.available??(quantity-reserved));
     const row=make('div','demo-summary-stock-item');
     row.dataset.product=item.product;
     row.append(make('div','demo-summary-stock-name',displayProduct(item.product)||item.product||'Товар'));
     const values=make('div','demo-summary-stock-values');
     for(const [key,label,value] of [['quantity','На складе',quantity],['reserved','В резерве',reserved],['available','Доступно',available]]){
       const cell=make('div',key==='available'&&value<0?'negative':'');
       const count=make('strong','',number.format(value)+' шт.');
       count.dataset.stockField=key;
       cell.append(make('small','',label),count);
       values.append(cell);
     }
     row.append(values);
     root.append(row);
   }
   const foot=el('demoSummaryPeriod');if(foot)foot.textContent='Финансовый период: '+MONTHS[month-1]+' '+year;
 }
 function render(){renderMetrics();renderContracts();renderNotes();renderDemoSummary();renderStock();renderFinance();drawQuick(toast);if(!el('homeCalendarPopover').hidden)renderCalendar()}
 function openProfile(){focusBeforeModal=document.activeElement;drawProfile(toast);el('homeProfileModal').hidden=false;el('homeProfileClose').focus()}
 function closeProfile(){el('homeProfileModal').hidden=true;focusBeforeModal?.focus?.()}
 function openCalendar(){const [y,m]=today().slice(0,7).split('-').map(Number);calYear=y;calMonth=m;calSelected=today();el('homeCalendarPopover').hidden=false;el('homeCalendarToggle').setAttribute('aria-expanded','true');renderCalendar()}
 function closeCalendar(){el('homeCalendarPopover').hidden=true;el('homeCalendarToggle').setAttribute('aria-expanded','false')}
 function renderCalendar(){
   if(el('homeCalendarPopover').hidden)return;
   if(!calYear){const [y,m]=today().slice(0,7).split('-').map(Number);calYear=y;calMonth=m;calSelected=today()}
   el('homeCalTitle').textContent=MONTHS[calMonth-1]+' '+calYear;
   const root=el('homeCalendarDays');root.replaceChildren();
   for(const day of ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'])root.append(make('span','home-cal-weekday',day));
   const offset=(new Date(Date.UTC(calYear,calMonth-1,1)).getUTCDay()+6)%7;
   for(let i=0;i<offset;i++)root.append(make('span'));
   const days=new Date(Date.UTC(calYear,calMonth,0)).getUTCDate();
   const events=calendarEvents(state.records,notes.notes,calYear,calMonth,today());
   for(let d=1;d<=days;d++){
     const date=calYear+'-'+String(calMonth).padStart(2,'0')+'-'+String(d).padStart(2,'0');
     const b=make('button','home-calendar-day',String(d));b.type='button';b.setAttribute('aria-label',fmtDate(date));
     if(date===calSelected)b.classList.add('selected');
     if(date===today())b.classList.add('today');
     const actions=events.get(date)||[];
     if(actions.some(x=>x.type==='contract'))b.classList.add('has-contract');
     if(actions.some(x=>x.type==='note'))b.classList.add('has-note');
     b.addEventListener('click',()=>{calSelected=date;renderCalendar()});root.append(b);
   }
   el('homeCalSelected').textContent='События на '+fmtDate(calSelected);
   const list=el('homeCalEvents');list.replaceChildren();
   const selected=events.get(calSelected)||[];
   if(!selected.length)list.append(make('p','home-empty-state','На выбранную дату событий нет.'));
   for(const item of selected){
     const b=make('button','home-cal-event',item.label);b.type='button';b.append(make('small','',item.detail));
     b.addEventListener('click',()=>{closeCalendar();item.type==='contract'?contractOpen(item.record):editNote(item.note)});
     list.append(b);
   }
 }
 function shiftCalendar(n){
   const newDate=new Date(Date.UTC(calYear,calMonth-1+n,1));
   calYear=newDate.getUTCFullYear();calMonth=newDate.getUTCMonth()+1;
   calSelected=calYear+'-'+String(calMonth).padStart(2,'0')+'-01';renderCalendar();
 }
 el('homeSupplierKpi').addEventListener('click',()=>headerPanel('signatures'));
 el('homeCustomerKpi').addEventListener('click',()=>headerPanel('customer'));
 el('homeUnpaidKpi').addEventListener('click',()=>headerPanel('unpaid'));
 el('homeContractSearch').addEventListener('input',e=>{query=e.target.value;renderContracts()});
 el('homeAllContracts').addEventListener('click',()=>switchView('contracts'));
 el('homeAllNotes').addEventListener('click',()=>{showAllNotes=!showAllNotes;renderNotes()});
 el('homeGoStock').addEventListener('click',()=>switchView('stock'));
 el('homeStockShortage').addEventListener('click',()=>switchView('stock'));
 el('homeGoSummary').addEventListener('click',()=>{state.summaryYear=Number(today().slice(0,4));switchView('summary')});
 el('homeOpenFullProfile').addEventListener('click',openProfile);
 el('homeProfileClose').addEventListener('click',closeProfile);
 el('homeProfileCloseBottom').addEventListener('click',closeProfile);
 el('homeProfileModal').addEventListener('click',e=>{if(e.target===el('homeProfileModal'))closeProfile()});
 el('homeProfileCopyAll').addEventListener('click',async()=>{try{await copyProfileValue(PROFILE_SECTIONS.flatMap(x=>x.fields.map(([k,v])=>k+': '+v)).join('\n'));toast('Все реквизиты скопированы')}catch(e){toast('Не удалось скопировать: '+e.message,true)}});
 el('homeProfilePrint').addEventListener('click',()=>window.print());
 el('homeNoteScheduleToggle').addEventListener('click',()=>{
   const panel=el('homeNoteScheduleFields');
   panel.hidden=!panel.hidden;
   el('homeNoteScheduleToggle').setAttribute('aria-expanded',String(!panel.hidden));
   if(!panel.hidden)el('homeNoteDue').focus();
 });
 el('homeNoteAdd').addEventListener('submit',async e=>{
   e.preventDefault();
   const input=el('homeNoteText'),text=input.value.trim();
   if(!text)return;
   const dueDate=el('homeNoteScheduleFields').hidden?'':el('homeNoteDue').value;
   const dueTime=el('homeNoteScheduleFields').hidden?'':el('homeNoteTime').value;
   if(dueTime&&!dueDate){toast('Укажите дату для выбранного времени',true);return}
   if(await mutate('create',{text,dueDate,dueTime})){
     input.value='';el('homeNoteDue').value='';el('homeNoteTime').value='';
     el('homeNoteScheduleFields').hidden=true;
     el('homeNoteScheduleToggle').setAttribute('aria-expanded','false');
     input.focus();
   }
 });
 el('homeNoteEditForm').addEventListener('submit',async e=>{
   e.preventDefault();
   if(!editingNote)return;
   const text=el('homeNoteEditText').value.trim();
   const dueDate=el('homeNoteEditDue').value,dueTime=el('homeNoteEditTime').value;
   if(dueTime&&!dueDate){toast('Укажите дату для выбранного времени',true);return}
   if(text&&await mutate('update',{id:editingNote.id,text,dueDate,dueTime}))closeNote();
 });
 el('homeNoteCancel').addEventListener('click',closeNote);
 el('homeNoteModal').addEventListener('click',e=>{if(e.target===el('homeNoteModal'))closeNote()});
 el('homeCalendarToggle').addEventListener('click',()=>el('homeCalendarPopover').hidden?openCalendar():closeCalendar());
 el('homeCalPrev').addEventListener('click',()=>shiftCalendar(-1));
 el('homeCalNext').addEventListener('click',()=>shiftCalendar(1));
 document.addEventListener('click',e=>{if(!el('homeCalendarPopover').hidden&&!el('homeCalendarPopover').contains(e.target)&&!el('homeCalendarToggle').contains(e.target))closeCalendar()});
 document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(!el('homeProfileModal').hidden){closeProfile();return}if(!el('homeNoteModal').hidden){closeNote();return}if(!el('homeCalendarPopover').hidden)closeCalendar()});
 for(const node of document.querySelectorAll('[data-icon]'))node.innerHTML=icons[node.dataset.icon]||icons.file;
 return {render,renderMetrics,setNotes,openProfile,closeCalendar,setSearch(value){query=String(value);el('homeContractSearch').value=query;}};
}
