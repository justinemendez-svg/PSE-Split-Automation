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
  select() { this.selected=true; }
  showModal() { this.open=true; }
  close() { this.open=false; this.events.close?.(); }
}
for(const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
  const node=new Node(match[1]);
  for(const entry of match[0].matchAll(/data-([a-z-]+)="([^"]+)"/g)) node.dataset[entry[1].replace(/-([a-z])/g,(_,v)=>v.toUpperCase())]=entry[2];
  nodes.set(node.id,node);
}
const categories=['tagged','different','untagged','all'].map(value=>{const node=new Node(value);node.dataset.pseCategory=value;return node;});
let csv;
const document={body:{style:{overflow:""}},getElementById:(id)=>{assert(nodes.has(id),'Missing DOM element '+id);return nodes.get(id);},
  querySelector:(selector)=>messages.get(/data-msg="([^"]+)"/.exec(selector)?.[1])||null,
  querySelectorAll:(selector)=>selector==='[data-pse-category]'?categories:selector==='.multi-filter'?['quarterFilter','typeFilter','segmentFilter','regionFilter','pseStatusFilter'].map(id=>nodes.get(id)):[],
  addEventListener:()=>{}, createElement:()=>({click(){},set href(value){},set download(value){}})};
const context={document,console,CSS:{escape:value=>value},setTimeout:()=>1,clearTimeout:()=>{},window:{confirm:()=>false},Blob:class{constructor(chunks){csv=chunks.join('');}},URL:{createObjectURL:()=>'',revokeObjectURL:()=>{}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('docs/app.js','utf8').replace(/load\(\);\s*$/,''),context);
const base={partnerType:null,partnerId:'partner',partnerOwnerId:'005000000000001AAA',partnerRepId:'005000000000001AAA',stage:'08 - Closed',nonCommissionable:false,type:'Renewal',partnerRepOnTeam:false,partnerRepOnSplit:false,partnerRepActive:true,partnerRepName:'Example PSE',closeDate:'2026-11-24'};
const snapshot={config:{scopeVersion:5},rows:[{...base,id:'tagged',name:'Alpha',partnerRepNeedsPopulate:false},{...base,id:'untagged',name:'Beta',partnerRepNeedsPopulate:true,partnerRepActive:false}],summary:{total:2,tagged:0,needsUpdate:2}};
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
 reset();
 run('state.rows.push({...state.rows[0],id:"different",name:"Different rep",partnerRepId:"005000000000002AAA"});renderUpdate()');
 assert.equal(nodes.get('cWithPse').textContent,'1');assert.equal(nodes.get('cDifferentPse').textContent,'1');
 assert.equal(nodes.get('cWithoutPse').textContent,'1');assert.equal(nodes.get('cNeedsUpdate').textContent,'3');
 categories.find(node=>node.dataset.pseCategory==='different').events.click();
 assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')),['different']);
 assert(nodes.get('categoryLabel').textContent.includes('different from'));
 run('exportCsv()');assert(csv.includes('Different rep'));assert(!csv.includes('Alpha'));assert(!csv.includes('Beta'));
 responder=path=>response(path==='/api/preview-all'?{idsToAdd:['different'],states:{}}:{states:{},succeeded:1,failed:0});
 await run('updateAll()');
 assert(requests.every(item=>item.body.oppIds.length===1&&item.body.oppIds[0]==='different'));
 reset();
 responder=()=>({ok:false,status:401,json:async()=>({needsLogin:true,error:'Salesforce login expired'})});
 await assert.rejects(()=>run('postJson("/api/preview",{})'),/login expired/);assert.equal(run('state.writeApi.loggedIn'),false);
 requests.length=0;
 responder=()=>response({authMode:'upload',uploadEnabled:true,refreshEnabled:false,writeEnabled:false});
 await run('detectApi()');
 assert(!nodes.get('uploadDataBtn').classList.contains('hidden'));
 nodes.get('uploadDataBtn').events.click();assert(nodes.get('uploadPage').open);
 nodes.get('firstTimeUploadLink').events.click();
 assert(nodes.get('setupInstructions').open); assert(nodes.get('uploadPage').open);
 nodes.get('closeSetupInstructions').events.click();
 assert(!nodes.get('setupInstructions').open); assert(nodes.get('uploadPage').open);
 assert(nodes.get('firstTimeUploadLink').focused);
 nodes.get('closeUploadPage').events.click();assert(!nodes.get('uploadPage').open);
 nodes.get('processFlowBtn').events.click();
 nodes.get('firstTimeProcessLink').events.click();
 assert(!nodes.get('processFlow').open); assert(nodes.get('uploadPage').open); assert(nodes.get('setupInstructions').open);
 nodes.get('processFlowBtn').focused=false;
 nodes.get('processFlow').events.close(); // Browsers may dispatch close after the next dialog opens.
 assert.equal(document.body.style.overflow,'hidden');
 assert(!nodes.get('processFlowBtn').focused);
 nodes.get('setupInstructions').close(); // Native Escape follows this same close event.
 assert(nodes.get('uploadPage').open);
 assert.equal(document.body.style.overflow,'hidden');
 let copiedPrompt;
 nodes.get('setupPrompt').value='Example complete setup prompt';
 context.navigator={clipboard:{writeText:async value=>{copiedPrompt=value;}}};
 await nodes.get('copySetupPrompt').events.click();
 assert.equal(copiedPrompt,nodes.get('setupPrompt').value);
 nodes.get('downloadPrompt').value='Download fresh Salesforce data using my own account';
 await nodes.get('copyDownloadPrompt').events.click();
 assert.equal(copiedPrompt,nodes.get('downloadPrompt').value);
 assert(nodes.get('copyDownloadMsg').textContent.includes('Copied'));
 context.navigator.clipboard.writeText=async()=>{throw new Error('Clipboard permission denied');};
 await nodes.get('copySetupPrompt').events.click();
 assert(nodes.get('setupPrompt').selected);
 assert(nodes.get('copySetupMsg').textContent.includes('copy the highlighted prompt'));
 await nodes.get('copyDownloadPrompt').events.click();
 assert(nodes.get('downloadPrompt').selected);
 assert(nodes.get('copyDownloadMsg').textContent.includes('copy the highlighted prompt'));
 nodes.get('closeUploadPage').events.click();
 assert.equal(document.body.style.overflow,'');
 assert(html.includes('MY_ACTUAL_SYNCED_FOLDER_PATH'));
 assert(html.includes('Do not modify Salesforce records'));
 assert(html.includes('Close · Back to Upload'));
 await run('previewUpload({size:10,text:async()=>"invalid JSON"})');
 assert(nodes.get('confirmUploadBtn').disabled);
 context.uploadSnapshot={...snapshot,generatedAt:'2026-11-24T00:00:00+00:00'};
 await run('previewUpload({size:100,text:async()=>JSON.stringify(uploadSnapshot)})');
 assert(!nodes.get('confirmUploadBtn').disabled);
 assert.equal(requests.length,1); // Selecting a file does not upload it.
 requests.length=0;
 responder=()=>response({ok:true,snapshot:context.uploadSnapshot});
 await run('uploadData()');
 assert.deepStrictEqual(requests.map(item=>item.path),['/api/upload']);
 assert.equal(requests[0].body.manual,true);
 assert(nodes.get('uploadMsg').textContent.includes('Upload complete'));
 responder=()=>({ok:false,status:400,json:async()=>({error:'Invalid Opportunity ID'})});
 await run('uploadData()');
 assert(nodes.get('uploadMsg').textContent.includes('previous saved data was kept'));
 assert.equal(run('state.rows.length'),2);
 responder=()=>{throw new Error('Connection lost after sending upload');};
 await run('uploadData()');
 assert(nodes.get('uploadMsg').textContent.includes('Upload status could not be confirmed'));
 assert(!nodes.get('uploadMsg').textContent.includes('previous saved data was kept'));
 await run('previewUpload({size:4,text:async()=>"null"})');
 assert(nodes.get('confirmUploadBtn').disabled);
 await run('previewUpload({size:30*1024*1024,text:async()=>{throw new Error("must not read oversized file");}})');
 assert(nodes.get('uploadMsg').textContent.includes('25 MB'));
 let finishEarlierFile;
 context.earlierFile={size:100,text:()=>new Promise(resolve=>{finishEarlierFile=resolve;})};
 const earlierPreview=run('previewUpload(earlierFile)');
 context.laterSnapshot={...context.uploadSnapshot,rows:[context.uploadSnapshot.rows[1]]};
 await run('previewUpload({size:100,text:async()=>JSON.stringify(laterSnapshot)})');
 finishEarlierFile(JSON.stringify(context.uploadSnapshot));
 await earlierPreview;
 requests.length=0;
 responder=()=>response({ok:true,snapshot:context.laterSnapshot});
 await run('uploadData()');
 assert.equal(requests[0].body.snapshot.rows.length,1);
 assert.equal(requests[0].body.snapshot.rows[0].id,'untagged');
 responder=()=>response({refreshEnabled:true,writeEnabled:true});
 await run('detectApi()');assert(nodes.get('uploadDataBtn').classList.contains('hidden'));
 console.log('UI checks passed: real HTML bindings, fiscal boundaries, immediate search, process navigation, cancellation, duplicate-click guard, single/bulk results, partial errors, CSV and expired login.');
})().catch(error=>{console.error(error);process.exitCode=1;});
