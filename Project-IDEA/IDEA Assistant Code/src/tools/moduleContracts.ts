export type ModuleTool = {
  name: string
  risk: 'read' | 'write' | 'network' | 'destructive'
  executionTargets: Array<'local' | 'cloud'>
  approval: 'none' | 'required'
}

export type ToolModuleManifest = {
  id: string
  version: string
  kind: 'tool-provider' | 'integration-provider' | 'workflow-provider'
  displayName?: string
  description?: string
  tools?: ModuleTool[]
  dependencies?: string[]
}

export const BROWSER_MODULE: ToolModuleManifest = {
  id: 'browser',
  version: '0.1.0',
  kind: 'tool-provider',
  displayName: '浏览器工具',
  description: '供客户端 Agent、云端 Agent 和自动化工作流复用的浏览器能力。',
  tools: [
    { name: 'browser.navigate', risk: 'network', executionTargets: ['local', 'cloud'], approval: 'required' },
    { name: 'browser.snapshot', risk: 'read', executionTargets: ['local', 'cloud'], approval: 'none' },
    { name: 'browser.click', risk: 'write', executionTargets: ['local', 'cloud'], approval: 'required' },
    { name: 'browser.type', risk: 'write', executionTargets: ['local', 'cloud'], approval: 'required' },
    { name: 'browser.screenshot', risk: 'read', executionTargets: ['local', 'cloud'], approval: 'none' },
  ],
}

export const EXTERNAL_SERVICES_MODULE: ToolModuleManifest = {
  id: 'external-services',
  version: '0.1.0',
  kind: 'integration-provider',
  description: '外部软件、MCP 和 HTTP 接口的统一集成边界。',
}

export const AUTOMATION_MODULE: ToolModuleManifest = {
  id: 'automation',
  version: '0.1.0',
  kind: 'workflow-provider',
  description: '定时任务和云端自动化工作流。',
  dependencies: ['browser', 'external-services'],
}

export const MODULE_MANIFESTS = [BROWSER_MODULE, EXTERNAL_SERVICES_MODULE, AUTOMATION_MODULE] as const
