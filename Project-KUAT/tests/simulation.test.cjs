const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const model=fs.readFileSync(require('node:path').join(__dirname,'../src/simulation-model.js'),'utf8');
vm.runInThisContext(model);

test('simulation model creates a scenario, records turns and validates state',()=>{
 let data=structuredClone(SimulationModel.initial);
 data=SimulationModel.create(data,{title:'裂缝扩大推演',premise:'如果裂缝在下一历元扩大',currentTime:12,step:2,actors:['entry-a']});
 assert.equal(data.simulations[0].title,'裂缝扩大推演');
 assert.deepEqual(data.simulations[0].actors.map(actor=>actor.entryId),['entry-a']);
 data=SimulationModel.appendManual(data,data.activeId,{title:'影子一族召开会议',body:'记录一轮应对结果',time:14});
 assert.equal(data.simulations[0].turn,1);
 assert.equal(data.simulations[0].currentTime,14);
 assert.equal(data.simulations[0].status,'运行中');
 data=SimulationModel.setStatus(data,data.activeId,'已暂停');
 assert.equal(data.simulations[0].status,'已暂停');
 assert.doesNotThrow(()=>SimulationModel.validate(data));
});

test('simulation model rejects invalid time and log data',()=>{
 let data=structuredClone(SimulationModel.initial);
 data=SimulationModel.create(data,{title:'测试',step:1});
 assert.throws(()=>SimulationModel.update(data,data.activeId,{step:0}),/时间参数/);
 assert.throws(()=>SimulationModel.appendManual(data,data.activeId,{title:'',body:'',time:1}),/标题不能为空/);
});
