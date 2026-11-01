import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {transformWithOxc} from 'vite'
import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {textCitation,evidenceExportLabel} from './textEvidence.ts'

const finding=()=>({source_handle:'src_fixture',pointer:'/protocolSection/eligibilityModule/eligibilityCriteria',quotation:'Adults age 18 or older.',
  provenance:{version:'text-finding-v1',resolution:'exact',source_type:'clinicaltrials',document_id:'NCT01234567',title:'Synthetic trial',url:'https://clinicaltrials.gov/study/NCT01234567',source_hash:'a'.repeat(64),document_hash:'b'.repeat(64),field_value_hash:'c'.repeat(64),quotation_hash:'d'.repeat(64),document_pointer:'',citation_id:'text_fixture',hash_serialization:'canonical_json_v1',source_hash_scope:'canonical_source_text_container',version_notice:'Its current content may differ from this saved version.'},
  source_field:{value_included:true,value:'Adults age 18 or older.',serialized_character_count:23}})

test('official links require the recorded identity and exact official URL',()=>{
  const f=finding()
  assert.equal(textCitation(f).url,'https://clinicaltrials.gov/study/NCT01234567')
  f.provenance.url='https://clinicaltrials.gov.evil.example/study/NCT01234567'
  assert.equal(textCitation(f).url,null)
  f.provenance.url='javascript:alert(1)'
  assert.equal(textCitation(f).url,null)
  f.provenance.document_id='NCT01234567/redirect'
  assert.equal(textCitation(f).label,'')
  f.provenance={...f.provenance,source_type:'pubmed',document_id:'12345',url:'https://pubmed.ncbi.nlm.nih.gov/12345/'}
  assert.equal(textCitation(f).label,'PMID 12345')
  assert.equal(textCitation(f).url,f.provenance.url)
})

test('legacy prose containing IDs cannot become a linked citation',()=>{
  const f={source_handle:'old',pointer:'',quotation:'NCT01234567 mentioned in a quote'}
  assert.equal(textCitation(f).url,null)
  assert.equal(textCitation(f).recorded,false)
  assert.match(textCitation(f).notice,/旧版/)
  assert.equal(textCitation({...f,provenance:{version:'legacy',resolution:'not_recorded'}}).url,null)
})

test('exact falsy values and omission notices remain distinct',()=>{
  for(const value of ['',0,false,null]){
    const f=finding();f.source_field.value=value
    assert.equal(textCitation(f).fieldText,typeof value==='string'?value:JSON.stringify(value))
  }
  const f=finding()
  f.source_field={value_included:false,value:null,omission_reason:'field_exceeds_32000_characters'}
  assert.equal(textCitation(f).fieldText,null)
  assert.match(textCitation(f).fieldNote,/32,000/)
  f.source_field.omission_reason='whole_source_container'
  assert.match(textCitation(f).fieldNote,/整份文档/)
})

test('answer-only evidence export appears without changing chart-only behavior',()=>{
  const chart={source_type:'clinicaltrials',evidence_ids:['ev_1'],metadata:{}}
  const answer={source_type:'answer',evidence_ids:[],metadata:{findings:[finding()]}}
  assert.equal(evidenceExportLabel([chart]),'图表贡献证据 ↓')
  assert.equal(evidenceExportLabel([answer]),'文字来源证据 ↓')
  assert.equal(evidenceExportLabel([chart,answer]),'图表与文字来源证据 ↓')
  assert.equal(evidenceExportLabel([{...answer,metadata:{findings:[]}}]),null)
  assert.equal(evidenceExportLabel([]),null)
})

// Render the actual TSX component with synthetic fixtures; this is offline SSR,
// not an HTTP server or interactive-browser acceptance test.
const source=await readFile(new URL('./TextFinding.tsx',import.meta.url),'utf8')
let compiled=(await transformWithOxc(source,'TextFinding.tsx',{jsx:{runtime:'automatic'}})).code
compiled=compiled.replaceAll('"react/jsx-runtime"',JSON.stringify(import.meta.resolve('react/jsx-runtime')))
compiled=compiled.replaceAll("'./textEvidence'",JSON.stringify(new URL('./textEvidence.ts',import.meta.url).href))
compiled=compiled.replaceAll('"./textEvidence"',JSON.stringify(new URL('./textEvidence.ts',import.meta.url).href))
const {TextFinding}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'))

test('actual citation component renders official link, escaped source field and distinct hashes',()=>{
  const f=finding();f.source_field.value='<script>source text</script>'
  const html=renderToStaticMarkup(createElement(TextFinding,{finding:f,index:0}))
  assert.match(html,/href="https:\/\/clinicaltrials.gov\/study\/NCT01234567"/)
  assert.match(html,/Synthetic trial/)
  assert.match(html,/&lt;script&gt;source text&lt;\/script&gt;/)
  assert.doesNotMatch(html,/<script>/)
  assert.match(html,/来源容器 SHA-256/)
  assert.match(html,/单篇文档 SHA-256/)
  assert.match(html,/原始字段与来源版本/)
  assert.match(html,/current content may differ/)
})

test('actual component keeps legacy quote readable without creating an official link',()=>{
  const html=renderToStaticMarkup(createElement(TextFinding,{finding:{source_handle:'old',pointer:'/field',quotation:'Legacy quote NCT01234567'},index:1}))
  assert.match(html,/Legacy quote NCT01234567/)
  assert.match(html,/旧版结果未记录/)
  assert.match(html,/来源 2/)
  assert.doesNotMatch(html,/<a /)
})
