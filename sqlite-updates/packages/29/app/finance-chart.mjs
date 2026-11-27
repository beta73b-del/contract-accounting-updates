// Annual financial comparison, purely derived from the already computed monthly summary.
// No additional storage, tax reassessment, network request or contract mutation.
import {MONTHS, formatMoney} from './logic.mjs?v=20261121-finance-overhead1';

export function visibleFinancialMonths(available, recordsByMonth, showAll=false, summaries=[]) {
  return showAll ? [...available] : available.filter(month => (recordsByMonth.get(month)?.length || 0) > 0 || ['common','fee','logistics','other'].some(key=>(summaries[month-1]?.[key]||0)>0));
}

// Source palette: the five requested colors, plus lavender for the sixth
// existing financial series. Neither series order nor underlying values change.
export const CHART_SERIES = Object.freeze([
  {key:'contract',label:'Сумма контрактов',color:'#E05244'},
  {key:'cost',label:'Себестоимость',color:'#1E3A8A'},
  {key:'logistics',label:'Логистика',color:'#2A9D8F'},
  {key:'tax',label:'Налоги (оценка)',color:'#E9C46A'},
  {key:'fee',label:'Комиссии',color:'#CBD5E1'},
  {key:'other',label:'Прочие',color:'#A78BDA'},
  {key:'common',label:'Общие расходы',color:'#6D28D9'},
]);

// Top face: RGB channels 15% brighter. Right face: 20% darker.
// Keep the original colors on front faces and legend.
export function shadeChartColor(hex, factor) {
  if(!/^#[\da-f]{6}$/i.test(hex) || !Number.isFinite(factor) || factor < 0)
    throw new TypeError('Недопустимый цвет или коэффициент');
  const channels=[1,3,5].map(i=>Math.min(255,Math.round(parseInt(hex.slice(i,i+2),16)*factor)));
  return '#'+channels.map(v=>v.toString(16).padStart(2,'0')).join('').toUpperCase();
}
const chartMoney=value=>value===null?'—':formatMoney(value);
const NS='http://www.w3.org/2000/svg';
const compactRubles=value=>new Intl.NumberFormat('ru-RU',{
  notation:'compact',maximumFractionDigits:1
}).format(value)+' ₽';

export function annualChartData(months) {
  if(!Array.isArray(months)||months.length!==12)throw new Error('Ожидаются ровно 12 месяцев');
  return months.map((m,index)=>{
    const contract=m.contract||0;
    const active=Number(m.count)>0||Number(m.common)>0;
    const amounts=Object.fromEntries(CHART_SERIES.map(s=>[s.key,(m[s.key]===null?null:(m[s.key]||0))]));
    const profit=m.profitAfter===null?null:(m.profitAfter||0);
    return {
      month:index+1,label:MONTHS[index],active,contract,profit,
      profitability:active&&contract>0&&profit!==null?Math.round(profit/contract*1000)/10:null,
      amounts,
    };
  });
}
export function chartAxisMax(data) {
  const peak=Math.max(0,...data.flatMap(row=>Object.values(row.amounts)));
  if(peak<=0)return 100;
  const raw=peak/4;
  const power=Math.pow(10,Math.floor(Math.log10(raw)));
  const base=raw/power;
  const step=(base<=1?1:base<=2?2:base<=5?5:10)*power;
  return Math.ceil(peak/step)*step;
}
const svg=(tag,attrs={})=>{
  const elem=document.createElementNS(NS,tag);
  for(const [key,value] of Object.entries(attrs))elem.setAttribute(key,String(value));
  return elem;
};
const make=(tag,cls,text)=>{
  const elem=document.createElement(tag);
  if(cls)elem.className=cls;
  if(text!==undefined)elem.textContent=text;
  return elem;
};

export function renderAnnualChart(container,months,year){
  if(!container)return;
  const rows=annualChartData(months);
  const max=chartAxisMax(rows);
  const any=rows.some(row=>row.active);
  container.replaceChildren();
  container.setAttribute('aria-label','Годовой график финансовых итогов за '+year+' год');
  const head=make('div','annual-chart-head');
  head.append(make('h2','', 'Годовой график'));
  head.append(make('span','annual-chart-year',String(year)+' год'));
  container.append(head);
  const legend=make('div','annual-chart-legend');
  for(const item of CHART_SERIES){
    const label=make('span','annual-chart-series');
    const dot=make('i','');dot.style.backgroundColor=item.color;dot.setAttribute('aria-hidden','true');
    label.append(dot,document.createTextNode(item.label));legend.append(label);
  }
  container.append(legend);
  const scroller=make('div','annual-chart-scroll');
  scroller.tabIndex=0;
  scroller.setAttribute('aria-label','График по месяцам. При необходимости прокрутите по горизонтали.');
  const viewport=make('div','annual-chart-viewport');
  const graph=svg('svg',{viewBox:'0 0 1100 375',role:'img','aria-label':
    'График: суммы контрактов, себестоимость и операционные расходы по месяцам за '+year});
  const graphTitle=svg('title');graphTitle.textContent='Финансы по месяцам за '+year;graph.append(graphTitle);
  const plot={left:92,right:1075,top:77,bottom:321};
  const width=plot.right-plot.left;
  const height=plot.bottom-plot.top;
  for(let j=0;j<=4;j++){
    const amount=max*j/4;
    const y=plot.bottom-height*j/4;
    graph.append(svg('line',{x1:plot.left,x2:plot.right,y1:y,y2:y,stroke:'#E5E7EB','stroke-width':1}));
    const txt=svg('text',{x:plot.left-10,y:y+4,'text-anchor':'end',
      fill:'#60798c','font-size':'12'});
    txt.textContent=compactRubles(amount/100);graph.append(txt);
  }
  const rentHeader=svg('text',{x:plot.left,y:20,fill:'#62798c','font-size':'12'});
  rentHeader.textContent='Рентабельность по месяцам';graph.append(rentHeader);
  const tip=make('div','annual-chart-tip');
  tip.hidden=true;
  tip.setAttribute('role','status');
  tip.setAttribute('aria-live','polite');
  let active=null;
  const show=(row,monthIndex)=>{
    active=monthIndex;
    tip.replaceChildren(make('strong','',row.label+' '+year));
    for(const item of CHART_SERIES){
      const entry=make('div','annual-chart-tip-row');
      entry.append(make('span','',item.label),make('b','',chartMoney(row.amounts[item.key])));
      tip.append(entry);
    }
    const result=make('div','annual-chart-tip-result');
    result.append(make('span','','Чистая прибыль'),
      make('b','',chartMoney(row.profit)+' ('+(row.profitability===null?'—':
        (row.profitability>0?'+':'')+row.profitability.toFixed(1).replace('.',',')+'%')+')'));
    tip.append(result);
    tip.hidden=false;
    const pct=100*(monthIndex+.5)/12;
    tip.style.left=Math.min(78,Math.max(12,pct))+'%';
  };
  const hide=monthIndex=>{if(active===monthIndex){tip.hidden=true;active=null;}};
  rows.forEach((row,index)=>{
    const cell=width/12;
    const center=plot.left+cell*(index+.5);
    const pct=svg('text',{x:center,y:55,'text-anchor':'middle',
      fill:row.profitability===null?'#93a1aa':row.profitability<0?'#b03b36':'#138047',
      'font-size':'14','font-weight':'700'});
    pct.textContent=row.profitability===null?'—':
      (row.profitability>0?'+':'')+row.profitability.toFixed(1).replace('.',',')+'%';
    graph.append(pct);
    const mon=svg('text',{x:center,y:349,'text-anchor':'middle',fill:'#506b7f','font-size':'13'});
    mon.textContent=MONTHS[index].slice(0,3);graph.append(mon);
    if(!row.active)return;
    // Six bars touch within a month; 10% month-to-month whitespace.
    // Three planar SVG faces form the 3D effect, with no raster or dot pattern.
    const groupWidth=cell*.90;
    const seriesWidth=groupWidth/CHART_SERIES.length;
    for(const [i,series] of CHART_SERIES.entries()){
      const value=row.amounts[series.key];
      if(value<=0)continue;
      const barH=Math.max(2.5,Math.min(height,height*value/max));
      const x=center-groupWidth/2+i*seriesWidth;
      const y=plot.bottom-barH;
      const depth=Math.min(3.4,seriesWidth*.26);
      const rise=Math.min(3.8,Math.max(2,barH*.6));
      const frontWidth=seriesWidth-depth;
      const bar=svg('g',{'class':'annual-chart-bar','data-series':series.key,'data-month':row.month});
      const front=svg('rect',{
        'class':'annual-chart-front',x,y,width:frontWidth,height:barH,fill:series.color
      });
      const top=svg('polygon',{
        'class':'annual-chart-top',
        points:`${x},${y} ${x+frontWidth},${y} ${x+seriesWidth},${y-rise} ${x+depth},${y-rise}`,
        fill:shadeChartColor(series.color,1.15)
      });
      const right=svg('polygon',{
        'class':'annual-chart-right',
        points:`${x+frontWidth},${y} ${x+seriesWidth},${y-rise} ${x+seriesWidth},${plot.bottom-rise} ${x+frontWidth},${plot.bottom}`,
        fill:shadeChartColor(series.color,.8)
      });
      const barTitle=svg('title');
      barTitle.textContent=row.label+': '+series.label+' '+formatMoney(value);
      bar.append(barTitle,front,top,right);
      graph.append(bar);
    }
    const hit=svg('rect',{x:center-cell/2,y:71,width:cell,height:plot.bottom-69,
      fill:'transparent',tabindex:0,'aria-label':row.label+' '+year+
        ', чистая прибыль '+chartMoney(row.profit)});
    hit.classList.add('annual-chart-hit');
    hit.addEventListener('pointerenter',()=>show(row,index));
    hit.addEventListener('pointerleave',()=>hide(index));
    hit.addEventListener('focus',()=>show(row,index));
    hit.addEventListener('blur',()=>hide(index));
    hit.addEventListener('click',()=>active===index?hide(index):show(row,index));
    hit.addEventListener('keydown',e=>{
      if(e.key==='Escape')hide(index);
      if(e.key==='Enter'||e.key===' '){e.preventDefault();active===index?hide(index):show(row,index);}
    });
    graph.append(hit);
  });
  viewport.append(graph);scroller.append(viewport);
  container.append(scroller,tip);
  if(!any)container.append(make('p','annual-chart-empty',
    'В выбранном году нет контрактов и общих расходов. График появится после сохранения данных.'));
}

/**
 * A one-month, compact view of the exact same six series as the annual
 * Financial Results chart. Input is the untouched calculateSummary(...).months.
 * No duplicated calculations, new tax logic, or separate financial storage.
 */
export function monthlyFinancialChartData(months, month) {
  if(!Number.isInteger(month) || month < 1 || month > 12)
    throw new RangeError('Месяц графика должен быть от 1 до 12');
  const row=annualChartData(months)[month-1];
  return {...row, series:CHART_SERIES.map(series=>({
    key:series.key,label:series.label,color:series.color,value:row.amounts[series.key]
  }))};
}

export function renderMonthlyFinanceChart(container, months, year, month) {
  if(!container)return;
  const row=monthlyFinancialChartData(months,month);
  container.replaceChildren();
  container.setAttribute('aria-label','Финансовые итоги за '+row.label+' '+year+' года');
  if(!row.active) {
    container.append(make('p','home-empty-state','За текущий месяц нет контрактов и общих расходов.'));
    return;
  }
  const maximum=chartAxisMax([row]);
  const graph=svg('svg',{viewBox:'0 0 360 210',role:'img',
    'aria-label':'Семь показателей за '+row.label+': '+row.series.map(s=>s.label+' '+chartMoney(s.value)).join('; ')});
  const plot={left:48,right:348,top:15,bottom:175};
  const plotHeight=plot.bottom-plot.top;
  for(let step=0;step<=4;step++) {
    const y=plot.bottom-step*plotHeight/4;
    graph.append(svg('line',{x1:plot.left,x2:plot.right,y1:y,y2:y,stroke:'#e4ebf3','stroke-width':1}));
    if(step%2===0) {
      const tick=svg('text',{x:plot.left-7,y:y+4,'text-anchor':'end',fill:'#69819a','font-size':'11'});
      tick.textContent=compactRubles(maximum*step/4/100);
      graph.append(tick);
    }
  }
  const cell=(plot.right-plot.left)/row.series.length;
  const barWidth=Math.min(31,cell*.69);
  for(const [i,series] of row.series.entries()) {
    const value=series.value;
    const center=plot.left+cell*(i+.5);
    if(value>0) {
      const barH=Math.max(2.6,Math.min(plotHeight,plotHeight*value/maximum));
      const x=center-barWidth/2,y=plot.bottom-barH;
      const depth=Math.min(5,barWidth*.19),rise=Math.min(4.2,Math.max(2,barH*.5));
      const frontWidth=barWidth-depth;
      const g=svg('g',{'class':'annual-chart-bar monthly-finance-bar','data-series':series.key});
      const front=svg('rect',{x,y,width:frontWidth,height:barH,fill:series.color});
      const top=svg('polygon',{points:`${x},${y} ${x+frontWidth},${y} ${x+barWidth},${y-rise} ${x+depth},${y-rise}`,
        fill:shadeChartColor(series.color,1.15)});
      const side=svg('polygon',{points:`${x+frontWidth},${y} ${x+barWidth},${y-rise} ${x+barWidth},${plot.bottom-rise} ${x+frontWidth},${plot.bottom}`,
        fill:shadeChartColor(series.color,.8)});
      const title=svg('title');title.textContent=series.label+': '+formatMoney(value);
      g.append(title,front,top,side);
      graph.append(g);
    }else if(value!==null){
      graph.append(svg('circle',{cx:center,cy:plot.bottom-2,r:2,fill:series.color}));
    }
    const key=svg('text',{x:center,y:194,'text-anchor':'middle',fill:'#5d7490','font-size':'11'});
    key.textContent=String(i+1);
    graph.append(key);
  }
  const drawing=make('div','home-month-chart-graphic');
  drawing.append(graph);
  container.append(drawing);

  const legend=make('div','home-month-chart-legend');
  for(const [i,item] of row.series.entries()) {
    const line=make('div','home-month-legend-line');
    line.dataset.series=item.key;
    const dot=make('span','home-month-legend-dot');
    dot.style.backgroundColor=item.color;
    const label=make('span','home-month-legend-label',(i+1)+'. '+item.label);
    const value=make('strong','home-month-legend-value',chartMoney(item.value));
    line.append(dot,label,value);
    legend.append(line);
  }
  container.append(legend);
  const profit=make('div','home-month-net-profit');
  profit.dataset.negative=String(row.profit<0);
  profit.append(make('span','','Чистая прибыль (план)'),make('strong','',chartMoney(row.profit)));
  container.append(profit);
}
