import test from 'node:test'
import assert from 'node:assert/strict'
import {selectHistoryRun} from './historySelection.ts'

test('clicking the active history version keeps its result and follow-up draft without a reload',()=>{
  const result={run_id:'run-A',status:'completed',task:{goal:'Phase counts'}}
  const state={runId:'run-A',run:result,query:'Only Phase 3',panel:'evidence',activeRunId:'run-A'}
  let reloads=0
  const select=id=>selectHistoryRun(state.runId,id,()=>{
    state.runId=id;state.run=null;state.query='';state.panel=null;state.activeRunId=id;reloads++
  })
  select('run-A');select('run-A')
  assert.equal(state.run,result)
  assert.equal(state.query,'Only Phase 3')
  assert.equal(state.panel,'evidence')
  assert.equal(reloads,0)
  select('run-B')
  assert.deepEqual(state,{runId:'run-B',run:null,query:'',panel:null,activeRunId:'run-B'})
  assert.equal(reloads,1)
  select('run-B') // Also safe while the changed version is still loading.
  assert.equal(reloads,1)
})
