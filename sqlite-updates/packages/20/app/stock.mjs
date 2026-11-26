import {formatMoney, moneyToKopecks, todayInMoscow, displayProduct, visibleStockItems} from './logic.mjs?v=1.7.21-shipped1';


// Advisory only: never merge records or change the submitted product name.
export function similarStockProducts(input, products) {
  const clean = text => String(text ?? '').toLocaleLowerCase('ru-RU').replace(/ё/g,'е').replace(/[^a-zа-я0-9]+/g,'');
  const value = clean(input);
  if (value.length < 4) return [];
  const codes = text => [...text.matchAll(/\d{3,}/g)].map(m => m[0]);
  const inputCodes = codes(String(input));
  return products.filter(name => {
    const candidate = clean(name);
    if (value === candidate) return true;
    const candidateCodes = codes(String(name));
    if (inputCodes.length && candidateCodes.length && !inputCodes.some(code => candidateCodes.includes(code))) return false;
    if (inputCodes.some(code => candidateCodes.includes(code))) {
      const letters = text => text.replace(/[0-9]/g,'');
      const a = letters(value), b = letters(candidate);
      return a.length >= 4 && b.length >= 4 && (a.includes(b) || b.includes(a) || a.slice(0,5) === b.slice(0,5));
    }
    return value.length >= 6 && candidate.length >= 6 && (value.includes(candidate) || candidate.includes(value));
  }).slice(0,5);
}

const quantity = new Intl.NumberFormat('ru-RU');
const number = value => quantity.format(value);
const dateText = value => value.split('-').reverse().join('.');
const labels = {
  receipt:'Приход', createProduct:'Создана карточка', editProduct:'Изменены характеристики', deleteProduct:'Карточка удалена', restoreProduct:'Карточка восстановлена', adjust:'Корректировка', reserve:'Резерв', release:'Снят резерв',
  ship:'Отправка', cancel:'Отмена отправки', return:'Возврат', editReceipt:'Исправление прихода', deleteReceipt:'Удаление прихода'
};
const select = id => document.getElementById(id);

function tableRow(body, cells) {
  const row = body.insertRow();
  for (const content of cells) {
    const cell = row.insertCell();
    if (content instanceof Node) cell.append(content);
    else cell.textContent = content;
  }
  return row;
}

// Sum only physical inventory value, in integer kopecks, including reserved goods.
// No quantity, reserve or availability totals across unlike products.
export function stockBalanceTotal(items) {
  let cents = 0n;
  for (const item of items) {
    if (item.deleted) continue;
    if (!Number.isSafeInteger(item.cents) || item.cents < 0) return 'Ошибка данных';
    cents += BigInt(item.cents);
  }
  return number(cents / 100n) + ',' + String(cents % 100n).padStart(2, '0') + ' ₽';
}

export function periodBalances(events, product, from, to) {
  let start = [0, 0];
  let finish = [0, 0];
  const totals = {};
  const movements = [];
  for (const event of events) {
    if (event.deleted || ['createProduct','deleteProduct','editReceipt','deleteReceipt'].includes(event.kind)) continue;
    const display = event.lines.length ? event.lines : event.needs;
    const entries = display.filter(line => !product || line.product === product);
    for (const line of event.lines.filter(line => !product || line.product === product)) {
      const delta = [line.quantity, line.cents];
      if (from && event.date < from) start = start.map((n,i) => n + delta[i]);
      if (!to || event.date <= to) finish = finish.map((n,i) => n + delta[i]);
      if ((!from || event.date >= from) && (!to || event.date <= to)) {
        totals[event.kind] = (totals[event.kind] || [0,0]).map((n,i) => n + delta[i]);
      }
    }
    if ((!from || event.date >= from) && (!to || event.date <= to)) {
      for (const line of entries) movements.push({event,line});
      if (!display.length && !product) movements.push({event,line:null});
    }
  }
  return {start,finish,totals,movements};
}

export function summarizeWarehouse(items, events, product, from, to, activeContractIds = null) {
 const active=visibleStockItems(items,events).filter(item=>!item.deleted && (!product||item.product===product));
 const rows=new Map(active.map(item=>[item.product,{product:item.product,receipt:0,receiptCents:0,sales:0,salesCents:0}]));
 const movements=[];
 const terminatedIds=new Set(events.filter(e=>e.kind==='trashDeleteContract' && e.termination===true).map(e=>e.trashId));
 for(const event of events){
  if(!['receipt','ship','return','cancel'].includes(event.kind) || event.deleted)continue;
  // Исторический складской журнал неизменен; исключаем из аналитики
  // движения ошибочно удалённых контрактов, но не фактические отправки и возвраты при расторжении.
  if(activeContractIds && event.contractId && !activeContractIds.has(event.contractId) && !terminatedIds.has(event.contractId))continue;
  if((from&&event.date<from)||(to&&event.date>to))continue;
  for(const line of event.lines||[]){
   const row=rows.get(line.product);if(!row || (event.kind==='receipt' && Number(line.quantity)<=0))continue;
   const qty=Number(line.quantity),cents=Number(line.cents);
   if(event.kind==='receipt'){row.receipt+=qty;row.receiptCents+=cents;}
   else {row.sales-=qty;row.salesCents-=cents;}
   movements.push({event,line});
  }
 }
 return {rows:[...rows.values()],movements};
}

export function initStock({state, api, toast, openForm, demo, updateCloudStatus, onContractChange}) {
  const nav = select('navStock');
  nav.hidden = demo;
  let activeTab = 'stockBalances';
  let stockPeriodMode = 'month';
  let sending = false;
  let formKind = 'receipt';
  let editTarget = '';
  let editVersion = '';
  let card = null;
  let selectedStockProduct = null;
  let showDeleted = false;
  let pendingKey = null;

  const products = () => visibleStockItems(state.stock?.items || [], state.stock?.events || []).map(item => item.product);
  function updateChoices(node, first, extra = []) {
    const old = node.value;
    node.replaceChildren(...[...first,...products().map(name => [name,displayProduct(name)]),...extra]
      .map(([value,label]) => new Option(label,value)));
    if ([...node.options].some(option => option.value === old)) node.value = old;
  }

  function showReserveDetails(item) {
    const existing = select('stockReserveReadOnly');
    if (existing) existing.remove();
    const panel = document.createElement('section');
    panel.id = 'stockReserveReadOnly';
    panel.className = 'panel stock-reservations';
    const title = document.createElement('h2');
    title.textContent = 'Резерв: ' + displayProduct(item.product);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'btn btn-secondary';
    close.textContent = 'Закрыть';
    close.addEventListener('click', () => panel.remove());
    const header = document.createElement('div');
    header.className = 'stock-card-heading';
    header.append(title, close);
    panel.append(header);
    let found = false;
    for (const [id, phase] of Object.entries(state.stock.phases || {})) {
      if (phase !== 'reserved') continue;
      const contract = state.records.find(record => record.id === id && !record.deleted);
      const event = state.stock.events.findLast(entry => entry.contractId === id && !entry.deleted);
      const need = event?.needs?.find(line => line.product === item.product);
      if (!contract || !need) continue;
      found = true;
      const line = document.createElement('p');
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'contract-customer';
      link.textContent = (contract.customer || 'Заказчик не указан') +
        (contract.contractNumber ? ' · № ' + contract.contractNumber : '') +
        ' · ' + number(need.quantity) + ' шт.';
      link.title = 'Открыть карточку контракта';
      link.addEventListener('click', () => openForm(contract));
      line.append(link);
      panel.append(line);
    }
    if (!found) {
      const empty = document.createElement('p');
      empty.textContent = 'Действующих резервов по этому товару нет';
      panel.append(empty);
    }
    select('stockProductCard').hidden = true;
    select('stockBalances').append(panel);
    panel.scrollIntoView({block:'nearest'});
  }

  function render() {
    if (!state.stock) return;
    const balances = select('stockBalanceBody');
    balances.replaceChildren();
    const items = visibleStockItems(state.stock.items, state.stock.events, showDeleted);
    select('stockBalanceTotal').textContent = stockBalanceTotal(items);
    for (const item of items) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'contract-customer';
      button.textContent = displayProduct(item.product) + (item.deleted ? ' (удалён)' : '');
      button.addEventListener('click', () => {
        select('stockReserveReadOnly')?.remove();
        if (sending) return;
        selectedStockProduct = item.product;
        select('stockProductCharacteristics').value = item.characteristics || '';
        select('stockProductDetailsMessage').textContent = '';
        select('productAdjustmentSection').open = false;
        select('productAdjustment').reset();
        select('productAdjustmentQuantity').value = item.quantity;
        select('productAdjustmentMessage').textContent = '';
        drawProductCard();
        select('stockProductCard').scrollIntoView({block:'nearest'});
      });
      const reserve = document.createElement('button');
      reserve.type = 'button';
      reserve.className = 'contract-customer';
      reserve.textContent = number(item.reserved);
      reserve.title = 'Посмотреть, за какими контрактами закреплён резерв';
      reserve.setAttribute('aria-label', 'Резерв ' + displayProduct(item.product) + ': ' + number(item.reserved) + ' шт. Открыть детализацию');
      reserve.addEventListener('click', () => showReserveDetails(item));
      const row = tableRow(balances, [button, number(item.quantity), reserve,
        number(item.available), item.shortage ? 'Не хватает ' + number(item.shortage) + ' шт.' : '—', formatMoney(item.cents)]);
      if (item.shortage) row.cells[4].classList.add('stock-shortage');
    }
    const filter = select('stockFilterProduct');
    const prior = filter.value;
    filter.replaceChildren(new Option('Все товары',''), ...visibleStockItems(state.stock.items,state.stock.events).map(item => new Option(displayProduct(item.product),item.product)));
    if ([...filter.options].some(option => option.value === prior)) filter.value = prior;
    drawProductCard();
    renderHistory();
  }

  function drawProductCard() {
    const panel = select('stockProductCard');
    const item = state.stock?.items.find(entry => entry.product === selectedStockProduct);
    panel.hidden = !item;
    if (!item) return;
    select('stockProductCardName').textContent = displayProduct(item.product) + (item.deleted ? ' (удалён)' : '');
    const fixed = ['Рутокен 1010','Рутокен ЭЦП 3.0 3120'].includes(item.product);
    select('stockProductDelete').hidden = fixed || Boolean(item.deleted);
    select('stockProductRestore').hidden = !item.deleted;
    select('stockProductDetailsForm').hidden = true;
    select('productAdjustmentSection').hidden = Boolean(item.deleted);
    select('stockProductCardBalance').textContent = `На складе ${number(item.quantity)} шт. · В резерве ${number(item.reserved)} шт. · Доступно ${number(item.available)} шт.`;
    const reservations = select('stockReservations');
    reservations.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = 'Резервы по контрактам';
    reservations.append(heading);
    let found = false;
    for (const [id, phase] of Object.entries(state.stock.phases)) {
      if (phase !== 'reserved') continue;
      const contract = state.records.find(record => record.id === id);
      const recent = state.stock.events.findLast(event => event.contractId === id);
      const need = recent?.needs.find(line => line.product === item.product);
      if (!contract || !need) continue;
      found = true;
      const entry = document.createElement('p');
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'contract-customer';
      link.textContent = contract.customer;
      link.addEventListener('click', () => openForm(contract));
      entry.append(link, ' · ' + number(need.quantity) + ' шт.' +
        (contract.fulfillBy ? ' · срок ' + dateText(contract.fulfillBy) : ''));
      reservations.append(entry);
    }
    if (!found) {
      const message = document.createElement('p');
      message.textContent = 'Действующих резервов нет';
      reservations.append(message);
    }
    const rawQuantity=select('productAdjustmentQuantity').value;
    const target=/^\d{1,9}$/.test(rawQuantity) ? Number(rawQuantity) : null;
    const preview=select('productAdjustmentPreview');
    preview.textContent=target===null ? 'Укажите целое количество от 0 до 999 999 999.' :
      'Сейчас на складе: '+number(item.quantity)+' шт. · После корректировки: '+number(target)+' шт. · В резерве: '+number(item.reserved)+' шт. · Доступно: '+number(target-item.reserved)+' шт.';
    const increased = target !== null && target > item.quantity;
    select('productAdjustmentPriceField').hidden = target === null || target === 0;
    select('productAdjustmentPrice').required = increased;
  }

  function changeTab(id) {
    activeTab = id;
    for (const tab of document.querySelectorAll('[data-stock-tab]')) {
      const current = tab.dataset.stockTab === id;
      tab.setAttribute('aria-selected', String(current));
      select(tab.dataset.stockTab).hidden = !current;
    }
    if (id === 'stockHistory') renderHistory();
  }

  const receiptVersion = receipt => {
    const correction = state.stock.events.findLast(event =>
      ['editReceipt','deleteReceipt'].includes(event.kind) && event.target === receipt.id);
    return correction?.id || receipt.id;
  };

  async function deleteReceipt(receipt) {
    if (demo || sending || !receipt || receipt.deleted) return;
    const line = receipt.lines?.[0];
    if (!line) return;
    const message = 'Удалить приход от ' + dateText(receipt.date) + ' — ' + displayProduct(line.product) +
      ', ' + number(line.quantity) + ' шт., ' + formatMoney(line.cents) +
      '?\n\nПриход исчезнет из аналитики и будет пересчитан складской остаток. Операция останется в журнале изменений для аудита.';
    if (!window.confirm(message)) return;
    sending = true;
    const version = receiptVersion(receipt);
    select('stockPeriodMessage').textContent = 'Проверяем и удаляем приход…';
    try {
      await save({kind:'deleteReceipt',target:receipt.id,expectedVersion:version, note:'Удалён ошибочный приход'});
      toast('Приход удалён; склад и аналитика пересчитаны');
    } catch(error) {
      select('stockPeriodMessage').textContent = 'Не удалось удалить приход: '+error.message;
      toast('Не удалось удалить приход: '+error.message,true);
    } finally { sending = false; }
  }

  function chooseStockPeriod(mode) {
    stockPeriodMode = mode;
    const today = todayInMoscow();
    if (mode === 'month') {
      select('stockFrom').value = today.slice(0,7) + '-01';
      select('stockTo').value = today;
    } else if (mode === 'year') {
      select('stockFrom').value = today.slice(0,4) + '-01-01';
      select('stockTo').value = today;
    }
    select('stockCustomDates').hidden = mode !== 'custom';
    for (const button of document.querySelectorAll('[data-stock-period]')) {
      button.setAttribute('aria-pressed', String(button.dataset.stockPeriod === mode));
    }
    renderHistory();
  }

  function renderHistory() {
    if (!state.stock) return;
    const from=select('stockFrom').value, to=select('stockTo').value;
    const shown = value => value ? value.split('-').reverse().join('.') : '—';
    select('stockPeriodLabel').textContent = shown(from) + ' — ' + shown(to);
    const body=select('stockMovementBody'), summary=select('stockPeriodBody'),chart=select('stockChart');
    body.replaceChildren();summary.replaceChildren();chart.replaceChildren();
    if(from&&to&&from>to){select('stockPeriodMessage').textContent='Дата начала позже окончания периода';return;}
    const {rows,movements}=summarizeWarehouse(state.stock.items,state.stock.events,select('stockFilterProduct').value,from,to,
      new Set(state.records.filter(record => record.check !== 'Удалён' && record.deleted !== true).map(record => record.id)));
    const series=[['receipt','Приход'],['sales','Реализация']];
    const maximum=Math.max(0,...rows.flatMap(row=>series.map(([key])=>Math.abs(row[key]))));
    for(const row of rows){
      tableRow(summary,[displayProduct(row.product),number(row.receipt),formatMoney(row.receiptCents),number(row.sales),formatMoney(row.salesCents)]);
      const group=document.createElement('div');group.className='stock-chart-item';
      const name=document.createElement('div');name.className='stock-chart-name';name.textContent=displayProduct(row.product);
      const bars=document.createElement('div');bars.className='stock-chart-bars';
      for(const [kind,label] of series){
       const line=document.createElement('div');line.className='stock-bar-row';
       const track=document.createElement('div');track.className='stock-bar-track';
       const bar=document.createElement('span');bar.className='stock-bar '+(kind==='sales'?'ship':kind)+(row[kind]<0?' negative':'');bar.style.setProperty('--width',(maximum?100*Math.abs(row[kind])/maximum:0)+'%');
       const value=document.createElement('span');value.textContent=number(row[kind])+' шт.';value.setAttribute('aria-label',label+': '+number(row[kind])+' шт.');
       const seriesLabel=document.createElement('span');seriesLabel.className='stock-bar-label';seriesLabel.textContent=label;
       track.append(bar);line.append(seriesLabel,track,value);bars.append(line);
      }
      group.append(name,bars);chart.append(group);
    }
    if(!rows.length){const message=document.createElement('p');message.textContent='Нет доступных товаров';chart.append(message);}
    select('stockPeriodMessage').textContent=movements.length?'':'За этот период приходов и реализации нет';
    for(const {event,line} of movements.slice().reverse().sort((a,b)=>b.event.date.localeCompare(a.event.date))){
      const operation=document.createElement('span');operation.textContent=event.kind==='receipt'?'Приход':'Реализация';
      if(event.kind==='return'||event.kind==='cancel'){
       const note=document.createElement('small');note.className='stock-movement-note';note.textContent=event.kind==='return'?(event.termination?'Возврат при расторжении':'Возврат'):'Отмена отправки';operation.append(' ',note);
      }
      const actions=document.createElement('div');actions.className='stock-receipt-actions';
      if(event.kind==='receipt'){
       const supplier = document.createElement('small');
       supplier.className='stock-movement-note';
       supplier.textContent=event.party ? ' · '+event.party : '';
       operation.append(supplier);
       const edit=document.createElement('button');
       edit.type='button';edit.className='item-action stock-receipt-edit';edit.textContent='Изменить';
       edit.title='Изменить дату, количество, цену или поставщика этого прихода';
       edit.addEventListener('click',()=>showForm('editReceipt',event));
       const remove=document.createElement('button');
       remove.type='button';remove.className='item-action stock-receipt-delete';remove.textContent='Удалить';
       remove.title='Удалить только этот приход с пересчётом остатков и аналитики';
       remove.addEventListener('click',()=>deleteReceipt(event));
       actions.append(edit,remove);
      }
      const direction=event.kind==='receipt'?1:-1;
      tableRow(body,[dateText(event.date),operation,displayProduct(line.product),number(direction*line.quantity),formatMoney(direction*line.cents),actions]);
    }
  }

  async function save(data) {
    const signature = JSON.stringify(data);
    if (pendingKey?.signature !== signature) pendingKey = {signature,id:crypto.randomUUID().replaceAll('-','')};
    const result = await api('/api/stock', {...data,id:pendingKey.id});
    pendingKey = null;
    state.stock = result.stock;
    if (result.records) state.records = result.records;
    Object.assign(state,{backupAt:result.backupAt,backupWarning:result.backupWarning,cloudStatus:result.cloudStatus});
    updateCloudStatus();
    render();
  }

  function showSimilarProducts() {
    const area = select('stockSimilarProducts');
    area.replaceChildren();
    if (formKind !== 'receipt' || select('stockProduct').value) return;
    const matches = similarStockProducts(select('stockNewProduct').value, (state.stock?.items || []).map(item => item.product));
    if (!matches.length) return;
    const label = document.createElement('span');
    label.textContent = 'Похожий товар уже есть на складе. Можно выбрать его или продолжить ввод нового: ';
    area.append(label);
    for (const name of matches) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'item-action';
      button.textContent = displayProduct(name);
      button.addEventListener('click', () => {
        const options = select('stockProduct');
        if (![...options.options].some(option => option.value === name)) options.add(new Option(displayProduct(name), name));
        options.value = name;
        options.dispatchEvent(new Event('change', {bubbles:true}));
      });
      area.append(button, document.createTextNode(' '));
    }
  }

  function showForm(kind, receipt = null) {
    if (demo || !state.stock || sending) return;
    formKind = kind;
    editTarget = receipt?.id || '';
    editVersion = receipt ? receiptVersion(receipt) : '';
    const form = select('stockForm');
    form.reset();
    select('stockDate').value = todayInMoscow();
    select('stockDate').max = todayInMoscow();
    select('stockSimilarProducts').replaceChildren();
    const effective = receipt && (state.stock.events.findLast(event => event.kind === 'editReceipt' && event.target === receipt.id) || receipt);
    updateChoices(select('stockProduct'), []);
    select('stockProduct').disabled = Boolean(receipt);
    if (effective) {
      const line = receipt.lines[0];
      const qty = effective.correctedQuantity ?? line.quantity;
      const cents = effective.correctedCents ?? line.cents;
      select('stockDate').value = effective.correctedDate || receipt.date;
      select('stockProduct').value = line.product;
      select('stockQuantity').value = qty;
      select('stockPrice').value = (cents / qty / 100).toFixed(2);
      select('stockParty').value = effective.party;
    }
    select('stockFormTitle').textContent = {
      receipt:'Приход товара',editReceipt:'Исправление прихода'
    }[kind];
    select('stockQtyLabel').textContent = 'Количество, шт.';
    select('stockQuantity').min = '1';
    select('stockPartyField').hidden = false;
    select('stockNote').required = false;
    select('stockPrice').required = true;
    select('stockFormMessage').textContent = '';
    select('stockNewProductField').hidden = true;
    select('stockNewProduct').required = false;
    form.hidden = false;
    form.scrollIntoView({block:'nearest'});
    select('stockQuantity').focus();
  }

  function renderCard(record) {
    card = record;
    const phase = state.stock?.phases[record?.id];
    select('contractStock').hidden = demo || !state.stock;
    select('cost').readOnly = phase === 'shipped';
    select('costStockHint').hidden = phase !== 'shipped';
    const text = {shipped:'Товар отправлен',reserved:'',returned:'Товар возвращён на склад'};
    select('stockCardState').textContent = state.stock?.returned?.[record?.id] && phase === 'reserved' ? 'Товар возвращён на склад' : (text[phase] || '');
    select('stockShip').hidden = phase !== 'reserved';
  }

  async function cardAction(kind) {
    if (!card || sending || state.waiting || state.quickSaving) return;
    const record = card;
    const data = {kind,contractId:record.id,revision:record.revision};
    const form = select('contractForm');
    if (JSON.stringify([...new FormData(form)]) !== state.formSnapshot) {
      toast('Сначала сохраните изменения карточки',true);
      return;
    }
    if (kind !== 'ship') return;
    sending = true;
    try {
      try { await save(data); }
      catch (error) {
        if (kind !== 'ship' || !error.message.startsWith('Часть товара зарезервирована') ||
            !window.confirm(error.message + ' Отправить товар по этому контракту?')) throw error;
        await save({...data,allowOtherReserve:true});
      }
      const updated = state.records.find(item => item.id === record.id) || record;
      form.elements.namedItem('revision').value = updated.revision;
      if(['ship','cancel','return'].includes(kind)){
        select('status').value=updated.status;
        select('handoverDate').value=updated.handoverDate||'';
        select('cancelHandover').hidden=!updated.handoverDate;
        select('status').dispatchEvent(new Event('change',{bubbles:true}));
      }
      select('cost').value = updated.cost.replace('.', ',');
      renderCard(updated);
      select('cost').dispatchEvent(new Event('input', {bubbles:true}));
      state.formSnapshot = JSON.stringify([...new FormData(form)]);
      if(['ship','cancel','return'].includes(kind)) onContractChange?.();
      toast(labels[kind] + ' · сохранено');
    } catch (error) { toast(error.message,true); }
    finally { sending = false; }
  }

  for (const [button,kind] of [['stockProductDelete','deleteProduct'],['stockProductRestore','restoreProduct']]) {
    select(button).addEventListener('click', async () => {
      if (sending || !selectedStockProduct) return;
      const name = selectedStockProduct;
      if (!window.confirm(kind === 'deleteProduct' ? 'Удалить карточку товара? Она останется в корзине 5 суток, история операций сохранится.' : 'Восстановить карточку товара?')) return;
      sending = true;
      try {
        await save({kind,product:name});
        if(kind === 'deleteProduct'){selectedStockProduct = null;drawProductCard();}
        else {select('stockProductCharacteristics').value = state.stock.items.find(item => item.product === name)?.characteristics || ''; drawProductCard();}
        toast(kind === 'deleteProduct' ? 'Карточка товара удалена' : 'Карточка товара восстановлена');
      } catch(error) {toast(error.message,true);}
      finally {sending = false;}
    });
  }
  select('stockCreateProduct').addEventListener('click', () => {
    if (sending) return;
    select('stockCreateForm').reset();
    select('stockCreateMessage').textContent = '';
    select('stockCreateSimilar').replaceChildren();
    select('stockCreateForm').hidden = false;
    select('stockCreateName').focus();
  });
  select('stockCreateCancel').addEventListener('click', () => {if (!sending) select('stockCreateForm').hidden = true;});
  select('stockCreateName').addEventListener('input', () => {
    const matches = similarStockProducts(select('stockCreateName').value, (state.stock?.items || []).map(item => item.product));
    select('stockCreateSimilar').textContent = matches.length ? 'Проверьте похожие товары: ' + matches.map(displayProduct).join(', ') : '';
  });
  select('stockCreateForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (sending) return;
    const product = select('stockCreateName').value.trim();
    sending = true; select('stockCreateSave').disabled = true; select('stockCreateMessage').textContent = '';
    try {
      const characteristics = select('stockCreateCharacteristics').value;
      await save({kind:'createProduct', product, characteristics});
      select('stockCreateForm').hidden = true;
      selectedStockProduct = null;
      drawProductCard();
      toast('Карточка товара создана');
    } catch (error) {select('stockCreateMessage').textContent = error.message;}
    finally {sending = false; select('stockCreateSave').disabled = false;}
  });
  select('stockReceipt').addEventListener('click', () => showForm('receipt'));
  select('stockProductDetailsForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || !selectedStockProduct) return;
    sending = true;
    select('stockProductDetailsSave').disabled = true;
    select('stockProductDetailsMessage').textContent = '';
    try {
      await save({kind:'editProduct', product:selectedStockProduct, characteristics:select('stockProductCharacteristics').value});
      select('stockProductCharacteristics').value = state.stock.items.find(item => item.product === selectedStockProduct)?.characteristics || '';
      toast('Характеристики сохранены');
    } catch (error) {select('stockProductDetailsMessage').textContent = error.message;}
    finally {sending = false; select('stockProductDetailsSave').disabled = false;}
  });
  select('stockProductCardClose').addEventListener('click', () => {
    if (sending) return;
    selectedStockProduct = null;
    drawProductCard();
  });
  select('productAdjustmentQuantity').addEventListener('input', drawProductCard);
  select('productAdjustment').addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || !selectedStockProduct) return;
    const note = select('productAdjustmentNote').value.trim();
    const itemBefore=state.stock.items.find(entry=>entry.product===selectedStockProduct);
    const quantityText=select('productAdjustmentQuantity').value;
    if(!itemBefore || !/^\d{1,9}$/.test(quantityText)){select('productAdjustmentMessage').textContent='Укажите целый фактический остаток от 0 до 999 999 999';return;}
    const target=Number(quantityText);
    const price=target>0 ? select('productAdjustmentPrice').value.trim() : '';
    if(target===itemBefore.quantity && !price){select('productAdjustmentMessage').textContent='Укажите новую цену или измените количество';return;}
    let extra='';
    if(price){
      try{
        const value=moneyToKopecks(price);if(value<=0)throw new Error('Цена должна быть больше нуля');
        if(!Number.isSafeInteger(value*target) || value*target>900000000000000)throw new Error('Стоимость превышает допустимый предел');
        extra=' Цена всех единиц: '+price+' ₽/шт.; новая стоимость остатка '+formatMoney(value*target)+'.';
      }catch(error){select('productAdjustmentMessage').textContent=error.message;return;}
    }
    if(!window.confirm('Изменить фактический остаток «'+displayProduct(selectedStockProduct)+'» с '+number(itemBefore.quantity)+' до '+number(target)+' шт.? В резерве останется '+number(itemBefore.reserved)+' шт. Доступно будет '+number(target-itemBefore.reserved)+' шт.'+extra))return;
    sending = true;
    select('productAdjustmentSave').disabled = true;
    try {
      await save({kind:'adjust', product:selectedStockProduct,
        quantity:select('productAdjustmentQuantity').value,
        price, setUnitPrice:Boolean(price), expectedQuantity:itemBefore.quantity, note});
      const item = state.stock.items.find(entry => entry.product === selectedStockProduct);
      select('productAdjustment').reset();
      select('productAdjustmentQuantity').value = item.quantity;
      select('productAdjustmentMessage').textContent = '';
      drawProductCard();
      toast('Корректировка сохранена');
    } catch (error) {
      select('productAdjustmentMessage').textContent = error.message;
    } finally {
      sending = false;
      select('productAdjustmentSave').disabled = false;
    }
  });
  select('stockCloseForm').addEventListener('click', () => {select('stockForm').hidden = true;});
  select('stockProduct').addEventListener('change', () => {
    select('stockNewProductField').hidden = true;
    select('stockNewProduct').required = false;
    showSimilarProducts();
  });
  select('stockNewProduct').addEventListener('input', showSimilarProducts);
  for (const id of ['stockFrom','stockTo','stockFilterProduct']) select(id).addEventListener('change',renderHistory);
  for (const button of document.querySelectorAll('[data-stock-period]')) {
    button.addEventListener('click', () => chooseStockPeriod(button.dataset.stockPeriod));
  }
  for (const tab of document.querySelectorAll('[data-stock-tab]')) {
    tab.addEventListener('click', () => changeTab(tab.dataset.stockTab));
  }
  select('stockFrom').value = todayInMoscow().slice(0,7) + '-01';
  select('stockTo').value = todayInMoscow();
  chooseStockPeriod('month');
  select('stockForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (sending) return;
    sending = true;
    const button = select('stockSave');
    button.disabled = true;
    try {
      await save({kind:formKind, target:editTarget, expectedVersion:editVersion, product:select('stockProduct').value || select('stockNewProduct').value,
        date:select('stockDate').value, quantity:select('stockQuantity').value, price:select('stockPrice').value,
        party:select('stockParty').value, note:select('stockNote').value});
      select('stockForm').hidden = true;
      toast(labels[formKind] + ' · сохранено');
    } catch (error) {select('stockFormMessage').textContent = error.message;}
    finally {button.disabled = false; sending = false;}
  });
  for (const [button,kind] of [['stockShip','ship']]) {
    select(button).addEventListener('click', () => cardAction(kind));
  }
  function captureUI(){return {stockMode:stockPeriodMode,stockTab:activeTab,stockProduct:select('stockFilterProduct').value,stockFrom:select('stockFrom').value,stockTo:select('stockTo').value};}
  function restoreUI(saved){render();chooseStockPeriod(['month','year','custom'].includes(saved.stockMode)?saved.stockMode:'month');if(saved.stockMode==='custom'){select('stockFrom').value=saved.stockFrom||'';select('stockTo').value=saved.stockTo||'';}if([...select('stockFilterProduct').options].some(o=>o.value===saved.stockProduct))select('stockFilterProduct').value=saved.stockProduct;if(['stockBalances','stockHistory'].includes(saved.stockTab))changeTab(saved.stockTab);renderHistory();}
  return {render, renderCard, changeTab, captureUI, restoreUI, get sending(){return sending;}, get activeTab(){return activeTab;}};
}
