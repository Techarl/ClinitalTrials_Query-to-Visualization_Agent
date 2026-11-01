import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Chart } from './Chart'
import { requestGate } from './requestGate'
import { submissionGate } from './submissionGate'
import {analysisNotes, artifactTitle, evidenceSelection, groupedHistory, scopeLabels} from './presentation'
import { api, terminal, statusText } from './types'
import type { Artifact, Evidence, Event, History, Row, Run } from './types'
import './style.css'

const toolNames: Record<string,string> = {define_task:'确认分析目标',search_trials:'获取试验记录',inspect_query_scope:'检查查询范围',profile_dataset:'检查字段与缺失',analyze:'计算图表与来源',select_entities:'按实际关系选择机构',resolve_entity_candidates:'核对药物名称',search_pubmed:'检索 PubMed 文献',fetch_articles:'读取文献摘要',official_count_overview:'获取官方精确计数',validate_result:'核验结果与证据',finalize_analysis:'整理分析结果',clarify_user:'确认缺失信息',stop_with_limit:'说明当前限制',lookup_trials:'查找原始试验',read_source:'阅读来源正文',publish_text_answer:'整理有引文的回答',search_experiences:'查找相关经验',read_skill:'阅读操作经验',apply_skill:'应用操作经验'}
const examples = [
  ['阶段分布','Show the phase distribution of melanoma studies involving pembrolizumab.'],
  ['关系 → 年度趋势','Among melanoma studies involving pembrolizumab from 2015 through 2025, find the top three sponsors sharing drugs with Merck Sharp & Dohme LLC, show the shared-drug network and compare their annual trial counts.'],
  ['入组与持续时间','For psoriasis studies involving secukinumab, plot enrollment against trial duration using exact start and completion dates. Explain missing values.'],
  ['文献 + 试验','Search PubMed for pembrolizumab in melanoma and show the phase distribution of the corresponding ClinicalTrials.gov melanoma studies involving pembrolizumab.'],
]

function App() {
  const shareToken = location.pathname.startsWith('/s/') ? location.pathname.split('/')[2] : null
  const [history, setHistory] = useState<History[]>([])
  const [runId,setRunId] = useState<string | null>(shareToken ? null : localStorage.getItem('trialscope.run'))
  const activeRunId=useRef(runId)
  const evidenceGate=useRef(requestGate())
  const submitGate=useRef(submissionGate(()=>crypto.randomUUID()))
  const panelElement=useRef<HTMLElement>(null)
  activeRunId.current=runId
  const [run,setRun] = useState<Run | null>(null)
  const [events,setEvents] = useState<Event[]>([])
  const [query,setQuery] = useState('')
  const [mode,setMode] = useState('full_evidence')
  const [filters,setFilters] = useState({condition:'',interventions:'',phases:'',countries:''})
  const [filterOpen,setFilterOpen] = useState(false)
  const [dateFilter,setDateFilter] = useState({field:'start',lower:'',upper:''})
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [shareUrl,setShareUrl] = useState('')
  const [shareId,setShareId] = useState('')
  const [evidence,setEvidence] = useState<Evidence | null>(null)
  const [evidenceLoading,setEvidenceLoading] = useState(false)
  const [evidenceError,setEvidenceError] = useState('')
  const [evidenceRef,setEvidenceRef] = useState<{artifact:Artifact;id:string} | null>(null)
  const [sharedEvidence,setSharedEvidence] = useState<Record<string,Evidence>>({})
  const [selected,setSelected] = useState<Artifact | null>(null)
  const [panel,setPanel] = useState<'explain'|'evidence'|null>(null)
  const [live,setLive] = useState(false)
  const [connection,setConnection] = useState('')
  const [drawer,setDrawer] = useState(()=>matchMedia('(max-width:1200px)').matches)
  useEffect(()=>{const media=matchMedia('(max-width:1200px)');const update=()=>setDrawer(media.matches);media.addEventListener('change',update);return()=>media.removeEventListener('change',update)},[])
  useEffect(()=>{
    if(!panel)return
    const previous=document.activeElement as HTMLElement | null
    const element=panelElement.current
    element?.querySelector<HTMLButtonElement>('button')?.focus()
    const keyboard=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){event.preventDefault();setPanel(null)}
      if(event.key==='Tab' && drawer && element){
        const controls=[...element.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,select,textarea,summary,[tabindex="0"]')].filter(e=>e.getClientRects().length)
        const first=controls[0],last=controls.at(-1)
        if(event.shiftKey && document.activeElement===first){event.preventDefault();last?.focus()}
        else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus()}
      }
    }
    document.addEventListener('keydown',keyboard)
    return()=>{document.removeEventListener('keydown',keyboard);if(previous?.isConnected)previous.focus()}
  },[panel,drawer])
  const refreshHistory = () => api<{runs:History[]}>('/v2/runs').then(v=>setHistory(v.runs)).catch(()=>{})
  const refresh = (rid:string) => api<Run>('/v2/runs/'+rid).then(value=>{if(activeRunId.current===rid)setRun(value);return value})
  useEffect(()=>{api('/health').then(()=>setLive(true)).catch(()=>setLive(false));if(!shareToken)refreshHistory()},[])
  useEffect(()=>{
    evidenceGate.current.invalidate();setEvidence(null);setEvidenceRef(null);setSelected(null);setPanel(null)
    setFilters({condition:'',interventions:'',phases:'',countries:''});setDateFilter({field:'start',lower:'',upper:''});setMode('full_evidence')
    if (shareToken) {
      api<{result:Run;evidence:Record<string,Evidence>}>('/v2/shares/'+shareToken).then(value=>{setRun(value.result);setSharedEvidence(value.evidence)}).catch(e=>setError(e.message))
      return
    }
    if(!runId) return
    localStorage.setItem('trialscope.run',runId)
    let source:EventSource | null=null, disposed=false
    setRun(null);setEvents([]);setError('');setConnection('正在恢复执行记录…')
    refresh(runId).then(value=>{
      if(disposed)return
      source = new EventSource(`/v2/runs/${runId}/events`)
      source.onopen=()=>setConnection('已连接')
      source.addEventListener('update',(raw)=>{
        const event:Event=JSON.parse((raw as MessageEvent).data)
        setEvents(old=>old.some(item=>item.sequence===event.sequence)?old:[...old,event].sort((a,b)=>a.sequence-b.sequence))
        if(['run_started','observation','artifact_ready','run_completed','run_failed','clarification_required'].includes(event.type))refresh(runId).catch(()=>{})
      })
      source.addEventListener('end',()=>{source?.close();setConnection('执行记录已同步');refresh(runId).then(()=>refreshHistory()).catch(()=>{})})
      source.onerror=()=>setConnection('连接中断，正在从上次事件继续…')
      if(terminal.has(value.status))setConnection('执行记录已同步')
    }).catch(e=>setError(e.message))
    return ()=>{disposed=true;source?.close()}
  },[runId])

  async function send(event:React.FormEvent) {
    event.preventDefault();if(!query.trim() || (runId && (!run || run.run_id!==runId)) || (run && !terminal.has(run.status)))return
    const explicit:Row={}
    for(const [key,value] of Object.entries(filters))if(value.trim())explicit[key]=['interventions','phases','countries'].includes(key)?value.split(',').map(s=>s.trim()).filter(Boolean):value
    const body:Row={query,language:'zh',source_mode:mode,...(Object.keys(explicit).length?{filters:explicit}:{})}
    if(dateFilter.lower || dateFilter.upper)body.date_range={field:dateFilter.field,lower:dateFilter.lower || null,upper:dateFilter.upper || null}
    if(run){body.session_id=run.session_id;body.parent_run_id=run.run_id}
    const submissionId=submitGate.current.begin(JSON.stringify(body))
    if(!submissionId)return
    body.submission_id=submissionId
    setBusy(true);setError('');setShareUrl('');setShareId('')
    let succeeded=false
    try {
      const result=await api<{run_id:string}>('/v2/runs',{method:'POST',body:JSON.stringify(body)})
      succeeded=true
      setRun(null);setRunId(result.run_id);setQuery('');setPanel(null);refreshHistory()
    }catch(e){setError((e as Error).message)}finally{submitGate.current.finish(succeeded);setBusy(false)}
  }
  function newSession(){setRun(null);setRunId(null);setEvents([]);setPanel(null);setShareUrl('');setFilters({condition:'',interventions:'',phases:'',countries:''});setDateFilter({field:'start',lower:'',upper:''});localStorage.removeItem('trialscope.run')}
  async function showEvidence(artifact:Artifact,id:string,offset=0){
    const current=evidenceGate.current.next()
    const requestedRun=run?.run_id
    setSelected(artifact);setPanel('evidence');setEvidenceRef({artifact,id});setEvidence(null);setEvidenceLoading(true);setEvidenceError('')
    try {
      if(shareToken){const full=sharedEvidence[id];setEvidence({...full,citations:full.citations.slice(offset,offset+25),offset,next_offset:offset+25<full.total_contributors?offset+25:null})}
      else {const value=await api<Evidence>(`/v2/results/${artifact.result_id}/evidence?run_id=${requestedRun}&evidence_id=${id}&offset=${offset}`);if(current() && activeRunId.current===requestedRun)setEvidence(value)}
    }catch(e){if(current())setEvidenceError((e as Error).message)}finally{if(current())setEvidenceLoading(false)}
  }
  async function share(){try{const response=await api<{url:string;token:string}>(`/v2/runs/${runId}/share`,{method:'POST'});setShareUrl(location.origin+response.url);setShareId(response.token)}catch(e){setError((e as Error).message)}}
  const running = busy || (run && !terminal.has(run.status))
  const currentHistory=history.find(item=>item.run_id===runId)
  const sessions=groupedHistory(history)
  const currentSession=sessions.find(s=>s.turns.some(t=>t.run_id===runId))
  const currentTurn=currentSession?.turns.findIndex(t=>t.run_id===runId)
  const scope=scopeLabels(run?.task)
  const point=evidenceRef?evidenceSelection(evidenceRef.artifact,evidenceRef.id):null
  const displayArtifacts=[...(run?.artifacts || [])].sort((a,b)=>Number(!a.visualization && a.source_type!=='answer')-Number(!b.visualization && b.source_type!=='answer'))
  const leadArtifact=displayArtifacts.find(a=>a.visualization || a.source_type==='answer')
  const resultTitle=leadArtifact?artifactTitle(leadArtifact):undefined
  function selectRun(id:string){setRunId(id);setPanel(null);setShareUrl('');setQuery('');setRun(null);activeRunId.current=id}
  const steps=events.filter(event=>['action_selected','tool_finished','run_failed','clarification_required'].includes(event.type))
  const entityObservations=events.filter(event=>event.type==='tool_finished' && event.data.resolutions)
  return <div className={'workspace '+(panel?'with-panel':'')}>
    <aside className="sidebar" inert={!!panel && drawer}><a href="/" className="brand"><span className="brand-mark">T<span>↗</span></span><span>TrialScope<small>临床试验分析工作台</small></span></a>
      {!shareToken && <button className="new-session" disabled={busy} onClick={newSession}><span>＋</span> 新建分析</button>}
      <div className="sidebar-label">{shareToken?'只读结果':'分析会话'}<span>{sessions.length || ''}</span></div>
      <nav aria-label="分析历史">{sessions.map(session=><details className="session-history" key={session.id} open={session.id===currentSession?.id}><summary><span className={'status-dot '+session.latest.status}/><span>{session.title}<small>{session.turns.length} 个结果版本 · {new Date(session.latest.created_at).toLocaleDateString()}</small></span></summary>{session.turns.map((item,i)=><button disabled={busy} key={item.run_id} className={'history-item '+(item.run_id===runId?'chosen':'')} aria-current={item.run_id===runId?'page':undefined} onClick={()=>selectRun(item.run_id)}><span className="turn-number">{i+1}</span><span>{item.query}<small>{statusText[item.status]}{item.parent_run_id && i>0 && item.parent_run_id!==session.turns[i-1].run_id?' · 从较早版本分支':''}</small></span></button>)}</details>)}</nav>
      <div className="sidebar-bottom"><span className={'status-dot '+(live?'completed':'failed')}/>{live?'本地服务已连接':'服务连接中'}<p>ClinicalTrials.gov · PubMed<br/>来源、口径与结果一起保留。</p><a href="/docs" target="_blank">API 文档 ↗</a></div>
    </aside>
    <main inert={!!panel && drawer}><header className="topbar"><span>临床试验研究工作台</span><div><span className="pill">{shareToken?'只读分享':'完整证据 · 可追溯'}</span><span className="avatar">CT</span></div></header>
      <div className="content">
        {!shareToken && history.length>0 && <label className="mobile-history">分析会话与版本 <select disabled={busy} aria-label="切换历史分析" value={runId || ''} onChange={e=>selectRun(e.target.value)}><option value="" disabled>选择一份分析</option>{sessions.map(session=><optgroup key={session.id} label={session.title}>{session.turns.map((item,i)=><option key={item.run_id} value={item.run_id}>版本 {i+1} · {item.query}</option>)}</optgroup>)}</select></label>}
        {shareToken && !run ? <section className="welcome"><div className="eyebrow">只读分析快照</div><h1>{error?'分享不可用':'正在读取分享…'}</h1><p>{error?'此链接可能已撤销或不存在。':'加载图表及对应的来源证据。'}</p></section> : !runId && !run ? <section className="welcome"><div className="eyebrow">从问题到证据</div><h1>让临床试验数据<br/><em>回答你的问题。</em></h1><p>探索研究分布、药物关系和时间趋势。<br/>每个图表元素，都能回到支持它的原始记录。</p><div className="example-grid">{examples.map(([title,text],i)=><button key={title} onClick={()=>setQuery(text)}><span className="example-number">0{i+1}</span><strong>{title}</strong><span className="example-arrow">↗</span><small>{text}</small></button>)}</div></section> : <>
          <div className="run-heading"><div className="eyebrow">{shareToken?'只读分析快照':`分析结果${currentTurn!==undefined && currentTurn>=0?` · 版本 ${currentTurn+1}`:''}`}</div><h1>{resultTitle || run?.task?.goal || currentHistory?.query || '正在理解你的问题…'}</h1>{resultTitle && run?.task?.goal && <details className="full-goal"><summary>查看完整分析目标</summary><p>{run.task.goal}</p></details>}
          <div className="run-meta"><span className={'state-label '+(run?.status || 'running')}>{statusText[run?.status || 'running']}</span><span>{shareToken?'此快照仅提供查看与引用访问':connection}</span>
          {!shareToken && run && terminal.has(run.status) && run.status!=='needs_clarification' && <><button onClick={share}>创建只读分享 ↗</button><a href={`/v2/runs/${runId}/export?format=json`}>结果与引文 JSON ↓</a>{run.artifacts.some(a=>a.visualization) && <a href={`/v2/runs/${runId}/export?format=csv`}>图表 CSV ↓</a>}{run.artifacts.some(a=>a.evidence_ids.length>0) && <a href={`/v2/runs/${runId}/export?format=evidence`}>图表贡献证据 ↓</a>}</>}
          {!shareToken && running && <button onClick={()=>api(`/v2/runs/${runId}/cancel`,{method:'POST'}).catch(e=>setError(e.message))}>停止分析</button>}</div></div>
          {shareUrl && <div className="share-box"><label>持有链接的人可查看当前结果与试验证据<input readOnly value={shareUrl} onFocus={e=>e.target.select()}/></label><a href={shareUrl} target="_blank">打开 ↗</a><button onClick={()=>{api(`/v2/runs/${runId}/shares/${shareId}`,{method:'DELETE'}).then(()=>{setShareUrl('');setShareId('')}).catch(e=>setError(e.message))}}>撤销</button></div>}
          {steps.length>0 && <details className="timeline" open={!!running}><summary><span className="timeline-icon">⌁</span> 执行记录 <span>{steps.length} 条真实步骤</span></summary><ol>{steps.map(event=><li key={event.sequence}><span className={'step-dot '+(event.data.status==='error'?'bad':'')}/><div><strong>{toolNames[event.data.action || event.data.tool] || event.type}</strong><small>{new Date(event.timestamp).toLocaleTimeString()} {event.data.status==='error'?'· '+event.data.message:event.data.selected_count!==undefined?`· 筛选后 ${event.data.selected_count} 条记录`:event.data.title || ''}</small></div></li>)}</ol></details>}
          {run?.task && scope.length>0 && <div className="scope-strip"><strong>本版本范围</strong>{scope.map(value=><span key={value}>{value}</span>)}</div>}
          {run?.questions?.length ? <section className="clarification"><span>需要补充</span>{run.questions.map(q=><p key={q}>{q}</p>)}<small>在下方回复；当前任务和已确认条件会保留。</small></section>:null}
          {run?.message && run.message!=='Results are assembled from validated artifacts. See each chart for its scope, metric, exclusions and source evidence.' && <p className={'run-message '+(['failed','limited','partial'].includes(run.status)?'notice':'')}>{run.message}</p>}
          {run?.unmet_goals.length ? <p className="notice">尚未完成：{run.unmet_goals.join('、')}</p>:null}
          {entityObservations.map(event=><section className="entity-mappings" key={event.sequence}><h3>名称核对与来源</h3>{event.data.same_entity_comparison && <p className="notice">这些名称指向同一种药物，不能作为两个不同药物比较。</p>}{event.data.resolutions.map((resolution:Row)=><div key={resolution.raw_name}><strong>{resolution.raw_name}</strong> · {resolution.resolution_status}{resolution.candidates.map((candidate:Row)=><p key={candidate.canonical_id}>{candidate.canonical_name} <a href={candidate.source} target="_blank" rel="noreferrer">映射依据 ↗</a><small> · {candidate.identity_basis} · {candidate.source_version}</small>{run?.status==='needs_clarification' && <button onClick={()=>setQuery(`I mean ${candidate.canonical_name}; retain all other conditions.`)}>使用此候选</button>}</p>)}</div>)}</section>)}
          {displayArtifacts.map((artifact,index)=><article className="artifact" key={artifact.result_id}><header><div><span className="eyebrow">{String(index+1).padStart(2,'0')} / {artifact.source_type==='pubmed'?'文献背景':artifact.source_type==='answer'?'来源支持的文字解读':'试验分析'}</span><h2>{artifactTitle(artifact)}</h2></div><button className="explain-button" onClick={()=>{setSelected(artifact);setPanel('explain')}}>口径与解释 ⓘ</button></header>
            {artifact.source_type!=='answer' && <div className="coverage"><span className="coverage-number">{artifact.coverage.matched_count ?? artifact.coverage.fetched_count}</span><span>{artifact.source_type==='pubmed'?'篇匹配文献':artifact.metadata.resolution?'个名称映射':artifact.metadata.root_scopes?.length>1?'条组内记录合计（各组可能重叠）':'项当前筛选范围内的独立研究'}<small>{artifact.coverage.count_exactness==='exact'?'精确统计':artifact.metadata.resolution?'来源已核对':'文献检索'} · {artifact.coverage.evidence_coverage==='complete_contributors'?'保留全部贡献研究':artifact.coverage.evidence_coverage==='sample_only'?'仅样本引用，非完整贡献集合':artifact.metadata.resolution?'名称映射依据':'元数据与可用摘要'}</small></span></div>}
            {artifact.source_type==='answer' && <section className="text-answer"><p className="qualitative-label">文字解读 · 引文已核对，不是总体统计</p><p>{artifact.metadata.answer}</p>{(artifact.metadata.findings||[]).map((finding:Row,i:number)=><blockquote key={i}><p>{finding.quotation}</p><cite>来源 {i+1}<details><summary>来源字段与版本标识</summary><code>{finding.source_handle}{finding.pointer}</code></details></cite></blockquote>)}{artifact.metadata.limitations?.length>0 && <div className="notice">{artifact.metadata.limitations.map((item:string)=><p key={item}>{item}</p>)}</div>}</section>}
            {artifact.metadata.resolution && <section className="entity-mappings">{artifact.metadata.resolution.same_entity_comparison && <p className="notice">这些名称指向同一种药物。</p>}{artifact.metadata.resolution.resolutions.map((resolution:Row)=><div key={resolution.raw_name}><strong>{resolution.raw_name}</strong>{resolution.candidates.map((candidate:Row)=><p key={candidate.canonical_id}>{candidate.canonical_name} · <a href={candidate.source} target="_blank" rel="noreferrer">映射依据 ↗</a><small> · {candidate.source_version}</small></p>)}</div>)}</section>}
            <Chart artifact={artifact} onEvidence={id=>showEvidence(artifact,id)}/>
            {artifact.metadata.ranking && <div className="table-scroll"><table><thead><tr><th>机构</th><th>共享药物数</th><th>支持关系的试验</th></tr></thead><tbody>{artifact.metadata.ranking.map((row:Row)=><tr key={row.id}><td>{row.name}</td><td>{row.shared_intervention_count}</td><td><button onClick={()=>showEvidence(artifact,row.evidence_set_id)}>查看引用</button></td></tr>)}</tbody></table></div>}
            {artifact.source_type==='pubmed' && <div className="articles">{(artifact.metadata.articles || []).map((article:Row)=><details key={article.pmid}><summary><span>PMID {article.pmid}</span>{article.title}</summary><a href={article.url} target="_blank" rel="noreferrer">PubMed 原文页面 ↗</a>{article.abstract.map((part:Row,i:number)=><p key={i}><strong>{part.label} </strong>{part.text}</p>)}{!!article.corrections.length && <p className="notice">更正 / 撤稿关联：{JSON.stringify(article.corrections)}</p>}</details>)}<p className="caption">仅获取元数据与可用摘要；文献数量与注册试验数量分开统计。</p></div>}
            {artifact.warnings.length>0 && <div className="advisories">{artifact.warnings.map(w=><p key={w.code+w.message}>ⓘ {w.code==='qualitative_answer'?'引文已与原文逐字核对；对引文的解读由模型生成，不等同于经核验的统计结果。':w.message}</p>)}</div>}
          </article>)}
        </>}
        {error && <p className="error" role="alert">{error}</p>}
      </div>
      {!shareToken && <div className="composer-wrap"><form onSubmit={send} className={'composer '+(filterOpen?'expanded':'')+(!query && run?' compact':'')}>
        {run?.task && <details className="inherited-scope"><summary>继续基于版本 {(currentTurn??0)+1} · {scope.slice(0,2).join(' · ') || '当前范围'}</summary><p>{scope.join('；')}</p><small>未修改的条件会继承。下方筛选仅填写这次要覆盖的条件；清除条件可直接在问题中说明。</small></details>}
        <textarea aria-label="分析问题" rows={1} placeholder={run?.status==='needs_clarification'?'回复上方具体问题…':run?'继续追问，例如：只看 Phase 3，按完成月份统计…':'描述疾病、药物与分析问题…'} value={query} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();e.currentTarget.form?.requestSubmit()}}}/>
        {filterOpen && <div className="filters">{Object.entries(filters).map(([k,v])=><label key={k}>{({condition:'疾病',interventions:'药物（逗号分隔）',phases:'阶段（如 PHASE3）',countries:'国家（逗号分隔）'} as Row)[k]}<input value={v} onChange={e=>setFilters(old=>({...old,[k]:e.target.value}))}/></label>)}</div>}
        {filterOpen && <div className="filters"><label>筛选日期字段<select value={dateFilter.field} onChange={e=>setDateFilter(old=>({...old,field:e.target.value}))}><option value="start">开始日期</option><option value="primary_completion">主要完成日期</option><option value="completion">完成日期</option><option value="first_posted">首次发布</option></select></label><label>起始日期<input type="date" value={dateFilter.lower} onChange={e=>setDateFilter(old=>({...old,lower:e.target.value}))}/></label><label>截止日期<input type="date" value={dateFilter.upper} onChange={e=>setDateFilter(old=>({...old,upper:e.target.value}))}/></label></div>}
        <div className="composer-controls"><div><button type="button" className={filterOpen?'active':''} onClick={()=>setFilterOpen(!filterOpen)}>☷ 筛选条件</button><select aria-label="数据获取模式" value={mode} onChange={e=>setMode(e.target.value)}><option value="full_evidence">完整记录与证据</option><option value="aggregate_overview">官方计数 · 有限引用</option></select></div><button className="send-button" disabled={!!running || !query.trim()} type="submit">{busy?'提交中…':run?'继续分析 ↑':'开始分析 ↑'}</button></div></form><p className="composer-note">Ctrl + Enter 发送 · 数据来自公开研究注册与文献；不用于诊疗决策。</p></div>}
    </main>
    {panel && drawer && <div className="drawer-backdrop" onClick={()=>setPanel(null)} aria-hidden="true"/>}
    {panel && <aside ref={panelElement} className="detail-panel" role={drawer?'dialog':'complementary'} aria-modal={drawer?true:undefined} aria-label={panel==='evidence'?'来源证据':'分析解释'}><header><div><span className="eyebrow">{panel==='evidence'?'对应数据点的来源':'统计口径与限制'}</span><h2>{panel==='evidence'?'回到原始记录':'理解这份结果'}</h2></div><button aria-label="关闭侧栏" onClick={()=>setPanel(null)}>×</button></header>
      {panel==='explain' && selected && <><h3>{selected.title}</h3><ul className="analysis-notes">{analysisNotes(selected).map(note=><li key={note}>{note}</li>)}</ul><details><summary>高级详情：完整统计元数据与来源范围</summary><pre>{JSON.stringify({metric:selected.metric,coverage:selected.coverage,...selected.metadata},null,2)}</pre></details><p className="caption">图表切换复用同一结果。改变分组、指标或筛选条件，请发起新的分析。</p></>}
      {panel==='evidence' && selected && point && <section className="selected-point"><small>{selected.title}</small><h3>{point.title}</h3><strong>{point.value}</strong>{point.cohort && <p>{point.cohort}</p>}</section>}
      {panel==='evidence' && evidenceLoading && <p className="evidence-status" role="status">正在读取此数据点的来源…</p>}
      {panel==='evidence' && evidenceError && <p className="error" role="alert">{evidenceError}</p>}
      {panel==='evidence' && evidence && <><div className="evidence-total"><strong>{evidence.total_contributors}</strong> 条{selected?.coverage.evidence_coverage==='sample_only'?'样本引用':'完整贡献记录'}<small>{evidence.total_contributors?`当前显示 ${evidence.offset+1}–${evidence.offset+evidence.citations.length}`:'此数据点为零，没有贡献记录。'}</small></div><details><summary>高级详情：此数据点的计算规则</summary><pre>{JSON.stringify(evidence.rule,null,2)}</pre></details>{evidence.citations.map(c=><article className="citation" key={c.dataset_id+c.nct_id}><a href={c.url} target="_blank" rel="noreferrer">{c.nct_id} ↗</a><details><summary>查看原始字段（{c.fields.length}）</summary>{c.fields.map(f=><div className="source-field" key={f.path}><code>{f.path}</code><pre>{typeof f.value==='string'?f.value:JSON.stringify(f.value,null,2)}</pre></div>)}</details>{c.missing_fields.length>0 && <details><summary>来源中未报告的字段 ({c.missing_fields.length})</summary><pre>{c.missing_fields.join('\n')}</pre></details>}</article>)}<div className="pagination"><button disabled={evidence.offset===0} onClick={()=>evidenceRef&&showEvidence(evidenceRef.artifact,evidenceRef.id,Math.max(0,evidence.offset-25))}>上一页</button><button disabled={evidence.next_offset===null} onClick={()=>evidenceRef&&showEvidence(evidenceRef.artifact,evidenceRef.id,evidence.next_offset!)}>下一页</button></div></>}
    </aside>}
  </div>
}
createRoot(document.getElementById('root')!).render(<App/> )
