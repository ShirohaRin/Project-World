/*
 * 世界模拟的决策提供者边界。
 * 这里先提供可审计的本地规则提供者；真正接入大模型时，只替换本文件的
 * generate 方法，不改变场景、角色、干预和历史数据结构。
 */
const SimulationProvider=(()=>{
  const local={
    id:'local-rules',
    label:'本地规则推演',
    description:'根据角色档案和有效干预生成可追溯的决策草稿，不调用外部模型。',
    async generate(input){
      const time=input.time;
      const decisions=input.actors.map(actor=>{
        const intervention=input.interventions.find(item=>item.enabled&&item.time<=time&&(!item.actorId||item.actorId===actor.entryId));
        const action=intervention?`响应干预「${intervention.instruction}」并重新评估局势`:(actor.goals?`围绕“${actor.goals}”采取下一步行动`:'依据当前性格与处境，观察局势并采取保守行动');
        const reason=[actor.personality&&`性格：${actor.personality}`,actor.emotions&&`情感：${actor.emotions}`,actor.memory&&`记忆：${actor.memory}`,actor.abilities&&`能力：${actor.abilities}`].filter(Boolean).join('；')||'角色档案尚未填写，决策只能作为待补充草稿';
        return {actorId:actor.entryId,action,reason,emotionalState:actor.emotions||'未设定',interventionId:intervention?.id||''};
      });
      const interventionCount=input.interventions.filter(item=>item.enabled&&item.time<=time).length;
      return {provider:local.id,time,decisions,interventions:input.interventions.filter(item=>item.enabled&&item.time<=time).map(item=>item.id),events:[{id:`event-${Date.now()}`,title:`第${input.turn}轮局势变化`,body:`本轮由 ${decisions.length} 个角色分别形成决策草稿；当前生效干预 ${interventionCount} 项。具体后果需要继续观察下一轮，或由创作者补充。`,actorIds:decisions.map(item=>item.actorId)}]};
    }
  };
  let active=local;
  return {local,get active(){return active;},setActive(provider){active=provider||local;},available:()=>[local],generate:input=>active.generate(input)};
})();
