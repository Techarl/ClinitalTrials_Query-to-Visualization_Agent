import {test} from 'node:test'
import assert from 'node:assert/strict'
import {submissionGate} from './submissionGate.ts'
import {groupedHistory,scopeLabels,evidenceSelection,analysisNotes,phaseLabel} from './presentation.ts'

test('submission blocks duplicate keyboard/click entry and reuses key for a failed transport retry',()=>{
  let next=0
  const gate=submissionGate(()=>String(++next))
  assert.equal(gate.begin('payload A'),'1')
  assert.equal(gate.begin('payload A'),null)
  assert.equal(gate.begin('payload B'),null)
  gate.finish(false)
  assert.equal(gate.begin('payload A'),'1')
  gate.finish(false)
  assert.equal(gate.begin('payload B'),'2')
  gate.finish(true)
  assert.equal(gate.begin('payload B'),'3')
})

test('history groups turns without losing historical branches or original title',()=>{
  const entry=(run_id,session_id,parent_run_id,query,day)=>({run_id,session_id,parent_run_id,query,created_at:`2026-11-0${day}`,status:'completed'})
  const grouped=groupedHistory([entry('branch','s1','root','Only Phase 3',3),entry('other','s2',null,'Asthma',2),entry('follow','s1','root','By year',2),entry('root','s1',null,'Melanoma',1)])
  assert.equal(grouped.length,2)
  assert.equal(grouped[0].title,'Melanoma')
  assert.deepEqual(grouped[0].turns.map(t=>t.run_id),['root','follow','branch'])
  assert.equal(grouped[0].turns[2].parent_run_id,'root')
})

test('scope and phase wording preserves joint phases and explicit field semantics',()=>{
  assert.equal(phaseLabel('Phase 1 / Phase 2'),'1 期 / 2 期')
  assert.deepEqual(scopeLabels({filters:{condition:'melanoma',phases:['PHASE3'],intervention_match:'any'},date_range:{field:'completion',lower:'2020-01-01',upper:null}}),['疾病：melanoma','阶段：3 期','完成日期：2020-01-01 至 不限结束'])
})

test('evidence heading binds to the selected point rather than only the chart title',()=>{
  const artifact={title:'Phase distribution',metadata:{analysis_parameters:{operation:'category',group_by:'phase'}},visualization:{data:[{category:'Phase 3',cohort:'Population',trial_count:26,evidence_set_id:'A'},{category:'Phase 4',cohort:'Population',trial_count:2,evidence_set_id:'B'}]}}
  assert.deepEqual(evidenceSelection(artifact,'A'),{title:'3 期',value:'26 项研究',cohort:'当前研究范围'})
  assert.equal(evidenceSelection(artifact,'B').value,'2 项研究')
})

test('network evidence identifies both endpoint labels and the shared-drug metric',()=>{
  const artifact={title:'Network',metadata:{analysis_parameters:{relationship:'sponsor_shared_drug'}},visualization:{data:{nodes:[{id:'a',label:'Sponsor A'},{id:'b',label:'Sponsor B'}],edges:[{source:'a',target:'b',weight:3,evidence_set_id:'edge'}]}}}
  assert.deepEqual(evidenceSelection(artifact,'edge'),{title:'Sponsor A ↔ Sponsor B',value:'3 种共享药物',cohort:''})
})

test('qualitative interpretation is not described as verified population statistics',()=>{
  const notes=analysisNotes({source_type:'answer',metadata:{limitations:['Only the abstract was available.']}})
  assert.match(notes[0],/不是总体统计/)
  assert.match(notes[1],/解读仍需/)
  assert.equal(notes[2],'Only the abstract was available.')
})
