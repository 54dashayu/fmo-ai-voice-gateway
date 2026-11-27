'use strict';
const $=id=>document.getElementById(id);
const labels={save_speech:'保存话术',queued:'等待检查',playing:'正在播放',completed:'发送完成',failed:'失败 / 中止',cancelled:'已停止',skipped:'已跳过',interrupted:'重启中断',once:'单次',daily:'每天',weekly:'每周',import:'导入',enable:'功能开关',restart:'服务重启',plan_created:'创建计划',toggle_plan:'计划开关',delete_plan:'删除计划',asset_deleted:'删除音频',stop:'停止',emergency_stop:'紧急停止',skipped_schedule:'跳过计划'};
const stamp=n=>new Date(n*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
let speechLoaded=false,speechDirty=false;
let cacheSelected=new Set();
let state={},loading=false,selectedAssets=[],selectedLocal=new Set(),selectedCatalog=new Map();
let catalogOffset=0,catalogTotal=0,catalogBusy=false,catalogItems=[],batchBusy=false,batchCancel=false;
function element(tag,text){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node}
function button(text,callback){const b=element('button',text);b.type='button';b.onclick=callback;return b}
function checkbox(text,checked,onchange){const label=element('label');label.className='check-row';const input=element('input');input.type='checkbox';input.checked=checked;input.onchange=()=>onchange(input.checked);label.append(input,element('span',text));return label}
function source(id){return(state.assets||[]).find(a=>a.id===id)?.source||id}
async function request(body){const r=await fetch('api/audio',{method:'POST',headers:{'Content-Type':'application/json','X-FMO-Admin':'1','X-FMO-Audio-Request':'1'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'操作失败');return d}
async function load(){if(loading)return;loading=true;try{const r=await fetch('api/audio',{headers:{'X-FMO-Admin':'1'},cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'读取失败');state=d;if(!speechLoaded){if(!speechDirty&&d.saved_speech){$('opening').value=d.saved_speech.opening_text;$('closing').value=d.saved_speech.closing_text;$('speech-status').textContent='已载入保存的话术';}speechLoaded=true;}render()}catch(e){$('message').textContent=e.message}finally{loading=false}}
function feedback(text){$('message').textContent=text;$('message').scrollIntoView({block:'center',behavior:'smooth'})}
async function send(body){
 if(batchBusy&&['play','plan'].includes(body.action)){feedback('请等待批量导入完成后，再生成或播放计划');return false}
 try{
  await request(body);await load();
  feedback(body.action==='plan'?`播放计划已生成：${body.assets.length} 段音频，${labels[body.mode]} ${body.time}（北京时间）。可在上方计划表中查看。`:body.action==='play'?'播放请求已提交，请查看最近结果':'操作已保存');
  if(body.action==='plan')$('plans').scrollIntoView({block:'center',behavior:'smooth'});
  return true;
 }catch(e){
  feedback((body.action==='plan'?'计划未生成：':'')+e.message);
  if(body.action==='plan'&&e.message.includes('2 分钟'))$('time').focus({preventScroll:true});
  return false;
 }
}

function render(){
 $('sstv-level-current').textContent=`当前 SSTV 图片正文：${state.sstv_gain_db??-24} dB（下次活动生效）`;
 if(document.activeElement!==$('sstv-level'))$('sstv-level').value=state.sstv_gain_db??-24;
 $('gate').textContent=state.transmit_gate?'服务发射安全门：已开启':'服务发射安全门：关闭（管理和导入可用，播放请求不会发射）';
 if(state.scheduler_error)$('message').textContent=state.scheduler_error;
 $('state').textContent=`音频播放：${state.enabled?'已启用':'已禁用'} · 时区：北京时间${state.exclusive?' · 活动独占中：呼入与 AI 回复已屏蔽':''}`;
 $('enable').textContent=state.enabled?'已启用 ✓':'未启用 · 点击启用';
 $('enable').classList.toggle('audio-enabled',!!state.enabled);
 $('enable').classList.toggle('audio-disabled',!state.enabled);
 $('enable').setAttribute('aria-pressed',String(!!state.enabled));
 $('enable').title=state.enabled?(state.transmit_gate?'音频功能已启用，按计划执行':'音频功能已启用，但发射安全门关闭'):'点击启用音频功能';
 const chimeChoice=$('chime').value;
 $('chime').replaceChildren(new Option('默认叮咚声','default'),new Option('关闭提示音','off'),...(state.assets||[]).filter(a=>a.duration<=2).map(a=>new Option(a.source,a.id)));
 $('chime').value=[...$('chime').options].some(o=>o.value===chimeChoice)?chimeChoice:'default';
 renderLocal();renderAssets();renderCache();renderPlayback();
 const recentJob=p=>(state.jobs||[]).find(j=>j.plan===p.id);
 const isHistory=p=>p.mode==='once'&&!p.enabled&&p.next<=Date.now()/1000&&!['queued','playing'].includes(recentJob(p)?.status);
 const planRow=(p,history=false)=>{
  const tr=element('tr'),info=element('td'),ids=p.assets||[p.asset];
  info.append(element('strong',`${p.gallery?'NAS随机 '+p.gallery.count+' 张':ids.length+' 段'} · ${labels[p.mode]}${p.mode==='weekly'?' 星期'+['一','二','三','四','五','六','日'][p.weekday]:''}`),element('p',p.gallery?`${p.gallery.name||'NAS图库'} · ${p.gallery.recursive?'包含子目录':'仅当前目录'} · 每次重新随机`:ids.map((id,i)=>`${i+1}. ${source(id)}`).join(' → ')),element('p',`${p.first_gap?'首段准备及段间':'旧计划：仅段间'} ${p.interval_seconds||0} 秒 · 开场：${p.opening_text||'不播报'} · 段间话术：${p.transition_texts?.length?'自动播报':'无'} · 结束：${p.closing_text||'不播报'}`));
  tr.append(info,element('td',stamp(p.next)),element('td',history?(labels[recentJob(p)?.status]||'已结束 / 已过期'):(['queued','playing'].includes(recentJob(p)?.status)?labels[recentJob(p).status]:p.enabled?'启用':'禁用')));
  const actions=element('td');if(!history)actions.append(button(p.enabled?'禁用':'启用',()=>send({action:'toggle_plan',id:p.id,enabled:!p.enabled})));actions.append(button('删除',()=>send({action:'delete_plan',id:p.id})));tr.append(actions);return tr;
 };
 const plans=state.plans||[];
 $('plans').replaceChildren(...plans.filter(p=>!isHistory(p)).map(p=>planRow(p)));
 $('plans-empty').hidden=plans.some(p=>!isHistory(p));
 $('history-plans').replaceChildren(...plans.filter(isHistory).sort((a,b)=>b.next-a.next).map(p=>planRow(p,true)));
 $('history-empty').hidden=plans.some(isHistory);
 $('audit').replaceChildren(...(state.audit||[]).map(a=>element('div',`${stamp(a.at)} · ${labels[a.event]||a.event} · ${a.detail}`)));
 for(const n of [...$('audit').children])n.className='entry';
}
function renderLocal(){
 const names=state.local_files||[];selectedLocal=new Set([...selectedLocal].filter(n=>names.includes(n)));
 $('local').replaceChildren(...names.map(name=>checkbox(name,selectedLocal.has(name),checked=>{if(checked)selectedLocal.add(name);else selectedLocal.delete(name)})));
 if(!names.length)$('local').append(element('p','音频收件目录暂无文件'));
}
function assetTypeEditor(a){
 const box=element('details');box.append(element('summary','音频类型设置（不属于本次选图）'));
 const select=element('select');select.setAttribute('aria-label',a.source+' 音频类型');
 for(const [value,label] of [['sstv','SSTV 图片音频（使用衰减）'],['audio','普通音频（保持原电平）']]){const option=element('option',label);option.value=value;select.append(option)}
 select.value=(a.is_sstv??!!(a.source_id&&a.source.startsWith('SSTV: ')))?'sstv':'audio';
 const status=element('span');status.setAttribute('role','status');
 select.onchange=async()=>{select.disabled=true;try{await request({action:'set_sstv_type',asset:a.id,is_sstv:select.value==='sstv'});a.is_sstv=select.value==='sstv';status.textContent='类型已保存'}catch(e){status.textContent=e.message}finally{select.disabled=false}};
 box.append(select,status);return box;
}
function renderAssets(){
 if(document.activeElement?.tagName==='SELECT'&&$('assets').contains(document.activeElement))return;
 const assets=state.assets||[];selectedAssets=selectedAssets.filter(id=>assets.some(a=>a.id===id));
 $('assets').replaceChildren(...assets.map(a=>{const row=element('div');row.append(checkbox(`${a.source} · ${a.duration} 秒`,selectedAssets.includes(a.id),checked=>{if(checked)selectedAssets.push(a.id);else selectedAssets=selectedAssets.filter(id=>id!==a.id);renderAssets()}),assetTypeEditor(a));return row}));
 if(!assets.length)$('assets').append(element('p','请先从声音库、本地文件或 URL 导入音频'));
 renderProgram();
}
function renderCache(){
 const assets=state.assets||[];cacheSelected=new Set([...cacheSelected].filter(id=>assets.some(a=>a.id===id)));
 $('cache-assets').replaceChildren(...assets.map(a=>checkbox(a.source,cacheSelected.has(a.id),v=>{if(v)cacheSelected.add(a.id);else cacheSelected.delete(a.id)})));
}
function renderProgram(){
 if($('gallery-mode')?.value==='nas'){$('playlist').replaceChildren();$('transition-preview').replaceChildren();$('program-summary').textContent=`NAS 每次随机 ${$('gallery-count').value} 张；Robot36 约37秒/张，另加首段准备、间隔及话术。`;return}
 $('transition-preview').replaceChildren(...selectedAssets.slice(1).map((id,i)=>element('p',`第 ${i+1} 段后：本段图片发送完毕，间隔 ${$('interval').value} 秒后继续发送，请各位友台保持接收。`)));
 $('playlist').replaceChildren(...selectedAssets.map((id,i)=>{const li=element('li');li.append(element('span',source(id)));const up=button('上移',()=>move(i,-1)),down=button('下移',()=>move(i,1));up.disabled=i===0;down.disabled=i===selectedAssets.length-1;li.append(up,down);return li}));
 const seconds=selectedAssets.reduce((sum,id)=>sum+((state.assets||[]).find(a=>a.id===id)?.duration||0),0)+selectedAssets.length*Number($('interval').value||0);
 $('program-summary').textContent=`已选 ${selectedAssets.length} 段 · 音频、首段准备与段间间隔合计约 ${Math.ceil(seconds)} 秒${$('opening').value.trim()?'，另加 AI 开场':''}；另加段间提示${$('closing').value.trim()?'和结束话术':''}（每条最多 45 秒）及结束话术前的 1 秒停顿；提示音计入间隔，不额外延长。勾选顺序即播放顺序，可上移/下移调整。`;
}
function move(index,delta){[selectedAssets[index],selectedAssets[index+delta]]=[selectedAssets[index+delta],selectedAssets[index]];renderProgram()}
function program(){return{...($('gallery-mode').value==='nas'?{gallery:{directory:$('gallery-directory').value,count:Number($('gallery-count').value),recursive:$('gallery-recursive').checked}}:{}),assets:[...selectedAssets],opening_text:$('opening').value.trim(),closing_text:$('closing').value.trim(),interval_seconds:Number($('interval').value),chime_asset:$('chime').value}}
function alreadyImported(task){return(state.assets||[]).find(a=>task.action==='import_sstv'?a.source_id===task.id:task.kind==='local'&&a.source==='本地: '+task.value)}
async function importBatch(tasks){
 if(batchBusy){$('message').textContent='正在批量导入，请等待或停止后续导入';return}
 if(!tasks.length){$('message').textContent='请先勾选要导入的音频';return}
 batchBusy=true;batchCancel=false;$('play').disabled=true;$('plan-save').disabled=true;$('batch-cancel').hidden=false;$('batch-results').replaceChildren();
 let ok=0,skipped=0,failed=0;
 try{
  for(const [index,task] of tasks.entries()){
   if(batchCancel)break;
   const name=task.label||task.value||task.id;$('batch-status').textContent=`正在导入 ${index+1}/${tasks.length}：${name}`;
   const existing=alreadyImported(task);
   if(existing){skipped++;if(!selectedAssets.includes(existing.id))selectedAssets.push(existing.id);$('batch-results').append(element('p',`已跳过：${name}（已导入）`));continue}
   try{const result=task.file?await uploadFile(task.file):await request(task);ok++;if(result.asset){state.assets=[...(state.assets||[]),result.asset];selectedAssets.push(result.asset.id)}$('batch-results').append(element('p',`成功：${name}`));renderAssets()}
   catch(e){failed++;$('batch-results').append(element('p',`失败：${name} — ${e.message}`))}
  }
  const summary=`${batchCancel?'已停止后续导入':'批量导入完成'}：成功 ${ok}，已导入跳过 ${skipped}，失败 ${failed}。`;
  $('batch-status').textContent=summary;$('message').textContent=summary;await load();
 }finally{batchBusy=false;$('play').disabled=false;$('plan-save').disabled=false;$('batch-cancel').hidden=true;renderAssets()}
}
$('batch-cancel').onclick=()=>{batchCancel=true;$('batch-status').textContent='当前文件处理完后停止后续导入'};
const localTasks=names=>names.map(value=>({action:'import',kind:'local',value,label:value}));
$('local-select').onclick=()=>{selectedLocal=new Set(state.local_files||[]);renderLocal()};$('local-clear').onclick=()=>{selectedLocal.clear();renderLocal()};$('local-all').onclick=()=>importBatch(localTasks(state.local_files||[]));
$('import-form').onsubmit=async e=>{e.preventDefault();if($('kind').value==='upload')await importBatch(uploadTasks([...chosenUploads]));else if($('kind').value==='local')await importBatch(localTasks([...selectedLocal]));else await importBatch([{action:'import',kind:'url',value:$('url').value,label:'URL 音频'}])};
$('kind').onchange=()=>{$('upload-label').hidden=$('kind').value!=='upload';$('local-label').hidden=$('kind').value!=='local';$('url-label').hidden=$('kind').value!=='url'};
$('asset-select').onclick=()=>{selectedAssets=(state.assets||[]).map(a=>a.id);renderAssets()};$('asset-clear').onclick=()=>{selectedAssets=[];renderAssets()};
for(const id of ['closing','opening'])$(id).oninput=()=>{speechDirty=true;$('speech-status').textContent='话术已修改，尚未保存';renderProgram()};$('interval').oninput=renderProgram;
$('mode').onchange=()=>{$('date-label').hidden=$('mode').value!=='once';$('weekday-label').hidden=$('mode').value!=='weekly'};
$('plan-form').onsubmit=e=>{
 e.preventDefault();
 if(!galleryReady())return;
 if($('gallery-mode').value!=='nas'&&!selectedAssets.length){feedback('计划未生成：请先勾选至少一段已导入音频');return}
 const mode=$('mode').value;
 if(!$('time').value||(mode==='once'&&!$('date').value)){feedback('计划未生成：请选择执行时间和单次执行日期');return}
 send({action:'plan',...program(),mode,time:$('time').value,date:$('date').value,weekday:Number($('weekday').value)});
};
$('enable').onclick=()=>send({action:'enable',enabled:true});$('disable').onclick=()=>send({action:'enable',enabled:false});$('stop').onclick=()=>send({action:'stop'});$('emergency').onclick=()=>send({action:'emergency_stop'});$('play').onclick=()=>{if(galleryReady())send({action:'play',...program()})};
$('delete-asset').onclick=async()=>{if(!cacheSelected.size){feedback('请在缓存清理区勾选素材');return}for(const asset of [...cacheSelected]){if(!await send({action:'delete_asset',asset}))break}};
$('refresh').onclick=load;
async function catalogPage(offset,query,refresh=false){const r=await fetch('api/audio/catalog?'+new URLSearchParams({offset:String(offset),q:query,refresh:refresh?'1':'0'}),{headers:{'X-FMO-Admin':'1'}});const d=await r.json();if(!r.ok)throw Error(d.error||'声音库读取失败');return d}
function renderCatalog(){
 $('catalog').replaceChildren(...catalogItems.map(item=>{
  const card=element('article');card.className='catalog-card';const img=element('img');img.src=item.imageUrl;img.alt=item.title;img.loading='lazy';const info=element('div');
  info.append(checkbox(item.title,selectedCatalog.has(item.id),checked=>{if(checked)selectedCatalog.set(item.id,item);else selectedCatalog.delete(item.id)}),element('p',`${item.mode} · ${item.duration} 秒`),button('导入此音频',()=>importBatch([{action:'import_sstv',id:item.id,label:item.title}])));
  card.append(img,info);return card;
 }));
 $('catalog-prev').disabled=catalogOffset===0;$('catalog-next').disabled=catalogOffset+20>=catalogTotal;
}
const catalogCacheKey='fmo-sstv-catalog-v1';
let catalogRetry=null;
let catalogPages={};try{catalogPages=JSON.parse(localStorage.getItem(catalogCacheKey)||'{}')}catch(e){}
function showCatalogPage(d,cached=false){catalogTotal=d.total;catalogItems=d.items;$('catalog-status').textContent=`共 ${d.total} 条 · 第 ${Math.floor(catalogOffset/20)+1} 页${cached?' · 已保存的目录':''}`;renderCatalog()}
async function loadCatalog(force=false){
 if(catalogBusy||batchBusy)return;catalogBusy=true;$('catalog-reload').disabled=true;
 clearTimeout(catalogRetry);catalogRetry=null;
 if(force){catalogPages={};try{localStorage.removeItem(catalogCacheKey)}catch(e){}}
 const cached=catalogPages[catalogOffset];if(cached)showCatalogPage(cached,true);else $('catalog-status').textContent='声音库正在加载…';
 try{const d=await catalogPage(catalogOffset,'',force);catalogPages[catalogOffset]=d;try{localStorage.setItem(catalogCacheKey,JSON.stringify(catalogPages))}catch(e){}showCatalogPage(d)}
 catch(e){$('catalog-status').textContent=cached?'正在展示已保存的声音库；暂时无法更新目录。':'声音库暂不可用，正在等待重试。';if(!cached)catalogRetry=setTimeout(()=>{catalogRetry=null;loadCatalog()},15000)}
 finally{catalogBusy=false;$('catalog-reload').disabled=false}
}
$('catalog-prev').onclick=()=>{if(catalogBusy||batchBusy)return;catalogOffset=Math.max(0,catalogOffset-20);loadCatalog()};$('catalog-next').onclick=()=>{if(catalogBusy||batchBusy)return;if(catalogOffset+20<catalogTotal){catalogOffset+=20;loadCatalog()}};
$('catalog-select').onclick=()=>{for(const item of catalogItems)selectedCatalog.set(item.id,item);renderCatalog()};$('catalog-clear').onclick=()=>{selectedCatalog.clear();renderCatalog()};
$('catalog-import').onclick=()=>importBatch([...selectedCatalog.values()].map(item=>({action:'import_sstv',id:item.id,label:item.title})));
$('catalog-all').onclick=async()=>{
 if(batchBusy||catalogBusy){$('message').textContent='请等待当前导入或目录读取完成';return}
 catalogBusy=true;const query='';const items=new Map();
 try{for(let offset=0;offset<=10000;offset+=20){$('catalog-status').textContent=`正在收集全部素材：${items.size} 条`;const page=await catalogPage(offset,query);for(const item of page.items)items.set(item.id,item);if(offset+20>=page.total||!page.items.length)break}$('catalog-status').textContent=`已收集 ${items.size} 条素材，正在批量导入`;await importBatch([...items.values()].map(item=>({action:'import_sstv',id:item.id,label:item.title})));$('catalog-status').textContent=`已处理 ${items.size} 条素材，结果见下方导入记录`}catch(e){$('catalog-status').textContent=e.message}finally{catalogBusy=false}
};
load();setInterval(()=>{if(!document.hidden&&!batchBusy)load()},3000);

let uploadFiles=[],chosenUploads=new Set();
function renderUploads(){$('upload-list').replaceChildren(...uploadFiles.map((f,i)=>checkbox(`${f.name} · ${(f.size/1048576).toFixed(2)} MiB`,chosenUploads.has(i),v=>{if(v)chosenUploads.add(i);else chosenUploads.delete(i)})))}
const uploadTasks=indices=>indices.map(i=>({file:uploadFiles[i],label:uploadFiles[i].name}));
$('upload-files').onchange=()=>{uploadFiles=Array.from($('upload-files').files);chosenUploads=new Set(uploadFiles.map((_,i)=>i));renderUploads()};
$('upload-select').onclick=()=>{chosenUploads=new Set(uploadFiles.map((_,i)=>i));renderUploads()};
$('upload-clear').onclick=()=>{chosenUploads.clear();renderUploads()};
$('upload-all').onclick=()=>importBatch(uploadTasks(uploadFiles.map((_,i)=>i)));
async function uploadFile(file){
 let blob=file,name=file.name,isSstv=false;
 if(/\.(png|jpe?g|webp)$/i.test(name)){
  if(file.size>8*1048576)throw Error('图片不能超过 8 MiB');
  const image=await createImageBitmap(file);
  try{
   if(image.width*image.height>20000000)throw Error('图片不能超过 2000 万像素');
   const canvas=document.createElement('canvas');canvas.width=320;canvas.height=240;const ctx=canvas.getContext('2d');ctx.fillStyle='#000';ctx.fillRect(0,0,320,240);
   const scale=Math.min(320/image.width,240/image.height),w=image.width*scale,h=image.height*scale;ctx.drawImage(image,(320-w)/2,(240-h)/2,w,h);
   isSstv=true;const encoder=await import('./robot36-encoder.js');
   const samples=await encoder.encodeRobot36(ctx.getImageData(0,0,320,240).data,p=>{$('batch-status').textContent=`正在编码 Robot36：${name} · ${Math.round(p*100)}%`});blob=encoder.createMono16Wav(samples);name=name.replace(/\.[^.]+$/,'')+'-Robot36.wav';
  }finally{image.close()}
 }else if(!/\.(wav|mp3|flac|ogg|aac|aiff?)$/i.test(name))throw Error('请选择支持的声音或 JPG、PNG、WebP 图片');
 if(!blob.size||blob.size>32*1048576)throw Error('声音文件不能为空或超过 32 MiB');
 $('batch-status').textContent=`正在上传并转码：${name}`;
 const r=await fetch('api/audio',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-FMO-Admin':'1','X-FMO-Audio-Request':'1','X-FMO-Filename':encodeURIComponent(name),'X-FMO-Media-Type':isSstv?'sstv':'audio'},body:blob});
 const d=await r.json();if(!r.ok)throw Error(d.error||'上传失败');return d;
}

loadCatalog();

$('chime-upload').onclick=async()=>{
 const file=$('chime-file').files[0];if(!file){$('chime-status').textContent='请先选择提示音文件';return}
 if(batchBusy){$('chime-status').textContent='请等待当前导入完成';return}
 batchBusy=true;$('chime-upload').disabled=true;$('play').disabled=true;$('plan-save').disabled=true;
 try{
  if(!/\.(wav|mp3|flac|ogg|aac|aiff?)$/i.test(file.name))throw Error('请选择声音文件');
  const context=new AudioContext();try{const decoded=await context.decodeAudioData(await file.arrayBuffer());if(decoded.duration>2)throw Error('提示音最长 2 秒，请先剪短后上传')}finally{await context.close()}
  const result=await uploadFile(file);await load();
  if(result.asset.duration>2)throw Error('提示音最长 2 秒；该文件已作为普通音频导入');
  $('chime').value=result.asset.id;$('chime-status').textContent=`已选择：${file.name} · ${result.asset.duration} 秒`;
 }catch(e){$('chime-status').textContent=e.message}
 finally{batchBusy=false;$('chime-upload').disabled=false;$('play').disabled=false;$('plan-save').disabled=false}
};

function renderPlayback(){
 const job=(state.jobs||[]).find(j=>['queued','playing'].includes(j.status))||(state.jobs||[])[0];
 const panel=$('playback-progress');panel.replaceChildren();
 if(!job){panel.append(element('p','尚未开始播放。计划启动或立即播放后，这里将显示分段进度。'));return}
 const running=['queued','playing'].includes(job.status),meta=job._progress;
 panel.append(element('strong',`${labels[job.status]||job.status} · ${job.result}`));
 if(!meta?.phases?.length){if(running){const bar=element('progress');bar.setAttribute('aria-label','正在准备播放');panel.append(bar)}return}
 const phases=meta.phases,total=phases.reduce((s,p)=>s+p.duration,0),index=meta.index;
 const elapsed=running&&index>=0?Math.max(0,Date.now()/1000-meta.started):0;
 const completed=job.status==='completed';const track=element('div');track.className='playback-track';track.setAttribute('aria-label','播放分段时间线');
 phases.forEach((phase,i)=>{
  const segment=element('div');segment.className=`playback-segment ${phase.kind}${i===index&&running?' current':''}`;segment.style.flexGrow=String(Math.max(.5,phase.duration));
  const percent=completed||i<index?100:i===index&&running?Math.min(99,elapsed/Math.max(.01,phase.duration)*100):0;
  const fill=element('span');fill.className='segment-fill';fill.style.width=`${percent}%`;segment.append(fill);
  segment.title=`${phase.label} · ${phase.duration.toFixed(1)} 秒`;segment.setAttribute('aria-label',segment.title);track.append(segment);
 });panel.append(track);
 const legend=element('div');legend.className='playback-legend';for(const [kind,text] of [['voice','开场 / 段间 / 结束话术'],['sstv','SSTV 图片'],['gap','间隔等待'],['cue','叮咚 / 自定义提示音']]){const item=element('span',text);item.className=kind;legend.append(item)}panel.append(legend);
 const steps=element('div');steps.className='playback-steps';phases.forEach((phase,i)=>{const item=element('span',`${i+1}. ${phase.label.replace('正在播放','')}`);item.className=`${phase.kind}${i===index?' current':''}`;steps.append(item)});panel.append(steps);
 const current=phases[index],done=completed?total:phases.slice(0,Math.max(0,index)).reduce((s,p)=>s+p.duration,0)+Math.min(elapsed,current?.duration||0);
 panel.append(element('p',running?`整体预计 ${Math.floor(done)} / ${Math.ceil(total)} 秒 · 当前段剩余约 ${Math.max(0,Math.ceil((current?.duration||0)-elapsed))} 秒${elapsed>(current?.duration||0)?' · 等待服务器确认下一阶段':''}`:completed?'全部阶段已完成':`已停止：${job.result}`));
}
setInterval(()=>{if(!document.hidden)renderPlayback()},1000);

$('save-speech').onclick=async()=>{
 const b=$('save-speech');b.disabled=true;
 const opening=$('opening').value,closing=$('closing').value;
 try{await request({action:'save_speech',opening_text:opening,closing_text:closing});
 speechLoaded=true;const changed=$('opening').value!==opening||$('closing').value!==closing;speechDirty=changed;
 $('speech-status').textContent=changed?'已保存提交的话术；之后的修改尚未保存':'话术已保存，下次打开页面将自动填入';
 }catch(e){$('speech-status').textContent='保存失败：'+e.message}finally{b.disabled=false}
};

let galleryLoaded=false,galleryBusy=false,galleryDraftVersion=0;
function galleryReady(){if($('gallery-mode').value==='nas'&&!$('gallery-directory').value){feedback('请先读取并选择有效的 NAS 图库目录');return false}return true}
$('gallery-mode').onchange=()=>{$('gallery-options').hidden=$('gallery-mode').value!=='nas';$('fixed-assets').hidden=$('gallery-mode').value==='nas';renderProgram();if($('gallery-mode').value==='nas'&&!galleryLoaded)loadGallery();};
$('gallery-count').oninput=renderProgram;
async function loadGallery(){
 if(galleryBusy)return;galleryBusy=true;const draftVersion=galleryDraftVersion;
 const previous=$('gallery-directory').value;const button=$('gallery-refresh');button.disabled=true;
 try{
  const r=await fetch('api/audio/gallery',{headers:{'X-FMO-Admin':'1'}});const d=await r.json();if(!r.ok)throw Error(d.error||'图库读取失败');
  const rows=d.directories||[];$('gallery-directory').replaceChildren(...rows.map(x=>{const o=document.createElement('option');o.value=x.id;o.textContent=(x.path||'全部图库')+' · '+x.count+' 张（含子目录 '+x.recursive_count+' 张）';return o}));
  if(previous)$('gallery-directory').value=previous;
  if(draftVersion!==galleryDraftVersion)$('gallery-directory').value='';
  galleryLoaded=true;
  $('gallery-status').textContent=!rows.length?'图库暂无目录':previous&&!$('gallery-directory').value?'原选目录已不存在，请重新选择目录':'目录已更新；每次重新选图，图片不足或损坏时本场失败。';
 }catch(e){galleryLoaded=false;$('gallery-directory').replaceChildren();$('gallery-status').textContent=e.message}finally{button.disabled=false;galleryBusy=false}
}
$('gallery-refresh').onclick=loadGallery;

$('sstv-level-save').onclick=()=>send({action:'set_sstv_gain',db:Number($('sstv-level').value)});

$('draft-clear').onclick=()=>{selectedAssets=[];galleryDraftVersion++;$('gallery-directory').value='';$('gallery-count').value='3';$('gallery-recursive').checked=false;renderAssets();$('draft-status').textContent='已清空本次选择（0 张）。素材仍保留在库中，类型设置与已保存计划不变。';};
$('catalog-reload').onclick=()=>loadCatalog(true);
try{$('catalog-panel').open=localStorage.getItem('fmo-catalog-expanded')!=='false'}catch(e){}
$('catalog-panel').ontoggle=()=>{try{localStorage.setItem('fmo-catalog-expanded',String($('catalog-panel').open))}catch(e){}};

$('catalog-panel').querySelector('summary').addEventListener('click',()=>{try{localStorage.setItem('fmo-catalog-expanded',String(!$('catalog-panel').open))}catch(e){}});
