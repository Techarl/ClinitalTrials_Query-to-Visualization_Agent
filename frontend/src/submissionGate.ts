/** Synchronous re-entry guard plus a stable idempotency key for transport retries. */
export function submissionGate(newId:()=>string) {
  let pending=false, payload='', key=''
  return {
    begin(value:string) {
      if(pending)return null
      if(value!==payload || !key){payload=value;key=newId()}
      pending=true
      return key
    },
    finish(success:boolean) {pending=false;if(success){payload='';key=''}},
  }
}
