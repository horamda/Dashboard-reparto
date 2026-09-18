const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(require('path').join(__dirname,'..','plantilla_dashboard.html'),'utf8');
for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi))if(m[1].trim())new vm.Script(m[1]);
const start=html.indexOf('function otifFilteredRows('),end=html.indexOf('function wavg(',start);
const context={DATA:{},state:{suc:'__all',cho:'__all'},unidadOk:()=>true,periodOk:()=>true,sucNorm:s=>s,cleanPersona:s=>s};vm.createContext(context);vm.runInContext(html.slice(start,end),context);
const row=(resultado,mes='2026-05')=>({resultado,mes,pedidos:2,visitas:[{}],choferes:['Ana'],suc:'Central'});
let result=context.otifCalc([row('cumple'),row('no_cumple'),row('pendiente')]);
assert.equal(result.total,3);assert.equal(result.evaluated,2);assert.equal(result.unknown,1);assert.equal(result.otif,50);assert.ok(Math.abs(result.coverage-200/3)<1e-10);assert.equal(result.pedidos,6);
assert.equal(context.otifCalc([]).otif,null);assert.equal(context.otifCalc([row('pendiente')]).otif,null);
const months=context.otifMes([row('cumple'),row('no_cumple','2026-06')]);assert.equal(months[0].otif,100);assert.equal(months[1].otif,0);
context.DATA.otif_clientes_dia=[row('cumple'),{...row('no_cumple'),suc:'Other',choferes:['Bob']}];
context.state.suc='Central';assert.equal(context.otifFilteredRows().length,1);context.state.suc='__all';context.state.cho='Bob';assert.equal(context.otifFilteredRows()[0].resultado,'no_cumple');
console.log('OTIF customer/day: sums, coverage, pending, monthly aggregation, filters and JS syntax OK');

assert.equal(context.otifCalc([{...row('no_cumple'),visitas:[]}]).otif,null);
