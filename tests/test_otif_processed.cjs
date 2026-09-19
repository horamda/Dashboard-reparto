const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('plantilla_dashboard.html','utf8');
const ctx={state:{unidad:'__all',suc:'__all',mes:'__all',fecha:'__all',cho:'__all'},UNIDADES:{casa_central:{sucs:['Mar de Ajo']},sucursal_dolores:{sucs:['Dolores','Chascomus']}},fmtPct:x=>x};
vm.createContext(ctx);
for(const name of ['sucNorm','cleanPersona']){const line=html.split('\n').find(l=>l.startsWith('const '+name+'='));vm.runInContext(line,ctx)}
for(const name of ['sucAliases','unidadOk','docOtifScope','docOtifStats']){const line=html.split('\n').find(l=>l.startsWith('function '+name+'('));vm.runInContext(line,ctx)}
vm.runInContext(html.split('\n').find(l=>l.startsWith('const periodOk=')),ctx);
const row=(suc,fecha,choferes,resultado)=>({suc,fecha,mes:fecha.slice(0,7),choferes,resultado});
ctx.rows=[row('Mar de Ajo','2026-05-01',['ANA RECARGA'],'cumple'),row('Mar de Ajo','2026-05-01',['ANA'],'no_cumple'),row('Dolores','2026-06-01',['BOB'],'pendiente'),row('Chascomus','2026-06-02',['BOB'],'cumple')];
const run=()=>vm.runInContext('docOtifScope(rows)',ctx);
assert.equal(run().length,4);ctx.state.unidad='casa_central';assert.equal(run().length,2);
ctx.state.unidad='sucursal_dolores';assert.equal(run().length,2);ctx.state.suc='Dolores';assert.equal(run().length,1);
ctx.state.unidad=ctx.state.suc='__all';ctx.state.mes='2026-05';assert.equal(run().length,2);ctx.state.fecha='2026-06-01';assert.equal(run().length,0);
ctx.state.mes=ctx.state.fecha='__all';ctx.state.cho='ANA';assert.equal(run().length,2);assert.equal(ctx.docOtifStats(run()).otif,50);
ctx.state.cho='Nobody';assert.equal(ctx.docOtifStats(run()).otif,null);
const stats=ctx.docOtifStats(ctx.rows);assert.equal(stats.pending,1);assert.equal(stats.coverage,75);assert.ok(Math.abs(stats.otif-200/3)<1e-9);
console.log('Processed OTIF: all dashboard filters, separate branches, pending denominator OK');
