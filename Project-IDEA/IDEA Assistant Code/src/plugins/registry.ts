export type Plugin = {
  id: string
  name: string
  version: string
  description: string
  category: '语言支持' | '工具'
  languages?: string[]
  extensions?: string[]
  toolchain?: string
  enabledByDefault: boolean
}

export const LANGUAGE_PLUGIN_IDS = [
  'idea.language-javascript',
  'idea.language-typescript',
  'idea.language-python',
  'idea.language-cpp',
  'idea.language-go',
  'idea.language-java',
  'idea.language-rust',
] as const

export const PLUGINS: Plugin[] = [
  { id: 'idea.language-javascript', name: 'JavaScript', version: '1.0.0', description: '提供 JavaScript 语法高亮，并使用本机 Node.js 运行与 Inspector 调试。', category: '语言支持', languages: ['JavaScript'], extensions: ['.js', '.mjs', '.cjs'], toolchain: 'node', enabledByDefault: true },
  { id: 'idea.language-typescript', name: 'TypeScript', version: '1.0.0', description: '提供 TypeScript 语法支持，并使用本机 tsc 编译后通过 Node.js 运行。', category: '语言支持', languages: ['TypeScript'], extensions: ['.ts'], toolchain: 'tsc + node', enabledByDefault: true },
  { id: 'idea.language-python', name: 'Python', version: '1.0.0', description: '提供 Python 语法支持，并使用本机 Python 解释器运行。', category: '语言支持', languages: ['Python'], extensions: ['.py'], toolchain: 'python', enabledByDefault: true },
  { id: 'idea.language-cpp', name: 'C / C++', version: '1.0.0', description: '提供 C/C++ 语法支持，并使用本机 g++ 编译和运行。', category: '语言支持', languages: ['C', 'C++'], extensions: ['.c', '.cc', '.cpp', '.cxx'], toolchain: 'g++', enabledByDefault: false },
  { id: 'idea.language-go', name: 'Go', version: '1.0.0', description: '提供 Go 语法支持，并使用本机 Go 工具链运行。', category: '语言支持', languages: ['Go'], extensions: ['.go'], toolchain: 'go', enabledByDefault: false },
  { id: 'idea.language-java', name: 'Java', version: '1.0.0', description: '提供 Java 语法支持，并使用本机 JDK 编译和运行单文件程序。', category: '语言支持', languages: ['Java'], extensions: ['.java'], toolchain: 'javac + java', enabledByDefault: false },
  { id: 'idea.language-rust', name: 'Rust', version: '1.0.0', description: '提供 Rust 语法支持，并使用本机 rustc 编译和运行。', category: '语言支持', languages: ['Rust'], extensions: ['.rs'], toolchain: 'rustc', enabledByDefault: false },
  { id: 'idea.workspace-tools', name: '工作区工具', version: '1.0.0', description: '提供文件树刷新、工作区选择和编辑器状态栏信息。', category: '工具', enabledByDefault: true },
]

export function languagePluginForFile(fileName: string): Plugin | undefined {
  const extension = fileName.slice(fileName.lastIndexOf('.')).toLowerCase()
  return PLUGINS.find((plugin) => plugin.category === '语言支持' && plugin.extensions?.includes(extension))
}
