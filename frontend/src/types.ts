export type Row = Record<string, any>
export interface Artifact {
  artifact_id: string; result_id: string; goal_id: string; title: string; source_type: string; scope_id: string | null;
  metric: string; visualization: {type: string; title: string; data: Row[] | {nodes: Row[]; edges: Row[]}; encoding: Row} | null;
  coverage: {statistics_basis: string; count_exactness: string; population_coverage: string; evidence_coverage: string; fetched_count: number; matched_count: number | null};
  warnings: {code: string; message: string; threshold: number | null}[];
  metadata: Row; evidence_ids: string[]; alternative_renderers: string[]; dependencies: string[];
}
export interface Run {
  run_id: string; session_id: string; status: string; artifacts: Artifact[]; task: Row | null;
  message: string; questions: string[]; unmet_goals: string[]; usage: Row;
}
export interface Event {run_id: string; sequence: number; timestamp: string; type: string; data: Row}
export interface History {run_id: string; session_id: string; parent_run_id: string | null; created_at: string; status: string; query: string}
export interface Evidence {rule: Row; citations: {nct_id: string; dataset_id: string; url: string; fields: {path: string; value: any}[]; missing_fields: string[]}[]; total_contributors: number; offset: number; next_offset: number | null}

export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {...init, headers: {'Content-Type': 'application/json', ...init?.headers}})
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`)
  }
  return response.json()
}
export const terminal = new Set(['completed', 'partial', 'failed', 'cancelled', 'limited', 'needs_clarification'])
export const statusText: Record<string,string> = {queued:'等待执行',running:'正在分析',completed:'分析完成',partial:'部分完成',failed:'执行失败',cancelled:'已取消',limited:'遇到限制',needs_clarification:'需要补充信息'}
