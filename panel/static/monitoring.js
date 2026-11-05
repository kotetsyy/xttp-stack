import {$,$$,main,esc,request,post,action,status,confirmAction,setTitle,scopedPoll,bytes,time,kv} from './app.js';
const freshness = (node, text = '') => status(node, `Обновлено ${new Date().toLocaleTimeString('ru-RU')}${text ? ' · '+text : ''}`);
function failed(node,error) { if(error.name !== 'AbortError') status(node,`Данные устарели: ${error.message}. Последние полученные значения оставлены на экране. Повторная загрузка — автоматически или кнопкой «Обновить».`,'error'); }
function table(headers,id,caption='') {return `<div class="table-wrap"><table>${caption?`<caption>${caption}</caption>`:''}<thead><tr>${headers.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody id="${id}"></tbody></table></div>`;}
async function traffic() {
  setTitle('Статистика','');
  main.innerHTML=`<section class="monitoring-view stats-view"><div class="metrics stats-grid" id="traffic-totals"></div><div class="stats-chart-wrap"><div class="toolbar stats-chart-head"><strong class="stats-chart-title">Трафик</strong><div class="actions monitoring-controls"><label>Период<select id="period"><option value="60">Минута</option><option value="300" selected>5 минут</option><option value="900">15 минут</option><option value="1800">30 минут</option><option value="3600">Час</option></select></label><label>Вид<select id="presentation"><option value="chart">График</option><option value="data">Данные</option></select></label><button type="button" id="refresh">Обновить</button></div></div><div class="legend stats-chart-legend"><label class="check"><input type="checkbox" id="show-down" checked>Входящий, КиБ/с</label><label class="check"><input type="checkbox" id="show-up" checked>Исходящий, КиБ/с</label></div><div class="chart-wrap" id="chart-wrap"><canvas id="traffic-chart" role="img" aria-label="История скоростей. Для точных значений выберите представление Данные."></canvas></div><p id="history-empty" class="empty" hidden>История ещё не накоплена. Ожидание следующего замера.</p><div id="history-data" hidden>${table(['Время','Входящий / с','Исходящий / с'],'history-rows','Те же значения, что на графике; не усреднены.')}<div class="pagination"><button type="button" id="history-prev">Назад</button><span id="history-count" class="muted"></span><button type="button" id="history-next">Далее</button></div></div></div><div class="toolbar stats-group-head"><strong>Статистика групп</strong><label>Поиск группы<input id="stats-search" type="search"></label></div>${table(['Группа','Совпадения','Соединения','Входящий','Исходящий','Политика'],'stats-rows')}<p class="status stats-meta" id="freshness">Загрузка…</p></section>`;
  let history=[],epoch='',cursor=0,snapshot,historyPage=0;
  const storageKey='xttp-traffic-view';
  try{const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(saved){if(['60','300','900','1800','3600'].includes(saved.period))$('#period').value=saved.period;$('#show-down').checked=saved.down!==false;$('#show-up').checked=saved.up!==false;}}catch{}
  function points(){if(!history.length)return [];const end=history[history.length-1].t;return history.filter(p=>p.t>=end-Number($('#period').value));}
  function draw(){
    if(document.hidden)return;
    const data=points();$('#history-empty').hidden=!!data.length;
    const isData=$('#presentation').value==='data';$('#history-data').hidden=!isData;$('#chart-wrap').hidden=isData;
    if(isData){const start=historyPage*100;const reverse=[...data].reverse();$('#history-rows').innerHTML=reverse.slice(start,start+100).map(p=>`<tr><td>${esc(time(p.t))}</td><td>${esc(bytes(p.downRate))}</td><td>${esc(bytes(p.upRate))}</td></tr>`).join('')||'<tr><td colspan="3">Нет замеров.</td></tr>';$('#history-count').textContent=`${Math.min(start+1,data.length)}–${Math.min(start+100,data.length)} из ${data.length}`;$('#history-prev').disabled=historyPage===0;$('#history-next').disabled=start+100>=data.length;return;}
    const canvas=$('#traffic-chart'),rect=canvas.getBoundingClientRect();if(!rect.width)return;const ratio=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);const width=rect.width,height=rect.height,pad={left:60,right:14,top:18,bottom:32},w=width-pad.left-pad.right,h=height-pad.top-pad.bottom;
    const showDown=$('#show-down').checked,showUp=$('#show-up').checked;const max=Math.max(1024,...data.map(p=>Math.max(showDown?p.downRate:0,showUp?p.upRate:0)))*1.1;
    ctx.font='11px system-ui';ctx.fillStyle='#8b93a7';ctx.strokeStyle='rgba(255,255,255,.06)';ctx.lineWidth=1;
    for(let i=0;i<=4;i++){const y=pad.top+h*i/4;ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(width-pad.right,y);ctx.stroke();ctx.fillText((max*(1-i/4)/1024).toLocaleString('ru-RU',{maximumFractionDigits:1}),2,y+4);}
    if(data.length){const end=data[data.length-1].t,start=end-Number($('#period').value);ctx.fillText(new Date(start*1000).toLocaleTimeString('ru-RU'),pad.left,height-8);ctx.textAlign='right';ctx.fillText(new Date(end*1000).toLocaleTimeString('ru-RU'),width-pad.right,height-8);for(const [key,color,show] of [['downRate','#3b82f6',showDown],['upRate','#22c55e',showUp]]){if(!show)continue;ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();data.forEach((point,index)=>{const x=pad.left+(point.t-start)/(end-start||1)*w,y=pad.top+h-point[key]/max*h;index?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}}
  }
  function renderGroups(){const q=$('#stats-search').value.toLocaleLowerCase();const list=(snapshot?.groups||[]).filter(g=>`${g.name} ${g.id}`.toLocaleLowerCase().includes(q));$('#stats-rows').innerHTML=list.map(g=>`<tr><td>${esc(g.name)}</td><td>${esc(g.hits)}</td><td>${esc(g.conns)}</td><td>${esc(bytes(g.down))}</td><td>${esc(bytes(g.up))}</td><td>${esc(g.proxy)}</td></tr>`).join('')||'<tr><td colspan="6">Нет совпадений или активных RuleSet. Сохраните и примените правила.</td></tr>';}
  const poll=scopedPoll(async signal=>{try{
    const data=await request(`/api/stats/snapshot?since=${encodeURIComponent(cursor)}&epoch=${encodeURIComponent(epoch)}`,{signal});if(signal.aborted)return;
    if(epoch!==data.epoch){history=[];cursor=0;historyPage=0;}epoch=data.epoch||'';
    for(const point of data.history||[]){const t=Number(point.t);if(t>cursor){history.push({t,upRate:Math.max(0,Number(point.upRate)||0),downRate:Math.max(0,Number(point.downRate)||0)});cursor=t;}}
    if(history.length>(data.historyMax||3700))history.splice(0,history.length-(data.historyMax||3700));snapshot=data;
    const last=history[history.length-1];$('#traffic-totals').innerHTML=[['Скорость ↓',last?bytes(last.downRate)+'/с':'—',`Всего ${bytes(data.downloadTotal)}`],['Скорость ↑',last?bytes(last.upRate)+'/с':'—',`Всего ${bytes(data.uploadTotal)}`],['Соединения',data.connections,`PROXY ${data.proxyConnections} · RAM ${bytes(data.memory)}`],['Обращения к правилам',(data.groups||[]).reduce((sum,group)=>sum+(Number(group.hits)||0),0),'Срабатываний правил всего']].map(([label,value,detail])=>`<div class="metric stat-card"><span class="stat-label">${esc(label)}</span><strong class="stat-value">${esc(value)}</strong><small class="stat-sub">${esc(detail)}</small></div>`).join('');renderGroups();draw();freshness($('#freshness'),`${history.length} точек истории`);
  }catch(error){failed($('#freshness'),error);}},2000);
  $('#refresh').addEventListener('click',()=>poll.refresh());$('#stats-search').addEventListener('input',renderGroups);
  for(const id of ['period','presentation','show-down','show-up'])$('#'+id).addEventListener('change',()=>{historyPage=0;draw();try{localStorage.setItem(storageKey,JSON.stringify({period:$('#period').value,down:$('#show-down').checked,up:$('#show-up').checked}));}catch{}});
  $('#history-prev').addEventListener('click',()=>{historyPage=Math.max(0,historyPage-1);draw();});$('#history-next').addEventListener('click',()=>{historyPage++;draw();});
  const resize=new ResizeObserver(()=>{if(!document.hidden&&$('#presentation').value==='chart')draw();});
  resize.observe($('#chart-wrap'));
  window.addEventListener('pagehide',event=>{if(!event.persisted)resize.disconnect();});
}
async function connections(){
  setTitle('Соединения','');
  main.innerHTML=`<section class="monitoring-view connections-view"><div class="toolbar conn-head"><label>Поиск соединений<input id="connection-search" type="search" placeholder="Хост, источник, правило"></label><span class="muted conn-meta" id="connection-count"></span><div class="actions conn-actions"><button type="button" id="refresh">Обновить</button><button type="button" id="kill-all" class="danger">Разорвать все</button></div></div><div id="connections" class="connection-list"></div><p id="connection-empty" class="empty" hidden>Соединений нет.</p><p class="status" id="freshness">Загрузка…</p></section>`;
  const nodes=new Map();let cache=[];
  function updateList(){
    const q=$('#connection-search').value.toLocaleLowerCase();const list=cache.filter(c=>[c.host,c.source,c.dest,c.rule,c.chain,c.process].join(' ').toLocaleLowerCase().includes(q));const ids=new Set(list.map(c=>c.id));
    for(const [id,node] of nodes){if(!ids.has(id)){if(node.contains(document.activeElement))$('#connection-search').focus();node.remove();nodes.delete(id);}}
    for(const item of list){let node=nodes.get(item.id);if(!node){node=document.createElement('article');node.className='connection-item';node.dataset.id=item.id;node.innerHTML=`<div class="connection-head"><strong data-field="host"></strong><button type="button" class="danger" data-kill>Разорвать</button></div><p class="muted" data-field="summary"></p><details><summary>Подробности соединения</summary><dl>${['source','dest','network','type','rule','chain','process','start','duration','upload','download'].map(field=>`<dt>${esc({source:'Источник',dest:'Назначение',network:'Сеть',type:'Тип',rule:'Правило',chain:'Цепочка',process:'Процесс',start:'Начало',duration:'Длительность',upload:'Исходящий',download:'Входящий'}[field])}</dt><dd data-field="${field}"></dd>`).join('')}</dl></details>`;$('#connections').append(node);nodes.set(item.id,node);}
      const fields={...item,summary:`${item.network||''} · ${item.rule||'—'} · Входящий ${bytes(item.download)} · Исходящий ${bytes(item.upload)}`,upload:bytes(item.upload),download:bytes(item.download),start:time(item.start)};
      $$('[data-field]',node).forEach(element=>{const text=String(fields[element.dataset.field]??'—');if(element.textContent!==text)element.textContent=text;});$('button',node).setAttribute('aria-label',`Разорвать соединение ${item.host}`);
    }
    $('#connection-empty').hidden=!!list.length;$('#connection-count').textContent=`Показано ${list.length} из ${cache.length}`;
  }
  const poll=scopedPoll(async signal=>{try{const data=await request('/api/connections',{signal});if(signal.aborted)return;cache=data.connections||[];updateList();freshness($('#freshness'));}catch(error){failed($('#freshness'),error);}},2000);
  $('#connection-search').addEventListener('input',updateList);$('#refresh').addEventListener('click',()=>poll.refresh());
  $('#connections').addEventListener('click',event=>{const button=event.target.closest('[data-kill]');if(!button)return;const node=button.closest('[data-id]'),item=cache.find(c=>c.id===node.dataset.id);action(button,async()=>{if(!await confirmAction('Разорвать соединение',`Закрыть соединение с ${item?.host||node.dataset.id}?`,'Разорвать'))return;await request(`/api/connections/${encodeURIComponent(node.dataset.id)}`,{method:'DELETE'});await poll.refresh();},$('#freshness'));});
  $('#kill-all').addEventListener('click',event=>action(event.currentTarget,async()=>{if(!await confirmAction('Разорвать все соединения','Все текущие соединения mihomo будут закрыты. Продолжить?','Разорвать все'))return;await request('/api/connections',{method:'DELETE'});await poll.refresh();},$('#freshness')));
}
async function logs(){
  setTitle('Логи','');
  main.innerHTML=`<section class="monitoring-view logs-view-panel"><div class="toolbar logs-head monitoring-controls"><label>Режим<select id="log-mode"><option value="tail">Последние строки (tail)</option><option value="live">Живой поток (live)</option></select></label><label>Фильтр уровня<select id="log-filter"><option value="all">Все уровни</option><option value="debug">Debug</option><option value="info">Info</option><option value="warning">Warning и Error</option><option value="error">Error</option></select></label><label>Поиск<input id="log-search" type="search"></label><button type="button" id="refresh">Обновить</button><button type="button" id="clear-logs">Очистить экран</button><label class="check"><input type="checkbox" id="follow-logs" checked>Следовать</label></div><div class="toolbar logs-collection monitoring-controls"><label>Уровень сбора ядра<select id="log-level"><option value="debug">Debug</option><option value="info" selected>Info</option><option value="warning">Warning</option><option value="error">Error</option></select></label><button type="button" id="set-log-level">Установить уровень сбора</button></div><div id="logs" class="logs logs-view" tabindex="0" role="region" aria-label="Полученные строки журнала"></div><div class="logs-foot"><p id="log-count" class="muted"></p><p id="freshness" class="status">Загрузка…</p></div></section>`;
  let epoch='',seq=0,items=[],clearedAt=0,initialized=false,levelEdited=false,resetRequested=false,logGeneration=0;
  function accepts(item){const filter=$('#log-filter').value,q=$('#log-search').value.toLocaleLowerCase();return (filter==='all'||item.type===filter||filter==='warning'&&item.type==='error')&&`${item.payload} ${item.type}`.toLocaleLowerCase().includes(q);}
  function logNode(item){const node=document.createElement('div');node.className='log-line';node.dataset.seq=String(item.seq);node.dataset.level=item.type;const stamp=document.createElement('time');stamp.dateTime=new Date(item.ts*1000).toISOString();stamp.textContent=`${time(item.ts)} `;const text=document.createElement('span');text.textContent=`[${item.type}] ${item.payload}`;node.append(stamp,text);return node;}
  function render(reset=false) {
    const area=$('#logs'),scroll=area.scrollTop,areaTop=area.getBoundingClientRect().top;
    const anchor=[...area.children].find(node=>node.getBoundingClientRect().bottom>areaTop);
    const anchorSeq=anchor?.dataset.seq,anchorTop=anchor?.getBoundingClientRect().top;
    const visible=items.filter(accepts);
    if(reset)area.replaceChildren();
    const present=new Set([...area.children].map(node=>Number(node.dataset.seq)));
    for(const item of visible)if(!present.has(item.seq))area.append(logNode(item));
    const current=new Set(visible.map(item=>item.seq));
    for(const node of [...area.children])if(!current.has(Number(node.dataset.seq)))node.remove();
    $('#log-count').textContent=`${visible.length} строк на экране · ${items.length} в локальном буфере (tail: 500, live: 1000)`;
    if($('#follow-logs').checked)area.scrollTop=area.scrollHeight;
    else {
      const retained=anchorSeq?[...area.children].find(node=>node.dataset.seq===anchorSeq):null;
      area.scrollTop=retained?scroll+retained.getBoundingClientRect().top-anchorTop:scroll;
    }
  }
  const poll=scopedPoll(async signal=>{try{
    const mode=$('#log-mode').value,generation=logGeneration;
    const data=await request(`/api/logs?mode=${mode}&level=all&limit=500&since=${resetRequested?0:seq}&epoch=${encodeURIComponent(epoch)}`,{signal});
    if(signal.aborted||generation!==logGeneration)return;
    const changed=initialized&&epoch!==data.epoch;if(changed){items=[];seq=0;clearedAt=0;$('#logs').replaceChildren();}
    epoch=data.epoch||'';initialized=true;if(resetRequested){items=[];seq=0;clearedAt=0;resetRequested=false;$('#logs').replaceChildren();}
    if(mode==='tail')items=(data.items||[]).filter(item=>item.seq>clearedAt);
    else {
      const seen=new Set(items.map(item=>item.seq));
      for(const item of data.items||[])if(item.seq>clearedAt&&!seen.has(item.seq)){items.push(item);seen.add(item.seq);}
    }
    seq=Number(data.seq)||0;if(items.length>1000)items.splice(0,items.length-1000);if(!levelEdited&&['debug','info','warning','error'].includes(data.level))$('#log-level').value=data.level;render();freshness($('#freshness'),changed?'Поток перезапущен, курсор сброшен':`сбор: ${data.level||'неизвестен'}`);
  }catch(error){failed($('#freshness'),error);}},1500);
  $('#log-level').addEventListener('change',()=>{levelEdited=true;});$('#set-log-level').addEventListener('click',event=>action(event.currentTarget,async()=>{const data=await post('/api/logs/level',{level:$('#log-level').value});levelEdited=false;status($('#freshness'),`Уровень сбора установлен: ${data.level}`,'success');},$('#freshness')));
  $('#refresh').addEventListener('click',()=>poll.refresh());
  $('#log-mode').addEventListener('change',()=>{logGeneration++;resetRequested=true;poll.refresh();});
  for(const id of ['log-search','log-filter'])$('#'+id).addEventListener(id==='log-search'?'input':'change',()=>render(true));
  $('#clear-logs').addEventListener('click',()=>{items=[];clearedAt=seq;$('#logs').replaceChildren();render();status($('#freshness'),'Экран очищен. Новые строки появятся после текущего курсора.');});$('#follow-logs').addEventListener('change',()=>{if($('#follow-logs').checked)$('#logs').scrollTop=$('#logs').scrollHeight;});
}
export async function mount(){const page=location.pathname.split('/')[2];if(page==='traffic')return traffic();if(page==='connections')return connections();if(page==='logs')return logs();throw new Error('Страница мониторинга не найдена.');}
