/* Offline category checks; no Salesforce connection or writes. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync('docs/app.js', 'utf8').replace(/load\(\);\s*$/, ''), context);
vm.runInContext(`
const base = { partnerId: 'partner', stage: '08 - Closed', nonCommissionable: false, type: 'Renewal', partnerRepActive: true, partnerOwnerId:'005000000000001AAA', partnerRepId:'005000000000001AAA' };
state.rows = [
 {...base,id:'tagged',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:true},
 {...base,id:'untagged',partnerRepNeedsPopulate:true,partnerRepOnTeam:true,partnerRepOnSplit:false},
 {...base,id:'missingBoth',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false},
 {...base,id:'different',partnerRepId:'005000000000002AAA',partnerOwnerName:'Same name',partnerRepName:'Same name',partnerRepNeedsPopulate:false,partnerRepOnTeam:true,partnerRepOnSplit:false},
 {...base,id:'legacyMatch',partnerOwnerId:null,partnerOwnerName:' Owner ',partnerRepName:'owner',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:true},
 {...base,id:'legacyDifferent',partnerOwnerId:null,partnerOwnerName:'Owner',partnerRepName:'Other rep',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false},
 {...base,id:'shortId',partnerRepId:'005000000000001',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false},
 {...base,id:'fieldOnly',partnerRepNeedsPopulate:true,partnerRepOnTeam:true,partnerRepOnSplit:true},
 {...base,id:'complete',partnerRepNeedsPopulate:false,partnerRepOnTeam:true,partnerRepOnSplit:true},
 {...base,id:'noPartner',partnerId:null,partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false},
 {...base,id:'open',stage:'02 - Confirm Need',partnerRepNeedsPopulate:false,partnerRepOnTeam:false,partnerRepOnSplit:false}
];
state.filterOptions.pseStatus = ['Active','Inactive'];
`, context);
const ids = (category) => Array.from(vm.runInContext(`filteredRows('${category}').map(row=>row.id)`, context)).sort();
assert.deepStrictEqual(ids('tagged'), ['legacyMatch', 'missingBoth', 'shortId', 'tagged']);
assert.deepStrictEqual(ids('different'), ['different', 'legacyDifferent']);
assert.deepStrictEqual(ids('untagged'), ['untagged']);
assert.deepStrictEqual(ids('all'), [...ids('tagged'), ...ids('different'), ...ids('untagged')].sort());
vm.runInContext("state.rows.push({...state.rows[0],id:'unknownOwner',partnerOwnerId:null,partnerOwnerName:null})", context);
assert(ids('all').includes('unknownOwner'));
assert(!ids('tagged').includes('unknownOwner'));
assert(!ids('different').includes('unknownOwner'));
vm.runInContext("state.query='untagged'", context);
assert.deepStrictEqual(ids('all'), ['untagged']);
assert.deepStrictEqual(ids('tagged'), []);
vm.runInContext("state.query='';state.pseStatus=new Set(['Inactive'])", context);
assert.deepStrictEqual(ids('all'), []);
console.log('Matching/different users, legacy exports, category union, partial memberships, exclusions, search and status checks passed.');
