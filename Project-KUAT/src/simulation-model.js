(function(root){
  const statuses = ['准备中', '运行中', '已暂停', '已完成'];
  const scopes = ['事件模拟', '世界走向'];
  const interventionTypes = ['信息投放', '资源调整', '角色指令', '环境变化', '自定义干预'];
  const initial = {schemaVersion: 2, storeRevision: 0, activeId: null, simulations: []};
  const timestamp = () => new Date().toISOString();
  const makeId = prefix => `${prefix}-${crypto.randomUUID()}`;
  const actor = entryId => ({entryId, role:'', personality:'', emotions:'', memory:'', abilities:'', goals:'', autonomy:0.8, state:''});

  function migrate(data){
    if (!data || typeof data !== 'object') return structuredClone(initial);
    if (data.schemaVersion === 2) return structuredClone(data);
    if (data.schemaVersion !== 1 || !Array.isArray(data.simulations)) throw Error('世界模拟档案格式不正确');
    const simulations = data.simulations.map(old => ({
      id:old.id,title:old.title,scope:'事件模拟',premise:old.premise||'',objective:'',expectedOutcome:'',targetEventId:'',
      currentTime:Number.isFinite(old.currentTime)?old.currentTime:0,step:Number.isFinite(old.step)&&old.step>0?old.step:1,horizon:10,
      status:statuses.includes(old.status)?old.status:'准备中',turn:old.turn||0,actors:(old.actors||[]).map(id=>actor(id)),interventions:[],
      history:(old.log||[]).map(item=>({id:makeId('turn'),turn:Number.isInteger(item.turn)?item.turn:1,time:Number.isFinite(item.time)?item.time:0,decisions:[],events:[{id:makeId('event'),title:item.title,body:item.body||'',actorIds:[]}],interventions:[],provider:'manual',createdAt:item.createdAt||timestamp()})),
      createdAt:old.createdAt||timestamp(),updatedAt:old.updatedAt||timestamp()
    }));
    return {schemaVersion:2,storeRevision:data.storeRevision||0,activeId:data.activeId||simulations[0]?.id||null,simulations};
  }
  function validateActor(value){
    if(!value||typeof value.entryId!=='string'||!value.entryId||typeof value.role!=='string'||typeof value.personality!=='string'||typeof value.emotions!=='string'||typeof value.memory!=='string'||typeof value.abilities!=='string'||typeof value.goals!=='string'||typeof value.state!=='string')throw Error('模拟角色档案不完整');
    if(!Number.isFinite(value.autonomy)||value.autonomy<0||value.autonomy>1)throw Error('角色自主度应为0到1');
    for(const key of ['role','personality','emotions','memory','abilities','goals','state'])if(value[key].length>20000)throw Error('模拟角色档案过长');
  }
  function validate(data){
    if(!data||data.schemaVersion!==2||!Array.isArray(data.simulations)||data.simulations.length>100)throw Error('世界模拟档案格式不正确');
    if(!Number.isInteger(data.storeRevision)||data.storeRevision<0)throw Error('世界模拟档案版本无效');
    const ids=new Set();
    for(const simulation of data.simulations){
      if(!simulation||typeof simulation.id!=='string'||!simulation.id||ids.has(simulation.id))throw Error('模拟场景编号无效');ids.add(simulation.id);
      if(typeof simulation.title!=='string'||!simulation.title.trim()||simulation.title.length>120||typeof simulation.premise!=='string'||simulation.premise.length>20000)throw Error('模拟场景名称或前提不合法');
      if(!scopes.includes(simulation.scope)||typeof simulation.objective!=='string'||typeof simulation.expectedOutcome!=='string'||typeof simulation.targetEventId!=='string')throw Error('模拟目标不合法');
      if(!Number.isFinite(simulation.currentTime)||Math.abs(simulation.currentTime)>1e9||!Number.isFinite(simulation.step)||simulation.step<=0||simulation.step>1e9||!Number.isInteger(simulation.horizon)||simulation.horizon<1||simulation.horizon>10000)throw Error('模拟时间参数不合法');
      if(!statuses.includes(simulation.status)||!Number.isInteger(simulation.turn)||simulation.turn<0)throw Error('模拟状态不合法');
      if(!Array.isArray(simulation.actors)||simulation.actors.length>100)throw Error('模拟角色数量不合法');simulation.actors.forEach(validateActor);
      if(!Array.isArray(simulation.interventions)||simulation.interventions.length>500)throw Error('模拟干预不合法');
      for(const intervention of simulation.interventions)if(!intervention||typeof intervention.id!=='string'||typeof intervention.instruction!=='string'||!intervention.instruction.trim()||intervention.instruction.length>20000||!interventionTypes.includes(intervention.type)||typeof intervention.actorId!=='string'||!Number.isFinite(intervention.time)||typeof intervention.enabled!=='boolean')throw Error('模拟干预内容不合法');
      if(!Array.isArray(simulation.history)||simulation.history.length>10000)throw Error('模拟历史不合法');
      for(const turn of simulation.history){
        if(!turn||typeof turn.id!=='string'||!Number.isInteger(turn.turn)||turn.turn<1||!Number.isFinite(turn.time)||!Array.isArray(turn.decisions)||!Array.isArray(turn.events)||!Array.isArray(turn.interventions)||typeof turn.provider!=='string'||typeof turn.createdAt!=='string')throw Error('模拟轮次格式不合法');
        for(const decision of turn.decisions)if(!decision||typeof decision.actorId!=='string'||typeof decision.action!=='string'||!decision.action.trim()||decision.action.length>20000||typeof decision.reason!=='string'||typeof decision.emotionalState!=='string')throw Error('角色决策不合法');
        for(const event of turn.events)if(!event||typeof event.id!=='string'||typeof event.title!=='string'||!event.title.trim()||typeof event.body!=='string'||!Array.isArray(event.actorIds))throw Error('模拟事件不合法');
      }
      for(const key of ['createdAt','updatedAt'])if(typeof simulation[key]!=='string')throw Error('模拟时间戳缺失');
    }
    if(data.activeId!==null&&(typeof data.activeId!=='string'||!ids.has(data.activeId)))throw Error('当前模拟场景不存在');return data;
  }
  function create(data,fields={}){const at=timestamp();const simulation={id:makeId('sim'),title:String(fields.title||'未命名世界模拟').trim(),scope:scopes.includes(fields.scope)?fields.scope:'事件模拟',premise:String(fields.premise||''),objective:String(fields.objective||''),expectedOutcome:String(fields.expectedOutcome||''),targetEventId:String(fields.targetEventId||''),currentTime:Number.isFinite(fields.currentTime)?fields.currentTime:0,step:Number.isFinite(fields.step)&&fields.step>0?fields.step:1,horizon:Number.isInteger(fields.horizon)&&fields.horizon>0?fields.horizon:10,status:'准备中',turn:0,actors:Array.isArray(fields.actors)?fields.actors.map(value=>typeof value==='string'?actor(value):{...actor(value.entryId),...value}):[],interventions:[],history:[],createdAt:at,updatedAt:at};return validate({...data,simulations:[simulation,...data.simulations],activeId:simulation.id});}
  function update(data,simulationId,fields){const old=data.simulations.find(item=>item.id===simulationId);if(!old)throw Error('模拟场景不存在');return validate({...data,simulations:data.simulations.map(item=>item.id===simulationId?{...item,...fields,id:item.id,updatedAt:timestamp()}:item),activeId:simulationId});}
  function upsertActor(data,simulationId,value){const simulation=data.simulations.find(item=>item.id===simulationId);if(!simulation)throw Error('模拟场景不存在');if(!value||typeof value.entryId!=='string'||!value.entryId)throw Error('请选择一个设定');const nextActors=simulation.actors.some(item=>item.entryId===value.entryId)?simulation.actors.map(item=>item.entryId===value.entryId?{...item,...value}:item):[...simulation.actors,{...actor(value.entryId),...value}];return update(data,simulationId,{actors:nextActors});}
  function removeActor(data,simulationId,entryId){const simulation=data.simulations.find(item=>item.id===simulationId);if(!simulation)throw Error('模拟场景不存在');return update(data,simulationId,{actors:simulation.actors.filter(item=>item.entryId!==entryId)});}
  function addIntervention(data,simulationId,fields){const simulation=data.simulations.find(item=>item.id===simulationId);if(!simulation)throw Error('模拟场景不存在');const item={id:makeId('intervention'),type:interventionTypes.includes(fields.type)?fields.type:'自定义干预',time:Number(fields.time),actorId:String(fields.actorId||''),instruction:String(fields.instruction||'').trim(),expectedEffect:String(fields.expectedEffect||'').trim(),enabled:fields.enabled!==false};return update(data,simulationId,{interventions:[...simulation.interventions,item]});}
  function removeIntervention(data,simulationId,interventionId){const simulation=data.simulations.find(item=>item.id===simulationId);if(!simulation)throw Error('模拟场景不存在');return update(data,simulationId,{interventions:simulation.interventions.filter(item=>item.id!==interventionId)});}
  function appendManual(data,simulationId,record){const simulation=data.simulations.find(item=>item.id===simulationId);if(!simulation)throw Error('模拟场景不存在');if(typeof record.title!=='string'||!record.title.trim())throw Error('模拟记录标题不能为空');const turn={id:makeId('turn'),turn:simulation.turn+1,time:Number(record.time),decisions:[],events:[{id:makeId('event'),title:record.title.trim(),body:String(record.body||'').trim(),actorIds:Array.isArray(record.actorIds)?record.actorIds:[]}],interventions:[],provider:'manual',createdAt:timestamp()};return update(data,simulationId,{currentTime:turn.time,turn:turn.turn,status:'运行中',history:[...simulation.history,turn]});}
  function appendGenerated(data,simulationId,generated){const simulation=data.simulations.find(item=>item.id===simulationId);if(!simulation)throw Error('模拟场景不存在');const turn={id:makeId('turn'),turn:simulation.turn+1,time:Number(generated.time),decisions:Array.isArray(generated.decisions)?generated.decisions:[],events:Array.isArray(generated.events)?generated.events:[],interventions:Array.isArray(generated.interventions)?generated.interventions:[],provider:String(generated.provider||'local-rules'),createdAt:timestamp()};return update(data,simulationId,{currentTime:turn.time,turn:turn.turn,status:'运行中',history:[...simulation.history,turn]});}
  function setStatus(data,simulationId,status){if(!statuses.includes(status))throw Error('模拟状态不合法');return update(data,simulationId,{status});}
  const api={initial,statuses,scopes,interventionTypes,actor,migrate,validate,create,update,upsertActor,removeActor,addIntervention,removeIntervention,appendManual,appendGenerated,setStatus};if(typeof module!=='undefined')module.exports=api;else root.SimulationModel=api;
})(globalThis);
