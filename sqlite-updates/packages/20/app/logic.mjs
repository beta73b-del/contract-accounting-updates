export const PRODUCTS = ["Рутокен 1010", "Рутокен 3.0 серт ФСТЭК", "Рутокен 3.0 серт ФСБ"];

const HIDDEN_LEGACY_PRODUCTS = new Set(['Рутокен 3.0 серт ФСТЭК', 'Рутокен 3.0 серт ФСБ']);
export function displayProduct(name) {
  return name === 'Рутокен 1010' ? 'Рутокен Lite 1010' : name;
}
export function visibleStockItems(items, events, includeDeleted = false) {
  const referenced = new Set(events.flatMap(event => [...(event.lines || []), ...(event.needs || [])].map(line => line.product)));
  return items.filter(item => (includeDeleted || !item.deleted) && !(item.product === 'Рутокен 3.0 серт ФСБ' && !item.quantity && !item.reserved) && (!HIDDEN_LEGACY_PRODUCTS.has(item.product) || item.quantity || item.reserved || referenced.has(item.product)));
}
export const STATUSES = ["формирование", "подписать до", "на подписи", "заключен", "Отправлен", "Вручен", "Оплачен"];
export const BOARD_COLUMNS = [
  {id: 'formation', status: 'формирование', label: 'На формировании'},
  {id: 'sign-by', status: 'подписать до', label: 'Подписать'},
  {id: 'signing', status: 'на подписи', label: 'На подписи у заказчика'},
  {id: 'signed', status: 'заключен', label: 'Заключен'},
  {id: 'shipped', status: 'Отправлен', label: 'Отправлен'},
  {id: 'delivered', status: 'Вручен', label: 'Ожидает оплаты'},
  {id: 'paid', status: 'Оплачен', label: 'Оплачен'},
];
export const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
export const PLATFORMS = ["Сбер", "Березка", "РТС", "РТС малая", "Росэлторг", "ТЭК-Торг", "Фабрикант", "Портал поставщиков", "ГПБ", "РАД", "Магазин СПБ", "Другая"];

const MAX_MONEY_KOPECKS = 9e13;
export const MAX_CONTRACT_ITEMS = 50;
export const MIN_ACCOUNT_YEAR = 2026;
export const MAX_ACCOUNT_YEAR = 2100;
const quantityError = 'Количество: целое число от 1 до 1 000 000 (до 7 цифр)';
const validQuantity = value => /^\d{1,7}$/.test(String(value ?? '')) && Number(value) >= 1 && Number(value) <= 1000000;

export function moneyToKopecks(value, {allowEmpty = false} = {}) {
  if ((value === null || value === undefined || String(value).trim() === '') && allowEmpty) return 0;
  const s = String(value ?? '').replace(/[\s\u00A0\u202F₽]/g,'').replace(',', '.');
  if (!/^\d+(?:\.\d{1,2})?$/.test(s)) throw new Error('Укажите неотрицательную сумму, максимум 2 знака после запятой');
  const [rub, kop = ''] = s.split('.');
  const v = Number(rub) * 100 + Number(kop.padEnd(2,'0'));
  if (!Number.isSafeInteger(v) || v > MAX_MONEY_KOPECKS) throw new Error('Сумма превышает допустимый предел');
  return v;
}

export const kopecksToDecimal = kopecks => {
  if (!Number.isSafeInteger(kopecks)) throw new Error('Неправильная денежная сумма');
  return `${kopecks < 0 ? '-' : ''}${Math.trunc(Math.abs(kopecks) / 100)}.${String(Math.abs(kopecks) % 100).padStart(2,'0')}`;
};

export function amountFromUnitPrice(quantity, unitPrice) {
  if (!validQuantity(quantity)) throw new Error(quantityError);
  const price = moneyToKopecks(unitPrice);
  if (price === 0) throw new Error('Цена за единицу должна быть больше нуля');
  const total = Number(quantity) * price;
  if (total > MAX_MONEY_KOPECKS) throw new Error('Сумма контракта превышает допустимый предел');
  return kopecksToDecimal(total);
}

export function unitPriceFromAmount(quantity, amount, {round = false} = {}) {
  if (!validQuantity(quantity)) return '';
  try {
    const total = moneyToKopecks(amount), count = Number(quantity);
    if (total <= 0) return '';
    if (total % count === 0) return kopecksToDecimal(total / count);
    // Rounded average is presentation only; the entered total remains authoritative.
    return round ? kopecksToDecimal(Math.floor((total + Math.floor(count / 2)) / count)) : '';
  } catch {
    return '';
  }
}

export const isActiveRecord = record => Boolean(record) &&
  record.deleted !== true &&
  String(record.check ?? '').trim().toLocaleLowerCase('ru-RU') !== 'удалён' &&
  String(record.status ?? '').trim().toLocaleLowerCase('ru-RU') !== 'удалён';

export function contractItems(record) {
  if (record.items?.length) return record.items.filter(item => item.deleted !== true && item.check !== 'Удалён');
  return [{
    product: record.product ?? '',
    quantity: record.quantity ?? '',
    amount: record.amount ?? '',
    unitPrice: unitPriceFromAmount(record.quantity, record.amount),
  }];
}

export function validateContractItems(rows, products = PRODUCTS) {
  const result = {items: [], errors: [], totalError: '', product: '', quantity: '', amount: ''};
  if (rows.length < 1 || rows.length > MAX_CONTRACT_ITEMS) {
    result.totalError = `Укажите от 1 до ${MAX_CONTRACT_ITEMS} позиций`;
    return result;
  }
  let totalQuantity = 0;
  let totalKopecks = 0;
  for (const {product, quantity, unitPrice, amount} of rows) {
    const errors = {};
    if (!products.includes(product)) errors.product = 'Выберите товар из справочника';
    if (!validQuantity(quantity)) errors.quantity = quantityError;
    const priced = String(unitPrice ?? '').trim() !== '';
    const moneyField = priced ? 'unitPrice' : 'amount';
    let kopecks;
    try {
      kopecks = moneyToKopecks(priced ? unitPrice : amount);
      if (kopecks === 0) throw new Error(priced ? 'Цена должна быть больше нуля' : 'Сумма должна быть больше нуля');
    } catch (error) {
      errors[moneyField] = error.message;
    }
    const lineTotal = priced ? Number(quantity) * kopecks : kopecks;
    if (lineTotal > MAX_MONEY_KOPECKS) errors.unitPrice = 'Сумма позиции превышает допустимый предел';
    result.errors.push(errors);
    if (Object.keys(errors).length) continue;
    totalQuantity += Number(quantity);
    totalKopecks += lineTotal;
    result.items.push({product, quantity: String(Number(quantity)), unitPrice: priced ? kopecksToDecimal(kopecks) : '', amount: kopecksToDecimal(lineTotal)});
  }
  if (result.items.length !== rows.length) return result;
  if (totalQuantity > 1000000) result.totalError = 'Общее количество не должно превышать 1 000 000 шт.';
  else if (totalKopecks > MAX_MONEY_KOPECKS) result.totalError = 'Сумма контракта превышает допустимый предел';
  else Object.assign(result, {product: rows[0].product, quantity: String(totalQuantity), amount: kopecksToDecimal(totalKopecks)});
  return result;
}

export function summarizeContractProducts(records) {
  let total = 0;
  const products = new Map();
  for (const record of records) {
    if (!isActiveRecord(record)) continue;
    for (const item of contractItems(record)) {
      const name = String(item.product ?? '').trim();
      const quantity = validQuantity(item.quantity) ? Number(item.quantity) : null;
      if (name === '' && quantity === null) continue;
      const previous = products.get(name) ?? null;
      products.set(name, quantity === null ? previous : (previous ?? 0) + quantity);
      total += quantity ?? 0;
    }
  }
  return {total, items: Array.from(products, ([name, quantity]) => ({name, quantity}))};
}

export function formatMoney(kop, digits = 2) {
  return new Intl.NumberFormat('ru-RU', {minimumFractionDigits:digits,maximumFractionDigits:digits}).format(kop / 100) + ' ₽';
}

export function validateDate(str, mandatory=false) {
  if (!str) return mandatory ? 'Укажите дату' : null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str);
  if (!m) return 'Дата должна быть в формате ГГГГ-ММ-ДД';
  const y=+m[1], mo=+m[2], d=+m[3];
  const dt = new Date(Date.UTC(y,mo-1,d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth()+1 !== mo || dt.getUTCDate() !== d || y<2020 || y>2100) return 'Недопустимая дата';
  return null;
}

const moscowDate = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'});
export const todayInMoscow = (instant = new Date()) => moscowDate.format(instant);

// Business calendar in Moscow; instants remain ISO UTC in storage.
export const MOSCOW_ZONE = 'Europe/Moscow';
export function clockAt(zone, instant = new Date()) {
  const date = new Intl.DateTimeFormat('ru-RU', {timeZone:zone,weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(instant);
  const time = new Intl.DateTimeFormat('ru-RU', {timeZone:zone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(instant);
  return {date,time};
}
export function formatMoscowTimestamp(instant) {
  return new Intl.DateTimeFormat('ru-RU',{timeZone:MOSCOW_ZONE,dateStyle:'short',timeStyle:'short',hourCycle:'h23'}).format(instant);
}
const cityZones = [
 ['Europe/Kaliningrad','Калининград|Советск (Калининградская область)|Балтийск'],
 ['Europe/Moscow','Москва|Санкт-Петербург|Петербург|Токсово|Всеволожск|Выборг|Гатчина|Псков|Великий Новгород|Мурманск|Петрозаводск|Архангельск|Северодвинск|Нарьян-Мар|Вологда|Череповец|Сыктывкар|Ухта|Воркута|Ярославль|Кострома|Иваново|Владимир|Суздаль|Рязань|Тула|Калуга|Обнинск|Брянск|Смоленск|Тверь|Орёл|Курск|Белгород|Воронеж|Липецк|Тамбов|Пенза|Саранск|Нижний Новгород|Дзержинск|Арзамас|Чебоксары|Йошкар-Ола|Казань|Набережные Челны|Альметьевск|Нижнекамск|Киров|Ростов-на-Дону|Таганрог|Шахты|Новочеркасск|Краснодар|Сочи|Новороссийск|Анапа|Геленджик|Майкоп|Ставрополь|Пятигорск|Кисловодск|Ессентуки|Минеральные Воды|Черкесск|Нальчик|Владикавказ|Грозный|Магас|Назрань|Махачкала|Дербент|Элиста|Волгоград|Волжский|Балашиха|Химки|Подольск|Мытищи|Королёв|Люберцы|Одинцово|Сергиев Посад|Домодедово|Красногорск|Раменское|Коломна|Серпухов|Электросталь'],
 ['Europe/Samara','Самара|Тольятти|Сызрань|Новокуйбышевск|Ижевск|Сарапул|Воткинск|Глазов'],
 ['Europe/Ulyanovsk','Ульяновск|Димитровград'],['Europe/Saratov','Саратов|Энгельс|Балаково'],['Europe/Astrakhan','Астрахань|Ахтубинск'],
 ['Asia/Yekaterinburg','Екатеринбург|Нижний Тагил|Каменск-Уральский|Первоуральск|Челябинск|Магнитогорск|Златоуст|Миасс|Курган|Шадринск|Тюмень|Тобольск|Ишим|Ханты-Мансийск|Сургут|Нижневартовск|Нефтеюганск|Салехард|Новый Уренгой|Ноябрьск|Пермь|Березники|Соликамск|Уфа|Стерлитамак|Салават|Нефтекамск|Благовещенск (Башкортостан)|Оренбург|Орск|Бузулук'],
 ['Asia/Omsk','Омск|Тара'],['Asia/Novosibirsk','Новосибирск|Бердск|Искитим'],['Asia/Tomsk','Томск|Северск'],['Asia/Barnaul','Барнаул|Бийск|Рубцовск|Горно-Алтайск'],['Asia/Novokuznetsk','Кемерово|Новокузнецк|Прокопьевск|Междуреченск'],['Asia/Krasnoyarsk','Красноярск|Норильск|Ачинск|Канск|Железногорск (Красноярский край)|Абакан|Черногорск|Кызыл'],
 ['Asia/Irkutsk','Иркутск|Ангарск|Братск|Усть-Илимск|Улан-Удэ|Северобайкальск'],['Asia/Chita','Чита|Краснокаменск'],['Asia/Yakutsk','Якутск|Нерюнгри|Мирный (Якутия)|Алдан|Благовещенск (Амурская область)|Белогорск (Амурская область)|Свободный|Тында'],['Asia/Vladivostok','Владивосток|Уссурийск|Находка|Артём|Хабаровск|Комсомольск-на-Амуре|Биробиджан'],['Asia/Ust-Nera','Усть-Нера'],['Asia/Khandyga','Хандыга'],['Asia/Magadan','Магадан'],['Asia/Sakhalin','Южно-Сахалинск|Корсаков|Холмск|Оха|Курильск'],['Asia/Srednekolymsk','Среднеколымск|Северо-Курильск'],['Asia/Kamchatka','Петропавловск-Камчатский|Елизово|Вилючинск'],['Asia/Anadyr','Анадырь|Певек|Билибино'],
 ['Europe/Berlin','Берлин|Франкфурт-на-Майне|Мюнхен|Гамбург'],['Europe/London','Лондон'],['Europe/Paris','Париж'],['Europe/Rome','Рим'],['Europe/Madrid','Мадрид'],['Europe/Istanbul','Стамбул|Анкара'],['Europe/Minsk','Минск'],['Asia/Almaty','Алматы|Астана'],['Asia/Tashkent','Ташкент'],['Asia/Yerevan','Ереван'],['Asia/Tbilisi','Тбилиси'],['Asia/Baku','Баку'],['Asia/Dubai','Дубай|Абу-Даби'],['Asia/Shanghai','Пекин|Шанхай'],['Asia/Tokyo','Токио'],['Asia/Seoul','Сеул'],['Asia/Singapore','Сингапур'],['America/New_York','Нью-Йорк'],['America/Chicago','Чикаго'],['America/Los_Angeles','Лос-Анджелес'],['Australia/Sydney','Сидней']
];
export const CLOCK_CITIES = Object.freeze(cityZones.flatMap(([zone,names])=>names.split('|').map(city=>Object.freeze({city,zone}))));
const cityKey = value => String(value??'').normalize('NFC').trim().toLocaleLowerCase('ru-RU').replaceAll('ё','е').replace(/[–—-]/g,' ').replace(/\s+/g,' ');
export function findClockCity(value) {
  const key=cityKey(value);return key?CLOCK_CITIES.find(x=>cityKey(x.city)===key)||null:null;
}
export function validClockZone(zone) {
  if(typeof zone!=='string'||!zone.includes('/')||zone.length>100)return false;
  try {new Intl.DateTimeFormat('ru-RU',{timeZone:zone});return true;}catch{return false;}
}


const canonicalStatuses = new Map(STATUSES.map(status => [status.toLowerCase(), status]));
export function canonicalStatus(status) {
  return typeof status === 'string' ? (canonicalStatuses.get(status.toLowerCase()) ?? status) : status;
}

const isSigned = status => ['заключен', 'Отправлен', 'Вручен', 'Оплачен'].includes(canonicalStatus(status));
export const closesDelivery = status => ['Вручен', 'Оплачен'].includes(canonicalStatus(status));
export const hasHandover = (record, today) =>
  Boolean(record.handoverDate && !validateDate(record.handoverDate) && record.handoverDate <= today);

export function contractDeadlines(record, today) {
  return [
    {kind: 'sign', label: 'Подписать', date: record.signBy, pending: !isSigned(record.status)},
    {kind: 'fulfill', label: 'Срок поставки', date: record.fulfillBy,
      pending: !closesDelivery(record.status) && !hasHandover(record, today)},
    {kind: 'payment', label: 'Срок оплаты', date: record.paymentDue,
      pending: canonicalStatus(record.status) === 'Вручен'},
  ].filter(({date}) => date && !validateDate(date))
    .map(({kind, label, date, pending}) => {
      const daysLeft = pending ? (Date.parse(date) - Date.parse(today)) / 86400000 : null;
      const urgency = daysLeft === null || daysLeft > 7 ? 'none' : daysLeft <= 3 ? 'urgent' : 'soon';
      return {kind, label, date, daysLeft, urgency, overdue: daysLeft !== null && daysLeft < 0};
    });
}

const boardStages = new Map(BOARD_COLUMNS.map(({id, status}) => [status, id]));

export function contractStage(record) {
  return boardStages.get(canonicalStatus(record.status)) ?? 'sign-by';
}

export function planStatusChange(record, status, today, markHandover = false) {
  const previousStatus = canonicalStatus(record.status);
  const clearsHandover = Boolean(record.handoverDate && closesDelivery(previousStatus) && !closesDelivery(status));
  const handoverDate = clearsHandover ? '' : (record.handoverDate || '');
  return {
    handoverDate: handoverDate || ((markHandover || status === 'Вручен') ? today : ''),
    needsSigningDate: Boolean(validateDate(record.signBy, status === 'подписать до')),
    needsDeliveryDeadline: Boolean(validateDate(record.fulfillBy)),
    needsHandoverDate: Boolean(handoverDate && !hasHandover(record, today)),
    clearsHandover,
    reversesPayment: previousStatus === 'Оплачен' && status !== 'Оплачен',
  };
}

// The table is a chronological feed: newest saved contract first.
// Preserve creation time on edit; source order resolves identical timestamps.
export function newestContractsFirst(records) {
  return records.map((record,index)=>({record,index})).sort((a,b)=>
    String(b.record?.created??'').localeCompare(String(a.record?.created??'')) ||
    b.index-a.index
  ).map(({record})=>record);
}

// Static attention colors supplement the existing pulsing action controls.
 // Only actionable signing and delivery deadlines qualify; delivered/paid
 // contracts and future stages never receive an inappropriate alert.
export function contractAttention(record, today) {
  const status=canonicalStatus(record?.status);
  const deadlines=contractDeadlines(record,today).filter(d=>
    d.daysLeft!==null && (
      (d.kind==='sign' && ['формирование','подписать до','на подписи'].includes(status)) ||
      (d.kind==='fulfill' && ['заключен','Отправлен'].includes(status))
    )
  );
  if(!deadlines.length)return null;
  const days=Math.min(...deadlines.map(d=>d.daysLeft));
  if(days<0)return 'overdue';
  if(days<=1)return 'critical';
  if(days<=3)return 'soon';
  return null;
}

export function sortBoardContracts(records, today) {
  // Same chronological order as the contract list: latest created at top.
  // Editing a record does not change its original creation timestamp.
  return newestContractsFirst(records);
}

// Only explicitly recorded purposes are displayed; unknown historical details
// are labelled as such by the Excel backend, never guessed here.
export function validateOtherExpenseDetails(rows) {
  if (!Array.isArray(rows) || rows.length > 25) return {error:'Допустимо не более 25 строк прочих расходов',index:0};
  const normalized=[];
  let total=0;
  for (const [index,row] of rows.entries()) {
    const description=String(row?.description??'').trim();
    if (!description || description.length>200 || /[\u0000-\u001f\u007f]/.test(description)) {
      return {error:'Укажите назначение расхода одной строкой, до 200 символов',index};
    }
    let amount;
    try {amount=moneyToKopecks(String(row?.amount??''));}
    catch (e) {return {error:e.message,index};}
    if (!Number.isSafeInteger(amount) || amount<=0) return {error:'Сумма строки должна быть больше нуля',index};
    total+=amount;
    if (!Number.isSafeInteger(total) || total>90000000000000) return {error:'Слишком большая сумма прочих расходов',index};
    normalized.push({description,amount:kopecksToDecimal(amount)});
  }
  return {normalized,total,error:'',index:-1};
}

export function validateRecord(raw, today = todayInMoscow()) {
  const errors={};
  const req = (key, msg) => { if (!String(raw[key]??'').trim()) errors[key]=msg; };
  for (const [k,msg] of [['platform','Укажите площадку'],['customer','Укажите заказчика'],['product','Выберите товар'],['status','Выберите статус']]) req(k,msg);
  if (String(raw.platform??'').trim().length > 120) errors.platform='Название площадки не должно превышать 120 символов';
  if (String(raw.customer??'').trim().length > 500) errors.customer='Название не должно превышать 500 символов';
  const contactPerson = String(raw.contactPerson ?? '').trim();
  const phone = String(raw.phone ?? '').trim();
  const controlChars = /[\u0000-\u001f\u007f]/;
  if (contactPerson.length > 200 || controlChars.test(contactPerson)) errors.contactPerson = 'Введите имя в одну строку, до 200 символов';
  if (phone && (phone.length > 80 || !/[0-9]/.test(phone) || controlChars.test(phone))) errors.phone = 'Введите телефон с цифрами, до 80 символов';
  const contractNumber=String(raw.contractNumber ?? '').trim();
  if(contractNumber.length>100 || controlChars.test(contractNumber)) errors.contractNumber='Введите номер в одну строку, до 100 символов';
  const contractDate=validateDate(raw.contractDate);
  if(contractDate) errors.contractDate=contractDate;
  if (!PRODUCTS.includes(raw.product)) errors.product='Выберите товар из справочника';
  if (!STATUSES.includes(raw.status)) errors.status='Недопустимый статус';
  if (!Number.isInteger(Number(raw.year)) || +raw.year < MIN_ACCOUNT_YEAR || +raw.year > MAX_ACCOUNT_YEAR) errors.year='Допустимы годы с 2026 по 2100';
  if (!Number.isInteger(Number(raw.month)) || +raw.month < 1 || +raw.month > 12 ||
      (+raw.year===2026 && +raw.month<11)) errors.month='Для 2026 года доступны ноябрь и декабрь';
  if (!validQuantity(raw.quantity)) errors.quantity=quantityError;
  for (const key of ['amount','cost','fee','logistics','other']) {
    try { moneyToKopecks(raw[key], {allowEmpty:key!=='amount'}); }
    catch (e) {errors[key]=e.message;}
  }
  if (!errors.amount && moneyToKopecks(raw.amount)===0) errors.amount='Сумма контракта должна быть больше нуля';
  if (String(raw.taxAmount??'').trim()) {
    try {moneyToKopecks(raw.taxAmount);}
    catch(e){errors.taxAmount=e.message;}
  }
  if (raw.otherDetails !== undefined) {
    const details=validateOtherExpenseDetails(raw.otherDetails);
    if (details.error) errors.other=details.error;
    else if (!errors.other && details.total !== moneyToKopecks(raw.other,{allowEmpty:true})) {
      errors.other='Итог прочих расходов не совпадает с расшифровкой';
    }
  }
  const sig=validateDate(raw.signBy,raw.status==='подписать до');
  const ful=validateDate(raw.fulfillBy);
  const pay=validateDate(raw.paymentDue);
  if(sig)errors.signBy=sig; if(ful)errors.fulfillBy=ful;
  if(pay)errors.paymentDue=pay;
  if(!pay && raw.paymentDue && raw.handoverDate && raw.paymentDue < raw.handoverDate)
    errors.paymentDue='Срок оплаты не может быть раньше даты вручения';
  const handover = validateDate(raw.handoverDate, raw.status === 'Вручен');
  if (handover) errors.handoverDate = handover;
  else if (raw.handoverDate > today) errors.handoverDate = 'Дата вручения позже сегодняшней';
  return errors;
}

export function annualRateBp(finance,year) {
  if(year===2026)return 500;
  const rate=finance?.rates?.[year];
  return rate===5||rate===15?rate*100:null;
}
export function calculateFinancialSummary(records,year,finance) {
  return calculateSummary(records,year,annualRateBp(finance,year),finance?.expenses||[],finance?.retainedExpenses||[]);
}
export function calculateSummary(records, year, rateBp=500, generalExpenses=[], retainedExpenses=[]) {
  if(!Number.isInteger(year)) throw new Error('Год должен быть целым числом');
  if(rateBp!==null && ![500,1500].includes(rateBp)) throw new Error('Недопустимая ставка УСН');
  // All valid, non-deleted contracts are included. The result is a PLANNED margin,
  // not received cash or verified tax accounting. Outstanding amounts stay separate.
  const months=Array.from({length:12},(_,i)=>({
    month:i+1,count:0,contract:0,paid:0,expected:0,cost:0,
    fee:0,logistics:0,other:0,common:0,expenses:0,profitBefore:0,tax:0,
    manualTax:0,autoTaxProfit:0,operatingExpenses:0,profitAfter:0
  }));
  for(const record of records) {
    if (!isActiveRecord(record) || +record.year!==year || record.check!=='OK')continue;
    if (!Number.isInteger(+record.month) || +record.month<1 || +record.month>12)continue;
    const get=k=>moneyToKopecks(record[k],{allowEmpty:k!=='amount'});
    try {
      const amount=get('amount'),cost=get('cost'),fee=get('fee'),
        logistics=get('logistics'),other=get('other');
      const explicitTax=String(record.taxAmount??'').trim();
      const enteredTax=explicitTax ? moneyToKopecks(explicitTax) : null;
      const m=months[+record.month-1];
      m.count++;m.contract+=amount;m.cost+=cost;m.fee+=fee;
      m.logistics+=logistics;m.other+=other;
      if(enteredTax === null){
        m.autoTaxProfit+=amount-cost-fee-logistics-other;
      }else m.manualTax+=enteredTax;
      if(canonicalStatus(record.status)==='Оплачен')m.paid+=amount;
      else m.expected+=amount;
    }catch{ /* A malformed record cannot be a valid financial item */ }
  }
  // Expenses from terminated contracts stay in their original period and categories.
  // No revenue, receivable, stock cost or paid contract tax estimate is retained here.
  for(const item of retainedExpenses){
    if(+item.year!==year||!Number.isInteger(+item.month)||+item.month<1||+item.month>12)continue;
    const m=months[+item.month-1];
    const fee=moneyToKopecks(item.fee,{allowEmpty:true}),logistics=moneyToKopecks(item.logistics,{allowEmpty:true}),other=moneyToKopecks(item.other,{allowEmpty:true});
    m.fee+=fee;m.logistics+=logistics;m.other+=other;
    m.autoTaxProfit-=fee+logistics+other;
  }
  for(const item of generalExpenses){
    const date=String(item.date||'');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||Number(date.slice(0,4))!==year)continue;
    const month=Number(date.slice(5,7));
    if(month<1||month>12)continue;
    const sum=moneyToKopecks(item.amount);
    if(sum<=0)throw new Error('Недопустимый общий расход');
    months[month-1].common+=sum;
  }
  for(const m of months) {
    m.expenses=m.cost+m.fee+m.logistics+m.other+m.common;
    m.profitBefore=m.contract-m.expenses;
    m.tax=rateBp===null?null:m.manualTax+Math.round(Math.max(0,m.autoTaxProfit-m.common)*rateBp/10000);
    m.operatingExpenses=rateBp===null?null:m.fee+m.logistics+m.other+m.tax;
    m.profitAfter=rateBp===null?null:m.contract-m.cost-m.operatingExpenses-m.common;
  }
  const total={month:0,count:0,contract:0,paid:0,expected:0,cost:0,
    fee:0,logistics:0,other:0,common:0,expenses:0,profitBefore:0,tax:0,
    manualTax:0,autoTaxProfit:0,operatingExpenses:0,profitAfter:0};
  for(const m of months) for(const key of
    ['count','contract','paid','expected','cost','fee','logistics','other','common','expenses','profitBefore',
     'manualTax','autoTaxProfit','tax','operatingExpenses','profitAfter'])
    total[key]+=m[key];
  // Monthly management forecast only. No annual 1% minimum, no year-end
  // reconciliation: those require the entrepreneur's complete tax ledger.
  // An explicit contract tax amount overrides the monthly estimate for it.
  // Annual totals are the exact sum of the twelve monthly rows.
  if(rateBp===null){total.tax=null;total.operatingExpenses=null;total.profitAfter=null;}
  const monthlyEstimatedTax=total.tax;
  return {year,months,total,monthlyEstimatedTax};
}

export function summarizeDelivered(records, phases, year, today=todayInMoscow()) {
  const months=Array(12).fill(0);
  for (const record of records) {
    if (!isActiveRecord(record) || !hasHandover(record,today) || +record.handoverDate.slice(0,4)!==year || phases?.[record.id]==='returned') continue;
    const lines=contractItems(record);
    if (!lines.length || lines.some(item=>!validQuantity(item.quantity))) continue;
    months[Number(record.handoverDate.slice(5,7))-1]+=lines.reduce((sum,item)=>sum+Number(item.quantity),0);
  }
  return {months,total:months.reduce((sum,count)=>sum+count,0)};
}

export function recordMonthLabel(year, month) {return `${MONTHS[month-1]} ${year}`;}

export function fieldToInput(record) {
  const mapped={...record};
  for(const k of ['amount','cost','fee','logistics','other']){
    try{mapped[k]=kopecksToDecimal(moneyToKopecks(record[k],{allowEmpty:true}));}catch{mapped[k]='0.00';}
  }
  if(String(record.taxAmount??'').trim()){
    try{mapped.taxAmount=kopecksToDecimal(moneyToKopecks(record.taxAmount));}catch{mapped.taxAmount='';}
  }else mapped.taxAmount='';
  return mapped;
}
