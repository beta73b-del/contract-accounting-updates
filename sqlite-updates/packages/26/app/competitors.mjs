import {sixMonthStart, modelMetrics, normalizeProductName, productIdentity, productLabel, productGroups,
  formatKopecks, winnerIdentity, winnerHistory} from './competitors-logic.mjs?v=build026';

const el = id => document.getElementById(id);
const node = (tag,className='',text) => {
  const element=document.createElement(tag);
  if(className) element.className=className;
  if(text!==undefined) element.textContent=String(text);
  return element;
};
const formatDate = d => String(d).split('-').reverse().join('.');
const quantityFormat = new Intl.NumberFormat('ru-RU');
const rublesFromCents = value => (Math.floor(value/100)+'.'+String(value%100).padStart(2,'0'));

export function initCompetitors({api,toast,today,demo}) {
  let entries=[],selectedProduct=null,selectedWinner=null,editRecord=null,saveKey=null,busy=false,open=false;
  const form=el('competitorForm'), backdrop=el('competitorFormBackdrop');
  const fields={date:'competitorDate',productName:'competitorModel',platform:'competitorPlatform',
    winner:'competitorWinner',inn:'competitorInn',quantity:'competitorQuantity',price:'competitorPrice'};
  const values=()=>Object.fromEntries(Object.entries(fields).map(([key,id])=>[key,el(id).value.trim()]));
  const msg=(text,error=false)=>{
    const target=el('competitorFeedback');
    target.hidden=!text;
    target.textContent=text||'';
    target.classList.toggle('error',error);
  };
  function closeForm() {
    if(busy)return;
    backdrop.hidden=true;
    editRecord=null;saveKey=null;
    el('competitorFormError').hidden=true;
    el('competitorAdd').focus({preventScroll:true});
  }
  function showFormError(text) {
    const message=el('competitorFormError');
    message.textContent=text;
    message.hidden=!text;
  }
  function openForm(row=null) {
    if(busy)return;
    editRecord=row;
    saveKey=row?null:crypto.randomUUID();
    form.reset();
    const t=today();
    el('competitorDate').max=t;
    el('competitorDate').value=row?.date || t;
    el('competitorModel').value=row ? productLabel(row) : (productGroups(entries).find(g=>g.model===selectedProduct)?.label || '');
    el('competitorPlatform').value=row?.platform || '';
    el('competitorWinner').value=row?.winner || '';
    el('competitorInn').value=row?.inn || '';
    el('competitorQuantity').value=row?.quantity || '';
    el('competitorPrice').value=row ? rublesFromCents(row.priceKopecks) : '';
    el('competitorFormTitle').textContent=row?'Изменить процедуру':'Добавить процедуру';
    showFormError('');
    backdrop.hidden=false;
    el('competitorModel').focus({preventScroll:true});
  }
  function validate(data) {
    if(!/^\d{4}-\d{2}-\d{2}$/.test(data.date) || data.date>today() || data.date<'2020-01-01') return 'Проверьте дату процедуры';
    if(!normalizeProductName(data.productName)) return 'Укажите наименование товара (1–200 символов)';
    if(!data.platform || data.platform.length>120) return 'Укажите площадку (не более 120 символов)';
    if(!data.winner || data.winner.length>200) return 'Укажите победителя (не более 200 символов)';
    if(data.inn && !/^(?:\d{10}|\d{12})$/.test(data.inn)) return 'ИНН должен содержать 10 или 12 цифр';
    if(!/^[1-9]\d{0,6}$/.test(data.quantity) || +data.quantity>1000000) return 'Количество: целое от 1 до 1 000 000';
    if(!/^\d{1,9}(?:[.,]\d{1,2})?$/.test(data.price)) return 'Цена: положительная сумма с точностью до копеек';
    const price=Number(data.price.replace(',','.'));
    if(!Number.isFinite(price) || price<=0 || price>900000000) return 'Недопустимая цена за штуку';
    return '';
  }
  function priceRow(row,withModel=false) {
    const card=node('div','comp-entry');
    const head=node('div','comp-entry-heading');
    head.append(node('time','',formatDate(row.date)),node('span','comp-platform',row.platform));
    card.append(head);
    const winner=node('button','comp-winner',row.winner);
    winner.type='button';winner.title='История победителя по всем товарам';
    winner.addEventListener('click',()=>{
      selectedWinner=winnerIdentity(row);
      renderWinner();
      el('competitorWinnerPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
    });
    card.append(winner);
    if(withModel) card.append(node('span','comp-model-tag',productLabel(row)));
    const foot=node('div','comp-entry-foot');
    foot.append(node('span','',quantityFormat.format(row.quantity)+' шт.'),
      node('strong','',formatKopecks(row.priceKopecks)));
    card.append(foot);
    return card;
  }
  function entryWithActions(row,withModel=false) {
    const wrapper=node('div','comp-entry-wrap');
    wrapper.append(priceRow(row,withModel));
    const actions=node('div','comp-entry-actions');
    const edit=node('button','','✎');
    edit.type='button';edit.title='Изменить запись';edit.setAttribute('aria-label','Изменить '+row.winner);
    edit.addEventListener('click',()=>openForm(row));
    const remove=node('button','','×');
    remove.type='button';remove.title='Удалить ошибочную запись';remove.setAttribute('aria-label','Удалить '+row.winner);
    remove.addEventListener('click',()=>removeRow(row));
    actions.append(edit,remove);wrapper.append(actions);
    return wrapper;
  }
  function productIcon() {
    const icon=node('span','comp-product-icon','▦');
    icon.setAttribute('aria-hidden','true');return icon;
  }
  function renderModel(group) {
    const stat=modelMetrics(entries,group.model,today());
    const section=node('section','comp-model');section.setAttribute('aria-label',group.label);
    const heading=node('h3');heading.append(productIcon(),node('span','',group.label));section.append(heading);
    const metrics=node('div','comp-metrics');
    for(const [label,value] of [['Минимальная цена',stat.min],['Медиана',stat.median]]) {
      const box=node('div');box.append(node('span','',label),node('strong','',value));metrics.append(box);
    }
    section.append(metrics);
    const list=node('div','comp-history');section.append(list);
    if(!stat.entries.length) list.append(node('p','comp-empty','Нет сохранённых цен за последние 6 месяцев'));
    else for(const row of stat.entries) list.append(entryWithActions(row));
    return section;
  }
  function renderProducts() {
    const groups=productGroups(entries);
    if(selectedProduct!==null && !groups.some(g=>g.model===selectedProduct)) selectedProduct=null;
    const filters=el('competitorProductFilters'), grid=el('competitorProductGroups');
    filters.replaceChildren();grid.replaceChildren();
    filters.hidden=groups.length===0;
    if(!groups.length) {grid.append(node('p','comp-empty','Добавьте первую процедуру — карточка товара появится автоматически.'));return;}
    for(const g of [{model:null,label:'Все товары'},...groups]) {
      const button=node('button','comp-product-filter');button.type='button';
      button.setAttribute('aria-pressed',String(g.model===selectedProduct));
      if(g.model!==null) button.append(productIcon());
      button.append(node('span','',g.label));
      button.addEventListener('click',()=>{selectedProduct=g.model;renderProducts();renderArchive();});
      filters.append(button);
    }
    for(const g of groups) if(selectedProduct===null || g.model===selectedProduct) grid.append(renderModel(g));
  }
  function renderArchive() {
    const olderThan=sixMonthStart(today());
    const rows=entries.filter(row=>row.date<olderThan && (selectedProduct===null || row.model===selectedProduct))
      .sort((a,b)=>b.date.localeCompare(a.date) || String(b.created||'').localeCompare(String(a.created||'')));
    const archive=el('competitorArchive');
    archive.hidden=rows.length===0;
    const list=el('competitorArchiveRows');list.replaceChildren();
    for(const row of rows) list.append(entryWithActions(row,true));
  }
  function renderWinner() {
    const panel=el('competitorWinnerPanel');
    panel.replaceChildren();
    if(!selectedWinner) {panel.hidden=true;return;}
    const history=winnerHistory(entries,selectedWinner,today());
    if(!history.length){selectedWinner=null;panel.hidden=true;return;}
    panel.hidden=false;
    const titleLine=node('div','comp-winner-heading');
    const title=node('div');
    title.append(node('h3','','История победителя: '+history[0].winner));
    const identity=history.find(e=>e.inn);
    if(identity)title.append(node('p','','ИНН: '+identity.inn));
    const close=node('button','','×');
    close.type='button';close.title='Закрыть историю';
    close.setAttribute('aria-label','Закрыть историю победителя');
    close.addEventListener('click',()=>{selectedWinner=null;renderWinner()});
    titleLine.append(title,close);
    panel.append(titleLine);
    for(const group of productGroups(history)) {
      const lines=history.filter(row=>row.model===group.model);
      const section=node('section','comp-winner-model');
      section.append(node('h4','',group.label));
      if(!lines.length) {
        section.append(node('p','comp-empty','Нет наблюдений'));
      } else {
        const recent=lines.filter(row=>!row.archived);
        const old=lines.filter(row=>row.archived);
        if(recent.length){
          const label=node('p','comp-winner-group','Последние 6 месяцев');
          section.append(label);
          const min=Math.min(...recent.map(x=>x.priceKopecks));
          section.append(node('p','comp-winner-min','Минимум: '+formatKopecks(min)));
          for(const item of recent) section.append(priceRow(item));
        }
        if(old.length){
          section.append(node('p','comp-winner-group comp-winner-archive','Архив — не входит в текущую аналитику'));
          for(const item of old) section.append(priceRow(item));
        }
      }
      panel.append(section);
    }
  }
  let renderedDay='';
  function render() {
    const t=today();renderedDay=t;
    el('competitorsWindow').textContent='('+formatDate(sixMonthStart(t))+' — '+formatDate(t)+')';
    renderProducts();
    renderArchive();
    renderWinner();
  }
  async function removeRow(row){
    if(busy)return;
    if(!window.confirm('Удалить запись о цене '+formatKopecks(row.priceKopecks)+' для '+row.winner+'?'))return;
    busy=true;
    try{
      if(demo){entries=entries.filter(e=>e.id!==row.id);}
      else {
        const res=await api('/api/competitors',{action:'delete',id:row.id,version:row.version,confirmed:true});
        entries=res.entries;
      }
      msg('Запись удалена');render();
    }catch(error){msg(error.message,true);toast(error.message,true);}
    finally{busy=false;}
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(busy)return;
    const entry=values();
    const error=validate(entry);
    if(error){showFormError(error);return;}
    busy=true;
    const button=el('competitorFormSave');
    button.disabled=true;
    try {
      if(demo){
        const current=editRecord?.id || crypto.randomUUID();
        const normalized=entry.price.replace(',','.');
        const [rub,frac='']=normalized.split('.');
        const cents=Number(rub)*100+Number(frac.padEnd(2,'0'));
        const version=editRecord ? editRecord.version+1 : 1;
        const record={...entry,...productIdentity(entry.productName,entries),id:current,priceKopecks:cents,quantity:Number(entry.quantity),version,
          created:editRecord?.created||new Date().toISOString()};
        entries=entries.filter(e=>e.id!==current).concat(record);
      } else {
        const body=editRecord ?
          {action:'edit',id:editRecord.id,version:editRecord.version,entry} :
          {action:'add',requestId:saveKey,entry};
        const res=await api('/api/competitors',body);
        entries=res.entries;
      }
      if(selectedProduct!==null) selectedProduct=productIdentity(entry.productName,entries).model;
      backdrop.hidden=true;editRecord=null;saveKey=null;
      render();msg(demo?'Демонстрация: данные сохранены только до обновления страницы':'Запись сохранена');
    }catch(e){showFormError(e.message);toast(e.message,true);}
    finally{busy=false;button.disabled=false;}
  });
  el('competitorAdd').addEventListener('click',()=>openForm());
  el('competitorFormCancel').addEventListener('click',closeForm);
  el('competitorFormClose').addEventListener('click',closeForm);
  backdrop.addEventListener('click',event=>{if(event.target===backdrop)closeForm();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!backdrop.hidden)closeForm();});
  const refreshDay=()=>{if(el('view-competitors').classList.contains('active')&&renderedDay!==today())render();};
  setInterval(refreshDay,15000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshDay();});
  return {
    async open(){
      if(open) return;
      open=true;
      try{
        if(!demo) {
          const data=await api('/api/competitors');
          entries=Array.isArray(data.entries)?data.entries:[];
        }
        msg('');render();
      }catch(e){msg('Не удалось загрузить базу конкурентов: '+e.message,true);}
      finally{open=false;}
    },
    render
  };
}
