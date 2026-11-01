/** Run changes alone trigger the loading effect. Selecting the same run must preserve its state. */
export function selectHistoryRun(currentId:string|null,nextId:string,change:()=>void) {
  if(!nextId || nextId===currentId)return
  change()
}
