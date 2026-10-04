// Isolated JavaScript logic checks with a minimal DOM stub. Not a real browser test.
const fs=require('fs'), vm=require('vm'), assert=require('assert');
class El {
 constructor(tag='div'){this.tag=tag;this.dataset={};this.listeners={};this.children=[];this.value='';this.textContent='';}
 addEventListener(name,fn){this.listeners[name]=fn;}
 appendChild(e){this.children.push(e);return e;}
 replaceChildren(...e){this.children=e;}
 focus(){}
}
const ids={};for(const id of ['pos','q','results','cart-body','err','empty-cart','t-gross','t-disc','t-tax','t-total','t-tax-label','row-tax','row-disc','paid','method','change','checkout','cust-note','notes'])ids[id]=new El();
ids.pos.dataset={taxRate:'0',taxInclusive:'1',currency:'K',maxDiscount:'10',canOverride:'0',searchUrl:'/search',checkoutUrl:'/checkout'};
ids.method.value='cash';
const doc={getElementById:id=>ids[id],querySelector:()=>({value:'token'}),createElement:tag=>new El(tag),createTextNode:t=>({textContent:t})};
const part={id:1,name:'Filter',sku:'FILTER',price:100,trade_price:90,qty:20,unit:'each',barcode:''};
vm.runInNewContext(fs.readFileSync('static/js/pos.js','utf8'),{document:doc,window:{},fetch:async()=>({json:async()=>({results:[part]})}),setTimeout,clearTimeout});
const results=[];
function check(test,expected){const actual=ids['t-total'].textContent;results.push({test,result:actual===expected?'Pass':'Fail',expected,actual});}
(async()=>{
 ids.q.value='FILTER';ids.q.listeners.keydown({key:'Enter',preventDefault(){}});
 await new Promise(r=>setTimeout(r,10));
 check('JS_POS_01_retail_total','K100.00');
 ids.pos.listeners['picker:select']({detail:{id:1,type:'trade',name:'Trade',credit_limit:1000,balance:0}});
 check('JS_POS_02_trade_total','K90.00');
 ids.pos.listeners['picker:clear']();
 check('JS_POS_03_clear_trade_customer_restores_retail','K100.00');
 const path=require('path');const out=process.env.PARTSDESK_UAT_OUTPUT||path.join(__dirname,'uat_results');fs.mkdirSync(out,{recursive:true});
 fs.writeFileSync(path.join(out,'js-results.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify(results,null,2));
 process.exitCode=results.some(x=>x.result==='Fail')?1:0;
})();
