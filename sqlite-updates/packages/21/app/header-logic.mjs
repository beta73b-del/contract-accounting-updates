import { canonicalStatus, contractDeadlines, moneyToKopecks, isActiveRecord } from './logic.mjs?v=1.7.21-shipped1';

// Дата контракта — единственный источник месяца для датированных записей.
// Не допускаем невозможные даты (например, 30 февраля) и импортные значения вроде «84».
export function validContractDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const ms = Date.parse(value+'T00:00:00Z');
  return Number.isFinite(ms) && new Date(ms).toISOString().slice(0,10) === value;
}
export function belongsToPeriod(record, year, month) {
  const date = record?.contractDate;
  return validContractDate(date) && date.slice(0, 7) === String(year).padStart(4, '0') + '-' + String(month).padStart(2, '0');
}
// Недатированные записи не скрываем. Для повреждённой непустой даты
// используем сохранённый учётный период лишь для отображения, не меняя БД.
export function registryRecords(records, year, month) {
  return uniqueContracts(records).filter(record => {
    if (validContractDate(record.contractDate)) return belongsToPeriod(record, year, month);
    if (!String(record.contractDate ?? '').trim()) return true;
    const y=Number(record.year), m=Number(record.month);
    return !(Number.isInteger(y)&&y>=2026&&y<=2100&&Number.isInteger(m)&&m>=1&&m<=12) || (y===Number(year)&&m===Number(month));
  });
}

export function uniqueContracts(records) {
  const seen = new Set();
  return records.filter(record => {
    const id = String(record?.id ?? '');
    if (!id || seen.has(id) || !isActiveRecord(record)) return false;
    seen.add(id);
    return true;
  });
}

export function signingSide(record) {
  const status = canonicalStatus(record?.status);
  if (status === 'подписать до') return 'Поставщик';
  if (status === 'на подписи') return 'Заказчик';
  return null;
}

export function signingQueue(records) {
  return uniqueContracts(records).filter(record => signingSide(record) !== null);
}

export function unpaidQueue(records) {
  // Оплата дискретная: пока не оплачен переданный заказчику товар — ожидаем весь договор.
  return uniqueContracts(records).filter(record => canonicalStatus(record?.status) === 'Вручен');
}

export function unpaidAmountKopecks(records) {
  return unpaidQueue(records).reduce((sum, record) => {
    try {
      return sum + (record.check === 'OK' ? moneyToKopecks(record.amount) : 0);
    } catch {
      return sum;
    }
  }, 0);
}

export function boardRecords(records, year, month) {
  return uniqueContracts(records).filter(record => {
    const status = canonicalStatus(record.status);
    return belongsToPeriod(record, year, month) ||
      !record.contractDate ||
      status === 'Вручен';
  });
}

export function activeNotifications(records, today) {
  const items = [], seen = new Set();
  for (const record of uniqueContracts(records)) {
    const status = canonicalStatus(record.status);
    for (const deadline of contractDeadlines(record, today)) {
      const relevant = (deadline.kind === 'sign' && signingSide(record) !== null) ||
        (deadline.kind === 'fulfill' && ['заключен','Отправлен'].includes(status));
      if (!relevant || deadline.daysLeft === null || deadline.daysLeft > 1) continue;
      const key = record.id + ':' + deadline.kind;
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({key,record,deadline});
    }
  }
  return items.sort((a,b)=>a.deadline.date.localeCompare(b.deadline.date));
}

export function shiftMonth(year, month, delta) {
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(delta)) {
    throw new Error('Некорректная дата периода');
  }
  const index = year * 12 + (month - 1) + delta;
  return {year: Math.floor(index / 12), month: index % 12 + 1};
}
