/* Offline UI checks with synthetic records and a fake DOM; never contacts Salesforce. */
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('docs/index.html','utf8');
const nodes = new Map(), messages = new Map(), requests = [];
class Node {
  constructor(id) { this.id=id; this.dataset={}; this.textContent=''; this.className=''; this.disabled=false; this.events={}; this.value=''; }
  get classList() { return { add:(c)=>{if(!this.classList.contains(c))this.className+=' '+c;},remove:(c)=>{this.className=this.className.split(' ').filter(x=>x!==c).join(' ');},contains:(c)=>this.className.split(' ').includes(c),toggle:(c,value)=>{if(value??!this.classList.contains(c))this.classList.add(c);else this.classList.remove(c);} }; }
  set innerHTML(value) { this.html=value; if(this.id==='actionList'){messages.clear();for(const match of value.matchAll(/data-msg="([^"]+)"/g)) messages.set(match[1],new Node('message'));} }
  get innerHTML() { return this.html||''; }
  querySelectorAll() { return []; }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(type,fn) { this.events[type]=fn; }
  focus() { this.focused=true; }
  showModal() { this.open=true; }
  close() { this.open=false; this.events.close?.(); }
}
for(const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
  const node=new Node(match[1]);
  for(const entry of match[0].matchAll(/data-([a-z-]+)="([^"]+)"/g)) node.dataset[entry[1].replace(/-([a-z])/g,(_,v)=>v.toUpperCase())]=entry[2];
  nodes.set(node.id,node);
}
const categories=['tagged','untagged','all'].map(value=>{const node=new Node(value);node.dataset.pseCategory=value;return node;});
let csv;
const document={body:{style:{}},getElementById:(id)=>{assert(nodes.has(id),'Missing DOM element '+id);return nodes.get(id);},
  querySelector:(selector)=>messages.get(/data-msg="([^"]+)"/.exec(selector)?.[1])||null,
  querySelectorAll:(selector)=>selector==='[data-pse-category]'?categories:selector==='.multi-filter'?['quarterFilter','typeFilter','segmentFilter','regionFilter','pseStatusFilter'].map(id=>nodes.get(id)):[],
  addEventListener:()=>{}, createElement:()=>({click(){},set href(value){},set download(value){}})};
const context={document,console,CSS:{escape:value=>value},setTimeout:()=>1,clearTimeout:()=>{},window:{confirm:()=>false},Blob:class{constructor(chunks){csv=chunks.join('');}},URL:{createObjectURL:()=>'',revokeObjectURL:()=>{}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('docs/app.js','utf8').replace(/load\(\);\s*$/,''),context);
const base={partnerId:'partner',stage:'08 - Closed',nonCommissionable:false,type:'Renewal',partnerRepOnTeam:false,partnerRepOnSplit:false,partnerRepActive:true,partnerRepName:'Example PSE',closeDate:'2026-11-24'};
const snapshot={config:{scopeVersion:4},rows:[{...base,id:'tagged',name:'Alpha',partnerRepNeedsPopulate:false},{...base,id:'untagged',name:'Beta',partnerRepNeedsPopulate:true,partnerRepActive:false}],summary:{total:2,tagged:0,needsUpdate:2}};
context.snapshot=snapshot;
const run=code=>vm.runInContext(code,context);
const reset=()=>{requests.length=0;run('state.query="";state.pseCategory="all";state.pseStatus.clear();hydrate(snapshot);state.writeApi.enabled=true;');};
let responder;
context.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):null;requests.push({path,body});return responder(path,body);};
const response=value=>({ok:true,status:200,json:async()=>value});
(async()=>{
 reset();
 assert.equal(nodes.get('quarterFilterButton').textContent,'Fiscal Quarter–Year');
 assert.equal(nodes.get('typeFilterButton').textContent,'Opportunity Type');
 for(const [date,label] of Object.entries({'2026-01-31':'FY2026 Q4','2026-02-01':'FY2027 Q1','2026-04-30':'FY2027 Q1','2026-05-01':'FY2027 Q2','2026-07-31':'FY2027 Q2','2026-08-01':'FY2027 Q3','2026-10-31':'FY2027 Q3','2026-11-01':'FY2027 Q4','2027-01-31':'FY2027 Q4','2027-02-01':'FY2028 Q1'})) assert.equal(run(`fiscalQuarterFromDate('${date}')`),label);
 nodes.get('search').events.input({target:{value:'Alpha'}});
 assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')),['tagged']);
 nodes.get('processFlowBtn').events.click();assert(nodes.get('processFlow').open);
 nodes.get('closeProcessFlow').events.click();assert(!nodes.get('processFlow').open);
 assert.equal(run('state.query'),'alpha');
 reset();
 responder=()=>response({...snapshot.rows[0],oppName:'Alpha',eligible:true});
 await run('updateOne("tagged", {disabled:false})');
 assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/preview');
 assert(messages.get('tagged').textContent.includes('Cancelled'));assert.equal(run('state.busy'),false);
 reset();context.window.confirm=()=>true;
 responder=path=>response(path==='/api/preview'?{...snapshot.rows[0],eligible:true}:{...snapshot.rows[0],done:true,partnerRepOnTeam:true,partnerRepOnSplit:true});
 await run('Promise.all([updateOne("tagged", {disabled:false}), updateOne("tagged", {disabled:false})])');
 assert.deepStrictEqual(requests.map(item=>item.path),['/api/preview','/api/write']);
 assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')),['untagged']);
 reset();
 responder=path=>response(path==='/api/preview'?{...snapshot.rows[0],eligible:true}:{...snapshot.rows[0],done:false,partnerRepOnTeam:true,error:'Example Salesforce validation error'});
 await run('updateOne("tagged", {disabled:false})');
 assert(messages.get('tagged').textContent.includes('validation error'));
 assert.equal(run('state.busy'),false);
 reset();run('state.pseStatus=new Set(["Inactive"]);state.pseCategory="untagged"');
 responder=path=>response(path==='/api/preview-all'?{idsToAdd:['untagged'],states:{}}:{states:{untagged:{...snapshot.rows[1],partnerRepNeedsPopulate:false,partnerRepOnTeam:true,partnerRepOnSplit:true}},succeeded:1,fieldPopulated:1,teamAdded:1,splitAdded:1,failed:0});
 await run('updateAll()');
 assert(requests.every(item=>item.body.oppIds.length===1&&item.body.oppIds[0]==='untagged'));
 assert.equal(requests[1].body.confirm,'CONFIRM');
 reset();run('state.pseCategory="tagged";exportCsv()');assert(csv.includes('fiscalQuarter'));assert(csv.includes('Alpha'));assert(!csv.includes('Beta'));
 responder=()=>({ok:false,status:401,json:async()=>({needsLogin:true,error:'Salesforce login expired'})});
 await assert.rejects(()=>run('postJson("/api/preview",{})'),/login expired/);assert.equal(run('state.writeApi.loggedIn'),false);
 requests.length=0;
 responder=()=>response({authMode:'oauth',loggedIn:false,refreshEnabled:false,writeEnabled:false});
 await run('detectApi()');
 assert(!nodes.get('connectSfBtn').classList.contains('hidden'));
 assert(!nodes.get('salesforceConnection').classList.contains('hidden'));
 await nodes.get('connectSfBtn').events.click();
 assert(nodes.get('connectionMsg').textContent.includes('SF_CLIENT_ID'));
 assert.deepStrictEqual(requests.map(item=>item.path),['/api/health']);
 responder=()=>response({authMode:'oauth',loggedIn:false,refreshEnabled:true,writeEnabled:true});
 await run('detectApi()'); requests.length=0;
 let authHandler, opened;
 context.window.location={origin:'https://dashboard.example'};
 context.window.open=(url)=>{opened=url;return {closed:false};};
 context.window.addEventListener=(type,handler)=>{authHandler=handler;};
 context.window.removeEventListener=()=>{};
 context.setInterval=()=>1;context.clearInterval=()=>{};
 const connecting=run('connectSalesforce()');
 assert.equal(opened,'/oauth/login?next=/');
 assert(nodes.get('connectSfBtn').disabled);
 responder=()=>response({authMode:'oauth',loggedIn:true,refreshEnabled:true,writeEnabled:true});
 authHandler({origin:'https://dashboard.example',data:{sfAuth:true}});
 await connecting;
 assert(nodes.get('salesforceConnection').classList.contains('hidden'));
 assert(nodes.get('refreshMsg').textContent.includes('Connected to Salesforce'));
 assert.deepStrictEqual(requests.map(item=>item.path),['/api/health']);
 responder=()=>response({authMode:'local',refreshEnabled:true,writeEnabled:true});
 await run('detectApi()');
 assert(nodes.get('connectSfBtn').classList.contains('hidden'));
 console.log('UI checks passed: real HTML bindings, fiscal boundaries, immediate search, process navigation, cancellation, duplicate-click guard, single/bulk results, partial errors, CSV and expired login.');
})().catch(error=>{console.error(error);process.exitCode=1;});
