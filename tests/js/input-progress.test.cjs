const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '../../static');
const context = vm.createContext({});
const common = fs.readFileSync(path.join(root, 'common.js'), 'utf8');
vm.runInContext(common.slice(common.indexOf('function setExactRangeValue')), context);
vm.runInContext(fs.readFileSync(path.join(root, 'import.js'), 'utf8').replace(/loadImportPage\(\);\s*$/, ''), context);

function element(value, min, max, step) {
    return {value:String(value), min:String(min), max:String(max), step:String(step), dataset:{},
        validity:{valid:true}, events:{}, addEventListener(type, action) {this.events[type]=action;}};
}

test('typed spending and percentage retain precision in the underlying range', () => {
    const slider=element(40000,0,500000,5000), input=element(0,0,1e12,'any');
    let calls=0;
    context.bindNumberToRange(slider,input,{onValid:()=>calls++,onInvalid:()=>assert.fail()});
    input.value='123456.78';input.events.input();
    assert.equal(Number(slider.value),123456.78);
    const rate=element(.05,0,.12,.0025), percent=element(0,-99,100,'any');
    context.bindNumberToRange(rate,percent,{scale:100,onValid:()=>calls++,onInvalid:()=>assert.fail()});
    assert.equal(Number(percent.value),5);
    percent.value='4.37';percent.events.input();
    assert.equal(Number(rate.value),.0437);
    assert.equal(calls,2);
});

test('invalid numeric text does not replace the last valid assumption', () => {
    const slider=element(60,45,75,1), input=element(60,0,120,1);
    let invalid=0;
    context.bindNumberToRange(slider,input,{onValid:()=>assert.fail(),onInvalid:()=>invalid++});
    input.value='';input.events.input();
    input.value='60.5';input.validity.valid=false;input.events.input();
    assert.equal(Number(slider.value),60);
    assert.equal(invalid,2);
});

test('age and quarter sliders keep integer steps and sync keyboard changes', () => {
    const slider=element(8,4,40,1), input=element(8,4,40,1);
    context.bindNumberToRange(slider,input,{onValid:()=>{},onInvalid:()=>assert.fail()});
    input.value='9';input.events.input();
    assert.equal(slider.step,'1');
    slider.value='10';slider.events.input();
    assert.equal(Number(input.value),10);
});

function quality() {
    return {snapshots:[{id:1,snapshot_date:'2026-03-31',balances:{cash:'confirmed_zero',ppk:'recorded',mortgage:'confirmed_zero'}},
        {id:2,snapshot_date:'2026-06-30',balances:{cash:'missing',ppk:'recorded',mortgage:'confirmed_zero'}}],
        cash_flows:{count:5,confirmed_through:'2026-03-31'}};
}
const steps=(q,id,dirty=false)=>Array.from(context.quarterProgress(q,id,dirty,'2026-09-26').steps);
test('progress is specific to the selected quarter and accepts confirmed zero',()=>{
    assert.deepEqual(steps(quality(),1),[true,true,true]);
    assert.deepEqual(steps(quality(),2),[true,false,false]);
    assert.deepEqual(steps(quality(),''),[false,false,false]);
});
test('unsaved balance edits and unconfirmed or empty flows prevent completion',()=>{
    assert.deepEqual(steps(quality(),1,true),[true,false,true]);
    let q=quality();q.cash_flows.confirmed_through=null;
    assert.deepEqual(steps(q,1),[true,true,false]);
    q=quality();q.cash_flows.count=0;
    assert.deepEqual(steps(q,1),[true,true,false]);
});
test('future snapshots and unavailable quality never show complete',()=>{
    const q=quality();q.snapshots[0].snapshot_date='2027-01-01';q.cash_flows.confirmed_through='2027-01-01';
    assert.deepEqual(steps(q,1),[false,true,true]);
    assert.deepEqual(steps(null,1),[false,false,false]);
});
