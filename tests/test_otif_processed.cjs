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
vm.runInContext(html.split('\n').find(l=>l.startsWith('function docOtifDrivers(')),ctx);
ctx.state.cho='__all';
const sample=[row('Mar de Ajo','2026-05-01',['ANA','ANA RECARGA'],'cumple'),row('Mar de Ajo','2026-05-01',['ANA','BOB'],'no_cumple'),row('Mar de Ajo','2026-05-01',['BOB'],'pendiente'),row('Mar de Ajo','2026-05-01',[],'pendiente')];
let ranking=ctx.docOtifDrivers(sample);
assert.equal(ranking.unassigned,1);assert.equal(ranking.drivers.length,2);
let ana=ranking.drivers.find(r=>r.key==='ANA'),bob=ranking.drivers.find(r=>r.key==='BOB');
assert.equal(ana.total,2);assert.equal(ana.shared,1);assert.equal(ana.otif,50);
assert.equal(bob.total,2);assert.equal(bob.otif,0);assert.equal(bob.coverage,50);assert.equal(bob.pending,1);
ctx.state.cho='ANA';ranking=ctx.docOtifDrivers(sample);assert.equal(ranking.drivers.length,1);assert.equal(ranking.drivers[0].key,'ANA');
ctx.state.cho='__all';assert.equal(ctx.docOtifDrivers([row('Dolores','2026-06-01',['BOB'],'pendiente')]).drivers[0].otif,null);
assert.equal(ctx.docOtifDrivers([]).drivers.length,0);
console.log('Driver OTIF: unique documents, shared drivers, missing assignments, pending and selection OK');
