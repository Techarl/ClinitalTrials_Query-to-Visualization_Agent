import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import type { Artifact, Row } from './types'
import {cohortLabel, phaseLabel} from './presentation'

const palette = ['#176b59','#bf854a','#638ab0','#95749c','#879e63']
const axisTitles: Record<string,string> = {enrollment:'入组人数（人）',trial_duration:'试验持续时间（天）',start_year:'开始年份',completion_year:'完成年份'}
const columnTitles: Record<string,string> = {source:'机构 / 药物 A',target:'机构 / 药物 B',contributor_count:'贡献研究数',shared_interventions:'共享药物',weight:'关系权重',weight_metric:'权重口径',category:'类别',cohort:'比较组',trial_count:'研究数',year:'年份',bin_start:'区间起点',bin_end:'区间终点',nct_id:'试验编号',x_type:'X 数值类型',y_type:'Y 数值类型'}
let mapPromise: Promise<any> | null = null
const countryAliases: Record<string,string> = {'United States':'United States of America','Russian Federation':'Russia','Korea, Republic of':'South Korea','Iran, Islamic Republic of':'Iran','Viet Nam':'Vietnam','Tanzania, United Republic of':'United Republic of Tanzania','Czech Republic':'Czechia','Taiwan':'Taiwan','Congo, The Democratic Republic of the':'Democratic Republic of the Congo'}
function loadMap() {
  mapPromise ??= fetch('/world.geojson').then(r => {if (!r.ok) throw new Error('地图资产未找到'); return r.json()}).then(map => {
    map.features.forEach((f: any) => {f.properties.name = f.properties.ADMIN})
    echarts.registerMap('world', map)
    return map
  })
  return mapPromise
}

export function Chart({artifact, onEvidence}: {artifact: Artifact; onEvidence: (id: string) => void}) {
  const element = useRef<HTMLDivElement>(null)
  const instance = useRef<echarts.ECharts | null>(null)
  const [renderer, setRenderer] = useState('chart')
  const [unmapped, setUnmapped] = useState<string[]>([])
  const [error, setError] = useState('')
  const visual = artifact.visualization
  const data: Row[] = !visual ? [] : Array.isArray(visual.data) ? visual.data : visual.data.edges
  const nodeLabels: Record<string,string> = visual && !Array.isArray(visual.data) ? Object.fromEntries(visual.data.nodes.map(n=>[n.id,n.label])) : {}
  useEffect(() => {
    if (!element.current || !visual || renderer === 'table') return
    let disposed = false
    const chart = echarts.init(element.current, undefined, {renderer:'canvas'})
    instance.current = chart
    const resize = new ResizeObserver(() => chart.resize())
    resize.observe(element.current)
    const base: any = {color:palette, animationDuration:300, aria:{enabled:true}, textStyle:{fontFamily:'Segoe UI, Microsoft YaHei, sans-serif',fontSize:13,color:'#344d41'},
      tooltip:{trigger:'item', renderMode:'richText'}, legend:{bottom:0,type:'scroll',textStyle:{fontSize:13}}, grid:{top:35,right:32,left:60,bottom:70,containLabel:true},
      toolbox:{show:false}}
    async function render() {
      if (renderer === 'map') {
        const map = await loadMap()
        if (disposed) return
        const names = new Set(map.features.map((f:any) => f.properties.name))
        const missing = data.filter(r => !names.has(countryAliases[r.category] || r.category)).map(r => r.category)
        setUnmapped(missing)
        chart.setOption({...base, visualMap:{min:0,max:Math.max(1,...data.map(r=>r.trial_count)),left:0,bottom:15,inRange:{color:['#e5f0e8','#176b59']},calculable:true},
          series:[{type:'map',map:'world',roam:true,label:{show:false},emphasis:{label:{show:true}},
            data:data.filter(r=>names.has(countryAliases[r.category] || r.category)).map(r=>({...r,name:countryAliases[r.category] || r.category,value:r.trial_count}))}]})
      } else if (visual!.type === 'network_graph' && !Array.isArray(visual!.data)) {
        chart.setOption({...base,legend:{show:false},series:[{type:'graph',layout:'force',roam:true,draggable:true,zoom:.7,
          force:{repulsion:220,edgeLength:[75,160],gravity:.08},label:{show:true,position:'right',fontSize:11,width:140,overflow:'truncate'},
          edgeLabel:{show:false},lineStyle:{color:'#b6c8bd',width:1.5,curveness:.1},emphasis:{focus:'adjacency',lineStyle:{width:4}},
          data:visual!.data.nodes.map(r=>({...r,name:r.label,value:r.trial_count,symbolSize:Math.min(42,14+Math.sqrt(r.trial_count)*3),itemStyle:{color:r.entity_type==='sponsor'?palette[0]:palette[1]}})),
          links:visual!.data.edges.map(r=>({...r,value:r.weight}))}]})
      } else if (visual!.type === 'scatter') {
        const groups = [...new Set(data.map(r=>r.cohort))]
        chart.setOption({...base,legend:{...base.legend,show:groups.length>1},xAxis:{type:'value',name:axisTitles[visual!.encoding.x.title] || visual!.encoding.x.title,nameLocation:'middle',nameGap:30},
          yAxis:{type:'value',name:axisTitles[visual!.encoding.y.title] || visual!.encoding.y.title},dataZoom:[{type:'inside'}],
          series:groups.map(group=>({name:cohortLabel(group),type:'scatter',symbolSize:10,data:data.filter(r=>r.cohort===group).map(r=>({...r,value:[r.x,r.y],name:r.nct_id}))}))})
      } else {
        const label = (r:Row) => visual!.type==='histogram' ? `${Number(r.bin_start).toFixed(1)}–${Number(r.bin_end).toFixed(1)}` : String(r.category ?? r.year)
        const categories = [...new Set(data.map(label))]
        const groups = [...new Set(data.map(r=>r.cohort || 'Trials'))]
        const phase=artifact.metadata.analysis_parameters?.group_by==='phase' && artifact.metadata.analysis_parameters?.operation==='category'
        const categoryAxis={type:'category',data:categories,axisLabel:{interval:phase?0:'auto',formatter:phase?phaseLabel:undefined,fontSize:13,hideOverlap:!phase,rotate:!phase && categories.length>10?30:0},inverse:phase}
        const countAxis={type:'value',minInterval:1,name:'研究数',axisLabel:{fontSize:13},splitLine:{lineStyle:{color:'#e7ece6'}}}
        chart.setOption({...base,legend:{...base.legend,show:groups.length>1},grid:{...base.grid,left:phase?12:35,right:phase?50:25,bottom:groups.length>1?55:30},tooltip:{trigger:'item',renderMode:'richText'},
          xAxis:phase?countAxis:categoryAxis,
          yAxis:phase?categoryAxis:countAxis,
          dataZoom:categories.length>16?[{type:'slider',bottom:35},{type:'inside'}]:[],
          series:groups.map(group=>({name:cohortLabel(group),type:visual!.type==='time_series'?'line':'bar',barMaxWidth:30,symbolSize:7,label:{show:phase,position:'right',color:'#244c3e',fontSize:13},
            data:categories.map(category=>{const row=data.find(r=>(r.cohort || 'Trials')===group && label(r)===category);return row?{...row,value:row.trial_count}:null})}))})
      }
      chart.on('click', (event:any) => {if (event.data?.evidence_set_id) onEvidence(event.data.evidence_set_id)})
    }
    render().catch(e=>setError(String(e)))
    return () => {disposed=true;resize.disconnect();chart.dispose();instance.current=null}
  }, [artifact.result_id, renderer])
  if (!visual) return null
  const columns = data.length ? Object.keys(data[0]).filter(k=>!['evidence_set_id','count_receipt_id','item_id','id'].includes(k) && data.some(row=>row[k]!==null && row[k]!==undefined)) : []
  return <>
    <div className="chart-toolbar"><div className="segmented" aria-label="图表呈现方式">
      <button className={renderer==='chart'?'active':''} onClick={()=>setRenderer('chart')}>图表</button>
      {artifact.alternative_renderers.includes('map') && <button className={renderer==='map'?'active':''} onClick={()=>setRenderer('map')}>国家地图</button>}
      <button className={renderer==='table'?'active':''} onClick={()=>setRenderer('table')}>数据 / 引用</button>
    </div><button className="text-button" disabled={renderer==='table'} title={renderer==='table'?'切换到图表后可导出 PNG；表格数据可使用页面顶部 CSV 导出。':'导出当前图表图片'} onClick={()=>{const url=instance.current?.getDataURL({pixelRatio:2,backgroundColor:'#fff'});if(url){const a=document.createElement('a');a.href=url;a.download='trialscope-chart.png';a.click()}}}>导出 PNG ↗</button></div>
    {renderer==='table' ? <div key="table" className="table-scroll"><table><thead><tr>{columns.map(c=><th key={c}>{columnTitles[c] || c}</th>)}<th>来源</th></tr></thead><tbody>{data.map((r,i)=><tr key={i}>{columns.map(c=><td key={c}>{(c==='source'||c==='target') && nodeLabels[r[c]] ? nodeLabels[r[c]] : c==='cohort'?cohortLabel(String(r[c])):c==='category'?phaseLabel(String(r[c])):typeof r[c]==='object'?JSON.stringify(r[c]):String(r[c]??'—')}</td>)}<td><button onClick={()=>onEvidence(r.evidence_set_id)}>查看引用</button></td></tr>)}</tbody></table></div> : <div key="chart" className="chart" style={artifact.metadata.analysis_parameters?.group_by==='phase' && artifact.metadata.analysis_parameters?.operation==='category'?{minHeight:Math.max(280,new Set(data.map(r=>r.category)).size*43+80)}:undefined} ref={element} role="img" aria-label={artifact.title+ '。可切换数据表通过键盘查看引用。'}/>}
    {error && <p className="error" role="alert">{error}</p>}
    {renderer==='map' && <p className="caption">地理底图：Natural Earth，公共领域。{unmapped.length>0 ? `未映射国家值（保留在数据表）：${unmapped.join('、')}` : '所有当前国家值均已映射。'}</p>}
    <p className="caption">点击图表元素查看对应来源；数据表提供相同的键盘可访问入口。</p>
  </>
}
