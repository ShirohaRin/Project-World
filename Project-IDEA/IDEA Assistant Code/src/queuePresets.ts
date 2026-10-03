/**
 * 预设队列：内置预设来自 `modules/workflow/queue-presets.json`（数据文件，**不进客户端构建**，
 * 改完即生效，不需要重新出包）；本机另可把当前画布存成自己的预设，存在 localStorage 里。
 *
 * 维护约定见 PROJECT_RULES.md 第 7.4 节：工作流每新增或改动，必须同步 JSON 里的对应条目；
 * 没有独立算法块的环节写进 `unmapped`，不得为了凑齐步骤伪造算法块。
 * 预设是组装的起点，不参与执行链路，也不是调用契约。
 */

export type PresetStep = {
    /** 对应 compute.ts 里 COMPUTE_METHODS 的算法 id */
    methodId: string;
    /** 该步在工作流里的名字（照抄 workflow.md 第 3 节的措辞） */
    step: string;
    /** 载入时预填的参数；不给就走算法自己的默认值 */
    input?: Record<string, string>;
};

/** 本机保存的队列结构：与画布上的块同形，但不带块 id（每次载入现生成）。 */
export type SavedQueue =
    | { kind: "method"; methodId: string; input: Record<string, string> }
    | { kind: "gate"; field: string; operator: string; value: string; yes: SavedQueue[]; no: SavedQueue[] }
    | { kind: "end" };

export type QueuePreset = {
    id: string;
    name: string;
    /** 归属的工作流；本机保存的预设为空 */
    workflow: string;
    /** 定义来源：改工作流时按这里去核对 */
    source: string;
    summary: string;
    /** 这条队列产出什么 */
    outputs: string[];
    /** 什么情况下用、什么情况下别用 */
    scope: string[];
    /** 工作流里有、但没有独立算法块的环节；如实列出，不补块 */
    unmapped: string[];
    caveats: string[];
    /** 按工作流顺序铺开的算法步；本机保存的预设为空 */
    steps: PresetStep[];
    /** 本机保存的整张画布（可含条件判断） */
    blocks?: SavedQueue[];
    /** 是否本机保存（可删除） */
    local: boolean;
};

const USER_PRESET_KEY = "idea-bio-compute-queue-presets";
const EMPTY = { outputs: [] as string[], scope: [] as string[], unmapped: [] as string[], caveats: [] as string[] };

function text(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function textList(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []; }

function normalizeSteps(value: unknown): PresetStep[] {
    if (!Array.isArray(value)) return [];
    return value.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const step = item as { methodId?: unknown; step?: unknown; input?: unknown };
        if (typeof step.methodId !== "string" || !step.methodId) return [];
        const input: Record<string, string> = {};
        if (step.input && typeof step.input === "object") for (const [key, entry] of Object.entries(step.input as Record<string, unknown>)) if (typeof entry === "string") input[key] = entry;
        return [{ methodId: step.methodId, step: text(step.step, step.methodId), input }];
    });
}

/** 把 JSON 里的一条预设整理成可用结构；结构不对（缺 id/name 或既没有 steps 也没有 blocks）就丢掉。 */
function normalizePreset(value: unknown): QueuePreset | undefined {
    if (!value || typeof value !== "object") return undefined;
    const raw = value as Record<string, unknown>;
    const id = text(raw.id);
    const name = text(raw.name);
    if (!id || !name) return undefined;
    const steps = normalizeSteps(raw.steps);
    const blocks = Array.isArray(raw.blocks) ? (raw.blocks as SavedQueue[]) : undefined;
    if (!steps.length && !blocks?.length) return undefined;
    return { id, name, workflow: text(raw.workflow), source: text(raw.source), summary: text(raw.summary), ...EMPTY, outputs: textList(raw.outputs), scope: textList(raw.scope), unmapped: textList(raw.unmapped), caveats: textList(raw.caveats), steps, blocks, local: false };
}

/** 读内置预设。桌面接口不可用（例如在纯浏览器里预览）时返回空表并给出原因，不抛错。 */
export async function loadBuiltinPresets(): Promise<{ presets: QueuePreset[]; path: string; error?: string }> {
    const bridge = window.ideaDesktop;
    if (!bridge?.getQueuePresets) return { presets: [], path: "", error: "桌面接口不可用，读不到内置预设" };
    try {
        const result = await bridge.getQueuePresets();
        const presets = Array.isArray(result?.presets) ? result.presets.map(normalizePreset).filter((item): item is QueuePreset => Boolean(item)) : [];
        return { presets, path: result?.path ?? "", error: result?.error };
    } catch (error) {
        return { presets: [], path: "", error: error instanceof Error ? error.message : String(error) };
    }
}

export function loadLocalPresets(): QueuePreset[] {
    try {
        const stored = localStorage.getItem(USER_PRESET_KEY);
        if (!stored) return [];
        const parsed: unknown = JSON.parse(stored);
        if (!Array.isArray(parsed)) return [];
        return parsed.flatMap((item) => {
            if (!item || typeof item !== "object") return [];
            const raw = item as Record<string, unknown>;
            const id = text(raw.id);
            const name = text(raw.name);
            if (!id || !name || !Array.isArray(raw.blocks) || !raw.blocks.length) return [];
            return [{ id, name, workflow: "", source: text(raw.source, "本机保存"), summary: text(raw.summary), ...EMPTY, steps: [], blocks: raw.blocks as SavedQueue[], local: true }];
        });
    } catch { return []; }
}

export function writeLocalPresets(presets: QueuePreset[]): void {
    try { localStorage.setItem(USER_PRESET_KEY, JSON.stringify(presets.filter((item) => item.local))); } catch { /* 存不下就算了，草稿仍在 */ }
}
