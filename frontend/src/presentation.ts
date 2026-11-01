import type {Artifact, History, Row} from './types'

export const dateNames: Record<string,string> = {start:'开始日期',completion:'完成日期',primary_completion:'主要完成日期',first_posted:'首次发布日期'}
export const phaseNames: Record<string,string> = {'Early Phase 1':'早期 1 期','Phase 1':'1 期','Phase 2':'2 期','Phase 3':'3 期','Phase 4':'4 期','Not applicable':'不适用','Not reported':'未报告',PHASE1:'1 期',PHASE2:'2 期',PHASE3:'3 期',PHASE4:'4 期',EARLY_PHASE1:'早期 1 期',NA:'不适用'}
export const phaseLabel = (value:string) => value.split(' / ').map(p=>phaseNames[p] || p).join(' / ')
export const cohortLabel = (value:string) => ['Population','Trials'].includes(value) ? '当前研究范围' : value

export function artifactTitle(artifact:Artifact) {
  const titles:Record<string,string>={'Source-grounded text answer':'来源支持的文字解读','Sourced drug identity mappings':'药物名称与来源映射'}
  return titles[artifact.title] || artifact.title
}

export function scopeLabels(task:Row | null | undefined) {
  if(!task)return []
  const names:Record<string,string>={condition:'疾病',interventions:'药物',sponsor:'主要申办机构',phases:'阶段',statuses:'状态',countries:'国家',start_year:'开始年份下限',end_year:'开始年份上限',enrollment_type:'入组人数类型'}
  const values=Object.entries(task.filters || {}).filter(([k,v])=>names[k] && v!==null && v!=='' && (!Array.isArray(v)||v.length)).map(([k,v])=>`${names[k]}：${Array.isArray(v)?v.map(x=>k==='phases'?phaseLabel(String(x)):String(x)).join('、'):String(v)}`)
  if(task.date_range){const range=task.date_range;values.push(`${dateNames[range.field] || range.field}：${range.lower || '不限起始'} 至 ${range.upper || '不限结束'}`)}
  if(task.intervention_scope==='comparison')values.push('各药物分别比较')
  else if(task.filters?.interventions?.length>1)values.push(task.filters.intervention_match==='all'?'同时包含所有药物':'包含任一药物')
  return values
}

export function groupedHistory(history:History[]) {
  const map=new Map<string,History[]>()
  for(const item of history)map.set(item.session_id,[...(map.get(item.session_id)||[]),item])
  return [...map].map(([id,items])=>{
    const turns=[...items].sort((a,b)=>a.created_at.localeCompare(b.created_at) || a.run_id.localeCompare(b.run_id))
    const root=turns.find(t=>!t.parent_run_id)||turns[0]
    return {id,title:root.query,turns,latest:items[0]}
  })
}

export function evidenceSelection(artifact:Artifact,id:string) {
  const visual=artifact.visualization
  const rows=visual ? Array.isArray(visual.data)?visual.data:[...visual.data.edges,...visual.data.nodes] : []
  const row=[...rows,...(artifact.metadata.ranking || [])].find((r:Row)=>r.evidence_set_id===id)
  if(!row)return {title:artifact.title,value:'',cohort:''}
  const nodes=visual && !Array.isArray(visual.data)?Object.fromEntries(visual.data.nodes.map(r=>[r.id,r.label])):{}
  const title=row.source && row.target?`${nodes[row.source]||row.source} ↔ ${nodes[row.target]||row.target}`:row.category?phaseLabel(String(row.category)):row.nct_id || row.name || row.label || (row.bin_start!==undefined?`${row.bin_start}–${row.bin_end}`:String(row.year || artifact.title))
  const value=row.shared_intervention_count!==undefined?`${row.shared_intervention_count} 种共享药物`:row.weight!==undefined?`${row.weight} ${artifact.metadata.analysis_parameters?.relationship==='sponsor_shared_drug'?'种共享药物':'项研究'}`:row.trial_count!==undefined?`${row.trial_count} 项研究`:row.x!==undefined?`X：${row.x} · Y：${row.y}`:''
  return {title,value,cohort:row.cohort?cohortLabel(row.cohort):''}
}

export function analysisNotes(artifact:Artifact) {
  const p=artifact.metadata.analysis_parameters || {}, notes:string[]=[]
  if(artifact.source_type==='answer')return ['这是基于来源摘录的文字解读，不是总体统计。','系统核对引文是否出自已读取的来源；模型对引文的解读仍需结合原文判断。',...(artifact.metadata.limitations||[])]
  if(artifact.source_type==='pubmed')return ['这里统计 PubMed 文献，文献篇数与注册试验数量分开呈现。','仅取得文献元数据与可用摘要，不代表已经阅读全文。']
  if(artifact.source_type==='entity')return ['这里展示来源支持的名称映射或机构排名。请结合每项映射依据与引用查看。']
  notes.push(p.operation==='scatter'?'每个点对应一项试验；横纵轴直接取自明确的原始字段。':'计数单位是独立注册试验，不是参与者人数或研究中心数量。')
  if(p.operation==='time_series')notes.push(`时间轴使用${dateNames[p.time_field]||p.time_field}，按${p.time_grain==='month'?'月':'年'}汇总；明确范围内没有试验的时间段显示为零。`)
  if(p.group_by==='phase')notes.push('联合阶段（例如 1 / 2 期）作为独立类别保留，不拆开重复计数。')
  if(p.group_by==='country')notes.push('一项跨国试验可以同时出现在多个国家，因此各国数量之和可能大于独立试验总数。')
  if(p.operation==='histogram')notes.push('按试验登记的入组人数分桶；通常左闭右开，最后一桶包含右端点。')
  if(p.operation==='network')notes.push(p.relationship==='sponsor_shared_drug'?'边权表示两家机构在当前范围内共享的不同药物数量；关系不代表直接合作。':'网络展示研究登记中的关系，不能由同一研究中出现推断为同一治疗组用药。')
  if(p.operation==='scatter' && [p.x,p.y].includes('trial_duration'))notes.push('持续时间只使用精确到日的开始和完成日期；缺失、精度不足或负持续时间的记录不作插补。')
  const cohorts=artifact.metadata.cohorts || []
  for(const c of cohorts)if(c.excluded_count)notes.push(`${cohortLabel(c.label)}有 ${c.excluded_count} 条记录因缺失或字段精度不足未进入图表。`)
  notes.push(artifact.coverage.evidence_coverage==='complete_contributors'?'每个数据点保留全部贡献试验，可通过图表或数据表查看。':artifact.coverage.evidence_coverage==='sample_only'?'数量来自官方计数；引用只提供样本，不是全体贡献记录。':'请查看来源覆盖信息。')
  return notes
}
