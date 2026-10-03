import { useEffect, useRef, useState } from "react";
import { COMPUTE_METHODS, LOCALLY_EXECUTABLE_METHOD_IDS, WORKFLOW_METHOD_IDS, type ComputeMethodDefinition } from "./compute";
import { loadBuiltinPresets, loadLocalPresets, writeLocalPresets, type QueuePreset, type SavedQueue } from "./queuePresets";

type Step = { id: string; kind: "method"; methodId: string; input: Record<string, string> };
type Gate = { id: string; kind: "gate"; field: string; operator: "equals" | "notEquals" | "greater" | "less"; value: string; yes: Block[]; no: Block[] };
type End = { id: string; kind: "end" };
type Block = Step | Gate | End;
type Draft = { name: string; blocks: Block[]; outputDir: string };
const STORAGE_KEY = "idea-bio-compute-queue-draft";
/** 队列里的"步骤块"只放算法；两条流程型入口（fastp / breseq）本身不是一步，排除在外。 */
const methods = COMPUTE_METHODS.filter((method) => method.id.startsWith("bio-") && !WORKFLOW_METHOD_IDS.includes(method.id));
const ZOOM_MIN = 0.4;
const ZOOM_MAX = 2;
const ZOOM_STEP = 1.1;

function clampZoom(value: number): number {
    return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 100) / 100));
}

function initialDraft(): Draft {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            const parsed: unknown = JSON.parse(stored);
            if (parsed && typeof parsed === "object" && "name" in parsed && "blocks" in parsed && typeof parsed.name === "string" && Array.isArray(parsed.blocks)) {
                const stored = parsed as Partial<Draft>;
                return { name: stored.name as string, blocks: stored.blocks as Block[], outputDir: typeof stored.outputDir === "string" ? stored.outputDir : "" };
            }
        }
    } catch { /* A corrupt local draft starts a fresh queue. */ }
    return { name: "未命名工作队列", blocks: [], outputDir: "" };
}

function findBlock(blocks: Block[], id: string): Block | undefined {
    for (const block of blocks) {
        if (block.id === id) return block;
        if (block.kind === "gate") {
            const nested = findBlock(block.yes, id) ?? findBlock(block.no, id);
            if (nested) return nested;
        }
    }
}

function editBlocks(blocks: Block[], id: string, edit: (block: Block) => Block): Block[] {
    return blocks.map((block) => block.id === id ? edit(block) : block.kind === "gate" ? { ...block, yes: editBlocks(block.yes, id, edit), no: editBlocks(block.no, id, edit) } : block);
}

function editLane(blocks: Block[], lane: string, edit: (items: Block[]) => Block[]): Block[] {
    if (lane === "root") return edit(blocks);
    const [gateId, side] = lane.split(":");
    return blocks.map((block) => block.kind === "gate" ? {
        ...block,
        yes: side === "yes" && block.id === gateId ? edit(block.yes) : editLane(block.yes, lane, edit),
        no: side === "no" && block.id === gateId ? edit(block.no) : editLane(block.no, lane, edit),
    } : block);
}

function removeBlock(blocks: Block[], id: string): Block[] {
    return blocks.filter((block) => block.id !== id).map((block) => block.kind === "gate" ? { ...block, yes: removeBlock(block.yes, id), no: removeBlock(block.no, id) } : block);
}

function methodFor(step: Step): ComputeMethodDefinition | undefined {
    return COMPUTE_METHODS.find((method) => method.id === step.methodId);
}

/** 同一路径上紧邻在前的那一个算法块；首块（或整条分支的入口）没有。 */
function previousMethod(blocks: Block[], index: number): Step | undefined {
    for (let cursor = index - 1; cursor >= 0; cursor--) {
        const block = blocks[cursor];
        if (block.kind === "method") return block;
    }
}

/** 找到某个块所在的路径，以及它前面那个算法块（用于「前输出即后输入」的绑定）。 */
function laneContext(blocks: Block[], id: string): { found: boolean; previous?: Step } {
    for (let index = 0; index < blocks.length; index++) {
        const block = blocks[index];
        if (block.id === id) return { found: true, previous: previousMethod(blocks, index) };
        if (block.kind === "gate") {
            const inYes = laneContext(block.yes, id);
            if (inYes.found) return inYes;
            const inNo = laneContext(block.no, id);
            if (inNo.found) return inNo;
        }
    }
    return { found: false };
}

/** 块上那一行数据流标注：输入从哪来。 */
function dataflowNote(blocks: Block[], index: number, kind: Block["kind"]) {
    const previous = previousMethod(blocks, index);
    if (previous) {
        const name = methodFor(previous)?.name ?? previous.methodId;
        return <div className="queue-dataflow"><span className="queue-flow-key">{kind === "gate" ? "判据" : "输入"}</span>← 上一步「{name}」{kind === "gate" ? "的结果" : "的输出"}</div>;
    }
    return <div className="queue-dataflow"><span className="queue-flow-key">输入</span>{kind === "method" ? "队列入口，需给路径" : "这条路径没有上游"}</div>;
}

/** 画布上的块整理成可保存的预设结构（去掉块 id）。 */
function toSavedBlocks(blocks: Block[]): SavedQueue[] {
    return blocks.map((block) => block.kind === "method" ? { kind: "method", methodId: block.methodId, input: { ...block.input } } : block.kind === "gate" ? { kind: "gate", field: block.field, operator: block.operator, value: block.value, yes: toSavedBlocks(block.yes), no: toSavedBlocks(block.no) } : { kind: "end" });
}

/** 预设结构还原成画布上的块：现生成 id，并挡住非法的比较方式。 */
function toBlocks(saved: SavedQueue[]): Block[] {
    return saved.map((block) => block.kind === "method" ? { id: crypto.randomUUID(), kind: "method", methodId: block.methodId, input: { ...block.input } } : block.kind === "gate" ? { id: crypto.randomUUID(), kind: "gate", field: block.field, operator: ["equals", "notEquals", "greater", "less"].includes(block.operator) ? block.operator as Gate["operator"] : "equals", value: block.value, yes: toBlocks(block.yes), no: toBlocks(block.no) } : { id: crypto.randomUUID(), kind: "end" });
}

export default function WorkflowQueue() {
    const [draft, setDraft] = useState<Draft>(initialDraft);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const [palette, setPalette] = useState("bio-quality-trimming");
    const [notice, setNotice] = useState("");
    const [activePreset, setActivePreset] = useState<string | null>(null);
    const [builtinPresets, setBuiltinPresets] = useState<QueuePreset[]>([]);
    const [localPresets, setLocalPresets] = useState<QueuePreset[]>(loadLocalPresets);
    const [presetNote, setPresetNote] = useState("");
    const selected = selectedId ? findBlock(draft.blocks, selectedId) : undefined;
    const allPresets = [...builtinPresets, ...localPresets];
    const preset = allPresets.find((item) => item.id === activePreset);
    const lane = selectedId ? laneContext(draft.blocks, selectedId) : undefined;
    const [view, setView] = useState({ x: 0, y: 0, zoom: 1 });
    const [panning, setPanning] = useState(false);
    const canvasRef = useRef<HTMLDivElement | null>(null);
    const panState = useRef<{ x: number; y: number; viewX: number; viewY: number } | null>(null);
    const stopPanRef = useRef<(() => void) | null>(null);
    useEffect(() => { localStorage.setItem(STORAGE_KEY, JSON.stringify(draft)); }, [draft]);
    useEffect(() => () => stopPanRef.current?.(), []);
    useEffect(() => {
        let alive = true;
        void loadBuiltinPresets().then((result) => {
            if (!alive) return;
            setBuiltinPresets(result.presets);
            setPresetNote(result.error ? `内置预设读不到：${result.error}` : "");
        });
        return () => { alive = false; };
    }, []);

    useEffect(() => {
        const node = canvasRef.current;
        if (!node) return;
        function handleWheel(event: WheelEvent) {
            event.preventDefault();
            const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? node!.clientHeight : 1;
            if (!event.ctrlKey && !event.metaKey) {
                // 不按 Ctrl：滚轮就是普通的上滚下滚（画布没有滚动条，滚动即平移视图）
                setView((current) => ({ ...current, x: current.x - event.deltaX * unit, y: current.y - event.deltaY * unit }));
                return;
            }
            const rect = node!.getBoundingClientRect();
            const pointerX = event.clientX - rect.left;
            const pointerY = event.clientY - rect.top;
            const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
            setView((current) => {
                const zoom = clampZoom(current.zoom * factor);
                if (zoom === current.zoom) return current;
                const ratio = zoom / current.zoom;
                return { x: pointerX - (pointerX - current.x) * ratio, y: pointerY - (pointerY - current.y) * ratio, zoom };
            });
        }
        node.addEventListener("wheel", handleWheel, { passive: false });
        return () => node.removeEventListener("wheel", handleWheel);
    }, []);

    function startPan(event: React.MouseEvent<HTMLDivElement>) {
        if (event.button !== 0) return;
        if ((event.target as HTMLElement).closest("input, select, textarea, button")) return;
        const start = { x: event.clientX, y: event.clientY, viewX: view.x, viewY: view.y };
        panState.current = start;
        function handleMove(moveEvent: MouseEvent) {
            const current = panState.current;
            if (!current) return;
            const dx = moveEvent.clientX - current.x;
            const dy = moveEvent.clientY - current.y;
            if (Math.abs(dx) < 3 && Math.abs(dy) < 3) return;
            document.body.style.userSelect = "none";
            setPanning(true);
            setView((currentView) => ({ ...currentView, x: current.viewX + dx, y: current.viewY + dy }));
        }
        function stopPan() {
            panState.current = null;
            stopPanRef.current = null;
            document.body.style.userSelect = "";
            setPanning(false);
            window.removeEventListener("mousemove", handleMove);
            window.removeEventListener("mouseup", stopPan);
        }
        stopPanRef.current = stopPan;
        window.addEventListener("mousemove", handleMove);
        window.addEventListener("mouseup", stopPan);
    }

    function loadPreset(target: QueuePreset) {
        if (draft.blocks.length && !window.confirm(`用预设「${target.name}」替换当前画布内容？`)) return;
        // 本机预设存的是整张画布（可含条件块），直接还原；内置预设是按顺序的算法步，末尾补一个结束块
        const blocks: Block[] = target.blocks?.length
            ? toBlocks(target.blocks)
            : [...target.steps.map((step) => ({ id: crypto.randomUUID(), kind: "method" as const, methodId: step.methodId, input: { ...step.input } })), { id: crypto.randomUUID(), kind: "end" as const }];
        setDraft({ name: target.name, blocks, outputDir: draft.outputDir });
        setSelectedId(null);
        setActivePreset(target.id);
        setNotice(`已载入预设「${target.name}」，${blocks.length} 个块`);
    }
    function saveAsPreset() {
        if (!draft.blocks.length) { setNotice("画布是空的，先摆几个块再存"); return; }
        const name = window.prompt("给这条预设起个名字", draft.name || "我的队列");
        if (!name?.trim()) return;
        const entry: QueuePreset = { id: `local-${Date.now().toString(36)}`, name: name.trim(), workflow: "", source: `本机保存 · ${new Date().toLocaleString("zh-CN")}`, summary: `从当前画布存下来的一条预设，共 ${draft.blocks.length} 个块；载入后可以照常增删改。`, outputs: [], scope: [], unmapped: [], caveats: ["本机保存的预设只在这台机器上，换设备不会跟着走。"], steps: [], blocks: toSavedBlocks(draft.blocks), local: true };
        const next = [...localPresets, entry];
        setLocalPresets(next);
        writeLocalPresets(next);
        setActivePreset(entry.id);
        setNotice(`已存为本机预设「${entry.name}」`);
    }
    function deletePreset(target: QueuePreset) {
        if (!window.confirm(`删除本机预设「${target.name}」？`)) return;
        const next = localPresets.filter((item) => item.id !== target.id);
        setLocalPresets(next);
        writeLocalPresets(next);
        if (activePreset === target.id) setActivePreset(null);
        setNotice(`已删除本机预设「${target.name}」`);
    }
    function makeBlock(value: string): Block {
        if (value === "gate") return { id: crypto.randomUUID(), kind: "gate", field: "status", operator: "equals", value: "completed", yes: [], no: [] };
        if (value === "end") return { id: crypto.randomUUID(), kind: "end" };
        // 把算法自己写了默认值的项先落到 input 里，免得界面上显示的默认值与实际存的不一致
        const input: Record<string, string> = {};
        for (const field of COMPUTE_METHODS.find((method) => method.id === value)?.inputSchema ?? []) if (field.defaultValue) input[field.name] = field.defaultValue;
        return { id: crypto.randomUUID(), kind: "method", methodId: value, input };
    }
    function insert(lane: string, index: number, value: string) {
        if (!["gate", "end"].includes(value) && !methods.some((method) => method.id === value)) return;
        const target = lane === "root" ? draft.blocks : (() => {
            const [gateId, side] = lane.split(":");
            const gate = findBlock(draft.blocks, gateId);
            return gate?.kind === "gate" ? gate[side as "yes" | "no"] : undefined;
        })();
        if (!target || index > target.length || target.slice(0, index).some((item) => item.kind !== "method") || ((value === "end" || value === "gate") && index < target.length)) return;
        const block = makeBlock(value);
        setDraft((current) => ({ ...current, blocks: editLane(current.blocks, lane, (items) => [...items.slice(0, index), block, ...items.slice(index)]) }));
        setSelectedId(block.id);
        setNotice("");
    }
    function update(id: string, edit: (block: Block) => Block) {
        setDraft((current) => ({ ...current, blocks: editBlocks(current.blocks, id, edit) }));
    }
    function move(lane: string, index: number, direction: -1 | 1) {
        setDraft((current) => ({ ...current, blocks: editLane(current.blocks, lane, (items) => {
            const next = [...items];
            const target = next[index + direction];
            if (!target || next[index]?.kind !== "method" || target.kind !== "method") return items;
            [next[index], next[index + direction]] = [next[index + direction], next[index]];
            return next;
        }) }));
    }
    function slot(lane: string, index: number) {
        return <div className="queue-slot" key={`${lane}-${index}`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); insert(lane, index, event.dataTransfer.getData("text/plain")); }}>
            <span className="queue-slot-line" />
            <button type="button" title="在此插入选中的块" onClick={() => insert(lane, index, palette)}>＋</button>
        </div>;
    }
    function renderLane(blocks: Block[], lane: string) {
        return <div className="queue-lane" key={lane}>
            {blocks.map((block, index) => <div key={block.id}>
                {index > 0 && blocks[index - 1].kind !== "method" ? <span className="queue-unreachable">旧草稿中此块位于路径终点之后，请移除或重建</span> : null}
                {index === 0 || blocks[index - 1].kind === "method" ? slot(lane, index) : null}
                <div className={`queue-block queue-block--${block.kind} ${selectedId === block.id ? "selected" : ""}`} onClick={(event) => { event.stopPropagation(); setSelectedId(block.id); }}>
                    <div className="queue-block-title"><span>{block.kind === "gate" ? "◇ 判断条件" : block.kind === "end" ? "■ 结束" : `▣ ${methodFor(block)?.name ?? "方法已移除"}`}</span><div className="queue-block-actions">
                        <button title="上移" disabled={index === 0 || block.kind !== "method" || blocks[index - 1]?.kind !== "method"} onClick={(event) => { event.stopPropagation(); move(lane, index, -1); }}>↑</button>
                        <button title="下移" disabled={index === blocks.length - 1 || block.kind !== "method" || blocks[index + 1]?.kind !== "method"} onClick={(event) => { event.stopPropagation(); move(lane, index, 1); }}>↓</button>
                        <button title="移除块" onClick={(event) => { event.stopPropagation(); setDraft((current) => ({ ...current, blocks: removeBlock(current.blocks, block.id) })); if (selectedId === block.id) setSelectedId(null); }}>×</button>
                    </div></div>
                    {dataflowNote(blocks, index, block.kind)}
                    {block.kind === "method" ? <small>{methodFor(block)?.category ?? block.methodId} · {Object.values(block.input).filter(Boolean).length} 项已填写</small> : block.kind === "end" ? <small>该路径在此终止，不再连接后续算法</small> : <div className="queue-gate-condition" onClick={(event) => event.stopPropagation()}><input aria-label="结果字段" title="上一个算法的结果字段" placeholder="结果字段" value={block.field} onChange={(event) => update(block.id, (item) => item.kind === "gate" ? { ...item, field: event.target.value } : item)} /><select aria-label="比较方式" value={block.operator} onChange={(event) => update(block.id, (item) => item.kind === "gate" ? { ...item, operator: event.target.value as Gate["operator"] } : item)}><option value="equals">等于</option><option value="notEquals">不等于</option><option value="greater">大于</option><option value="less">小于</option></select><input aria-label="比较值" placeholder="比较值" value={block.value} onChange={(event) => update(block.id, (item) => item.kind === "gate" ? { ...item, value: event.target.value } : item)} /></div>}
                </div>
                {block.kind === "gate" ? <>
                    <div className="queue-fork" aria-hidden="true" />
                    <div className="queue-branches" onClick={(event) => event.stopPropagation()}><div className="queue-branch"><span className="queue-branch-label yes">满足</span>{renderLane(block.yes, `${block.id}:yes`)}</div><div className="queue-branch"><span className="queue-branch-label no">不满足</span>{renderLane(block.no, `${block.id}:no`)}</div></div>
                </> : null}
            </div>)}
            {blocks.length === 0 || blocks[blocks.length - 1].kind === "method" ? slot(lane, blocks.length) : null}
        </div>;
    }
    return <section className="queue-editor">
        <header className="queue-header"><div><span className="runtime-eyebrow">BIO WORK QUEUE BUILDER</span><h2>工作队列组装</h2><p>拼接算法与判断分支，草稿自动保存在本机。</p></div><div className="queue-header-actions"><span>仅组装 · 暂不支持执行</span><button type="button" className="secondary-button" onClick={() => { if (!window.confirm("清空当前工作队列草稿？")) return; setDraft({ name: "未命名工作队列", blocks: [], outputDir: "" }); setSelectedId(null); setActivePreset(null); setNotice("已清空草稿"); }}>清空</button></div></header>
        <div className="queue-layout">
            <aside className="queue-palette"><h3>块库</h3><label className="queue-search"><span>查找方法</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称或分类" /></label><div className="queue-palette-scroll"><strong>预设队列</strong>{builtinPresets.map((item) => <button type="button" key={item.id} className={activePreset === item.id ? "active preset" : "preset"} onClick={() => loadPreset(item)}><span>{item.name}</span><small>{item.steps.length} 步 · {item.workflow}</small></button>)}{presetNote ? <small className="queue-palette-note">{presetNote}</small> : null}<strong>我的预设</strong>{localPresets.map((item) => <div className="queue-local-preset" key={item.id}><button type="button" className={activePreset === item.id ? "active preset" : "preset"} onClick={() => loadPreset(item)}><span>{item.name}</span><small>{item.blocks?.length ?? 0} 个块 · 本机</small></button><button type="button" className="queue-preset-delete" title="删除这条本机预设" onClick={() => deletePreset(item)}>×</button></div>)}<button type="button" className="queue-save-preset" onClick={saveAsPreset}>＋ 把当前画布存为预设</button><strong>逻辑</strong><button type="button" className={palette === "gate" ? "active gate" : "gate"} draggable onDragStart={(event) => event.dataTransfer.setData("text/plain", "gate")} onClick={() => setPalette("gate")}>◇ 条件判断</button><button type="button" className={palette === "end" ? "active end" : "end"} draggable onDragStart={(event) => event.dataTransfer.setData("text/plain", "end")} onClick={() => setPalette("end")}>■ 结束</button><strong>算法</strong>{methods.filter((method) => `${method.name} ${method.category}`.toLowerCase().includes(search.toLowerCase())).map((method) => <button type="button" key={method.id} className={palette === method.id ? "active" : ""} draggable onDragStart={(event) => event.dataTransfer.setData("text/plain", method.id)} onClick={() => setPalette(method.id)}><span>{method.name}</span><small>{method.category}</small></button>)}</div></aside>
            <main className="queue-canvas"><div className="queue-canvas-toolbar"><label>队列名称<input value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} /></label><label>输出目录<input value={draft.outputDir} onChange={(event) => setDraft((current) => ({ ...current, outputDir: event.target.value }))} placeholder="整条队列的输出目录" /></label><div className="queue-canvas-actions"><span>{draft.blocks.length} 个主线块 · 每条路径需放结束块 · 左键拖拽平移 · Ctrl+滚轮缩放</span><b>{Math.round(view.zoom * 100)}%</b><button type="button" onClick={() => setView({ x: 0, y: 0, zoom: 1 })}>重置视图</button></div></div><div className={`queue-canvas-scroll ${panning ? "panning" : ""}`} ref={canvasRef} onMouseDown={startPan} style={{ backgroundPosition: `${view.x}px ${view.y}px`, backgroundSize: `${20 * view.zoom}px ${20 * view.zoom}px` }}><div className="queue-flow" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}><div className="queue-endpoint">开始</div>{renderLane(draft.blocks, "root")}</div></div></main>
            <aside className="queue-inspector"><h3>块设置</h3>{selected ? selected.kind === "method" ? <><div className="queue-purpose"><span className="queue-purpose-tag">算法 · {methodFor(selected)?.category ?? "未知类别"}</span><h4>{methodFor(selected)?.name ?? "方法已移除"}</h4><p><span className="queue-purpose-key">作用</span>{methodFor(selected)?.description ?? "该算法在清单中没有描述。"}</p>{methodFor(selected)?.outputDescription ? <p><span className="queue-purpose-key">输出</span>{methodFor(selected)?.outputDescription}</p> : null}</div>{!LOCALLY_EXECUTABLE_METHOD_IDS.includes(selected.methodId) ? <div className="queue-warning">该算法目前只有调用契约，尚未接通独立执行链路。</div> : null}{lane?.previous && !methodFor(selected)?.inputSchema.some((field) => field.role === "upstream") ? <div className="queue-warning">该算法没有标注数据入口，上游输出传不进来。</div> : null}<div className="queue-dataflow"><div className="queue-dataflow-row"><span className="queue-flow-key">输入</span>{lane?.previous ? `上一步「${methodFor(lane.previous)?.name ?? lane.previous.methodId}」的输出` : "队列入口，在下面把真实路径填上"}</div>{methodFor(selected)?.inputSchema.some((field) => field.role === "output") ? <div className="queue-dataflow-row"><span className="queue-flow-key">输出</span>路径由队列按步骤分配，不需要填</div> : null}</div><div className="queue-inspector-fields">{methodFor(selected)?.inputSchema.filter((field) => field.role !== "output").map((field) => field.role === "upstream" && lane?.previous ? <div className="queue-linked" key={field.name}><span className="queue-flow-key">{field.label}</span>由上游输出提供</div> : <label key={field.name}>{field.label}{field.role === "resource" ? <em className="queue-field-tag">旁路输入</em> : null}{field.type === "select" ? <select value={selected.input[field.name] ?? field.defaultValue ?? ""} onChange={(event) => update(selected.id, (block) => block.kind === "method" ? { ...block, input: { ...block.input, [field.name]: event.target.value } } : block)}><option value="">请选择</option>{field.options?.map((option) => { const value = typeof option === "string" ? option : option.value; return <option value={value} key={value}>{typeof option === "string" ? option : option.label}</option>; })}</select> : field.type === "switch" ? <input type="checkbox" checked={selected.input[field.name] === "true"} onChange={(event) => update(selected.id, (block) => block.kind === "method" ? { ...block, input: { ...block.input, [field.name]: String(event.target.checked) } } : block)} /> : field.type === "textarea" ? <textarea value={selected.input[field.name] ?? field.defaultValue ?? ""} onChange={(event) => update(selected.id, (block) => block.kind === "method" ? { ...block, input: { ...block.input, [field.name]: event.target.value } } : block)} /> : <input type={field.type === "number" ? "number" : "text"} value={selected.input[field.name] ?? field.defaultValue ?? ""} onChange={(event) => update(selected.id, (block) => block.kind === "method" ? { ...block, input: { ...block.input, [field.name]: event.target.value } } : block)} />}{field.description ? <small>{field.description}</small> : null}</label>)}</div></> : selected.kind === "end" ? <div className="queue-purpose"><span className="queue-purpose-tag">逻辑 · 终点</span><h4>结束</h4><p><span className="queue-purpose-key">作用</span>标记所在路径到此为止，结束块之后不再执行任何算法。它放在哪条分支里，就只结束那条分支。</p></div> : <><div className="queue-purpose"><span className="queue-purpose-tag">逻辑 · 分支</span><h4>条件判断</h4><p><span className="queue-purpose-key">作用</span>读取上一个已执行块的结果字段，按判定结果把后续流程分给「满足」或「不满足」两条分支；两条分支各自独立，互不汇流。</p><p><span className="queue-purpose-key">提示</span>运行语义尚未接通，当前只保存判断条件，不会真的分支。</p></div><div className="queue-inspector-fields"><label>结果字段<input value={selected.field} onChange={(event) => update(selected.id, (block) => block.kind === "gate" ? { ...block, field: event.target.value } : block)} placeholder="例如 status" /></label><label>比较方式<select value={selected.operator} onChange={(event) => update(selected.id, (block) => block.kind === "gate" ? { ...block, operator: event.target.value as Gate["operator"] } : block)}><option value="equals">等于</option><option value="notEquals">不等于</option><option value="greater">大于</option><option value="less">小于</option></select></label><label>比较值<input value={selected.value} onChange={(event) => update(selected.id, (block) => block.kind === "gate" ? { ...block, value: event.target.value } : block)} /></label></div></> : preset ? <><div className="queue-purpose"><span className="queue-purpose-tag">{preset.local ? "本机预设" : `预设队列 · ${preset.workflow}`}</span><h4>{preset.name}</h4><p><span className="queue-purpose-key">来源</span>{preset.source}</p><p>{preset.summary}</p></div>{preset.outputs.length ? <div className="queue-preset-section"><h5>输出内容</h5><ul>{preset.outputs.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}{preset.scope.length ? <div className="queue-preset-section"><h5>使用范围</h5><ul>{preset.scope.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}{preset.unmapped.length ? <div className="queue-warning">工作流里有 {preset.unmapped.length} 个环节没有独立算法块，这份骨架没有对应块：{preset.unmapped.join("；")}。</div> : null}<ul className="queue-inspector-notes">{preset.caveats.map((item) => <li key={item}>{item}</li>)}</ul><p>可以在预设基础上直接改：删块、从块库加块、往分支里塞算法，改的就是当前画布上的草稿。</p></> : <p>选中画布中的块，这里会显示它的作用；算法块还会列出调用参数。可以从左侧拖入块，也可以选择块后点击连接线上的 ＋。</p>}{notice ? <p role="status">{notice}</p> : null}<div className="queue-warning">草稿只保存在当前客户端；算法间输出传递、分支判断与队列执行尚未接入后端。</div></aside>
        </div>
    </section>;
}
