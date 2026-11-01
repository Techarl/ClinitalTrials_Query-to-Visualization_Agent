import type {Row} from './types'
import {textCitation} from './textEvidence'

export function TextFinding({finding,index}:{finding:Row;index:number}) {
  const source=textCitation(finding)
  const p=finding.provenance
  return <blockquote className="text-finding">
    <p className="finding-quotation">{finding.quotation}</p>
    <footer>
      <div className="finding-source"><span className="finding-number">来源 {index+1}</span>
        {source.url?<a href={source.url} target="_blank" rel="noopener noreferrer">{source.label} ↗</a>:source.label?<strong>{source.label}</strong>:<span>已保存的来源摘录</span>}
      </div>
      {source.title && <p className="finding-title">{source.title}</p>}
      {!source.recorded && <p className="finding-notice">{source.notice}</p>}
      {source.recorded && !source.url && <p className="finding-notice">官方来源链接不可用，请核对下方保存的标识。</p>}
      <details className="finding-details"><summary>原始字段与来源版本</summary>
        <dl><dt>来源字段</dt><dd><code>{source.pointer}</code></dd>
          {source.recorded && <><dt>文档位置</dt><dd><code>{p.document_pointer || '根文档'}</code></dd>
            {p.retrieved_at && <><dt>获取时间</dt><dd>{p.retrieved_at}</dd></>}
            {finding.source_field?.serialized_character_count!==undefined && <><dt>字段字符数</dt><dd>{finding.source_field.serialized_character_count}</dd></>}
          </>}
        </dl>
        <p>{source.fieldNote}</p>
        {source.fieldText!==null && <pre className="finding-field-value">{source.fieldText}</pre>}
        {source.recorded && <p className="finding-notice">{source.notice}</p>}
        {source.recorded && p.version_notice && <p className="finding-version-original" lang="en">{p.version_notice}</p>}
        <dl className="finding-hashes">{source.hashes.map(([label,value])=><div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}</dl>
        <details className="finding-internal"><summary>内部引用标识</summary><dl>
          <dt>来源句柄</dt><dd><code>{finding.source_handle}</code></dd>
          {source.recorded && <><dt>引文标识</dt><dd><code>{p.citation_id}</code></dd><dt>哈希格式</dt><dd><code>{p.hash_serialization}</code></dd><dt>来源哈希范围</dt><dd><code>{p.source_hash_scope}</code></dd></>}
        </dl></details>
      </details>
    </footer>
  </blockquote>
}
