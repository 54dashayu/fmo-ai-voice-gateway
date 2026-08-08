const $=id=>document.getElementById(id);
const escapeHtml=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state=(id,on)=>{const el=$(id);el.textContent=on?'开启':'关闭';el.className=on?'on':'off'};
const age=seconds=>{if(seconds<60)return `${seconds} 秒`;if(seconds<3600)return `${Math.floor(seconds/60)} 分钟`;if(seconds<86400)return `${Math.floor(seconds/3600)} 小时`;return `${Math.floor(seconds/86400)} 天`};
let callsignRows=[];
let callsignSort={key:'last_at',direction:'desc'};
let controls={auto_reply:true,hourly_announcement:true};
let knowledgePath='';
let blacklistValues=[];
let blacklistLoaded=false;
let gatewayConfigLoading=false;
let providerConfigLoading=false;
const numericCallsignColumns=new Set(['heard','answered','blocked','last_at']);
const gatewayDefaultConfig={mqtt_servers:[],providers:{chat:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'qwen-plus',api_key_env:'DASHSCOPE_API_KEY',has_api_key:false},embedding:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'text-embedding-v4',api_key_env:'DASHSCOPE_API_KEY',has_api_key:false},asr:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'qwen3-asr-flash',api_key_env:'DASHSCOPE_ASR_API_KEY',has_api_key:false},tts:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'cosyvoice-v3-flash',api_key_env:'DASHSCOPE_TTS_API_KEY',has_api_key:false}}};
let gatewayConfigCache = structuredClone(gatewayDefaultConfig);
const providerConfigFields=['chat','embedding','asr','tts'];
const providerDefaults={chat:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'qwen-plus',api_key_env:'DASHSCOPE_API_KEY'},embedding:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'text-embedding-v4',api_key_env:'DASHSCOPE_API_KEY'},asr:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'qwen3-asr-flash',api_key_env:'DASHSCOPE_ASR_API_KEY'},tts:{provider:'dashscope',base_url:'https://dashscope.aliyuncs.com/compatible-mode/v1',model:'cosyvoice-v3-flash',api_key_env:'DASHSCOPE_TTS_API_KEY'}};
function staticPageLikely(){
  const path = String(window.location.pathname || '');
  return window.location.protocol === 'file:' || (window.location.port === '8088' && (!path || path === '/' || path.endsWith('.html')));
}

function toIntOrDefault(value, fallback){
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function boolFromValue(value, fallback=false){
  if(typeof value === 'boolean') return value;
  if(value === 'true' || value === '1') return true;
  if(value === 'false' || value === '0') return false;
  return fallback;
}

function renderGatewayForm(config){
  const servers = (config && Array.isArray(config.mqtt_servers)) ? config.mqtt_servers : [];
  const primary = servers.find(x=>x && x.enabled !== false) || servers[0] || {};
  $('gw-name').value = String(primary.name || 'primary');
  $('gw-host').value = String(primary.host || '127.0.0.1');
  $('gw-port').value = String(toIntOrDefault(primary.port, 1884));
  $('gw-topic').value = String(primary.topic || 'FMO/RAW');
  $('gw-client-id').value = String(primary.client_id || 'FMO-AI-MONITOR-CHANGE-ME');
  $('gw-username').value = primary.username ? String(primary.username) : '';
  $('gw-password').value = '';
  const tls = (primary.tls && typeof primary.tls === 'object') ? primary.tls : {};
  $('gw-enabled').checked = primary.enabled !== false;
  $('gw-tls-enabled').checked = boolFromValue(tls.enabled, false);
  $('gw-ca-file').value = tls.ca_file || '';
  $('gw-cert-file').value = tls.cert_file || '';
  $('gw-key-file').value = tls.key_file || '';
}

function collectGatewayConfigFromForm(){
  const tls = {
    enabled: $('gw-tls-enabled').checked,
    ca_file: ($('gw-ca-file').value || '').trim() || null,
    cert_file: ($('gw-cert-file').value || '').trim() || null,
    key_file: ($('gw-key-file').value || '').trim() || null,
  };
  const mqttServer = {
    name: ($('gw-name').value || 'primary').trim(),
    host: ($('gw-host').value || '127.0.0.1').trim(),
    port: toIntOrDefault($('gw-port').value, 1884),
    topic: ($('gw-topic').value || 'FMO/RAW').trim(),
    client_id: ($('gw-client-id').value || 'FMO-AI-MONITOR-CHANGE-ME').trim(),
    username: ($('gw-username').value || '').trim(),
    password: $('gw-password').value || '',
    enabled: $('gw-enabled').checked,
    tls,
  };
  const merged = structuredClone(gatewayConfigCache.providers ? gatewayConfigCache : gatewayDefaultConfig);
  merged.mqtt_servers = [mqttServer];
  return merged;
}

function detectGatewayConfigCandidates(resource){
  const normalized = String(resource || '').replace(/^\/+|\/+$/g, '');
  if (!normalized) return [];
  const candidates = [];
  const add = v => { if (v && !candidates.includes(v)) candidates.push(v); };
  const basePath = window.location.pathname.startsWith('/ai/') ? '/ai' : '';

  add(`${basePath}/${normalized}`);
  add(`/ai/${normalized}`);
  add(`api/${normalized}`);
  add(`ai/api/${normalized}`);
  add(`/ai/api/${normalized}`);
  add(`admin/${normalized}`);
  add(`/admin/${normalized}`);
  add(`/api/${normalized}`);

  return candidates;
}
function renderBlacklistDialog(){
  $('blacklist-dialog-count').textContent=blacklistValues.length;
  $('blacklist-list').innerHTML=blacklistValues.length?blacklistValues.map(callsign=>`<div class="blacklist-item"><strong>${escapeHtml(callsign)}</strong><button class="blacklist-remove-button" type="button" data-callsign="${escapeHtml(callsign)}">移出黑名单</button></div>`).join(''):'<div class="empty-state">黑名单为空</div>';
  $('blacklist-list').querySelectorAll('.blacklist-remove-button').forEach(button=>button.addEventListener('click',()=>removeCallsignFromBlacklist(button)));
}
function renderCallsignStats(){
  const {key,direction}=callsignSort,sign=direction==='asc'?1:-1;
  const rows=callsignRows.slice().sort((a,b)=>{
    let result;
    if(numericCallsignColumns.has(key))result=(Number(a[key])||0)-(Number(b[key])||0);
    else result=String(a[key]||'').localeCompare(String(b[key]||''),'zh-CN',{numeric:true,sensitivity:'base'});
    if(result===0&&key!=='callsign')result=String(a.callsign||'').localeCompare(String(b.callsign||''),'zh-CN',{numeric:true,sensitivity:'base'});
    return result*sign;
  }).slice(0,30);
  $('callsign-stats').innerHTML=rows.length?rows.map(x=>{const blocked=blacklistValues.includes(String(x.callsign||'').toUpperCase());return `<tr><td>${escapeHtml(x.callsign)}</td><td>${x.heard||0}</td><td>${x.answered||0}</td><td>${x.blocked||0}</td><td>${x.last_at?new Date(x.last_at*1000).toLocaleString('zh-CN',{hour12:false}):'—'}</td><td>${blocked?'<span class="blacklisted-label">已在黑名单</span>':`<button class="row-blacklist-button" type="button" data-callsign="${escapeHtml(x.callsign)}">加入黑名单</button>`}</td></tr>`}).join(''):'<tr><td colspan="6">暂无记录</td></tr>';
  $('callsign-stats').querySelectorAll('.row-blacklist-button').forEach(button=>button.addEventListener('click',()=>addCallsignToBlacklist(button)));
  document.querySelectorAll('.sort-button').forEach(button=>{
    const active=button.dataset.sort===key;
    button.setAttribute('aria-sort',active?(direction==='asc'?'ascending':'descending'):'none');
    button.querySelector('.sort-indicator').textContent=active?(direction==='asc'?'▲':'▼'):'';
  });
}
function changeCallsignSort(key){
  callsignSort=key===callsignSort.key?{key,direction:callsignSort.direction==='asc'?'desc':'asc'}:{key,direction:key==='callsign'?'asc':'desc'};
  renderCallsignStats();
}
async function refresh(){
  try{
    const response=await fetch('api/status',{cache:'no-store'});
    if(!response.ok)throw new Error('status');
    const d=await response.json(),g=d.gateway,p=d.policy;
    $('overall').textContent='运行正常';$('overall').className='pill ok';
    $('gateway').textContent='在线';$('uptime').textContent=`已运行 ${age(Math.max(0,Math.floor(Date.now()/1000)-g.started_at))}`;
    $('nas').textContent=d.nas.ok?'在线':'不可达';$('sections').textContent=(d.nas.sections==null?'切片数未知':`${d.nas.sections} 个知识切片`)+' · 点击管理';
    $('chat-model').textContent=d.models.chat;$('embedding-model').textContent=d.models.embedding;
    $('requests').textContent=g.requests_total;$('chat-count').textContent=g.chat_total;$('knowledge-count').textContent=g.knowledge_total;$('last-result').textContent=g.last_result;
    const w=d.watchdog||{},c=w.checks||{};
    $('heartbeat-public').textContent=c.mqtt_public?.ok?`${c.mqtt_public.latency_ms} ms`:'异常';
    $('heartbeat-emqx').textContent=c.mqtt_local?.ok&&c.emqx_process?.ok?'正常':'异常';
    $('heartbeat-sas').textContent=c.sas_http?.ok&&c.sas_process?.ok?'正常':'异常';
    $('heartbeat-action').textContent=w.last_action||'—';$('heartbeat-interval').textContent=w.interval_seconds||'—';
    state('ai-enabled',p.ai_enabled);$('callsigns').textContent=p.allowed_callsigns_count;state('mqtt',p.mqtt);state('asr',p.asr);state('tts',p.tts);state('ptt',p.ptt);
    const m=d.mqtt||{},b=m.last_burst||null;
    $('mqtt-mode').textContent=m.mode==='automatic'?'自动问答':(m.mode==='control_only'?'仅管理员语音控制':(m.mode==='read_only'?'只读监听':(m.mode||'离线')));
    $('mqtt-bursts').textContent=m.bursts_total||0;
    $('mqtt-last-frames').textContent=b?b.frames:'—';
    $('mqtt-last-duration').textContent=b?`${b.duration_ms} ms`:'—';
    $('mqtt-end-gap').textContent=`${m.end_gap_ms||0} ms`;
    const h=m.hourly_announcement||{};
    $('hourly-enabled').textContent=h.enabled?'开启':'关闭';$('hourly-enabled').className=h.enabled?'on':'off';
    $('hourly-window').textContent=h.enabled?`${String(h.first_hour).padStart(2,'0')}:00–${String(h.last_hour).padStart(2,'0')}:00`:'—';
    $('hourly-idle').textContent=h.idle_seconds||'—';
    const hourlyResults={success:'播报成功',skipped_busy:'频道繁忙，已跳过',failed:'播报失败'};
    $('hourly-last-result').textContent=hourlyResults[h.last_result]||'等待首次播报';
    $('hourly-last-time').textContent=h.last_attempt_at?new Date(h.last_attempt_at*1000).toLocaleString('zh-CN',{hour12:false}):'—';
    controls=m.controls||controls;
    renderControls();
    $('callsign-policy').textContent=m.callsign_policy==='blacklist'?'允许全部，黑名单除外':'允许名单';
    $('blacklist-count').textContent=blacklistLoaded?blacklistValues.length:(m.blacklist_count||0);
    callsignRows=Array.isArray(m.callsign_stats)?m.callsign_stats:[];
    renderCallsignStats();
    $('updated').textContent=new Date().toLocaleString('zh-CN',{hour12:false});
  }catch(e){$('overall').textContent='状态不可达';$('overall').className='pill error'}
}
async function loadBlacklist(){const r=await fetch('api/blacklist',{headers:{'X-FMO-Admin':'1'},cache:'no-store'});if(r.ok){const d=await r.json();blacklistValues=(d.callsigns||[]).map(x=>String(x).toUpperCase());blacklistLoaded=true;$('blacklist-count').textContent=blacklistValues.length;$('blacklist-input').value=blacklistValues.join(', ');renderCallsignStats();renderBlacklistDialog()}}
async function persistBlacklist(callsigns){const r=await fetch('api/blacklist',{method:'POST',headers:{'Content-Type':'application/json','X-FMO-Admin':'1'},body:JSON.stringify({callsigns})}),d=await r.json();if(!r.ok)throw new Error(d.error||'未知错误');blacklistValues=(d.callsigns||[]).map(x=>String(x).toUpperCase());blacklistLoaded=true;$('blacklist-count').textContent=blacklistValues.length;$('blacklist-input').value=blacklistValues.join(', ');renderCallsignStats();renderBlacklistDialog();refresh();return d}
async function saveBlacklist(){const callsigns=$('blacklist-input').value.split(/[,，\s]+/).filter(Boolean);try{await persistBlacklist(callsigns);$('blacklist-result').textContent='黑名单已保存并立即生效'}catch(e){$('blacklist-result').textContent=`保存失败：${e.message}`}}
async function addCallsignToBlacklist(button){const callsign=String(button.dataset.callsign||'').toUpperCase();if(!callsign)return;button.disabled=true;try{await persistBlacklist([...new Set([...blacklistValues,callsign])]);$('blacklist-result').textContent=`${callsign} 已加入黑名单，后续语音将不再处理`}catch(e){$('blacklist-result').textContent=`加入失败：${e.message}`;button.disabled=false}}
async function removeCallsignFromBlacklist(button){const callsign=String(button.dataset.callsign||'').toUpperCase();button.disabled=true;try{await persistBlacklist(blacklistValues.filter(value=>value!==callsign));$('blacklist-dialog-result').textContent=`${callsign} 已移出黑名单`}catch(e){$('blacklist-dialog-result').textContent=`移除失败：${e.message}`;button.disabled=false}}
function renderControls(){document.querySelectorAll('.control-toggle').forEach(button=>{const on=!!controls[button.dataset.control];button.textContent=on?'已开启':'已关闭';button.className=`control-toggle ${on?'on':'off'}`;button.disabled=false})}
async function toggleControl(button){button.disabled=true;const name=button.dataset.control;try{const r=await fetch('api/control',{method:'POST',headers:{'Content-Type':'application/json','X-FMO-Admin':'1'},body:JSON.stringify({name,enabled:!controls[name]})});const d=await r.json();if(!r.ok)throw new Error(d.error||'未知错误');controls=d.controls;renderControls();$('control-result').textContent='开关已保存'}catch(e){$('control-result').textContent=`操作失败：${e.message}`;button.disabled=false}}
async function loadPersona(){const r=await fetch('api/persona',{headers:{'X-FMO-Admin':'1'},cache:'no-store'});if(!r.ok)return;const d=await r.json(),p=d.persona||{},t=d.tts||{};for(const key of ['name','gender','age','accent','description'])$(`persona-${key}`).value=p[key]||'';$('persona-voice').textContent=t.voice||'—';$('persona-acoustic-accent').textContent=t.accent_effective?(t.acoustic_accent||'普通话'):`${t.requested_accent||'未设定'}（仅文字风格）`}
async function savePersona(){const button=$('persona-save');button.disabled=true;const body={};for(const key of ['name','gender','age','accent','description'])body[key]=$(`persona-${key}`).value.trim();try{const r=await fetch('api/persona',{method:'POST',headers:{'Content-Type':'application/json','X-FMO-Admin':'1'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.error||'未知错误');$('persona-result').textContent='身份设定已保存，将用于后续百炼模型回复';loadPersona()}catch(e){$('persona-result').textContent=`保存失败：${e.message}`}finally{button.disabled=false}}
const formatBytes=n=>{n=Number(n)||0;if(n<1024)return `${n} B`;if(n<1048576)return `${(n/1024).toFixed(1)} KB`;return `${(n/1048576).toFixed(1)} MB`};
function renderBreadcrumb(){const parts=knowledgePath?knowledgePath.split('/'):[];let html='<button type="button" data-path="">根目录</button>',built='';for(const part of parts){built=built?`${built}/${part}`:part;html+=`<span>/</span><button type="button" data-path="${escapeHtml(built)}">${escapeHtml(part)}</button>`}$('kb-breadcrumb').innerHTML=html;$('kb-breadcrumb').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>loadKnowledgeFiles(b.dataset.path)))}
async function loadKnowledgeOverview(){try{const r=await fetch('api/knowledge/overview',{headers:{'X-FMO-Admin':'1'},cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.error||'接口异常');$('kb-files').textContent=d.files??0;$('kb-documents').textContent=d.documents??0;$('kb-sections').textContent=d.sections??0;$('kb-searches').textContent=d.searches??0;$('kb-hits').textContent=d.hits??0;$('kb-latest').textContent=d.latest_search?.at?new Date(d.latest_search.at*1000).toLocaleString('zh-CN',{hour12:false}):'暂无'}catch(e){$('kb-result').textContent=`统计读取失败：${e.message}`}}
async function loadKnowledgeFiles(path=knowledgePath){knowledgePath=path;renderBreadcrumb();try{const r=await fetch(`api/knowledge/files?path=${encodeURIComponent(path)}`,{headers:{'X-FMO-Admin':'1'},cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.error||'目录读取失败');const entries=(d.entries||[]).filter(x=>x.name!=='@eaDir');$('kb-file-list').innerHTML=entries.length?entries.map(x=>`<tr><td>${x.type==='directory'?`<button class="file-link" type="button" data-path="${escapeHtml(x.path)}">📁 ${escapeHtml(x.name)}</button>`:escapeHtml(x.name)}</td><td>${x.type==='directory'?'目录':'文件'}</td><td>${x.type==='directory'?'—':formatBytes(x.size)}</td><td>${new Date(x.modified_at*1000).toLocaleString('zh-CN',{hour12:false})}</td><td>${x.type==='directory'?'—':(x.indexed_sections>0?`已入库 · ${x.indexed_sections}片`:'待入库')}</td></tr>`).join(''):'<tr><td colspan="5">目录为空</td></tr>';$('kb-file-list').querySelectorAll('.file-link').forEach(b=>b.addEventListener('click',()=>loadKnowledgeFiles(b.dataset.path)))}catch(e){$('kb-file-list').innerHTML=`<tr><td colspan="5">${escapeHtml(e.message)}</td></tr>`}}
const bytesToBase64=buffer=>{const bytes=new Uint8Array(buffer);let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(binary)};
async function uploadKnowledge(){const files=[...$('kb-upload-files').files];if(!files.length){$('kb-result').textContent='请先选择文件';return}const button=$('kb-upload-button');button.disabled=true;try{for(let fileIndex=0;fileIndex<files.length;fileIndex++){const file=files[fileIndex];if(file.size>100*1024*1024)throw new Error(`${file.name} 超过100MB`);const uploadId=[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');let offset=0;while(offset<file.size){const end=Math.min(file.size,offset+512*1024),chunk=bytesToBase64(await file.slice(offset,end).arrayBuffer());const r=await fetch('api/knowledge/upload',{method:'POST',headers:{'Content-Type':'application/json','X-FMO-Admin':'1'},body:JSON.stringify({path:knowledgePath,name:file.name,upload_id:uploadId,offset,total_size:file.size,chunk})}),d=await r.json();if(!r.ok)throw new Error(d.error||`${file.name} 上传失败`);offset=end;$('kb-upload-progress').style.width=`${Math.round(((fileIndex+offset/file.size)/files.length)*100)}%`}$('kb-result').textContent=`已上传 ${fileIndex+1}/${files.length}：${file.name}`}$('kb-upload-files').value='';await Promise.all([loadKnowledgeFiles(),loadKnowledgeOverview()])}catch(e){$('kb-result').textContent=`上传失败：${e.message}`}finally{button.disabled=false;setTimeout(()=>{$('kb-upload-progress').style.width='0'},1500)}}
function openKnowledge(){const overlay=$('knowledge-overlay');overlay.hidden=false;document.body.classList.add('modal-open');loadKnowledgeOverview();loadKnowledgeFiles();$('knowledge-close').focus()}
function closeKnowledge(){const overlay=$('knowledge-overlay');overlay.hidden=true;document.body.classList.remove('modal-open');$('nas-card').focus()}
function openBlacklist(){const overlay=$('blacklist-overlay');overlay.hidden=false;document.body.classList.add('modal-open');$('blacklist-dialog-result').textContent='';loadBlacklist();$('blacklist-close').focus()}
function closeBlacklist(){const overlay=$('blacklist-overlay');overlay.hidden=true;document.body.classList.remove('modal-open');$('blacklist-open').focus()}
function formatGatewayTemplate(config){
  const normalized = structuredClone(config||gatewayDefaultConfig);
  if (Array.isArray(normalized.mqtt_servers) && normalized.mqtt_servers.length) {
    normalized.mqtt_servers = normalized.mqtt_servers.map(item=>({
      name: item.name || 'default',
      host: item.host || '127.0.0.1',
      port: item.port || 1884,
      topic: item.topic || 'FMO/RAW',
      client_id: item.client_id || 'FMO-AI-MONITOR',
      username: item.username || '',
      password: '',
      enabled: item.enabled !== false,
      tls: item.tls || {enabled:false,ca_file:'',cert_file:'',key_file:''}
    }));
  }
  return JSON.stringify(normalized,null,2);
}

function renderProviderConfig(config){
  const providers = config && config.providers ? config.providers : {};
  const chat = providers.chat || {};
  const embedding = providers.embedding || {};
  const asr = providers.asr || {};
  const tts = providers.tts || {};

  // 基础模式：统一参数
  const base = {
    name: chat.provider || providerDefaults.chat.provider,
    base_url: chat.base_url || providerDefaults.chat.base_url,
    key_env: chat.api_key_env || providerDefaults.chat.api_key_env,
    has_key: Boolean(chat.has_api_key || embedding.has_api_key || asr.has_api_key || tts.has_api_key),
  };

  $('provider-global-name').value = base.name;
  $('provider-global-base').value = base.base_url;
  $('provider-global-key-env').value = base.key_env;
  $('provider-global-key').value = '';
  $('provider-global-key').placeholder = base.has_key ? '留空不改（已配置）' : '留空不改';

  // 简化模型设置
  $('provider-chat-model-simple').value = chat.model || providerDefaults.chat.model;
  $('provider-embedding-model-simple').value = embedding.model || providerDefaults.embedding.model;
  $('provider-asr-model-simple').value = asr.model || providerDefaults.asr.model;
  $('provider-tts-model-simple').value = tts.model || providerDefaults.tts.model;

  // 高级配置默认保持收起；是否展开由管理员手动控制
  //（避免刷新后“自动展开”干扰对比）
  $('provider-advanced').open = false;

  // 高级区回填
  providerConfigFields.forEach((capability)=>{
    const value = providers[capability] || {};
    const capDefault = providerDefaults[capability] || providerDefaults.chat;
    $(`provider-${capability}-name`).value = value.provider || capDefault.provider;
    $(`provider-${capability}-model`).value = value.model || capDefault.model;
    $(`provider-${capability}-base`).value = (value.base_url || capDefault.base_url);
    $(`provider-${capability}-key-env`).value = value.api_key_env || capDefault.api_key_env;
    $(`provider-${capability}-key`).value = '';
    $(`provider-${capability}-key`).placeholder = value.has_api_key ? '留空不改（已配置）' : '留空不改';
  });
}

function collectProviderConfig(){
  const providers = {};
  const useAdvanced = $('provider-advanced').open;
  const globalProvider = ($('provider-global-name').value || providerDefaults.chat.provider).trim();
  const globalBase = ($('provider-global-base').value || providerDefaults.chat.base_url).trim();
  const globalKeyEnv = ($('provider-global-key-env').value || providerDefaults.chat.api_key_env).trim();
  const globalKey = ($('provider-global-key').value || '').trim();

  const simpleModels = {
    chat: ($('provider-chat-model-simple').value || providerDefaults.chat.model).trim(),
    embedding: ($('provider-embedding-model-simple').value || providerDefaults.embedding.model).trim(),
    asr: ($('provider-asr-model-simple').value || providerDefaults.asr.model).trim(),
    tts: ($('provider-tts-model-simple').value || providerDefaults.tts.model).trim(),
  };

  providerConfigFields.forEach((capability)=>{
    const model = simpleModels[capability] || providerDefaults[capability].model;
    let provider = globalProvider;
    let base_url = globalBase;
    let api_key_env = globalKeyEnv;
    const payload = {
      provider,
      model,
      base_url,
      api_key_env,
    };

    if (useAdvanced) {
      provider = ($(`provider-${capability}-name`).value || payload.provider).trim();
      const advModel = ($(`provider-${capability}-model`).value || payload.model).trim();
      const advBase = ($(`provider-${capability}-base`).value || payload.base_url).trim();
      const advEnv = ($(`provider-${capability}-key-env`).value || payload.api_key_env).trim();
      payload.provider = provider;
      payload.model = advModel;
      payload.base_url = advBase;
      payload.api_key_env = advEnv;
    }

    if(!payload.provider || !payload.model || !payload.base_url || !payload.api_key_env){
      throw new Error(`${capability} 配置不完整`);
    }

    const keyValue = (useAdvanced ? ($(`provider-${capability}-key`).value || '').trim() : globalKey);
    if (keyValue) {
      payload.api_key = keyValue;
    }
    providers[capability] = payload;
  });

  return {providers};
}


async function loadGatewayConfig(){
  if (gatewayConfigLoading) return;
  gatewayConfigLoading=true;
  const button=$('gateway-config-load');
  button.disabled=true; button.textContent='读取中';
    try{
    const {response:r, data:d}=await apiRequestJsonCandidates(
      detectGatewayConfigCandidates('gateway-config'),
      {headers:{'X-FMO-Admin':'1'},cache:'no-store'},
    );
    if(!r.ok) throw new Error(d.error||'读取失败');
    gatewayConfigCache = (d && typeof d === 'object') ? d : gatewayDefaultConfig;
    renderGatewayForm(gatewayConfigCache);
    $('gateway-config-editor').value = formatGatewayTemplate(d);
    $('gateway-config-result').textContent='配置已读取';
  }catch(e){
    $('gateway-config-result').textContent=`读取失败：${e.message}`;
  }
  button.disabled=false; button.textContent='重新读取';
  gatewayConfigLoading=false;
}

async function apiRequestJsonCandidates(candidates, options = {}) {
  const seen = new Set();
  const uniq = [];
  for (const candidate of candidates) {
    const normalized = String(candidate || '').trim().replace(/^\s+|\s+$/g, '');
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    uniq.push(normalized);
  }

  const attempts = [];
  let lastErr = '无法获取有效 JSON 响应';
  for (const normalized of uniq) {
    const target = normalized;
    attempts.push(target);
    try {
      const response = await fetch(target, options);
      const raw = await response.text();

      if (response.status === 404) {
        lastErr = `路径 ${target} 返回 404`;
        continue;
      }

      if (!raw) {
        lastErr = `路径 ${target} 返回空响应`;
        continue;
      }

      if (response.headers.get('content-type')?.includes('application/json')) {
        return {response, data: JSON.parse(raw)};
      }

      if (raw.trim().startsWith('<')) {
        lastErr = `路径 ${target} 返回 HTML（可能是错误页）`;
        continue;
      }

      return {response, data: JSON.parse(raw)};
    } catch (error) {
      lastErr = `路径 ${target} 解析失败：${error.message}`;
      continue;
    }
  }

  const hints = staticPageLikely()
    ? '当前页面是本地静态页(如127.0.0.1:8088)预览，不承载网关API。请改用带反代入口 /ai/ 的地址打开，或确认 AI 网关服务已启动。'
    : '当前环境未返回可解析JSON的网关配置接口，请检查反代/后端服务。';
  throw new Error(`${lastErr}，已尝试：${attempts.join(' -> ')}。${hints}`);
}

async function loadProviderConfig(){
  if (providerConfigLoading) return;
  providerConfigLoading=true;
  const button=$('provider-load');
  button.disabled=true;
  button.textContent='读取中';
  try{
    const {response:r, data:d}=await apiRequestJsonCandidates(
      detectGatewayConfigCandidates('gateway-config'),
      {headers:{'X-FMO-Admin':'1'},cache:'no-store'},
    );
    if(!r.ok) throw new Error(d.error||'读取失败');
    renderProviderConfig(d);
    $('provider-result').textContent='模型配置已读取';
    $('provider-advanced').open = false;
  }catch(e){
    $('provider-result').textContent=`读取失败：${e.message}`;
    $('provider-advanced').open = false;
  }
  button.disabled=false;
  button.textContent='读取模型配置';
  providerConfigLoading=false;
}

async function saveProviderConfig(){
  const button=$('provider-save');
  button.disabled=true;
  $('provider-result').textContent='保存中…';
  try{
    const cfg=collectProviderConfig();
    const {response:r, data:d}=await apiRequestJsonCandidates(
      detectGatewayConfigCandidates('gateway-config'),
      {method:'POST',headers:{'Content-Type':'application/json','X-FMO-Admin':'1'},body:JSON.stringify(cfg)},
    );
    if(!r.ok) throw new Error(d.error||'保存失败');
    renderProviderConfig(d);
    $('provider-result').textContent='模型配置已保存；请重启服务后生效。';
  }catch(e){
    $('provider-result').textContent=`保存失败：${e.message}`;
  }
  button.disabled=false;
}

async function saveGatewayConfig(){
  const button=$('gateway-config-save');
  button.disabled=true;
  try{
    const cfg = $('gateway-config-advanced') && $('gateway-config-advanced').open
      ? JSON.parse($('gateway-config-editor').value)
      : collectGatewayConfigFromForm();
    const {response:r,data:d}=await apiRequestJsonCandidates(
      detectGatewayConfigCandidates('gateway-config'),
      {method:'POST',headers:{'Content-Type':'application/json','X-FMO-Admin':'1'},body:JSON.stringify(cfg)},
    );
    if(!r.ok) throw new Error(d.error||'保存失败');
    gatewayConfigCache = (d && typeof d === 'object') ? d : gatewayConfigCache;
    renderGatewayForm(gatewayConfigCache);
    $('gateway-config-editor').value = formatGatewayTemplate(gatewayConfigCache);
    $('gateway-config-result').textContent='保存成功；请重启服务后生效。';
  }catch(e){
    $('gateway-config-result').textContent=`保存失败：${e.message}`;
  }
  button.disabled=false;
}

function openGatewayConfig(){
  const overlay=$('gateway-config-overlay');
  overlay.hidden=false;
  document.body.classList.add('modal-open');
  const advanced = $('gateway-config-advanced');
  if (advanced) advanced.open = false;
  const connectionAdvanced = $('gateway-connection-advanced');
  if (connectionAdvanced) connectionAdvanced.open = false;
  $('gateway-config-result').textContent='';
  loadGatewayConfig();
  $('gateway-config-close').focus();
}

function closeGatewayConfig(){
  const overlay=$('gateway-config-overlay');
  overlay.hidden=true;
  document.body.classList.remove('modal-open');
}
$('nas-card').addEventListener('click',openKnowledge);$('nas-card').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openKnowledge()}});$('knowledge-close').addEventListener('click',closeKnowledge);$('knowledge-overlay').addEventListener('click',e=>{if(e.target===$('knowledge-overlay'))closeKnowledge()});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('knowledge-overlay').hidden)closeKnowledge()});
$('blacklist-open').addEventListener('click',openBlacklist);$('blacklist-close').addEventListener('click',closeBlacklist);$('blacklist-overlay').addEventListener('click',e=>{if(e.target===$('blacklist-overlay'))closeBlacklist()});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('blacklist-overlay').hidden)closeBlacklist()});
$('gateway-config-open').addEventListener('click',openGatewayConfig);$('gateway-config-close').addEventListener('click',closeGatewayConfig);$('gateway-config-overlay').addEventListener('click',e=>{if(e.target===$('gateway-config-overlay'))closeGatewayConfig()});document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('gateway-config-overlay').hidden)closeGatewayConfig()});
$('blacklist-save').addEventListener('click',saveBlacklist);$('persona-save').addEventListener('click',savePersona);$('kb-upload-button').addEventListener('click',uploadKnowledge);$('gateway-config-load').addEventListener('click',loadGatewayConfig);$('gateway-config-save').addEventListener('click',saveGatewayConfig);refresh();loadBlacklist();loadPersona();setInterval(refresh,15000);setInterval(()=>{if(!$('knowledge-overlay').hidden)loadKnowledgeOverview()},60000);
$('provider-config-panel') && ($('provider-config-panel').open = false);
$('provider-advanced') && ($('provider-advanced').open = false);
$('provider-load').addEventListener('click',loadProviderConfig);$('provider-save').addEventListener('click',saveProviderConfig);renderProviderConfig({providers:{}});loadProviderConfig();
document.querySelectorAll('.sort-button').forEach(button=>button.addEventListener('click',()=>changeCallsignSort(button.dataset.sort)));
document.querySelectorAll('.control-toggle').forEach(button=>button.addEventListener('click',()=>toggleControl(button)));
