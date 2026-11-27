import { initStock } from './stock.mjs?v=20261115-stock-period-icons1';

import { initHome, paymentPreviewLabel } from './home.mjs?v=build027-ui2';

import {supplierActions,customerActions,currentAction,customerActionLabel,urgentCustomerActions,unpaidContracts} from './home-logic.mjs?v=build020-customer1';

import { renderAnnualChart, visibleFinancialMonths } from './finance-chart.mjs?v=20261121-finance-overhead1';

import { initProfile } from './profile.mjs?v=1';

import { initCompetitors } from './competitors.mjs?v=build026';

import { registryRecords, validContractDate, signingSide, signingQueue, unpaidQueue, unpaidAmountKopecks, activeNotifications, shiftMonth } from './header-logic.mjs?v=build021-registry1';

import { displayProduct, visibleStockItems, PRODUCTS, STATUSES, MONTHS, PLATFORMS, MAX_CONTRACT_ITEMS, MIN_ACCOUNT_YEAR, MAX_ACCOUNT_YEAR, moneyToKopecks, kopecksToDecimal, formatMoney, validateRecord, calculateSummary, calculateFinancialSummary, annualRateBp, summarizeDelivered, validateOtherExpenseDetails, recordMonthLabel, fieldToInput, amountFromUnitPrice, unitPriceFromAmount, contractItems, validateContractItems, contractDeadlines, summarizeContractProducts, todayInMoscow, clockAt, MOSCOW_ZONE, formatMoscowTimestamp, CLOCK_CITIES, findClockCity, validClockZone, newestContractsFirst, contractAttention, hasHandover, closesDelivery, canonicalStatus, planStatusChange, isActiveRecord } from './logic.mjs?v=build020-clock1';



const DEMO = new URLSearchParams(location.search).get('demo') === '1';

const el = id => document.getElementById(id);

const [todayYear,todayMonth]=todayInMoscow().split('-').map(Number);

const dateSupported=(todayYear>2026||(todayYear===2026&&todayMonth>=11))&&todayYear<=MAX_ACCOUNT_YEAR;

const startYear=Math.min(MAX_ACCOUNT_YEAR,Math.max(MIN_ACCOUNT_YEAR,todayYear));

const startMonth=startYear===2026?Math.max(todayMonth,11):todayMonth;

const startPeriod={year:startYear,month:startMonth,summaryYear:startYear};

const state={owner:'Контракты ИП',records:[],finance:{expenses:[],rates:{}},...startPeriod, cloudFolder:'',defaultCloudFolder:'',cloudRemote:'', cloudStatus:'',backupAt:'',backupWarning:'', dbPath:'', nonce:'',view:'contracts',contractSort:'newest',expandedMonths:new Set(),summaryShowAllMonths:false,waiting:false,quickSaving:false};

const statusColors = new Map([

  ['формирование', 'formation'], ['подписать до', 'sign-by'], ['на подписи', 'signing'],

  ['заключен', 'signed'], ['Отправлен', 'shipped'], ['Вручен', 'delivered'], ['Оплачен', 'paid'],

]);

const statusLabel = value => value === 'подписать до' ? 'Подписать' : value ? value[0].toUpperCase() + value.slice(1) : 'Статус не указан';

const statusOptions = STATUSES.map(value => ({value, label: statusLabel(value), color: statusColors.get(value)}));

let renderedDate = '';

const expenseFields = ['cost', 'taxAmount', 'fee', 'logistics', 'other'];

const itemFields = ['product', 'quantity', 'unitPrice', 'amount'];

const formatInputAmount = value => value ? formatMoney(moneyToKopecks(value)).slice(0, -2) : '';

const quantityFormat = new Intl.NumberFormat('ru-RU');

const formatQuantity = quantity => quantityFormat.format(quantity) + ' шт.';



function toast(msg,error=false) {

 const node=el('toast'); node.textContent=msg; node.classList.toggle('error',error);node.classList.add('visible');

 clearTimeout(toast.timer);toast.timer=setTimeout(()=>node.classList.remove('visible'),4100);

}

function options(select, names, value) {

  select.replaceChildren();

  for (const item of names) {

    const option = document.createElement('option');

    option.value = String(item.value ?? item);

    option.textContent = item.label ?? item;

    if (item.color) option.dataset.statusColor = item.color;

    select.append(option);

  }

  if (value !== undefined) select.value = String(value);

}

function availableMonths(year){return year===2026?[11,12]:Array.from({length:12},(_,i)=>i+1);}

const yearChoices=Array.from({length:MAX_ACCOUNT_YEAR-MIN_ACCOUNT_YEAR+1},(_,i)=>({value:MIN_ACCOUNT_YEAR+i,label:String(MIN_ACCOUNT_YEAR+i)}));

function ensureMonth(){const months=availableMonths(state.year);if(!months.includes(state.month))state.month=months[0];}

function updateHeader() {

  const titles = {home: 'Главная',stock: 'Склад', calculator: 'Калькулятор', proposal: 'Формирование КП', act: 'Акт', summary: 'Финансовые итоги', competitors: 'Конкуренты', profile: 'Карточка ИП', trash: 'Корзина', settings: 'Настройки'};

  const title = titles[state.view] ?? '';

  const registryName = String(state.owner || '').trim() || 'Контракты ИП';
  el('owner-side').removeAttribute('title');
  el('owner-side').querySelector('span').textContent = registryName;
  el('owner-side').querySelector('small').hidden = true;
  document.title = registryName;

  el('openProfile').removeAttribute('title');

  const heading = el('title');

  heading.textContent = title;

  heading.hidden = state.view === 'contracts' || state.view === 'home'; // Показываем контекст вместо пустой второй строки.

  renderHeaderMetrics();

}



let headerPanelKind = null;

function updateMoscowClock() {
  const {date,time}=clockAt(MOSCOW_ZONE);
  el('headerMoscowDate').textContent=date;
  el('headerMoscowTime').textContent=time+' МСК';
}

function initCityClock() {
  const dialog=el('cityClockDialog'),input=el('cityClockInput'),zones=el('cityClockZone'),result=el('cityClockResult');
  const search=el('cityClockSearch'),results=el('cityClockOnlineResults'),status=el('cityClockSearchStatus');
  for(const {city} of [...CLOCK_CITIES].sort((a,b)=>a.city.localeCompare(b.city,'ru'))){const option=document.createElement('option');option.value=city;el('cityClockOptions').append(option);}
  const choices=new Set([...CLOCK_CITIES.map(x=>x.zone),...(Intl.supportedValuesOf?.('timeZone')||[])]);
  for(const zone of [...choices].sort()){const option=document.createElement('option');option.value=zone;option.textContent=zone;zones.append(option);}
  let lastText='',places=[],selected=null,generation=0;
  const label=p=>[p.name,p.district,p.region,p.country].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i).join(', ');
  function reset(){generation++;selected=null;places=[];search.disabled=false;results.disabled=true;results.hidden=true;el('cityClockResultLabel').hidden=true;results.value='';status.textContent='';}
  function render(){
    const city=input.value.trim(),known=findClockCity(city),zone=selected?(selected.zone||zones.value):(known?.zone||zones.value);
    zones.disabled=Boolean(selected?validClockZone(selected.zone):known);
    const text=!city?'Введите название города.':!validClockZone(zone)?'Город не найден в справочнике или часовой пояс не определён. Найдите в интернете или выберите его часовой пояс вручную.':(()=>{const {date,time}=clockAt(zone);return `${selected?label(selected):(known?.city||city)}: ${time} · ${date} (${zone})`;})();
    if(text!==lastText){result.textContent=text;lastText=text;}
  }
  async function findOnline(){
    const query=input.value.trim();
    reset();const ticket=generation;
    if(query.length<2){status.textContent='Введите не менее двух символов.';return;}
    search.disabled=true;status.textContent='Поиск…';
    try{
      const data=await api('/api/city/search',{query});if(ticket!==generation)return;
      places=Array.isArray(data.places)?data.places:[];results.replaceChildren();
      const empty=document.createElement('option');empty.value='';empty.textContent='Выберите населённый пункт';results.append(empty);
      for(const place of places){const option=document.createElement('option');option.value=String(place.id);option.textContent=label(place);results.append(option);}
      results.value='';results.disabled=!places.length;results.hidden=places.length<2;el('cityClockResultLabel').hidden=places.length<2;status.textContent=places.length?`Найдено: ${places.length}. Выберите нужный пункт.`:'Ничего не найдено. Уточните название или страну.';
      if(places.length===1){results.value=String(places[0].id);await selectOnline();}
    }catch(error){if(ticket===generation)status.textContent=error.message||'Не удалось выполнить интернет-поиск.';}
    finally{if(ticket===generation)search.disabled=false;}
  }
  async function selectOnline(){
    const place=places.find(p=>String(p.id)===results.value);const ticket=++generation;selected=place?{...place,zone:''}:null;search.disabled=false;zones.value='';render();
    if(!place){status.textContent='Выберите населённый пункт.';return;}
    status.textContent='Определение часового пояса…';
    try{
      const zone=validClockZone(place.zone)?place.zone:(await api('/api/city/timezone',{lat:place.lat,lng:place.lng})).zone;
      if(ticket!==generation)return;
      if(!validClockZone(zone))throw new Error('Часовой пояс не поддерживается браузером. Выберите его вручную.');
      selected={...place,zone};status.textContent='Часовой пояс определён.';render();
    }catch(error){if(ticket===generation){status.textContent=error.message||'Не удалось определить часовой пояс.';render();}}
  }
  search.addEventListener('click',findOnline);results.addEventListener('change',selectOnline);
  input.addEventListener('input',()=>{reset();zones.value='';render();});input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();findOnline();}});zones.addEventListener('change',render);
  el('cityClockOpen').addEventListener('click',()=>{if(!dialog.open){dialog.showModal();render();input.focus();}});
  el('cityClockClose').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{generation++;search.disabled=false;});
  setInterval(()=>{if(dialog.open)render();},1000);
}

function updatePeriodNavigation() {

  el('periodLabel').textContent = MONTHS[state.month - 1] + ' ' + state.year;

  options(el('periodPickYear'), yearChoices, state.year);
  options(el('periodPickMonth'), availableMonths(state.year).map(month => ({label: MONTHS[month-1], value: month})), state.month);
  const [currentYear,currentMonth] = todayInMoscow().split('-').map(Number);
  el('periodToday').disabled = state.year === currentYear && state.month === currentMonth;

}

function headerActionRows() {

  if (headerPanelKind === 'signatures') return supplierActions(state.records,todayInMoscow()).map(item => ({

    record:item.record,label:(item.record.contractNumber?'№ '+item.record.contractNumber+' · ':'')+customerActionLabel(item)

  }));

  if (headerPanelKind === 'unpaid') return unpaidContracts(state.records).map(record => ({

    record, label: (record.contractNumber?'№ '+record.contractNumber+' · ':'')+paymentPreviewLabel(currentAction(record,todayInMoscow()))

  }));

  if (headerPanelKind === 'customer' || headerPanelKind === 'bell') {
    const actions = (headerPanelKind === 'customer' ? customerActions : urgentCustomerActions)(state.records,todayInMoscow());
    return actions.map(item => ({record:item.record,label:(headerPanelKind==='customer'&&item.record.contractNumber?'№ '+item.record.contractNumber+' · ':'')+customerActionLabel(item)}));
  }

  return [];

}

function renderHeaderActionPanel() {

  const panel = el('headerActionPanel');

  const bell = el('headerBell');

  bell.setAttribute('aria-expanded', String(headerPanelKind === 'bell'));
  for(const [id,kind] of [['homeSupplierKpi','signatures'],['homeCustomerKpi','customer'],['homeUnpaidKpi','unpaid']]) el(id).setAttribute('aria-expanded',String(headerPanelKind===kind));

  panel.replaceChildren();

  panel.hidden = !headerPanelKind;

  if (!headerPanelKind) return;

  const title = document.createElement('h3');

  title.textContent = ({signatures:'Требуются мои действия',unpaid:'Ожидаем оплату',bell:'Срочные сроки заказчика',customer:'Требуются действия от заказчика'})[headerPanelKind];

  const close = document.createElement('button');

  close.type = 'button'; close.className = 'header-panel-close'; close.textContent = '×';

  close.setAttribute('aria-label','Закрыть');

  close.addEventListener('click', () => { headerPanelKind = null; renderHeaderActionPanel(); });

  const bar = document.createElement('div'); bar.className = 'header-panel-title';

  bar.append(title,close); panel.append(bar);

  const items = headerActionRows();

  if (!items.length) {

    const empty = document.createElement('p'); empty.className = 'header-panel-empty';

    empty.textContent = 'Актуальных действий нет'; panel.append(empty);

  }

  for (const {record,label} of items) {

    const row = document.createElement('button');

    row.type = 'button'; row.className = 'header-action-row';

    const customer = document.createElement('strong');

    customer.textContent = record.customer || 'Без названия заказчика';

    const info = document.createElement('span'); info.textContent = label;

    row.append(customer,info);

    row.addEventListener('click', () => {

      headerPanelKind = null; renderHeaderActionPanel();

      switchView('contracts'); openForm(record);

    });

    panel.append(row);

  }

}

function renderHeaderMetrics() {

  el('headerSignCount').textContent = String(supplierActions(state.records,todayInMoscow()).length);

  el('headerUnpaidAmount').textContent = formatMoney(unpaidAmountKopecks(state.records));

  const count = urgentCustomerActions(state.records,todayInMoscow()).length;

  el('headerBellCount').textContent = String(count);

  el('headerBellCount').hidden = count === 0;

  // Показываем только просроченные и ближайшие 3 дня; обычное ожидание остаётся в KPI заказчика.

  el('headerBell').classList.toggle('has-alerts', count > 0);

  el('headerBell').setAttribute('aria-label',

    count > 0 ? 'Уведомления по срокам: ' + count : 'Уведомления по срокам: нет');

  renderHeaderActionPanel();

  if(state.view==='home' && typeof home!=='undefined')home.renderMetrics();

}

function toggleHeaderPanel(kind) {

  headerPanelKind = headerPanelKind === kind ? null : kind;

  renderHeaderActionPanel();

}



function switchView(view) {

  // Карточка ИП — полноценный раздел; навигация выделяет его как активный.

  state.view = view;
  if (view === 'home') el('openProfile').setAttribute('aria-current','page');
  else el('openProfile').removeAttribute('aria-current');

  document.body.classList.toggle('home-mode',view==='home');

  el('listPeriod').hidden = view !== 'contracts';

  // Create-contract shortcut is contextual: visible only in the Contracts section.

  el('top-new').hidden = view !== 'contracts';

  for (const nav of document.querySelectorAll('.nav-item')) {

    const active = nav.dataset.view === view;

    nav.classList.toggle('active', active);

    if (active) nav.setAttribute('aria-current', 'page');

    else nav.removeAttribute('aria-current');

  }

  // На узком экране выбранный раздел всегда виден в горизонтальном меню.

  // Не прокручиваем страницу и не трогаем открытые карточки/калькулятор.

  if (window.innerWidth <= 800) {

    const selected = document.querySelector('.sidebar .nav .nav-item.active');

    const strip = selected?.closest('.nav');

    if (selected && strip) {

      const bounds = selected.getBoundingClientRect();

      const navBounds = strip.getBoundingClientRect();

      strip.scrollLeft += bounds.left - navBounds.left - (strip.clientWidth - bounds.width) / 2;

    }

  }

  document.querySelectorAll('.view').forEach(section => section.classList.toggle('active', section.id === `view-${view}`));

  updateHeader();

  if (view === 'home')home.render();

  

  if (view === 'contracts') renderContracts();

  if (view === 'summary') renderSummary();

  if (view === 'stock') stock.render();

  if (view === 'competitors') competitors.open();

  if (view === 'settings') renderSettings();

  if (view === 'trash') refreshTrash();

}



function recordValid(record) {

  return record.check === 'OK';

}



function renderProductTotals(records) {

  const {total, items} = summarizeContractProducts(records);

  el('stat-quantity').textContent = formatQuantity(total);

  el('stat-products-empty').hidden = items.length > 0;

  const list = el('stat-products');

  list.replaceChildren();

  for (const {name, quantity} of items) {

    const line = document.createElement('div');

    const label = document.createElement('dt');

    label.textContent = name ? displayProduct(name) : 'Товар не указан';

    const value = document.createElement('dd');

    value.textContent = quantity === null ? '—' : formatQuantity(quantity);

    if (quantity === null) value.title = 'Количество не указано';

    line.append(label, value);

    list.append(line);

  }

}



function renderDeadlines(cell, deadlines, handoverDate) {

  for (const deadline of deadlines.filter(deadline => deadline.daysLeft !== null)) {

    const line = document.createElement('div');

    line.className = `deadline ${deadline.urgency}`;

    line.dataset.deadline = deadline.kind;

    const time = document.createElement('time');

    time.dateTime = deadline.date;

    time.textContent = deadline.date.split('-').reverse().join('.');

    const countdown = document.createElement('span');

    countdown.className = 'deadline-countdown';

    if (deadline.overdue) countdown.classList.add('deadline-warning');

    const {daysLeft} = deadline;

    countdown.textContent = daysLeft < 0 ? `Просрочено на ${-daysLeft} дн.` :

      daysLeft === 0 ? 'Сегодня' : `Осталось ${daysLeft} дн.`;

    line.append(deadline.label + ' ', time, countdown);

    cell.append(line);

  }

  if (handoverDate) {

    const time = document.createElement('time');

    time.className = 'handover-recorded';

    time.dateTime = handoverDate;

    time.textContent = 'Вручение ' + handoverDate.split('-').reverse().join('.');

    cell.append(time);

  }

  if (!cell.hasChildNodes()) cell.textContent = '—';

}



function customerButton(record) {

  const button = document.createElement('button');

  button.type = 'button';

  button.className = 'contract-customer';

  button.textContent = record.customer || 'Заказчик не указан';

  button.title = 'Открыть карточку контракта';

  button.addEventListener('click', () => openForm(record));

  return button;

}



function appendContractMeta(container, record, showPlatform = true) {

  if(record.check === 'ДОПОЛНИТЬ'){const note=document.createElement('div');note.className='contract-returned-note';note.textContent='Нужно дополнить';container.append(note);}

  const {items} = summarizeContractProducts([record]);

  const products = items.map(({name, quantity}) => [displayProduct(name), quantity && formatQuantity(quantity)].filter(Boolean).join(' — '));

  const meta = document.createElement('div');

  meta.className = 'contract-meta';

  meta.textContent = [showPlatform && record.platform, ...products].filter(Boolean).join(' · ');

  if (meta.textContent) container.append(meta);

  if (String(record.contractDate ?? '').trim() && !validContractDate(record.contractDate)) {
    const note=document.createElement('div');note.className='contract-incomplete';
    note.textContent='Некорректная дата контракта: '+String(record.contractDate);container.append(note);
  }

  if (state.stock?.returned?.[record.id]) {

    const returned = document.createElement('span');

    returned.className = 'contract-returned-note';

    returned.textContent = 'Товар возвращён';

    container.append(returned);

  }

  if (!recordValid(record)) {

    const note = document.createElement('div');

    note.className = 'contract-incomplete';

    note.textContent = 'Есть пропуски в обязательных полях';

    container.append(note);

  }

}



function recordAmount(record) {

  try {

    return record.amount ? moneyToKopecks(record.amount) : null;

  } catch {

    return null;

  }

}



function contractTotal(records) {

  return records.reduce((total, record) => total + (recordValid(record) ? (recordAmount(record) ?? 0) : 0), 0);

}



function amountLabel(record) {

  const amount = recordAmount(record);

  return amount === null || record.check === 'ДОПОЛНИТЬ' ? '—' : formatMoney(amount);

}



function statusBadge(status) {

  const badge = document.createElement('span');

  badge.className = 'status-badge';

  badge.dataset.statusColor = statusColors.get(status) ?? 'formation';

  badge.textContent = statusLabel(status);

  return badge;

}



function paintFormStatus() {

  const status = el('status');

  status.dataset.statusColor = statusColors.get(status.value) ?? 'formation';

}



function setQuickSaving(saving) {

  state.quickSaving = saving;

  document.querySelectorAll('[data-quick-change]').forEach(control => { control.disabled = saving; });

}



function focusContract(container, id, selector = '.contract-customer') {

  const record = [...container.querySelectorAll('[data-record-id]')].find(node => node.dataset.recordId === id);

  const control = record?.querySelector(selector);

  if (!control?.getClientRects().length) return false;

  control.focus({preventScroll: true});

  return true;

}



async function saveQuickChange(record, status, handoverDate, control, successMessage = 'Статус сохранён', returnStockConfirmed = false, shipConfirmed = false) {

  if (state.quickSaving || state.waiting) return;

  const wasFocused = document.activeElement === control;

  setQuickSaving(true);

  try {

    await persist({...record, status, handoverDate, returnStockConfirmed, shipConfirmed});

    if (state.view === 'summary') renderSummary();

    toast(state.folderWarning ? successMessage + ', но документы: ' + state.folderWarning :

      state.backupWarning ? successMessage + ', но ' + state.backupWarning :

      cloudHasProblem() ? successMessage + ' локально. Проблема резервного копирования' : successMessage,

    Boolean(state.folderWarning) || Boolean(state.backupWarning) || cloudHasProblem());

  } catch (error) {

    if (control.tagName === 'SELECT') control.value = record.status;

    toast(error.message, true);

  } finally {

    setQuickSaving(false);

    if (wasFocused && state.view === 'contracts' && el('modalBackdrop').hidden) {

      const container = el('contractList');

      focusContract(container, record.id, control.tagName === 'SELECT' ? '.status-select' : '.contract-customer');

    }

  }

}



function confirmStatusCorrection({clearsHandover, reversesPayment, returnsShipment, shipsStock = false, shippingSummary = ''}) {

  const effects = [];

  if (shipsStock) effects.push('Подтвердите ФАКТИЧЕСКУЮ отправку товара. Он будет списан со склада и резерв будет снят.' + shippingSummary);

  if (clearsHandover) effects.push('Дата вручения будет очищена.');

  if (reversesPayment) effects.push('Сумма контракта будет исключена из итогов оплаты. Документы останутся в папке «Исполненные контракты» и будут доступны через карточку.');

  if (returnsShipment) effects.push('Товар фактически вернулся на склад? После подтверждения он будет в резерве.');

  return effects.length === 0 || window.confirm(effects.join(' ') + ' Сохранить исправление статуса?');

}



function changeContractStatus(record, status, control, markHandover = false) {

  if (state.quickSaving || state.waiting) return;

  const plan = planStatusChange(record, status, todayInMoscow(), markHandover);

  const {handoverDate, needsSigningDate, needsDeliveryDeadline, needsHandoverDate, clearsHandover} = plan;

  if (needsSigningDate || needsDeliveryDeadline || needsHandoverDate) {

    if (control.tagName === 'SELECT') control.value = record.status;

    openForm(record);

    el('status').value = status;

    if (clearsHandover) {

      el('handoverDate').value = '';

      el('cancelHandover').hidden = true;

    }

    paintFormStatus();

    if (needsSigningDate) setFieldError('signBy', 'Проверьте срок подписания');

    if (needsDeliveryDeadline) setFieldError('fulfillBy', 'Проверьте срок поставки');

    if (needsHandoverDate) setFieldError('handoverDate', 'Проверьте фактическую дату вручения');

    el('formMessage').textContent = clearsHandover

      ? 'При сохранении отметка о вручении будет снята'

      : 'Проверьте выделенные даты и сохраните карточку';

    document.querySelector('#modalBackdrop [aria-invalid="true"]').focus();

    return;

  }

  const stockPhase=state.stock?.phases?.[record.id];

  const shipsStock=status === 'Отправлен' && stockPhase !== 'shipped';

  const returnsShipment=Boolean(stockPhase === 'shipped' &&

    !['Отправлен','Вручен','Оплачен'].includes(canonicalStatus(status)) &&

    canonicalStatus(record.status) !== canonicalStatus(status));

  if (returnsShipment) { if (control.tagName === 'SELECT') control.value = record.status; toast('Для возврата отгруженного товара используйте «Расторгнуть контракт»',true); return; }

  const shippingSummary=shipsStock ? ' Проверьте позиции и количество в карточке.' : '';

  if (!confirmStatusCorrection({...plan, returnsShipment, shipsStock, shippingSummary})) {

    if (control.tagName === 'SELECT') control.value = record.status;

    return;

  }

  return saveQuickChange(record, status, handoverDate, control,

    shipsStock ? 'Товар отправлен и списан со склада' : markHandover ? 'Вручение сохранено' : 'Статус сохранён',

    returnsShipment, shipsStock);

}



function handoverButton(record, urgent) {

  const button = document.createElement('button');

  button.type = 'button';

  button.className = 'handover-button';

  button.classList.toggle('deadline-urgent-control', urgent);

  button.dataset.quickChange = '';

  button.disabled = state.quickSaving;

  button.textContent = 'Вручен';

  button.setAttribute('aria-label', `Отметить вручение: ${record.customer || record.id}`);

  button.addEventListener('click', () => {

    changeContractStatus(record, 'Вручен', button, true);

  });

  return button;

}



function renderContracts() {

  const today = todayInMoscow();

  renderedDate = today;

  ensureMonth();

  el('yearSelect').value = state.year;

  options(el('monthSelect'), availableMonths(state.year).map(month => ({label: MONTHS[month-1], value: month})), state.month);

  updatePeriodNavigation();

  renderHeaderMetrics();

  const inMonth = registryRecords(state.records, state.year, state.month);

  el('stat-count').textContent = inMonth.length;

  el('stat-contract').textContent = formatMoney(contractTotal(inMonth));

  renderProductTotals(inMonth);

  el('contractSort').value=state.contractSort;
  renderContractList(inMonth, today);

}



function renderContractList(records, today) {

  const body = el('contractsBody');

  body.replaceChildren();

  if (!records.length) {

    const cell = body.insertRow().insertCell();

    cell.colSpan = 4;

    cell.className = 'empty-state';

    cell.textContent = 'В этом месяце нет контрактов.';

    return;

  }

  const sorted = newestContractsFirst(records);
  if(state.contractSort==='oldest')sorted.reverse();
  else if(state.contractSort==='customer')sorted.sort((a,b)=>String(a.customer||'').localeCompare(String(b.customer||''),'ru'));

  for (const record of sorted) {

    const row = body.insertRow();

    row.dataset.recordId = record.id;

    row.dataset.statusColor = statusColors.get(record.status) ?? 'formation';

    const attention=contractAttention(record,today);

    if(attention)row.dataset.attention=attention;



    const customerCell = row.insertCell();

    customerCell.append(customerButton(record));

    appendContractMeta(customerCell, record);

    const valueCell = row.insertCell();

    valueCell.className = 'money';

    valueCell.textContent = amountLabel(record);



    const status = document.createElement('select');

    status.className = 'status-select';

    status.dataset.statusColor = statusColors.get(record.status) ?? 'formation';

    status.dataset.quickChange = '';

    status.disabled = state.quickSaving;

    status.setAttribute('aria-label', `Статус контракта ${record.customer || record.id}`);

    options(status, statusOptions, record.status);

    const deadlines = contractDeadlines(record, today);

    const sign = deadlines.find(deadline => deadline.kind === 'sign');

    const delivery = deadlines.find(deadline => deadline.kind === 'fulfill');

    if (sign?.urgency === 'urgent') status.classList.add('deadline-urgent-control');

    row.insertCell().append(status);

    const deadlineCell = row.insertCell();

    renderDeadlines(deadlineCell, deadlines,

      closesDelivery(record.status) && hasHandover(record, today) ? record.handoverDate : '');

    if (state.stock?.phases?.[record.id] === 'shipped' &&

        canonicalStatus(record.status) === 'Отправлен') {

      deadlineCell.append(handoverButton(record, delivery?.urgency === 'urgent'));

    } else if (canonicalStatus(record.status) === 'заключен') {

      deadlineCell.append(quickStatusAction(record, 'Отправлен', 'Отправить товар', delivery?.urgency === 'urgent'));

    }

    status.addEventListener('change', () => changeContractStatus(record, status.value, status));

  }

}



function quickStatusAction(record, status, label, urgent = false) {

  const button = document.createElement('button');

  button.type = 'button';

  button.className = 'board-action';

  button.classList.toggle('deadline-urgent-control', urgent);

  button.dataset.quickChange = '';

  button.disabled = state.quickSaving;

  button.textContent = label;

  button.setAttribute('aria-label', `${label}: ${record.customer || record.id}`);

  button.addEventListener('click', () => changeContractStatus(record, status, button));

  return button;

}



function openContractLetter(record) {

  if (!record?.id) { toast('Сначала сохраните контракт',true); return; }

  const url = '/app/cover-letter.html?contract=' + encodeURIComponent(record.id);

  window.open(url, '_blank', 'noopener');

}



const BASE_FORM = {

  customer: '', platform: 'Сбер', product: 'Рутокен 1010', quantity: '', amount: '',

  contactPerson: '', phone: '', contractNumber: '', contractDate: '',

  cost: '', taxAmount: '', fee: '', logistics: '', other: '', otherDetails: [], status: 'формирование',

  signBy: '', fulfillBy: '', handoverDate: '', paymentDue: '', id: '', revision: 0,

};



function formSnapshot() {

  return JSON.stringify([Array.from(new FormData(el('contractForm'))), itemRows().map(row => row.dataset.priceBasis)]);

}



function updateCardPeriod() {

  el('cardPeriod').textContent = recordMonthLabel(el('contractYear').value, Number(el('contractMonth').value));

}



function updateExpensesTotal() {

  try {

    const total = expenseFields.reduce((sum, field) => sum + moneyToKopecks(el(field).value, {allowEmpty: true}), 0);

    el('expensesTotal').textContent = formatMoney(total);

  } catch {

    el('expensesTotal').textContent = 'Проверьте суммы';

  }

}



// A variable number of expense purposes. The "other" total is computed from

// detailed rows and stays in the old Excel numeric column for compatibility.

const otherRowElements=()=>Array.from(el('otherExpenseRows').children);

function readOtherExpenseRows() {

  return otherRowElements().map(row=>({

    description:row.querySelector('[name="otherDetail.description"]').value.trim(),

    amount:row.querySelector('[name="otherDetail.amount"]').value.trim(),

  })).filter(row=>row.description || row.amount);

}

function addOtherExpenseRow(detail={}) {

  const row=document.createElement('div');

  row.className='other-expense-row';

  row.innerHTML='<label class="expense-purpose-field">Назначение <input name="otherDetail.description" type="text" maxlength="200" placeholder="Например, упаковка"></label>'+

    '<label class="expense-money-field">Сумма, ₽ <input name="otherDetail.amount" inputmode="decimal" type="text" placeholder="0,00"></label>'+

    '<button type="button" class="remove-other-expense" aria-label="Удалить этот прочий расход" title="Удалить расход">×</button>';

  row.querySelector('[name="otherDetail.description"]').value=detail.description??'';

  row.querySelector('[name="otherDetail.amount"]').value=formatInputAmount(detail.amount);

  el('otherExpenseRows').append(row);

  return row;

}

function updateOtherExpenseTotal() {

  const result=validateOtherExpenseDetails(readOtherExpenseRows());

  el('other').value=result.error ? '' : kopecksToDecimal(result.total);

  el('otherExpenseError').textContent=result.error || '';

  updateExpensesTotal();

  return result;

}



const itemRows = () => Array.from(el('contractItems').children);

const itemInput = (row, field) => row.querySelector(`[name="item.${field}"]`);



// Last manually edited monetary field determines which value is authoritative.
// A blank stored unitPrice is the existing server format for an amount-based row.
function readItemRow(row) {
  const item = Object.fromEntries(itemFields.map(field => [field, itemInput(row, field).value.trim()]));
  if (row.dataset.priceBasis === 'amount') item.unitPrice = '';
  return item;
}

function updateItemAmount(row, changed = '') {
  const price = itemInput(row, 'unitPrice');
  const amount = itemInput(row, 'amount');
  const quantity = itemInput(row, 'quantity').value;
  const label = price.closest('.field').querySelector('label');
  if (changed === 'unitPrice') row.dataset.priceBasis = 'price';
  else if (changed === 'amount') row.dataset.priceBasis = 'amount';
  else if (!row.dataset.priceBasis) row.dataset.priceBasis = price.value.trim() ? 'price' : 'amount';

  amount.readOnly = false;
  let approximate = false;
  if (row.dataset.priceBasis === 'price') {
    try { amount.value = formatInputAmount(amountFromUnitPrice(quantity, price.value)); }
    catch { amount.value = ''; }
  } else {
    const exact = unitPriceFromAmount(quantity, amount.value);
    const average = exact || unitPriceFromAmount(quantity, amount.value, {round:true});
    price.value = formatInputAmount(average);
    approximate = Boolean(average && !exact);
  }
  label.textContent = approximate ? 'Цена за ед., ₽ (≈)' : 'Цена за ед., ₽';
  price.title = approximate
    ? 'Средняя цена округлена до копеек; введённая сумма позиции не изменяется'
    : 'При вводе цены пересчитывается сумма, при вводе суммы — цена';
}



function stockProducts() { return state.stock ? visibleStockItems(state.stock.items, state.stock.events).map(item=>item.product) : PRODUCTS; }



function updateItemTotals() {

  const rows = itemRows();

  const result = validateContractItems(rows.map(readItemRow), stockProducts());

  el('contractAmount').textContent = result.amount ? formatMoney(moneyToKopecks(result.amount)) : '—';

  el('contractItemsError').textContent = result.totalError;

  return result;

}



function updateItemControls() {

  const rows = itemRows();

  for (const [index, row] of rows.entries()) {

    const remove = row.querySelector('.remove-contract-item');

    remove.disabled = rows.length === 1;

    remove.setAttribute('aria-label', `Удалить позицию ${index + 1}`);

    for (const field of itemFields) {

      const input = itemInput(row, field);

      const error = row.querySelector(`[data-item-error="${field}"]`);

      input.id = index === 0 ? field : `${field}-${index + 1}`;

      error.id = `${input.id}Error`;

      input.closest('.field').querySelector('label').htmlFor = input.id;

      input.setAttribute('aria-describedby', error.id);

    }

  }

  el('addContractItem').disabled = rows.length >= MAX_CONTRACT_ITEMS;

}



function appendItemRow(item = {}) {

  const row = el('contractItemTemplate').content.firstElementChild.cloneNode(true);

  const visible = state.stock ? visibleStockItems(state.stock.items, state.stock.events).map(entry => entry.product) : stockProducts();

  if (item.product && !visible.includes(item.product)) visible.push(item.product);

  options(itemInput(row, 'product'), [{value: '', label: 'Выберите товар'}, ...visible.map(value => ({value, label: displayProduct(value)}))], item.product ?? '');

  itemInput(row, 'quantity').value = item.quantity ?? '';

  for (const field of ['unitPrice', 'amount']) itemInput(row, field).value = formatInputAmount(item[field]);

  updateItemAmount(row);

  el('contractItems').append(row);

  return row;

}



function showFieldError(input, message) {

  input.closest('.field').classList.toggle('has-error', Boolean(message));

  if (message) input.setAttribute('aria-invalid', 'true');

  else input.removeAttribute('aria-invalid');

  el(input.getAttribute('aria-describedby').split(' ')[0]).textContent = message;

}



function setFieldError(name, message) {

  showFieldError(el('contractForm').elements.namedItem(name), message);

}



function clearFieldErrors() {

  for (const node of document.querySelectorAll('#modalBackdrop [data-error]')) {

    setFieldError(node.dataset.error, '');

  }

}



function openForm(record = null) {

  state.returnFocus = document.activeElement;

  el('formTitle').textContent = record ? 'Редактирование контракта' : 'Новый контракт';

  el('terminateContract').hidden = !record || DEMO;

  el('contractFolderOpen').hidden = !record || DEMO;

  el('contractLetterOpen').hidden = !record || DEMO;

  const data = record ? fieldToInput(record) : {...BASE_FORM, year: state.year, month: state.month};

  const form = el('contractForm');

  form.reset();

  el('contractYear').value = data.year;

  options(el('contractMonth'), availableMonths(data.year).map(month => ({label: MONTHS[month-1], value: month})), data.month);

  for (const [key, value] of Object.entries(data)) {

    const input = form.elements.namedItem(key);

    if (input) input.value = value ?? '';

  }

  paintFormStatus();

  for (const key of expenseFields) el(key).value = formatInputAmount(el(key).value);

  el('otherExpenseRows').replaceChildren();

  const historicalUnspecified=Number(data.other)>0 && (!Array.isArray(data.otherDetails) || !data.otherDetails.length);

  const detailsToOpen=historicalUnspecified ? [{

    description:'Без расшифровки (старые данные)',amount:data.other

  }] : (Array.isArray(data.otherDetails) ? data.otherDetails : []);

  for (const detail of detailsToOpen) addOtherExpenseRow(detail);

  updateOtherExpenseTotal();

  el('contractItems').replaceChildren();

  for (const item of contractItems(data)) appendItemRow(item);

  updateItemControls();

  updateItemTotals();

  updateCardPeriod();

  updateExpensesTotal();

  el('handoverDate').max = todayInMoscow();

  el('cancelHandover').hidden = !el('handoverDate').value;

  el('formPeriod').open = false;

  clearFieldErrors();

  el('formMessage').textContent = '';

  stock.renderCard(record);

  state.formSnapshot = formSnapshot();

  el('modalBackdrop').hidden = false;

  el('customer').focus();

  document.body.style.overflow = 'hidden';

}



function hasUnsavedForm() {

  return !el('modalBackdrop').hidden && formSnapshot() !== state.formSnapshot;

}



function closeForm(force = false) {

  if (!force && state.waiting) return;

  if (!force && hasUnsavedForm() && !window.confirm('Введённые изменения не сохранены. Закрыть карточку?')) return;

  el('modalBackdrop').hidden = true;

  document.body.style.overflow = '';

  if (state.returnFocus?.isConnected) state.returnFocus.focus();

  else el('top-new').focus();

}



async function submitForm(event) {

  event.preventDefault();

  if (state.waiting || stock.sending) return;

  if (state.quickSaving) {

    el('formMessage').textContent = 'Дождитесь сохранения статуса';

    return;

  }

  const form = el('contractForm');

  const items = updateItemTotals();

  const raw = Object.fromEntries(Array.from(new FormData(form)).filter(([name]) =>

    !name.startsWith('item.') && !name.startsWith('otherDetail.')));

  raw.otherDetails=readOtherExpenseRows();

  raw.year = Number(raw.year);

  raw.month = Number(raw.month);

  raw.revision = Number(raw.revision);

  raw.customer = raw.customer.trim();

  raw.platform = raw.platform.trim();

  raw.contactPerson = raw.contactPerson.trim();

  raw.phone = raw.phone.trim();

  Object.assign(raw, {items: itemRows().map(readItemRow), product: items.product, quantity: items.quantity, amount: items.amount});

  const previous = state.records.find(record => record.id === raw.id);

  const correction = previous && planStatusChange(previous, raw.status, todayInMoscow());

  const clearExistingHandover = Boolean(correction?.clearsHandover && raw.handoverDate);

  if (clearExistingHandover) raw.handoverDate = '';

  const typedOther=el('other').value;
  let typedOtherError='';
  try {moneyToKopecks(typedOther,{allowEmpty:true});}
  catch(e){typedOtherError=e.message;}
  const expenseValidation=typedOtherError ? {error:typedOtherError,index:-1} : updateOtherExpenseTotal();
  raw.other=typedOtherError ? typedOther : el('other').value;

  const errors = validateRecord(raw);

  for (const field of ['product', 'quantity', 'amount']) delete errors[field];

  for (const field of ['platform','customer','signBy']) if (!String(raw[field] ?? '').trim()) delete errors[field];



  clearFieldErrors();

  let firstInvalidItem;

  for (const [index, row] of itemRows().entries()) {

    for (const field of itemFields) {

      const input = itemInput(row, field);

      const message = input.value.trim() ? (items.errors[index]?.[field] || '') : '';

      showFieldError(input, message);

      if (message) firstInvalidItem ??= input;

    }

  }

  const invalidFields = Object.keys(errors);

  const invalidExpenseRow=expenseValidation.error ? otherRowElements()[expenseValidation.index] : null;

  if (invalidFields.length || firstInvalidItem || items.totalError || expenseValidation.error) {

    if (errors.year || errors.month) el('formPeriod').open = true;

    for (const key of invalidFields) setFieldError(key, errors[key]);

    el('formMessage').textContent = 'Исправьте выделенные поля';

    const chosen=invalidExpenseRow?.querySelector(

      expenseValidation.error.includes('назначение') ?

      '[name="otherDetail.description"]' : '[name="otherDetail.amount"]');

    (chosen || (invalidFields[0]==='other' ? el('other') :

      form.elements.namedItem(invalidFields[0] ?? '')) || firstInvalidItem || el('addContractItem')).focus();

    return;

  }

  const stockPhase=previous && state.stock?.phases?.[previous.id];

  const shipsStock=raw.status === 'Отправлен' && stockPhase !== 'shipped';

  const returnsShipment=Boolean(previous && stockPhase === 'shipped' &&

    !['Отправлен','Вручен','Оплачен'].includes(canonicalStatus(raw.status)) &&

    canonicalStatus(previous.status) !== canonicalStatus(raw.status));

  if (returnsShipment) { toast('Для возврата отгруженного товара используйте «Расторгнуть контракт»',true); return; }

  if (!confirmStatusCorrection({

    ...(correction || {}), clearsHandover: clearExistingHandover || Boolean(previous?.handoverDate && !raw.handoverDate),

    returnsShipment, shipsStock,

    shippingSummary: shipsStock ? ' Проверьте позиции и количество в карточке.' : ''

  })) return;



  if (shipsStock) raw.shipConfirmed = true;



  for (const key of expenseFields) {

    if(key==='taxAmount' && !String(raw[key]??'').trim()){raw[key]='';continue;}

    raw[key]=kopecksToDecimal(moneyToKopecks(raw[key], {allowEmpty: true}));

  }

  for (const item of raw.items) for (const key of ['unitPrice', 'amount']) if (item[key]) item[key] = kopecksToDecimal(moneyToKopecks(item[key]));

  for (const item of raw.otherDetails) item.amount=kopecksToDecimal(moneyToKopecks(item.amount));

  if (!raw.id && !DEMO) {

    raw.id = crypto.randomUUID().replaceAll('-', '');

    form.elements.namedItem('id').value = raw.id;

  }

  state.waiting = true;

  el('saveContract').disabled = true;

  const fromSummary = state.view === 'summary';

  const sourceMonth = previous?.month;

  try {

    const saved = await persist(raw);

    closeForm(true);

    if (fromSummary) {

      renderSummary();

      if (!focusContract(el('summaryBody'), saved.id)) {

        document.querySelector(`[aria-controls="summary-details-${sourceMonth}"]`)?.focus({preventScroll: true});

      }

    } else {

      state.year = raw.year;

      state.month = raw.month;

      switchView('contracts');

    }

    toast(state.folderWarning ? 'Контракт сохранён, но операция с документами не завершена: ' + state.folderWarning :

      state.backupWarning ? 'Контракт сохранён, но ' + state.backupWarning :

      cloudHasProblem() ? 'Контракт сохранён локально; проблема резервного копирования' : 'Контракт сохранён',

    Boolean(state.folderWarning) || Boolean(state.backupWarning) || cloudHasProblem());

  } catch (error) {

    el('formMessage').textContent = error.message;

    toast(error.message, true);

  } finally {

    state.waiting = false;

    el('saveContract').disabled = false;

  }

}




const summaryMoney = value => value===null || value===undefined ? '—' : formatMoney(value);
let commonPeriod=null, commonEditing=null, commonDraftId=null, financeBusy=false;
const financePending=new Map();
const financeId=()=>crypto.randomUUID().replaceAll('-','');
function financialMonthEntries(){
  if(!commonPeriod)return [];
  return (state.finance?.expenses||[]).filter(x=>x.date.slice(0,7)===commonPeriod.prefix).sort((a,b)=>a.date.localeCompare(b.date)||a.id.localeCompare(b.id));
}
function resetCommonForm(){
  commonEditing=null;commonDraftId=financeId();
  const f=el('commonExpenseForm');f.reset();
  const date=el('commonExpenseDate'),today=todayInMoscow();
  date.min=commonPeriod.prefix+'-01';
  date.max=commonPeriod.prefix+'-'+String(new Date(Date.UTC(commonPeriod.year,commonPeriod.month,0)).getUTCDate()).padStart(2,'0');
  date.value=today.startsWith(commonPeriod.prefix)?today:date.min;
  el('commonExpenseSave').textContent='Добавить';
  el('commonExpenseCancelEdit').hidden=true;
  el('commonExpenseError').textContent='';
}
function renderCommonExpenses(){
  const entries=financialMonthEntries(),tbody=el('commonExpenseRows');tbody.replaceChildren();
  let sum=0;
  for(const item of entries){
    const row=tbody.insertRow();
    const date=row.insertCell();date.textContent=item.date.split('-').reverse().join('.');
    const desc=row.insertCell();desc.textContent=item.comment||'—';
    const amount=row.insertCell();amount.textContent=formatMoney(moneyToKopecks(item.amount));sum+=moneyToKopecks(item.amount);
    const actions=row.insertCell();actions.className='common-expense-actions';
    for(const [label,key,handler] of [['Изменить','expenseEdit',()=>editCommonExpense(item)],['Удалить','expenseDelete',()=>deleteCommonExpense(item)]]){
      const btn=document.createElement('button');btn.type='button';btn.className='btn btn-secondary';btn.textContent=label;btn.dataset[key]=item.id;btn.disabled=financeBusy;btn.addEventListener('click',handler);actions.append(btn);
    }
  }
  if(!entries.length){const row=tbody.insertRow();const cell=row.insertCell();cell.colSpan=4;cell.textContent='Расходов за месяц пока нет';}
  el('commonExpenseTotal').textContent=formatMoney(sum);
}
function openCommonExpenses(year,month){
  commonPeriod={year,month,prefix:year+'-'+String(month).padStart(2,'0')};
  el('commonExpenseTitle').textContent='Общие расходы · '+MONTHS[month-1]+' '+year;
  resetCommonForm();renderCommonExpenses();el('commonExpenseDialog').showModal();
}
function editCommonExpense(item){
  commonEditing=item;el('commonExpenseDate').value=item.date;el('commonExpenseAmount').value=item.amount;
  el('commonExpenseComment').value=item.comment||'';el('commonExpenseSave').textContent='Сохранить';
  el('commonExpenseCancelEdit').hidden=false;el('commonExpenseError').textContent='';el('commonExpenseAmount').focus();
}
function setFinanceBusy(busy){
  financeBusy=busy;
  for(const id of ['commonExpenseSave','commonExpenseClose','commonExpenseCancelEdit','commonExpenseAmount','commonExpenseDate','commonExpenseComment'])el(id).disabled=busy;
  el('summaryRate').disabled=busy||state.summaryYear===2026;
  if(el('commonExpenseDialog').open)renderCommonExpenses();
}
async function mutateFinance(operation){
  if(financeBusy)throw Error('Дождитесь сохранения');
  const key=JSON.stringify(operation),opId=financePending.get(key)||financeId();financePending.set(key,opId);
  setFinanceBusy(true);
  try{
    if(DEMO){
      const f=state.finance;
      if(operation.action==='rate')f.rates[operation.year]=operation.rate;
      else if(operation.action==='delete')f.expenses=f.expenses.filter(x=>x.id!==operation.id);
      else{
        const next={id:operation.id,revision:(operation.revision||0)+1,date:operation.date,amount:operation.amount,comment:operation.comment};
        f.expenses=f.expenses.filter(x=>x.id!==next.id);f.expenses.push(next);
      }
    }else{
      const result=await api('/api/finance',{...operation,opId});
      state.finance=result.finance;
      for(const k of ['backupAt','backupWarning','cloudStatus'])if(k in result)state[k]=result[k];
      updateCloudStatus();
    }
    financePending.delete(key);
  }finally{
    setFinanceBusy(false);renderSummary();
    if(state.view==='home')home.render();
  }
}
async function saveCommonExpense(event){
  event.preventDefault();if(financeBusy)return;
  const error=el('commonExpenseError');error.textContent='';
  try{
    const date=el('commonExpenseDate').value,raw=el('commonExpenseAmount').value.trim().replace(/\s/g,'').replace(',','.');
    if(!date.startsWith(commonPeriod.prefix)||date<el('commonExpenseDate').min||date>el('commonExpenseDate').max)throw Error('Выберите дату внутри месяца');
    if(!/^\d+(?:\.\d{1,2})?$/.test(raw))throw Error('Введите сумму с точностью до копеек');
    const amount=moneyToKopecks(raw);if(amount<=0)throw Error('Введите положительную сумму');
    const op={action:commonEditing?'edit':'create',id:commonEditing?.id||commonDraftId,date,amount:kopecksToDecimal(amount),comment:el('commonExpenseComment').value.trim()};
    if(commonEditing)op.revision=commonEditing.revision;
    await mutateFinance(op);resetCommonForm();renderCommonExpenses();toast('Расход сохранён');
  }catch(e){error.textContent=e.message;}
}
async function deleteCommonExpense(item){
  if(financeBusy||!window.confirm('Удалить расход '+formatMoney(moneyToKopecks(item.amount))+'?'))return;
  try{await mutateFinance({action:'delete',id:item.id,revision:item.revision});if(commonEditing?.id===item.id)resetCommonForm();renderCommonExpenses();toast('Расход удалён');}
  catch(e){el('commonExpenseError').textContent=e.message;}
}
async function changeSummaryRate(){
  const year=state.summaryYear,previous=annualRateBp(state.finance,year),value=Number(el('summaryRate').value);
  if(year===2026||![5,15].includes(value)||previous===value*100){renderSummary();return;}
  if(previous!==null&&!window.confirm('Изменить ставку УСН за '+year+' год с '+previous/100+'% на '+value+'%? Итоги года будут пересчитаны.')){renderSummary();return;}
  try{await mutateFinance({action:'rate',year,rate:value,previous:previous===null?null:previous/100});toast('Ставка сохранена');}
  catch(e){toast(e.message,true);renderSummary();}
}

function addSummaryValues(row, values, month=null) {

  for (const key of ['contract','cost','expected','operatingExpenses','common','profitAfter']) {

    const cell=row.insertCell();

    if(key==='common' && month!==null){
      const btn=document.createElement('button');
      btn.type='button';btn.className='common-expense-button';btn.dataset.commonMonth=month;
      btn.textContent=summaryMoney(values[key]);btn.title='Открыть общие расходы за месяц';
      btn.addEventListener('click',()=>openCommonExpenses(state.summaryYear,month));
      cell.append(btn);
    }else cell.textContent=summaryMoney(values[key]);

    cell.dataset.financial=key;

    if(key==='operatingExpenses')cell.title='Комиссии, логистика, прочие расходы и налог — введённый или расчётный';

    if(key==='profitAfter') {

      cell.className='profit-cell';

      cell.title='Плановая чистая прибыль, не фактическая оплата и не КУДиР';

      if(values[key]<0){

        const label=document.createElement('small');label.textContent='Убыток';

        cell.classList.add('loss-cell');cell.append(label);

      }else if(values[key]>0)cell.classList.add('gain-cell');

    }

  }

}



function renderMonthContracts(container, records) {

  const title = document.createElement('h3');

  title.textContent = 'Контракты месяца';

  container.append(title);

  if (!records.length) {

    const empty = document.createElement('p');

    empty.className = 'summary-contracts-empty';

    empty.textContent = 'В этом месяце нет контрактов.';

    container.append(empty);

    return;

  }

  const list = document.createElement('ul');

  for (const record of records) {

    const item = document.createElement('li');

    item.dataset.recordId = record.id;

    item.dataset.statusColor = statusColors.get(record.status) ?? 'formation';

    const info = document.createElement('div');

    info.append(customerButton(record));

    appendContractMeta(info, record, false);

    const amount = document.createElement('strong');

    amount.className = 'summary-contract-amount';

    amount.textContent = amountLabel(record);

    item.append(info, statusBadge(record.status), amount);

    list.append(item);

  }

  container.append(list);

}



function renderMonthOtherExpenses(container,records,retained=[]) {

  const rows=[];

  let monthTotal=0;

  for(const record of records) {

    if(record.check!=='OK')continue;

    const details=Array.isArray(record.otherDetails) && record.otherDetails.length ?

      record.otherDetails : (Number(record.other)>0 ? [{

        description:'Без расшифровки (старые данные)',amount:record.other

      }] : []);

    for(const detail of details) {

      try {

        const amount=moneyToKopecks(detail.amount);

        if(amount>0){

          rows.push({record,description:String(detail.description||'Без расшифровки'),amount});

          monthTotal+=amount;

        }

      }catch{}

    }

  }

  for(const record of retained){
    const details=record.otherDetails?.length?record.otherDetails:(Number(record.other)>0?[{description:'Без расшифровки',amount:record.other}]:[]);
    for(const detail of details){const amount=moneyToKopecks(detail.amount);if(amount>0){rows.push({record,description:detail.description,amount,terminated:true});monthTotal+=amount;}}
  }

  const block=document.createElement('section');

  block.className='summary-other-details';

  const heading=document.createElement('h4');

  heading.textContent='Прочие расходы: на что потрачено';

  block.append(heading);

  if(!rows.length){

    const empty=document.createElement('p');

    empty.textContent='Прочих расходов с расшифровкой за период нет.';

    block.append(empty);

  }else{

    const list=document.createElement('ul');

    for(const {record,description,amount,terminated} of rows){

      const item=document.createElement('li');

      const name=document.createElement('div');

      if(terminated)name.textContent=record.customer+' · Расторгнут';else name.append(customerButton(record));

      const purpose=document.createElement('span');

      purpose.textContent=description;

      const sum=document.createElement('strong');

      sum.textContent=formatMoney(amount);

      item.append(name,purpose,sum);

      list.append(item);

    }

    block.append(list);

    const total=document.createElement('div');

    total.className='summary-other-total';

    const caption=document.createElement('span');

    caption.textContent='Всего прочих расходов за месяц';

    const sum=document.createElement('strong');

    sum.textContent=formatMoney(monthTotal);

    total.append(caption,sum);

    block.append(total);

  }

  container.append(block);

}



function renderRetainedExpenses(container,retained){
  if(!retained.length)return;
  const section=document.createElement('section');section.className='summary-other-details';
  const heading=document.createElement('h4');heading.textContent='Расходы расторгнутых контрактов (сохранены в месяце)';section.append(heading);
  const list=document.createElement('ul');
  for(const r of retained){
    const item=document.createElement('li');const label=document.createElement('span');label.textContent=[r.customer,r.contractNumber,'Расторгнут'].filter(Boolean).join(' · ');
    const amounts=document.createElement('span');amounts.textContent='Комиссия: '+formatMoney(moneyToKopecks(r.fee))+' · Доставка: '+formatMoney(moneyToKopecks(r.logistics))+' · Прочие: '+formatMoney(moneyToKopecks(r.other));item.append(label,amounts);list.append(item);
  }
  section.append(list);container.append(section);
}

function renderSummary() {

  const year = state.summaryYear;

  el('summaryYear').value = year;

  const monthFilter=el('exportMonth'), selectedMonth=monthFilter.value || '0';

  options(monthFilter, [{value:0,label:'Весь год'},...availableMonths(year).map(month=>({value:month,label:MONTHS[month-1]}))], selectedMonth);

  if (!monthFilter.value) monthFilter.value='0';

  const {months, total} = calculateFinancialSummary(state.records, year, state.finance);
  const bp=annualRateBp(state.finance,year);
  el('summaryRate').value=bp===null?'':String(bp/100);
  el('summaryRate').disabled=year===2026||financeBusy;
  el('summaryRateNotice').hidden=bp!==null;

  const body = el('summaryBody');

  body.replaceChildren();

  const recordsByMonth = new Map(availableMonths(year).map(month => [month, []]));

  for (const record of state.records) {

    if (isActiveRecord(record) && +record.year === year) recordsByMonth.get(+record.month)?.push(record);

  }

  const available=availableMonths(year);

  const visible=visibleFinancialMonths(available, recordsByMonth, state.summaryShowAllMonths, months);

  const monthButton=el('summaryShowAllMonths');

  monthButton.setAttribute('aria-pressed', String(state.summaryShowAllMonths));

  monthButton.textContent=state.summaryShowAllMonths ? 'Скрыть пустые месяцы' :

    (available.length===12 ? 'Показать все 12 месяцев' : 'Показать все месяцы');

  if (!visible.length) {

    const empty=body.insertRow();

    empty.className='summary-empty-row';

    const cell=empty.insertCell();

    cell.colSpan=7;

    cell.textContent='В выбранном году нет контрактов или общих расходов';

  }

  for (const month of visible) {

    const key = `${year}-${month}`;

    const values = months[month - 1];

    const row = body.insertRow();

    row.className = 'summary-month';

    const heading = document.createElement('th');

    heading.scope = 'row';

    row.append(heading);

    const toggle = document.createElement('button');

    toggle.type = 'button';

    toggle.className = 'month-details-toggle';

    toggle.textContent = MONTHS[month - 1];

    heading.append(toggle);

    addSummaryValues(row, values, month);



    const details = body.insertRow();

    details.id = `summary-details-${month}`;

    details.className = 'summary-details';

    details.hidden = !state.expandedMonths.has(key);

    toggle.setAttribute('aria-expanded', String(!details.hidden));

    toggle.setAttribute('aria-controls', details.id);

    const cell = details.insertCell();

    cell.colSpan = 7;

    const grid = document.createElement('dl');

    grid.className = 'expense-breakdown';

    for (const [label, key] of [

      ['Операционные расходы всего', 'operatingExpenses'],

      ['В том числе комиссии', 'fee'], ['В том числе логистика', 'logistics'],

      ['В том числе прочие расходы', 'other'],

      ['Общие расходы', 'common'],
      ['Налог УСН', 'tax'],

      ['Контрактов в расчёте', 'count'],

    ]) {

      const item = document.createElement('div');

      const term = document.createElement('dt');

      term.textContent = label;

      const amount = document.createElement('dd');

      amount.textContent = key==='count' ? String(values[key]) :

        summaryMoney(values[key]);

      item.append(term, amount);

      grid.append(item);

    }

    const contracts = document.createElement('section');

    contracts.className = 'summary-month-contracts';

    const purposes=document.createElement('div');
    const retained=(state.finance?.retainedExpenses||[]).filter(r=>+r.year===year&&+r.month===month);
    const renderPurposes=()=>{renderMonthOtherExpenses(purposes,recordsByMonth.get(month),retained);renderRetainedExpenses(purposes,retained);};

    cell.append(grid,purposes,contracts);

    if (!details.hidden) {

      renderPurposes();

      renderMonthContracts(contracts,recordsByMonth.get(month));

    }

    toggle.addEventListener('click', () => {

      details.hidden = !details.hidden;

      if (details.hidden) state.expandedMonths.delete(key);

      else {

        state.expandedMonths.add(key);

        if(!purposes.hasChildNodes())renderPurposes();

        if(!contracts.hasChildNodes())renderMonthContracts(contracts,recordsByMonth.get(month));

      }

      toggle.setAttribute('aria-expanded', String(!details.hidden));

    });

  }



  const footer = el('summaryTotal');

  footer.replaceChildren();

  const yearRow = footer.insertRow();

  const heading = document.createElement('th');

  heading.scope = 'row';

  heading.textContent = `Итого за ${year} год`;

  yearRow.append(heading);

  addSummaryValues(yearRow, total);

  renderAnnualChart(el('annualChart'), months, year);

}



function setBackupSensor(id,kind,status,detail){
 const sensor=el(id);sensor.dataset.status=status;sensor.querySelector('.sensor-tooltip').textContent=kind+': '+detail;
}
function backupFooterModel(s,busy=false){
 const status=s.cloudStatus||'';
 const folderError=/ОШИБКА резервного копирования|КОНФЛИКТ/i.test(status);
 const remoteError=/ОШИБКА облачной проверки/i.test(status);
 const folderOK=Boolean(s.cloudFolder&&s.cloudPath&&s.cloudAt&&!folderError);
 const localOK=Boolean(s.backupAt&&s.backupPath);
 const warning=[s.backupWarning,s.cloudRetentionWarning].filter(Boolean).join(' · ');
 const localDetail=[busy?'Создаётся резервная копия':folderError?status:s.cloudFolder?(folderOK?'Архив в выбранной папке создан и проверен':'Архив в выбранной папке пока не создан'):(localOK?'Локальная копия SQLite создана':'Резервной копии пока нет'),s.cloudFolder?s.cloudAt:s.backupAt,s.cloudFolder?s.cloudPath:s.backupPath,warning,'Нажмите для создания копии'].filter(Boolean).join(' · ');
 const remoteDetail=[remoteError?status:s.cloudVerified?'Подтверждена на сервере':s.cloudPending?'Ожидает загрузки или проверки':!s.cloudRemote?'Загрузка облачным клиентом не проверяется приложением':'Не подтверждена',s.cloudVerifyAt,s.cloudServerPath,'Нажмите для настроек'].filter(Boolean).join(' · ');
 return {local:busy?'pending':folderError||s.backupWarning?'error':s.cloudRetentionWarning?'waiting':(s.cloudFolder?folderOK:localOK)?'ok':s.cloudFolder?'waiting':'unknown',localDetail,remote:remoteError?'error':s.cloudVerified?'ok':s.cloudPending?'pending':'unknown',remoteDetail};
}
function updateCloudStatus(){
 const status=state.cloudStatus||'';
 el('backupStatus').textContent=state.backupAt ? `Копия от ${state.backupAt}` : 'Резервной копии пока нет';
 const indicator=el('db-indicator'),label=el('db-status'),detail=el('cloud-status');
 const problem=/КОНФЛИКТ|ОШИБКА|недоступен/i.test(status);
 const model=backupFooterModel(state,el('saveApp').disabled);
 setBackupSensor('saveApp','Резервная копия',model.local,model.localDetail);
 setBackupSensor('uploadSensor','Загрузка',model.remote,model.remoteDetail);
 label.textContent=problem?'Ошибка синхронизации':'Локальная база доступна';detail.textContent=status;detail.title=status;
 indicator.classList.add('online');indicator.classList.remove('offline');indicator.closest('.health').classList.toggle('sync-error',problem);
}
function showBackupConnectionError(error){
 const indicator=el('db-indicator');indicator.classList.remove('online');indicator.classList.add('offline');indicator.closest('.health').classList.add('sync-error');el('db-status').textContent='Нет подключения к базе SQLite';el('cloud-status').textContent=error.message;setBackupSensor('saveApp','Резервная копия','error','Нет подключения к приложению · '+error.message);setBackupSensor('uploadSensor','Загрузка','unknown','Нет подключения к приложению');el('backupStatus').textContent='Время копии недоступно';
}

function cloudHasProblem(){return /КОНФЛИКТ|ОШИБКА|недоступен/i.test(state.cloudStatus||'');}

function renderBackupDetails(){
 el('settingsBackupAt').textContent=state.backupAt||'Пока нет';
 el('settingsBackupPath').textContent=state.backupPath||'—';
 el('settingsCloudAt').textContent=state.cloudAt||'Записи не было';
 el('settingsCloudPath').textContent=state.cloudPath||'—';
 el('settingsCloudVerifyAt').textContent=state.cloudVerifyAt||'—';
 el('settingsCloudLastVerifiedAt').textContent=state.cloudLastVerifiedAt||'—';
 el('settingsCloudServerPath').textContent=state.cloudLastVerifiedPath||'—';
 el('settingsBackupWarning').textContent=[state.backupWarning,state.cloudRetentionWarning].filter(Boolean).join(' · ');
 el('settingsCloudChecksum').textContent=state.cloudChecksum?`${state.cloudSize} байт · ${state.cloudChecksum}`:'—';
 el('settingsCloudVerified').textContent=state.cloudVerified?'Подтверждена (размер и содержимое)':state.cloudPending?'Ожидает подтверждения':state.cloudPath&&!state.cloudRemote?'Не проверяется: копия в папке':'Не подтверждена';
}
function renderLaunchSettings(){
 const cfg=state.launch||{};el('launchBrowser').value=cfg.browser||'system';el('launchBrowserPath').value=cfg.browserPath||'';el('launchBrowserPathField').hidden=el('launchBrowser').value==='system';
 el('launchPort').value=String(cfg.fixedPort||0);el('launchAutostart').checked=Boolean(cfg.autostart);el('launchAutoOpen').checked=cfg.autoOpen!==false;el('launchBackground').checked=Boolean(cfg.background);el('launchActivePort').textContent=state.port?'http://127.0.0.1:'+state.port+'/':'—';
}
function renderSettings(){
 renderLaunchSettings();
 el('installedVersion').textContent=state.version?String(state.version).padStart(3,'0'):'—';
 renderBackupDetails();

 el('settingOwner').value=state.owner;

 el('settingCloud').value=state.cloudFolder;

 el('settingCloudRemote').value=state.cloudRemote||'';

 el('checkCloudNow').disabled=!state.cloudRemote;

 el('sourcePath').textContent=state.dbPath||'contracts.sqlite3';

 el('settingsCloudStatus').textContent=state.cloudStatus||'Облако не настроено';

 el('settingsBackupAt').textContent=state.backupAt||'Пока нет';

 el('settingsBackupWarning').textContent=[state.backupWarning,state.cloudRetentionWarning].filter(Boolean).join(' · ');

 updateCloudStatus();

}

async function api(url,body=null){const init={cache:'no-store'};if(body!==null){init.method='POST';init.headers={'Content-Type':'application/json','X-Contracts-Key':state.nonce};init.body=JSON.stringify(body);}let response;try{response=await fetch(url,init);}catch{throw new Error('Нет связи с локальной программой. Проверьте её запуск.');}

 let data;try{data=await response.json();}catch{throw new Error('Ошибка ответа программы.');}

 if(!response.ok || data.error)throw new Error(data.error||`Ошибка сервера: ${response.status}`);
 for(const k of ['backupAt','backupPath','backupWarning','cloudStatus','cloudAt','cloudPath','cloudVerified','cloudVerifyAt','cloudLastVerifiedAt','cloudLastVerifiedPath','cloudServerPath','cloudChecksum','cloudSize','cloudPending','cloudCheckError','cloudRetentionWarning'])if(Object.hasOwn(data,k))state[k]=data[k];
 if(Object.hasOwn(data,'backupPath'))renderBackupDetails();
 return data;

}

async function refreshTrash() {

  const area=el('trashItems'), message=el('trashMessage');

  // Пока не пришёл свежий ответ, запрещаем очистку по старому снимку корзины.

  el('trashClear').disabled=true;

  state.trashEntries=[];

  if(DEMO){message.textContent='Корзина недоступна в демонстрационном режиме';return;}

  try {

    const result=await api('/api/trash/list',{});area.replaceChildren();

    state.trashEntries=result.items.map(({type,id,expiresAt})=>({type,id,expiresAt}));

    el('trashClear').disabled=state.trashEntries.length===0;

    message.textContent=result.items.length ? '' : 'Корзина пуста';

    for(const item of result.items){

      const row=document.createElement('div');row.className='trash-row';

      const info=document.createElement('div');const name=document.createElement('strong');name.textContent=(item.type==='product'?'Товар: ':item.termination?'Расторгнут: ':'Контракт: ')+item.name;

      const date=document.createElement('div');date.textContent='Восстановить до '+formatMoscowTimestamp(new Date(item.expiresAt))+' МСК';

      info.append(name,date);

      const button=document.createElement('button');button.type='button';button.className='btn btn-secondary';button.textContent='Восстановить';

      button.addEventListener('click',async()=>{

        if(state.waiting)return;
        if(item.termination && !window.confirm('Восстановить расторгнутый контракт в рабочий реестр? Возврат товара и денег не отменяется. При необходимости будет восстановлен резерв, но не отправка или оплата. Ранее учтённые расходы останутся в первоначальном месяце; в восстановленной карточке они не будут учтены повторно.'))return;
        state.waiting=true;button.disabled=true;

        try{

          const r=await api('/api/trash/restore',{type:item.type,id:item.id,confirmedTerminationRestore:!!item.termination});

          state.records=normalizeRecords(r.records);state.stock=r.stock;if(r.finance)state.finance=r.finance;state.backupAt=r.backupAt||state.backupAt;state.backupWarning=r.backupWarning||'';state.cloudStatus=r.cloudStatus||state.cloudStatus;updateCloudStatus();

          await refreshTrash();toast(item.type==='product'?'Товар восстановлен':'Контракт восстановлен');

        }catch(error){message.textContent=error.message;toast(error.message,true);button.disabled=false;}

        finally{state.waiting=false;}

      });

      row.append(info,button);area.append(row);

    }

  }catch(error){el('trashClear').disabled=true;message.textContent='Не удалось открыть корзину: '+error.message;}

}

function normalizeRecords(records) {

  return records.map(record => {

    const status = canonicalStatus(record.status);

    return status === record.status ? record : {...record, status};

  });

}

async function confirmReceivedFullPayment(record) {

  const dialog=el('paymentConfirmationDialog');

  const checkbox=el('paymentConfirmationCheck');

  const accept=el('paymentConfirmationAccept');

  const cancel=el('paymentConfirmationCancel');

  const name=String(record.customer||record.contractNumber||'Контракт').trim();

  const amount=String(record.amount??'').trim();

  const promptText='Фактически получена ПОЛНАЯ оплата по контракту «'+name+'»'+(amount?' на сумму '+amount+' ₽':'')+'? Документы будут перемещены в «Исполненные контракты».';

  if(!dialog?.showModal) return window.confirm(promptText);

  el('paymentConfirmationContract').textContent=name+(amount?' · '+amount+' ₽':'');

  checkbox.checked=false;

  accept.disabled=true;

  return new Promise(resolve=>{

    let finished=false;

    const finish=approved=>{

      if(finished)return;

      finished=true;

      dialog.onclose=null;

      dialog.oncancel=null;

      accept.onclick=null;

      cancel.onclick=null;

      checkbox.onchange=null;

      if(dialog.open)dialog.close();

      resolve(approved);

    };

    checkbox.onchange=()=>{accept.disabled=!checkbox.checked;};

    accept.onclick=()=>{if(checkbox.checked)finish(true);};

    cancel.onclick=()=>finish(false);

    dialog.onclose=()=>finish(false);

    dialog.oncancel=()=>finish(false);

    dialog.showModal();

    checkbox.focus();

  });

}

async function persist(raw){

  const previous=state.records.find(record=>record.id===raw.id);

  // Date the contract was concluded: fill only on transition and only when absent.
  if(canonicalStatus(raw.status)==='заключен' && canonicalStatus(previous?.status)!=='заключен' && !String(raw.contractDate??'').trim()){
    raw={...raw,contractDate:todayInMoscow()};
  }

  const becomingPaid=canonicalStatus(raw.status)==='Оплачен' && canonicalStatus(previous?.status)!=='Оплачен';

  if(becomingPaid){

    if(!await confirmReceivedFullPayment(raw)){

      throw new Error('Отметка оплаты отменена. Данные контракта не изменены.');

    }

    raw={...raw,paymentConfirmed:true};

  }

  if(DEMO){

    const candidate={...raw,id:raw.id||`demo-${Date.now()}`,revision:(+raw.revision||0)+1,check:'OK',created:raw.created||new Date().toISOString()};

    state.records=state.records.filter(r=>r.id!==candidate.id).concat(candidate);

    renderContracts();

    return candidate;

  }

  const res=await api('/api/record',{record:raw});

  state.folderWarning=res.folderError||'';

  state.records=normalizeRecords(res.records);

  state.stock=res.stock;

  state.backupAt=res.backupAt||state.backupAt;

  state.backupWarning=res.backupWarning||'';

  state.cloudStatus=res.cloudStatus||state.cloudStatus;

  updateCloudStatus();

  renderContracts();

  return res.record;

}


let uiReady=false,uiTimer=0,uiLast='',uiSeq=0;
const uiSession=globalThis.crypto?.randomUUID?.()||String(Date.now())+'-'+Math.random().toString(16).slice(2);
function captureUI(){
 return {view:state.view,year:state.year,month:state.month,summaryYear:state.summaryYear,contractView:'list',contractSort:state.contractSort,expandedMonths:[...state.expandedMonths].slice(-120),summaryShowAllMonths:state.summaryShowAllMonths,cardId:el('modalBackdrop').hidden?'':(el('contractForm').elements.namedItem('id').value||''),homeSearch:el('homeContractSearch').value.slice(0,200),...stock.captureUI()};
}
function saveUI(){
 if(!uiReady||DEMO||!state.nonce)return;const value=captureUI(),serialized=JSON.stringify(value);if(serialized===uiLast)return;uiLast=serialized;const seq=++uiSeq;
 fetch('/api/ui-state',{method:'POST',headers:{'Content-Type':'application/json','X-Contracts-Key':state.nonce},body:JSON.stringify({...value,session:uiSession,seq}),keepalive:true,cache:'no-store'}).then(r=>{if(!r.ok&&seq===uiSeq)uiLast='';}).catch(()=>{if(seq===uiSeq)uiLast='';});
}
function scheduleUI(){if(!uiReady||DEMO)return;clearTimeout(uiTimer);uiTimer=setTimeout(saveUI,100);}
function restoreUI(saved){
 if(!saved||typeof saved!=='object')saved={};
 for(const k of ['year','month','summaryYear','contractSort','summaryShowAllMonths'])if(Object.hasOwn(saved,k))state[k]=saved[k];ensureMonth();
 state.expandedMonths=new Set(saved.expandedMonths||[]);stock.restoreUI(saved);home.setSearch(saved.homeSearch||'');
 const view=document.getElementById('view-'+saved.view)?saved.view:'home';switchView(view);
 const card=state.records.find(r=>r.id===saved.cardId);if(card)openForm(card);
 uiReady=true;uiLast=JSON.stringify(captureUI());
}

async function init(){

 options(el('platform'),PLATFORMS);options(el('status'),statusOptions);

 const handoverDate = el('handoverDate');

 const status = el('status');

 const cancelHandover = el('cancelHandover');

 const updateHandover = () => {

   cancelHandover.hidden = !handoverDate.value;

   if (handoverDate.value && status.value === 'заключен') status.value = 'Вручен';

   if (!handoverDate.value && status.value === 'Вручен') status.value = 'заключен';

   paintFormStatus();

 };

 handoverDate.addEventListener('change', updateHandover);

 status.addEventListener('change', () => {

   if (status.value === 'заключен' && !el('contractDate').value) el('contractDate').value = todayInMoscow();

   if (status.value === 'Вручен' && !handoverDate.value) handoverDate.value = todayInMoscow();

   cancelHandover.hidden = !handoverDate.value;

   paintFormStatus();

 });

 cancelHandover.addEventListener('click', () => {

   handoverDate.value = '';

   updateHandover();

   handoverDate.focus();

 });

 setInterval(() => {

   if (state.view === 'contracts' && renderedDate !== todayInMoscow()) renderContracts();

 }, 30000);

 for(const id of ['yearSelect','summaryYear','contractYear']) options(el(id),yearChoices,state.year);

 options(el('contractMonth'),availableMonths(2026).map(m=>({label:MONTHS[m-1],value:m})),11);

 el('monthSelect').addEventListener('change',()=>{state.month=+el('monthSelect').value;renderContracts();});

 const closePeriodPicker = () => {
   el('periodPicker').hidden = true;
   el('periodPickerToggle').setAttribute('aria-expanded','false');
 };
 el('periodPickerToggle').addEventListener('click',()=>{
   const willOpen = el('periodPicker').hidden;
   el('periodPicker').hidden = !willOpen;
   el('periodPickerToggle').setAttribute('aria-expanded',String(willOpen));
 });
 el('periodPickYear').addEventListener('change',()=>{
   state.year = Number(el('periodPickYear').value);
   if(!availableMonths(state.year).includes(state.month)) state.month = availableMonths(state.year)[0];
   renderContracts();
 });
 el('periodPickMonth').addEventListener('change',()=>{
   state.month = Number(el('periodPickMonth').value);
   renderContracts();closePeriodPicker();
 });
 document.addEventListener('click',event=>{
   if(!el('periodPicker').hidden && !event.target.closest('.month-navigation')) closePeriodPicker();
 });
 document.addEventListener('keydown',event=>{if(event.key==='Escape') closePeriodPicker();});

 el('periodToday').addEventListener('click',()=>{

   const [year,month] = todayInMoscow().split('-').map(Number);

   if (year < MIN_ACCOUNT_YEAR || year > MAX_ACCOUNT_YEAR || (year === 2026 && month < 11)) {

     toast('Сегодняшний месяц вне диапазона учёта',true); return;

   }

   state.year=year;state.month=month;renderContracts();

 });

 el('headerSignatures').addEventListener('click',()=>toggleHeaderPanel('signatures'));

 el('headerUnpaid').addEventListener('click',()=>toggleHeaderPanel('unpaid'));

 el('headerBell').addEventListener('click',()=>toggleHeaderPanel('bell'));

 updateMoscowClock();
 initCityClock();

 let metricsDay=todayInMoscow();

 setInterval(()=>{

   updateMoscowClock();

   const currentDay=todayInMoscow();

   if(currentDay!==metricsDay){metricsDay=currentDay;renderHeaderMetrics();}

 },1000);

 el('yearSelect').addEventListener('change',()=>{state.year=+el('yearSelect').value;ensureMonth();renderContracts();});

 el('summaryYear').addEventListener('change',()=>{state.summaryYear=+el('summaryYear').value;renderSummary();});
 el('summaryRate').addEventListener('change',changeSummaryRate);
 el('commonExpenseForm').addEventListener('submit',saveCommonExpense);
 el('commonExpenseClose').addEventListener('click',()=>{if(!financeBusy)el('commonExpenseDialog').close();});
 el('commonExpenseCancelEdit').addEventListener('click',resetCommonForm);
 el('commonExpenseDialog').addEventListener('cancel',event=>{if(financeBusy)event.preventDefault();});

 el('summaryShowAllMonths').addEventListener('click',()=>{

   state.summaryShowAllMonths=!state.summaryShowAllMonths;

   renderSummary();

 });

 el('exportExecuted').addEventListener('click',async()=>{

   if(DEMO){toast('Выгрузка недоступна в демонстрационном режиме');return;}

   const button=el('exportExecuted'),year=state.summaryYear,month=Number(el('exportMonth').value);

   button.disabled=true;

   try{

     const response=await fetch('/api/export-executed',{method:'POST',headers:{'Content-Type':'application/json','X-Contracts-Key':state.nonce},body:JSON.stringify({year,month}),cache:'no-store'});

     if(!response.ok){let data;try{data=await response.json()}catch{}throw new Error(data?.error||'Не удалось сформировать файл');}

     const blob=await response.blob();if(!blob.size)throw new Error('Файл выгрузки пуст');

     const url=URL.createObjectURL(blob),link=document.createElement('a');

     link.href=url;link.download='Исполненные_контракты_'+year+(month?'_'+String(month).padStart(2,'0'):'')+'.xlsx';

     document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);

     toast('Файл Excel подготовлен. Проверьте загрузки браузера.');

   }catch(error){toast(error.message,true)}finally{button.disabled=false;}

 });



 el('contractSort').addEventListener('change',()=>{state.contractSort=el('contractSort').value;renderContracts();});
 document.querySelectorAll('.nav-item').forEach(n=>n.addEventListener('click',()=>switchView(n.dataset.view)));

   el('openProfile').addEventListener('click',()=>switchView('home'));

 initProfile(toast);

 el('uploadSensor').addEventListener('click',()=>{switchView('settings');el('settingCloud').scrollIntoView({block:'nearest'});});

 el('saveApp').addEventListener('click',async()=>{

  const btn=el('saveApp');

  if(state.waiting || btn.disabled)return;

  if(DEMO){toast('Демонстрация: запись в рабочую базу не выполняется');return;}

  btn.disabled=true;btn.setAttribute('aria-busy','true');setBackupSensor('saveApp','Резервная копия','pending','Создаётся резервная копия');

  try{

   // Records are committed at every edit. This explicitly creates and verifies a recovery snapshot.

   const result=await api('/api/backup',{});

   state.backupAt=result.backupAt||state.backupAt;

   state.cloudStatus=result.cloudStatus||state.cloudStatus;

   state.backupWarning=result.backupWarning||'';

   updateCloudStatus();

   if(state.view==='settings')renderSettings();

   toast(cloudHasProblem()?'Локальная копия создана; ошибка дополнительной копии':'Резервная копия создана и проверена.',cloudHasProblem());

  }catch(e){state.backupWarning='Не удалось создать резервную копию: '+e.message;toast(state.backupWarning,true);}

  finally{btn.disabled=false;btn.removeAttribute('aria-busy');updateCloudStatus();}

 });

 el('trashClear').addEventListener('click',async()=>{

   if(state.waiting || !state.trashEntries?.length)return;

   const entries=state.trashEntries.map(item=>({...item}));

   if(!window.confirm('Очистить корзину? Удалённые карточки нельзя будет восстановить через корзину. Складские движения, резервные копии и папки документов сохранятся. Папки можно удалить только вручную.'))return;

   state.waiting=true;el('trashClear').disabled=true;

   try{

     const r=await api('/api/trash/clear',{confirmed:true,entries});

     state.records=normalizeRecords(r.records);state.stock=r.stock;if(r.finance)state.finance=r.finance;state.backupAt=r.backupAt||state.backupAt;state.backupWarning=r.backupWarning||'';state.cloudStatus=r.cloudStatus||state.cloudStatus;updateCloudStatus();

     await refreshTrash();toast('Корзина очищена');

   }catch(error){await refreshTrash();toast(error.message,true);}

   finally{state.waiting=false;}

 });

 el('top-new').addEventListener('click',()=>openForm());el('contractForm').addEventListener('submit',submitForm);

 el('contractLetterOpen').addEventListener('click',()=>{

   const contractId = el('contractForm').elements.namedItem('id').value;

   const record = state.records.find(item => item.id === contractId);

   if (!record) { toast('Сначала сохраните контракт',true); return; }

   if (hasUnsavedForm() && !window.confirm('В карточке есть несохранённые изменения. Для письма будут использованы последние сохранённые данные. Продолжить?')) return;

   openContractLetter(record);

 });

 el('contractFolderOpen').addEventListener('click',async()=>{

   if(state.waiting || DEMO) return;

   const id=el('contractForm').elements.namedItem('id').value;

   if(!id){toast('Сначала сохраните карточку контракта',true);return;}

   const button=el('contractFolderOpen');

   button.disabled=true;

   try {

     const status=await api('/api/contract-folder',{action:'status',id});

     if(!status.exists){

       const question=status.known ?

         'Связанная папка документов отсутствует. Восстановить её по сохранённому пути?' :

         'Создать отдельную папку документов для этого контракта?';

       if(!window.confirm(question))return;

       await api('/api/contract-folder',{action:'ensure',id});

     }

     await api('/api/contract-folder',{action:'open',id});

     toast('Открыта папка документов');

   }catch(error){

     el('formMessage').textContent='Документы: '+error.message;

     toast('Не удалось открыть папку: '+error.message,true);

   }finally{button.disabled=false;}

 });

 el('closeModal').addEventListener('click',()=>closeForm());el('cancelForm').addEventListener('click',()=>closeForm());

 el('terminateContract').addEventListener('click', async () => {
   if (state.waiting || stock.sending || state.quickSaving || DEMO) return;
   const form=el('contractForm');
   const id=form.elements.namedItem('id').value,revision=Number(form.elements.namedItem('revision').value);
   const record=state.records.find(item=>item.id===id);
   if(!record)return;
   if(hasUnsavedForm()){toast('Сначала сохраните или отмените изменения карточки',true);return;}
   const shipped=state.stock?.phases?.[id]==='shipped';
   if(shipped && !window.confirm('Весь товар по контракту фактически вернулся на склад и пригоден для реализации? При частичном возврате или товаре в пути расторжение пока не оформляйте.'))return;
   if(!window.confirm(record.status==='Оплачен'?'Подтвердите, что ВСЯ полученная оплата фактически возвращена заказчику.':'Подтвердите, что оплаты по контракту не было либо все полученные деньги уже полностью возвращены заказчику.'))return;
   if(!window.confirm('Расторгнуть контракт '+(record.contractNumber||record.customer)+'?\n'+(shipped?'Товар будет возвращён на склад по себестоимости отправки.':'Повторного прихода товара не будет.')+' Резерв снимается. Сумма контракта исключается из финансовых итогов. Комиссии, доставка и прочие расходы остаются в исходном месяце. Статус: Расторгнут. Карточка переносится в корзину на 5 суток. Папка и документы сохраняются.'))return;
   state.waiting=true;el('terminateContract').disabled=true;
   try{
     const result=await api('/api/delete-record',{id,revision,mode:'terminate',confirmed:true,settlementConfirmed:true,returnConfirmed:shipped});
     state.records=normalizeRecords(result.records);state.stock=result.stock;state.finance=result.finance;
     Object.assign(state,{backupAt:result.backupAt,backupWarning:result.backupWarning,cloudStatus:result.cloudStatus});updateCloudStatus();
     closeForm(true);switchView(state.view);toast('Контракт расторгнут. Склад и финансовые итоги пересчитаны');
   }catch(error){el('formMessage').textContent=error.message;toast(error.message,true);}
   finally{state.waiting=false;el('terminateContract').disabled=false;}
 });
 el('modalBackdrop').addEventListener('click',ev=>{if(ev.target.id==='modalBackdrop')closeForm();});

 window.addEventListener('beforeunload',ev=>{if(hasUnsavedForm()){ev.preventDefault();ev.returnValue='';}});

 window.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&!el('modalBackdrop').hidden)closeForm();if(ev.ctrlKey&&ev.key==='Enter'&&!el('modalBackdrop').hidden){ev.preventDefault();el('contractForm').requestSubmit();}});

 el('contractYear').addEventListener('change', () => {

   const months = availableMonths(Number(el('contractYear').value));

   options(el('contractMonth'), months.map(month => ({label: MONTHS[month-1], value: month})), months[0]);

   updateCardPeriod();

 });

 el('contractMonth').addEventListener('change', updateCardPeriod);

 el('addContractItem').addEventListener('click', () => {

   const row = appendItemRow();

   updateItemControls();

   updateItemTotals();

   itemInput(row, 'product').focus();

 });

 el('contractItems').addEventListener('click', event => {

   if (!event.target.matches('.remove-contract-item') || event.target.disabled) return;

   const row = event.target.closest('.contract-item');

   const focusRow = row.nextElementSibling || row.previousElementSibling;

   row.remove();

   updateItemControls();

   updateItemTotals();

   itemInput(focusRow, 'product').focus();

 });

 el('contractItems').addEventListener('input', event => {

   const row = event.target.closest('.contract-item');

   updateItemAmount(row, event.target.name?.split('.')[1]);

   showFieldError(event.target, '');

   showFieldError(itemInput(row, 'amount'), '');

   updateItemTotals();

 });

 for (const key of expenseFields) if(key!=='other') el(key).addEventListener('input', updateExpensesTotal);
 el('other').addEventListener('input',()=>{
   const value=el('other').value.trim();
   try {
     const amount=moneyToKopecks(value,{allowEmpty:true});
     el('otherExpenseRows').replaceChildren();
     if(amount>0)addOtherExpenseRow({description:'Прочие расходы',amount:kopecksToDecimal(amount)});
     el('otherExpenseError').textContent='';
     setFieldError('other','');
   } catch(e) {
     el('otherExpenseError').textContent=e.message;
   }
   updateExpensesTotal();
 });

 el('addOtherExpense').addEventListener('click',()=>{

   const row=addOtherExpenseRow();

   updateOtherExpenseTotal();

   row.querySelector('[name="otherDetail.description"]').focus();

 });

 el('otherExpenseRows').addEventListener('input', updateOtherExpenseTotal);

 el('otherExpenseRows').addEventListener('click',event=>{

   const button=event.target.closest('.remove-other-expense');

   if(!button) return;

   const row=button.closest('.other-expense-row');

   const next=row.nextElementSibling || row.previousElementSibling;

   row.remove();

   updateOtherExpenseTotal();

   (next?.querySelector('[name="otherDetail.description"]')||el('addOtherExpense')).focus();

 });

 async function loadBackupChoices(){

  const select=el('restoreBackup'), current=select.value;

  if (DEMO) {

    select.replaceChildren(new Option('Недоступно в демонстрационном режиме', ''));

    el('settingsBackupAt').textContent = 'Недоступно в демонстрации';

    return;

  }

  try {const r=await api('/api/backups',{});el('settingsBackupAt').textContent=r.backups.length?r.backups[0].created:'Пока нет';select.replaceChildren(new Option('Выберите копию','')); for(const b of r.backups){const opt=new Option(b.label,b.name);opt.dataset.hash=b.hash;select.add(opt);} if([...select.options].some(o=>o.value===current))select.value=current;}

  catch(e){toast('Не удалось получить список копий: '+e.message,true);}

 }

 async function openLocalFolder(kind){

  if(DEMO){toast('Демо: открытие папок доступно только в рабочем режиме');return;}

  try{await api('/api/open-folder',{kind});}catch(e){toast(e.message,true);}

 }

 el('openBackupFolder').addEventListener('click',()=>openLocalFolder('backup'));

 el('openDatabaseFolder').addEventListener('click',()=>openLocalFolder('database'));
 el('openPackageFolder').addEventListener('click',()=>openLocalFolder('package'));
 let availableUpdate=null;
 el('launchBrowser').addEventListener('change',()=>{const browser=el('launchBrowser').value;el('launchBrowserPathField').hidden=browser==='system';el('launchBrowserPath').value=browser===state.launch?.browser?state.launch.browserPath:(state.browsers?.[browser]||'');});
 el('chooseBrowser').addEventListener('click',async()=>{if(DEMO)return;try{const r=await api('/api/choose-browser',{});if(r.path)el('launchBrowserPath').value=r.path;}catch(e){el('launchStatus').textContent=e.message;}});
 el('launchSettingsForm').addEventListener('submit',async event=>{event.preventDefault();if(DEMO)return;const btn=event.currentTarget.querySelector('button[type=submit]');btn.disabled=true;try{const r=await api('/api/launch/settings',{browser:el('launchBrowser').value,browserPath:el('launchBrowserPath').value.trim(),fixedPort:Number(el('launchPort').value),autostart:el('launchAutostart').checked,autoOpen:el('launchAutoOpen').checked,background:el('launchBackground').checked});Object.assign(state,r);renderLaunchSettings();el('launchStatus').textContent='Настройки сохранены. Браузер и порт применяются при следующем запуске.';}catch(e){el('launchStatus').textContent=e.message;}finally{btn.disabled=false;}});
 el('checkUpdate').addEventListener('click',async()=>{
  if(DEMO){toast('Демо: обновление недоступно');return;}
  const check=el('checkUpdate'),install=el('installUpdate'),status=el('updateStatus');check.disabled=true;install.hidden=true;availableUpdate=null;
  try{status.textContent='Проверка GitHub…';const r=await api('/api/update/check',{});el('installedVersion').textContent=String(r.version).padStart(3,'0');
   status.textContent=(r.available?(r.compatible?'Доступна сборка '+r.latest+' — '+r.notes:'Требуется полная совместимая сборка '+r.latest):'Установлена актуальная версия.')+(r.last?.message?' Последняя установка: '+r.last.message:'');
   if(r.available&&r.compatible){availableUpdate=r.latest;install.hidden=false;}
  }catch(e){status.textContent='Проверка не выполнена: '+e.message;}finally{check.disabled=false;}
 });
 el('installUpdate').addEventListener('click',async()=>{
  if(DEMO||availableUpdate===null)return;
  if(hasUnsavedForm()){toast('Сначала сохраните изменения карточки',true);return;}
  if(!window.confirm('Сохраните все изменения и закройте другие вкладки приложения. Установить проверенное обновление и перезапустить приложение?'))return;
  const check=el('checkUpdate'),install=el('installUpdate'),status=el('updateStatus');check.disabled=true;install.disabled=true;
  try{status.textContent='Загрузка и проверка пакета…';await api('/api/update/install',{version:availableUpdate,confirmed:true});status.textContent='Обновление подготовлено. Приложение перезапускается. Закройте эту старую вкладку.';}
  catch(e){status.textContent=e.message;check.disabled=false;install.disabled=false;}
 });

 el('transferPackage').addEventListener('click',async()=>{
  if(DEMO){toast('Демо: перенос доступен только в рабочем режиме');return;}
  const button=el('transferPackage'),status=el('transferStatus');button.disabled=true;
  try{const selected=await api('/api/choose-folder',{});if(!selected.folder)return;
   if(!window.confirm('Создать полный комплект приложения, текущей базы и документов в выбранной папке? Исходные файлы останутся на месте.'))return;
   status.textContent='Копирование и проверка файлов… Не изменяйте документы до завершения.';
   const result=await api('/api/transfer-package',{destination:selected.folder,confirmed:true});
   status.textContent='Комплект создан: '+result.path+' · файлы программы: '+result.applicationFiles+', документы: '+result.documentFiles;
   toast('Полный комплект создан и проверен');
  }catch(e){status.textContent=e.message;toast(e.message,true);}finally{button.disabled=false;}
 });
 el('stopForTransfer').addEventListener('click',async()=>{
  if(DEMO)return;
  if(!window.confirm('Сохраните открытые изменения. Завершить сервер для ручного переноса общей папки?'))return;
  try{await api('/api/shutdown',{confirmed:true});el('transferStatus').textContent='Остановка запрошена. Закройте вкладку. Перед перемещением дождитесь завершения процесса сервера.';toast('Остановка сервера запрошена');}catch(e){toast(e.message,true);}
 });


 el('chooseCloudFolder').addEventListener('click',async()=>{if(DEMO){toast('Демо: выбор папки доступен только в рабочем режиме');return;}try{const r=await api('/api/choose-folder',{});if(r.folder){el('settingCloud').value=r.folder;toast('Папка выбрана. Нажмите «Сохранить настройки»');}}catch(e){toast(e.message,true);}});

 el('settingsBackupNow').addEventListener('click',async()=>{if(DEMO){toast('Демо: резервные копии доступны только в рабочем режиме');return;}try{const r=await api('/api/backup',{});state.backupAt=r.backupAt;state.backupWarning=r.backupWarning;state.cloudStatus=r.cloudStatus;updateCloudStatus();el('settingsCloudStatus').textContent=state.cloudStatus;el('settingsBackupAt').textContent=state.backupAt||'Пока нет';el('settingsBackupWarning').textContent=[state.backupWarning,state.cloudRetentionWarning].filter(Boolean).join(' · ');await loadBackupChoices();toast(cloudHasProblem()?'Локальная копия создана; '+state.cloudStatus:'Резервная копия создана и проверена',cloudHasProblem());}catch(e){toast(e.message,true);}});

 el('restoreBackupButton').addEventListener('click',async()=>{if(DEMO){toast('Демо: восстановление копий недоступно');return;}const select=el('restoreBackup'),opt=select.selectedOptions[0];if(!opt||!opt.value){toast('Выберите резервную копию',true);return;}if(!window.confirm('Восстановить контракты и склад из копии «'+opt.textContent+'»? Текущая база будет сохранена в отдельную резервную копию. Данные после даты копии исчезнут из рабочей базы.'))return;try{await api('/api/restore-backup',{name:opt.value,hash:opt.dataset.hash});window.alert('Копия восстановлена. Страница будет обновлена.');window.location.reload();}catch(e){toast(e.message,true);}});

 document.querySelector('[data-view=settings]').addEventListener('click',loadBackupChoices);

 el('restoreFullZip').addEventListener('click',async()=>{
  if(DEMO){toast('Демо: восстановление недоступно');return;}
  const button=el('restoreFullZip');button.disabled=true;
  try{const selected=await api('/api/choose-backup-zip',{});if(!selected.path)return;
   if(!window.confirm('Восстановить ВСЕ данные из доверенного ZIP?\n'+selected.path+'\nТекущие данные и документы будут заменены, а перед этим сохранены в отдельной страховочной копии. Восстановление также заменит персональные шаблоны и настройки запуска.'))return;
   const r=await api('/api/restore-full-backup',{path:selected.path,confirmed:true});
   window.alert('Восстановлено контрактов: '+r.records+'\nСтраховочная копия: '+r.safetyPath+'\nПерезапустите приложение и проверьте настройки запуска и резервирования.');window.location.reload();
  }catch(e){toast(e.message,true);}finally{button.disabled=false;}
 });
 el('restoreBackup').addEventListener('focus',loadBackupChoices);


 el('syncNow').addEventListener('click',async()=>{

  if(DEMO){toast('Демо: синхронизация не запускается');return;}

  try{

   const owner=el('settingOwner').value.trim(),cloudFolder=el('settingCloud').value.trim(),cloudRemote=el('settingCloudRemote').value.trim();
   const changed=owner!==state.owner||cloudFolder!==state.cloudFolder||cloudRemote!==state.cloudRemote;
   const r=changed?await api('/api/settings',{owner,cloudFolder,cloudRemote}):await api('/api/sync',{});
   if(changed){state.owner=r.owner;state.cloudFolder=r.cloudFolder;state.cloudRemote=r.cloudRemote;updateHeader();}
   state.cloudStatus=r.cloudStatus;state.backupAt=r.backupAt||state.backupAt;renderSettings();

   toast(state.cloudStatus||'Состояние синхронизации недоступно',cloudHasProblem());

  }catch(e){state.cloudStatus='ОШИБКА синхронизации: '+e.message;updateCloudStatus();el('settingsCloudStatus').textContent=state.cloudStatus;toast(e.message,true);}

 });

 el('checkCloudNow').addEventListener('click',async()=>{
  if(DEMO)return;
  try{await api('/api/cloud/check',{});el('settingsCloudStatus').textContent=state.cloudStatus;updateCloudStatus();toast('Запущена проверка облачной копии');}catch(e){toast(e.message,true);}
 });
 let cloudPolling=false;
 if(!DEMO)setInterval(async()=>{if(cloudPolling||!state.nonce)return;cloudPolling=true;try{await api('/api/cloud/status',{});el('settingsCloudStatus').textContent=state.cloudStatus;updateCloudStatus();}catch(e){showBackupConnectionError(e);}finally{cloudPolling=false;}},5000);

 el('settingsForm').addEventListener('submit',async ev=>{ev.preventDefault();const owner=el('settingOwner').value.trim(),cloudFolder=el('settingCloud').value.trim(),cloudRemote=el('settingCloudRemote').value.trim();

 if(DEMO){state.owner=owner;state.cloudFolder=cloudFolder;state.cloudRemote=cloudRemote;toast('Демо: настройки изменены только на экране');updateHeader();return;}

 try{const r=await api('/api/settings',{owner,cloudFolder,cloudRemote});state.owner=r.owner;state.cloudFolder=r.cloudFolder;state.cloudRemote=r.cloudRemote;state.cloudStatus=r.cloudStatus;updateHeader();renderSettings();toast(cloudHasProblem()?state.cloudStatus:(state.cloudPath?'Настройки сохранены. '+state.cloudStatus:'Настройки сохранены'),cloudHasProblem());}catch(e){toast(e.message,true);}});

 try {

  document.body.classList.add('demo-adaptive-home');if(DEMO){const demo=await fetch('/app/demo.json').then(r=>r.json());Object.assign(state,{...demo,...startPeriod,view:'home'});document.body.insertAdjacentHTML('beforeend','<div class="demo-banner">ДЕМОНСТРАЦИОННЫЙ РЕЖИМ — данные вымышлены, запись в рабочую базу не выполняется</div>');}

  else {const boot=await api('/api/bootstrap');Object.assign(state,{...boot,...startPeriod,view:'home'});}

  state.records=normalizeRecords(state.records);

  home.setNotes(state.notes||{revision:0,notes:[]});

  if(!DEMO) await api('/api/trash/list',{});

  el('db-indicator').classList.add('online');el('db-status').textContent=DEMO?'Демонстрация':'Локальная база доступна';updateCloudStatus();if(DEMO)el('cloud-status').textContent='Без подключения к базе';

 }catch(e){showBackupConnectionError(e);toast(e.message,true);}

 if(!DEMO&&state.nonce){restoreUI(state.uiState);for(const event of ['click','change','input'])document.addEventListener(event,scheduleUI);window.addEventListener('pagehide',saveUI);window.addEventListener('beforeunload',e=>{if(hasUnsavedForm()){e.preventDefault();e.returnValue='';}});setInterval(saveUI,3000);}else switchView('home');

 if(!DEMO) setInterval(() => { if (!state.waiting && !state.quickSaving && !stock.sending) api('/api/trash/list',{}).catch(() => {}); }, 600000);

 if(!dateSupported) toast('Текущий месяц вне диапазона учёта: ноябрь 2026 — декабрь 2100.',true);

}

const calculatorFrame = el('calculatorFrame');

let calculatorResizeObserver;

calculatorFrame.addEventListener('load', () => {

  calculatorResizeObserver?.disconnect();

  const app = calculatorFrame.contentDocument?.querySelector('.app');

  if (!app) return;

  const resize = () => { calculatorFrame.style.height = Math.ceil(app.getBoundingClientRect().height + 20) + 'px'; };

  calculatorResizeObserver = new ResizeObserver(resize);

  calculatorResizeObserver.observe(app);

  resize();

});

// The embedded КП keeps its own document and browser storage. Adjust only the

// iframe height to its actual content so there are no two vertical scrollbars.

const proposalFrame = el('proposalFrame');

let proposalResizeObserver;

function resizeProposalFrame() {

  const doc = proposalFrame.contentDocument;

  if (!doc?.body) return;

  const target = Math.max(980, Math.ceil(doc.body.scrollHeight + 36));

  if (Math.abs(proposalFrame.getBoundingClientRect().height - target) > 3) {

    proposalFrame.style.height = target + 'px';

  }

}

proposalFrame.addEventListener('load', () => {

  proposalResizeObserver?.disconnect();

  const doc = proposalFrame.contentDocument;

  if (!doc?.body) return;

  proposalResizeObserver = new ResizeObserver(resizeProposalFrame);

  proposalResizeObserver.observe(doc.body);

  doc.addEventListener('input', resizeProposalFrame, {passive: true});

  doc.addEventListener('change', resizeProposalFrame, {passive: true});

  resizeProposalFrame();

});

window.addEventListener('resize', resizeProposalFrame);

const stock = initStock({state,api,toast,openForm,demo:DEMO,updateCloudStatus,onContractChange:()=>{if(state.view==='summary')renderSummary();else if(state.view==='contracts')renderContracts();}});

const competitors=initCompetitors({api,toast,today:todayInMoscow,demo:DEMO});

const home=initHome({state,api,toast,openForm,switchView,demo:DEMO,renderSummary,headerPanel:toggleHeaderPanel,stock});

init();

