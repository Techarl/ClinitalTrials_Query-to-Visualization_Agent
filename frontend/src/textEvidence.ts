import type {Artifact, Row} from './types'

export function textCitation(finding:Row) {
  const p = finding.provenance
  const recorded = p?.version==='text-finding-v1' && p?.resolution==='exact'
  const id = typeof p?.document_id==='string'?p.document_id:''
  const expected = p?.source_type==='clinicaltrials' && /^NCT[0-9]{8}$/.test(id)
    ? `https://clinicaltrials.gov/study/${id}`
    : p?.source_type==='pubmed' && /^[1-9][0-9]*$/.test(id)
      ? `https://pubmed.ncbi.nlm.nih.gov/${id}/` : null
  // Only link an exact server-recorded official identity. Never infer IDs from quotes.
  const url = recorded && expected===p.url?expected:null
  const label = recorded && expected?(p.source_type==='pubmed'?`PMID ${id}`:id):''
  const field = finding.source_field
  let fieldNote = '此旧版结果未保存可移植的来源字段值；保留原始摘录与内部来源标识。'
  if(field?.value_included===true) fieldNote = '保存版本中的原始字段值'
  else if(field?.omission_reason==='whole_source_container') fieldNote = '此引用指向整个来源文档，导出保留摘录与哈希，不嵌入整份文档。'
  else if(field?.omission_reason==='field_exceeds_32000_characters') fieldNote = '来源字段超过 32,000 字符，导出保留准确摘录、字段长度与哈希。'
  else if(recorded) fieldNote = '未提供完整字段值；下方摘录保留引用内容。'
  const hashes:[string,string][] = []
  if(recorded) for(const [key,title] of [
    ['source_hash','来源容器 SHA-256'],['document_hash','单篇文档 SHA-256'],
    ['field_value_hash','字段值 SHA-256'],['quotation_hash','摘录 SHA-256'],
    ['receipt_hash','获取回执 SHA-256'],
  ]) if(typeof p[key]==='string' && p[key])hashes.push([title,p[key]])
  return {
    recorded, url, label,
    title:recorded && typeof p.title==='string'?p.title:'',
    pointer:typeof finding.pointer==='string'?(finding.pointer || '根文档'):'未记录',
    fieldText:field?.value_included===true
      ? (typeof field.value==='string'?field.value:JSON.stringify(field.value,null,2)) : null,
    fieldNote, hashes,
    notice:recorded?'官方链接用于定位来源；网页当前内容可能与本次保存的版本不同。':'旧版结果未记录可移植来源信息，无法据此生成官方链接。',
  }
}

export function evidenceExportLabel(artifacts:Artifact[]) {
  const chart = artifacts.some(a=>a.evidence_ids?.length>0)
  const text = artifacts.some(a=>a.source_type==='answer' && a.metadata.findings?.length>0)
  return chart && text?'图表与文字来源证据 ↓':text?'文字来源证据 ↓':chart?'图表贡献证据 ↓':null
}
