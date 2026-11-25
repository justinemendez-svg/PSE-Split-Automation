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
  querySelectorAll:(selector)=>selector==='[data-pse-category]'?categories:selector==='.multi-filter'?['quarterFilter','monthFilter','typeFilter','segmentFilter','regionFilter','pseStatusFilter'].map(id=>nodes.get(id)):[],
  addEventListener:()=>{}, createElement:()=>({click(){},set href(value){},set download(value){}})};
class FixedDate extends Date { constructor(...args) { super(...(args.length ? args : ["2026-11-25T04:00:00Z"])); } static now() { return new Date("2026-11-25T04:00:00Z").getTime(); } }
const context={Date:FixedDate,document,console,CSS:{escape:value=>value},setTimeout:()=>1,clearTimeout:()=>{},window:{confirm:()=>false},Blob:class{constructor(chunks){csv=chunks.join('');}},URL:{createObjectURL:()=>'',revokeObjectURL:()=>{}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('docs/app.js','utf8').replace(/load\(\);\s*$/,''),context);
const base={partnerType:null,partnerId:'partner',partnerOwnerId:'005000000000001AAA',partnerRepId:'005000000000001AAA',stage:'08 - Closed',nonCommissionable:false,type:'Renewal',partnerRepOnTeam:false,partnerRepOnSplit:false,partnerRepActive:true,partnerRepName:'Example PSE',closeDate:'2026-11-24'};
const snapshot={config:{scopeVersion:5},rows:[{...base,id:'tagged',name:'Alpha',partnerRepNeedsPopulate:false},{...base,id:'untagged',name:'Beta',partnerRepNeedsPopulate:true,partnerRepActive:false}],summary:{total:2,tagged:0,needsUpdate:2}};
snapshot.config.scopeVersion = 6;
context.snapshot=snapshot;
const run=code=>vm.runInContext(code,context);
const reset=()=>{requests.length=0;run('state.query="";state.pseCategory="all";state.pseStatus.clear();hydrate(snapshot);state.writeApi.enabled=true;');};
let responder;
context.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):null;requests.push({path,body});return responder(path,body);};
const response=value=>({ok:true,status:200,json:async()=>value});
(async()=>{
 reset();
   context.localSnapshot={...snapshot,rows:[...snapshot.rows,
     {...base,id:'other',name:'Other Type',type:'Other',partnerRepNeedsPopulate:false,partnerRegion:'APAC',partnerOwnerProfile:'Owner profile',taggedPseProfile:'<PSE profile>',partnerDealSource:'Referral',dealType:'Direct',totalBookingArrUsd:1234.5},
     {...base,id:'aws',partnerName:'AWS Marketplace'},
     {...base,id:'startup',partnerType:'Startup Program Partner'}]};
   run('hydrate(localSnapshot)');
   assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')).sort(),['other','tagged','untagged']);
   assert(nodes.get('actionList').innerHTML.includes('APAC · Referral · Direct · —'));
   assert(!nodes.get('actionList').innerHTML.includes('additional-fields'));
   assert(!nodes.get('actionList').innerHTML.includes('PSE profile'));
   assert(Array.from(run('state.filterOptions.type')).includes('Other'));
   run('state.type=new Set(["Other"]);exportCsv()');
   assert(csv.includes('partnerOwnerProfile'));assert(csv.includes('1234.5'));assert(!csv.includes('Alpha'));
   run('state.type.clear()');reset();
   context.planSnapshot={...snapshot,rows:[
     {...base,id:'ready',name:'Ready example',membershipDetailsLoaded:true,updatePlan:{status:'ready',changes:[{object:'OpportunityTeamMember',action:'Correct Team role'}],targetName:'Example PSE',targetRole:'Partner Account Executive',teamCurrent:[],splitCurrent:[]}},
     {...base,id:'review',name:'Review example',partnerRepId:'005000000000002AAA',partnerOwnerName:'Partner Owner',partnerRepName:'Different PSE',membershipDetailsLoaded:true,updatePlan:{status:'review',changes:[],teamCurrent:[],splitCurrent:[]}},
     {...base,id:'inactive',name:'Inactive example',partnerRepActive:false,updatePlan:{status:'rvp',changes:[]}}
   ]};
   context.planSnapshot.rows.push({...context.planSnapshot.rows[0]},{...context.planSnapshot.rows[1]});
   run('hydrate(planSnapshot)');
   assert.equal(run('state.rows.length'),3);
   assert.equal(nodes.get('cNeedsUpdate').textContent,'3');
   assert.equal(nodes.get('bulkCount').textContent,'3');
   assert.equal(run('uniqueOpportunityRows([{id:"006000000000001"},{id:"006000000000001AAA"},{id:null}]).length'),1);
   assert(nodes.get('actionList').innerHTML.includes('mismatch-name'));
   assert(nodes.get('actionList').innerHTML.includes('Current'));
   assert(nodes.get('actionList').innerHTML.includes('Required'));
   assert(nodes.get('actionList').innerHTML.includes('Correct Team role'));
   assert(nodes.get('actionList').innerHTML.includes('Review assignment'));
   assert(nodes.get('actionList').innerHTML.includes('Set covering PSE'));
   assert.equal(nodes.get('updateAllBtn').textContent,'Update all ready — 1 in Salesforce →');
   assert.equal(nodes.get('readyFilterCount').textContent,'1');
   assert.equal(nodes.get('rvpFilterCount').textContent,'1');
   run('state.updatePage=4');
   nodes.get('readinessRvp').events.click();
   assert.equal(run('state.updatePage'),1);
   assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')),['inactive']);
   assert.equal(nodes.get('bulkCount').textContent,'3');
   assert.equal(nodes.get('readyFilterCount').textContent,'1');
   assert(nodes.get('updateAllBtn').disabled);
   assert.equal(requests.length,0);
   run('exportCsv()');assert(csv.includes('Inactive example'));assert(!csv.includes('Ready example'));
   run('state.query="review"');assert.equal(run('filteredRows().length'),0);run('state.query=""');
   nodes.get('readinessAll').events.click();assert.equal(run('filteredRows().length'),3);
   nodes.get('readinessReady').events.click();
   assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')),['ready']);
   assert.equal(nodes.get('readinessReady')['aria-pressed'],'true');
   responder=()=>response({idsToAdd:['ready'],states:{}});
   await run('updateAll()');
   assert.equal(requests.length,1);assert.deepStrictEqual(requests[0].body.oppIds,['ready']);
   context.dateSnapshot={...snapshot,rows:[
     {...base,id:'aug',closeDate:'2026-08-15',partnerRegion:'APAC',region:'EMEA'},
     {...base,id:'nov',closeDate:'2026-11-15',partnerRegion:'EMEA',region:'APAC'},
     {...base,id:'dec',closeDate:'2026-12-15',partnerRegion:'APAC',region:'EMEA'},
     {...base,id:'jan',closeDate:'2027-01-15',partnerRegion:'APAC',region:'EMEA'},
     {...base,id:'feb',closeDate:'2027-02-15',partnerRegion:'EMEA',region:'APAC'}
   ]};
   run('state.readinessFilter="all";state.fiscalQuarter.clear();state.month.clear();state.region.clear();hydrate(dateSnapshot)');
   assert.equal(nodes.get('monthFilterButton').textContent,'Month');
   run('state.region=new Set(["APAC"])');
   assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')).sort(),['aug','dec','jan']);
   run('state.region.clear()');
   const quarterChange=(value,checked)=>nodes.get('quarterFilter').events.change({target:{matches:()=>true,value,checked}});
   quarterChange('FY2027 Q4',true);
   assert.deepStrictEqual(Array.from(run('state.filterOptions.month')),['2026-11','2026-12','2027-01']);
   assert(nodes.get('monthFilterMenu').innerHTML.includes('January 2027'));
   assert(!nodes.get('monthFilterMenu').innerHTML.includes('August'));
   nodes.get('monthFilter').events.change({target:{matches:()=>true,value:'2027-01',checked:true}});
   assert.equal(nodes.get('monthFilterButton').textContent,'January 2027');
   assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')),['jan']);
   quarterChange('FY2027 Q3',true);assert.equal(run('state.filterOptions.month.length'),6);
   quarterChange('FY2027 Q4',false);
   assert.equal(run('state.month.size'),0);
   assert.deepStrictEqual(Array.from(run('state.filterOptions.month')),['2026-08','2026-09','2026-10']);
   assert.deepStrictEqual(Array.from(run('filteredRows().map(row=>row.id)')),['aug']);
   nodes.get('quarterFilter').events.click({target:{closest:()=>({hasAttribute:()=>false})},stopPropagation:()=>{}});
   assert.equal(run('state.fiscalQuarter.size'),0);assert.equal(run('state.filterOptions.month.length'),4);
   assert.equal(requests.length,1); // All month/quarter/region changes stay local.
   for(const [today,start,end] of [['2026-11-25T04:00:00Z','2026-08-01','2027-02-01'],['2027-02-01T04:00:00Z','2026-11-01','2027-05-01'],['2027-05-01T04:00:00Z','2027-02-01','2027-08-01'],['2027-01-31T16:00:00Z','2026-11-01','2027-05-01']]) {
     context.asOf=today;
     assert.deepStrictEqual(Array.from(run('fiscalWindowBounds(new Date(asOf))')),[start,end]);
     context.bounds=[start,end]; context.boundary=start;
     assert(run('inFiscalWindow(boundary,bounds)'));
     context.boundary=end; assert(!run('inFiscalWindow(boundary,bounds)'));
   }
   assert(!run('inFiscalWindow("2026-07-31")'));
   assert(!run('inFiscalWindow("2027-02-01")'));
   context.coverageRow={...base,id:'coverage',membershipDetailsLoaded:true,
     updatePlan:{status:'ready',targetName:'Covering PSE',targetRole:'Partner Account Executive - Coverage',
       teamCurrent:[],splitCurrent:[{Opportunity_Team_Role__c:'Partner Account Executive - Coverage',Coverage__c:true,SplitPercentage:100}],changes:[]}};
   const coverageCard=run('opportunityCard(coverageRow)');
   assert(coverageCard.includes('Coverage: Checked'));
   assert(!coverageCard.includes('disabled checked> Coverage'));
   assert(!coverageCard.includes('Flag: Coverage role'));
   context.coverageRow.updatePlan.targetRole='Partner Account Executive';
   context.coverageRow.updatePlan.targetSplitRole='Partner Account Executive - Coverage';
   assert(!run('opportunityCard(coverageRow)').includes('disabled checked> Coverage'));
   context.coverageRow.updatePlan.splitCurrent[0].Coverage__c=false;
   assert(run('opportunityCard(coverageRow)').includes('Coverage: Unchecked'));
   assert(run('opportunityCard(coverageRow)').includes('Flag: Coverage role has Coverage unchecked'));
   context.coverageRow.updatePlan.splitCurrent[0].Coverage__c=null;
   assert(!run('opportunityCard(coverageRow)').includes('Flag: Coverage role'));
   nodes.get('processFlowBtn').events.click();
   assert(nodes.get('processFlow').open);
   nodes.get('closeProcessFlow').events.click();
   assert(!nodes.get('processFlow').open);
   assert(nodes.get('processFlowBtn').focused);
   assert.equal(document.body.style.overflow,'');
   for(const [date,label] of Object.entries({'2026-01-31':'FY2026 Q4','2026-02-01':'FY2027 Q1','2026-05-01':'FY2027 Q2','2026-08-01':'FY2027 Q3','2026-11-01':'FY2027 Q4','2027-01-31':'FY2027 Q4','2027-02-01':'FY2028 Q1'})) assert.equal(run(`fiscalQuarterFromDate('${date}')`),label);
   assert(!html.includes('uploadPage'));
   assert(!html.includes('local-app.js'));
   const choiceRole=new Node('role'), choiceBox=new Node('coverage'), choiceLabel=new Node('label');
   context.choicePanel={querySelector:selector=>selector==='[data-review-role]'?choiceRole:selector==='[data-coverage-confirm]'?choiceBox:choiceLabel};
   choiceRole.value='Partner Account Executive - Coverage';choiceBox.checked=true;
   run('toggleCoverageChoice(choicePanel)');assert(!choiceLabel.classList.contains('hidden'));assert.equal(choiceBox.checked,false);
   choiceRole.value='Partner Account Executive';run('toggleCoverageChoice(choicePanel)');assert(choiceLabel.classList.contains('hidden'));
   context.inactiveChoice={...base,id:'inactive-choice',name:'Keep this PSE',partnerRepName:'Identified inactive PSE',partnerRepActive:false,
     updatePlan:{status:'rvp',targetName:'Identified inactive PSE',targetRole:'Partner Account Executive',teamCurrent:[],splitCurrent:[],changes:[{object:'OpportunityTeamMember',action:'Add Team member'}]}};
   const inactiveCard=run('opportunityCard(inactiveChoice)');
   assert(inactiveCard.indexOf('Set covering PSE')<inactiveCard.indexOf('Set inactive PSE'));
   assert(inactiveCard.includes('data-coverage-confirm> Tick coverage box'));
   run('state.readinessFilter="all";state.fiscalQuarter.clear();state.month.clear();state.region.clear();hydrate({...snapshot,rows:[inactiveChoice]});state.writeApi.enabled=true');
   requests.length=0;context.window.confirm=()=>false;
   await run('setInactivePse("inactive-choice")');assert.equal(requests.length,0);
   context.window.confirm=()=>true;
   responder=()=>response({...context.inactiveChoice,updatePlan:{...context.inactiveChoice.updatePlan,status:'ready',retainedInactive:true}});
   await run('setInactivePse("inactive-choice")');
   assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/review-inactive-pse');
   assert.equal(requests[0].body.confirmed,true);assert(!requests.some(request=>request.path.startsWith('/api/write')));
   assert(nodes.get('actionList').innerHTML.includes('Inactive PSE selected'));
   assert(nodes.get('actionList').innerHTML.includes('Set covering PSE'));
   responder=()=>({ok:false,status:400,json:async()=>({error:'The identified PSE is no longer inactive.'})});
   await run('setInactivePse("inactive-choice")');
   assert(messages.get('inactive-choice').textContent.includes('no longer inactive'));
   assert.equal(run('state.busy'),false);
   context.oldSnapshot={...snapshot,config:{scopeVersion:5}};
   assert.equal(run('hydrate(oldSnapshot)'),false);
   assert(nodes.get('dashboard').classList.contains('hidden'));
   assert.equal(requests.length,2);
   console.log('Local UI checks passed: metadata, current/required/actions, red mismatch names, review/RVP states and bulk ready-only selection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
