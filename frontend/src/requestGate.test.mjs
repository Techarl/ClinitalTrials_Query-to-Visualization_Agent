import {test} from 'node:test'
import assert from 'node:assert/strict'
import {requestGate} from './requestGate.ts'

test('late evidence and pagination responses cannot replace the latest selection', async () => {
  const gate=requestGate()
  let selected
  let resolveA, resolveB
  const a=new Promise(resolve=>{resolveA=resolve}), b=new Promise(resolve=>{resolveB=resolve})
  const validA=gate.next(), pa=a.then(value=>{if(validA())selected=value})
  const validB=gate.next(), pb=b.then(value=>{if(validB())selected=value})
  resolveB('B');await pb
  resolveA('A');await pa
  assert.equal(selected,'B')
  const old=gate.next();gate.invalidate()
  assert.equal(old(),false)
})
