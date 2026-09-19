const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('plantilla_dashboard.html','utf8'),ctx={};vm.createContext(ctx);
for(const name of ['wavg','rechUniquePdv','rechNds'])vm.runInContext(html.split('\n').find(l=>l.startsWith('function '+name+'(')),ctx);
assert.equal(ctx.rechUniquePdv([{pdv_clientes:['1','2']},{pdv_clientes:['2','3']}]),3);
assert.equal(ctx.rechUniquePdv([{pdv_clientes:['1']},{}]),null);
assert.equal(ctx.rechNds([{nds:null,pedidos_pdv_atendidos:100},{nds:0,pedidos_pdv_atendidos:10},{nds:100,pedidos_pdv_atendidos:10}]),50);
console.log('Rejection metrics: distinct customers across days and valid NDS denominator OK');
