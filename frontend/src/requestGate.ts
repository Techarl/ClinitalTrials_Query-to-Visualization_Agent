/** A response is usable only for the latest selection, including pagination. */
export function requestGate() {
  let generation = 0
  return {
    next() { const ticket = ++generation; return () => ticket === generation },
    invalidate() { generation++ },
  }
}
