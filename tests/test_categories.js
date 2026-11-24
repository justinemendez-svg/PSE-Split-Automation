/* Offline checks: the All box is exactly the union of the two PSE categories. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync('docs/app.js', 'utf8').replace(/load\(\);\s*$/, ''), context);
vm.runInContext(`
const base = { partnerId: 'partner', stage: '08 - Closed', nonCommissionable: false, type: 'Renewal', partnerRepActive: true };
state.rows = [
 {...base,id:'tagged',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:true},
 {...base,id:'untagged',partnerRepNeedsPopulate:true,partnerRepOnTeam:true,partnerRepOnSplit:false},
 {...base,id:'missingBoth',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false},
 {...base,id:'fieldOnly',partnerRepNeedsPopulate:true,partnerRepOnTeam:true,partnerRepOnSplit:true},
 {...base,id:'complete',partnerRepNeedsPopulate:false,partnerRepOnTeam:true,partnerRepOnSplit:true},
 {...base,id:'noPartner',partnerId:null,partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false},
 {...base,id:'open',stage:'02 - Confirm Need',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false}
];
state.filterOptions.pseStatus = ['Active','Inactive'];
`, context);
const ids = (category) => Array.from(vm.runInContext(`filteredRows('${category}').map(row=>row.id)`, context)).sort();
assert.deepStrictEqual(ids('tagged'), ['missingBoth', 'tagged']);
assert.deepStrictEqual(ids('untagged'), ['untagged']);
assert.deepStrictEqual(ids('all'), [...ids('tagged'), ...ids('untagged')].sort());
vm.runInContext("state.query='untagged'", context);
assert.deepStrictEqual(ids('all'), ['untagged']);
assert.deepStrictEqual(ids('tagged'), []);
vm.runInContext("state.query='';state.pseStatus=new Set(['Inactive'])", context);
assert.deepStrictEqual(ids('all'), []);
console.log('Category union, partial memberships, exclusions, search and status combinations passed.');
