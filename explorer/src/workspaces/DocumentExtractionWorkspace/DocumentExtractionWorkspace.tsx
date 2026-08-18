import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  BookCopy,
  CheckCircle2,
  Clock3,
  Copy,
  Database,
  Download,
  FileJson,
  FileText,
  KeyRound,
  Layers3,
  LoaderCircle,
  Network,
  Plus,
  Save,
  ScanText,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import './DocumentExtractionWorkspace.css';

type View = 'extract' | 'templates' | 'settings';
type PreviewTab = 'graph' | 'text' | 'entities' | 'relationships';

type ScenarioRef = { id: string; name: string; version: number };
type ScenarioTemplate = {
  id: string;
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  built_in: boolean;
  version: number;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};
type ScenarioWrite = Pick<ScenarioTemplate, 'name' | 'description' | 'category' | 'enabled' | 'config'>;

type LLMSettings = {
  provider: 'kimi';
  display_name: string;
  model: string;
  base_url: string;
  has_api_key: boolean;
  updated_at: string;
};

type Evidence = { text?: string; chunk_id?: string };
type Entity = {
  id: string;
  type: string;
  text: string;
  confidence: number;
  metadata?: { evidence?: Evidence[]; scenario_refs?: ScenarioRef[] };
};
type Relationship = {
  id: string;
  source: string;
  target: string;
  type: string;
  weight?: number;
  metadata?: { confidence?: number; evidence?: Evidence[]; scenario_refs?: ScenarioRef[] };
};
type ExtractionResult = {
  run: { scenario: string; scenario_ids?: string[]; scenario_names?: string[]; method: string; provider?: string; model?: string };
  scenarios?: Array<ScenarioRef & { runs_succeeded: number; runs_failed: number; entities: number; relationships: number }>;
  documents: Array<{ id?: string; name: string; text_preview: string; character_count: number; chunk_count: number; scenario_refs?: ScenarioRef[] }>;
  entities: Entity[];
  relationships: Relationship[];
  statistics: {
    documents: number;
    documents_failed?: number;
    chunks: number;
    chunks_processed: number;
    entities: number;
    relationships: number;
    warnings: number;
  };
  warnings: string[];
  batch?: { total: number; succeeded: number; failed: number; items: BatchItem[] };
};
type BatchItem = {
  index: number;
  source_name: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  statistics?: ExtractionResult['statistics'];
  error?: string;
  chunks_total?: number;
  chunks_completed?: number;
  chunk_index?: number;
  stage?: string;
  stage_started_at?: string;
  stage_elapsed_seconds?: number;
  scenario_id?: string;
  scenario_name?: string;
  scenario_version?: number;
  file_index?: number;
  entities_found?: number;
  relationships_found?: number;
  started_at?: string;
  completed_at?: string;
};
type ExtractionPayload = {
  job_id: string;
  status: 'completed' | 'partial' | 'failed';
  result: ExtractionResult;
  items?: BatchItem[];
};
type JobProgress = {
  job_id: string;
  status: 'queued' | 'processing' | 'completed' | 'partial' | 'failed';
  phase: string;
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  percent: number;
  current_index: number | null;
  current_file: string | null;
  current_scenario?: string | null;
  current_stage: string | null;
  elapsed_seconds: number;
  result_ready: boolean;
  method?: 'llm' | 'regex' | 'ml';
  model?: string | null;
  message?: string;
  items: BatchItem[];
};
type BatchSubmission = {
  job_id: string;
  status: 'queued';
  progress_url: string;
  progress: JobProgress;
};

const ACTIVE_JOB_STORAGE_KEY = 'semantica.document-extraction.active-job';

const API_BASE = (() => {
  const configured = import.meta.env.VITE_DOCUMENT_EXTRACTOR_URL as string | undefined;
  if (configured) return configured.replace(/\/$/, '');
  return `${window.location.protocol}//${window.location.hostname}:9004`;
})();

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  const payload = await response.json().catch(() => null) as { detail?: string } | null;
  if (!response.ok) {
    throw new Error(payload?.detail || `请求失败（HTTP ${response.status}）`);
  }
  return payload as T;
}

function confidence(value: number | undefined) {
  return typeof value === 'number' ? value.toFixed(2) : '—';
}

function evidence(metadata?: { evidence?: Evidence[] }) {
  return metadata?.evidence?.[0]?.text || '—';
}

function scenarioNames(metadata?: { scenario_refs?: ScenarioRef[] }) {
  return metadata?.scenario_refs?.map((item) => item.name).join('、') || '—';
}

function durationLabel(seconds: number) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return minutes ? `${minutes} 分 ${remainder.toString().padStart(2, '0')} 秒` : `${remainder} 秒`;
}

function rememberActiveJob(jobId: string | null) {
  try {
    if (jobId) window.localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, jobId);
    else window.localStorage.removeItem(ACTIVE_JOB_STORAGE_KEY);
  } catch {
    // Progress still works in the current page when browser storage is unavailable.
  }
}

function readRememberedActiveJob() {
  try {
    return window.localStorage.getItem(ACTIVE_JOB_STORAGE_KEY);
  } catch {
    return null;
  }
}

const EXTRACTION_STAGES = [
  { id: 'loading_document', label: '读取文档' },
  { id: 'splitting_text', label: '文本分块' },
  { id: 'checking_model', label: '检查模型' },
  { id: 'extracting_entities', label: '抽取实体' },
  { id: 'extracting_relations', label: '抽取关系' },
  { id: 'merging_results', label: '合并结果' },
] as const;

function stageLabel(stage: string | undefined, method: JobProgress['method'] = 'llm') {
  const labels: Record<string, string> = {
    waiting: '等待后台调度',
    loading_document: '正在读取文档内容',
    splitting_text: '正在切分文本分块',
    checking_model: '正在检查模型连接',
    preparing_extractor: '正在准备抽取器',
    extracting_entities: method === 'llm' ? 'Kimi 正在抽取实体' : '正在抽取实体',
    extracting_relations: method === 'llm' ? 'Kimi 正在分析实体关系' : '正在抽取实体关系',
    merging_results: '正在合并、过滤和去重',
    chunk_completed: '当前文本分块已完成',
    chunk_failed: '当前文本分块处理失败',
    document_completed: '文档抽取完成，正在保存',
  };
  return labels[stage || 'waiting'] || '正在处理';
}

function StageTimeline({ item, method }: { item: BatchItem; method: JobProgress['method'] }) {
  const normalizedStage = item.stage === 'preparing_extractor' ? 'checking_model' : item.stage;
  const activeIndex = EXTRACTION_STAGES.findIndex((stage) => stage.id === normalizedStage);
  const allDone = ['chunk_completed', 'document_completed'].includes(item.stage || '') || item.status === 'completed';
  return (
    <div className="de-stage-timeline" aria-label="当前文件处理阶段">
      {EXTRACTION_STAGES.map((stage, index) => {
        const state = allDone || (activeIndex >= 0 && index < activeIndex) ? 'done' : index === activeIndex ? 'active' : 'pending';
        return (
          <div key={stage.id} data-state={state}>
            <span>{state === 'done' ? <CheckCircle2 size={12} /> : index + 1}</span>
            <small>{stage.id === 'checking_model' && method !== 'llm' ? '准备抽取' : stage.label}</small>
          </div>
        );
      })}
    </div>
  );
}

function JobProgressPanel({ progress }: { progress: JobProgress }) {
  const current = progress.items.find((item) => item.status === 'processing');
  const terminal = ['completed', 'partial', 'failed'].includes(progress.status);
  const title = terminal
    ? progress.status === 'completed' ? '全部文件抽取完成' : progress.status === 'partial' ? '抽取完成，部分文件失败' : '抽取任务失败'
    : current ? `正在处理第 ${current.index} / ${progress.total} 个“文档 × 模板”任务` : '任务已进入后台队列';

  return (
    <div className="de-progress-card">
      <div className="de-progress-heading">
        <div>
          <span className="ws-eyebrow">Background extraction · {progress.job_id.slice(0, 8)}</span>
          <h2>{title}</h2>
          <p>{current ? `${current.source_name} · ${current.scenario_name || '场景模板'}` : progress.message || (terminal ? '结果正在准备预览。' : '文件已上传，正在等待抽取。')}</p>
        </div>
        <strong className="de-progress-percent">{progress.percent}%</strong>
      </div>
      <div className="de-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent}>
        <span style={{ width: `${progress.percent}%` }} />
      </div>
      <div className="de-progress-metrics">
        <div><strong>{progress.completed} / {progress.total}</strong><span>已处理任务</span></div>
        <div><strong>{progress.succeeded}</strong><span>成功</span></div>
        <div><strong>{progress.failed}</strong><span>失败</span></div>
        <div><strong><Clock3 size={13} />{durationLabel(progress.elapsed_seconds)}</strong><span>已用时间</span></div>
      </div>
      {current ? (
        <>
          <div className="de-current-file">
            <LoaderCircle className="ws-spin" size={15} />
            <span><strong>{stageLabel(current.stage, progress.method)}</strong>{current.source_name} · {current.scenario_name}</span>
            <small>
              已等待 {durationLabel(current.stage_elapsed_seconds || 0)}
              {current.chunks_total ? ` · 分块 ${Math.min((current.chunks_completed || 0) + 1, current.chunks_total)}/${current.chunks_total}` : ''}
            </small>
          </div>
          <StageTimeline item={current} method={progress.method} />
          {current.stage === 'extracting_relations' && typeof current.entities_found === 'number' ? (
            <div className="de-stage-detail">实体抽取已完成：发现 {current.entities_found} 个候选实体；正在基于这些实体识别关系。</div>
          ) : null}
        </>
      ) : null}
      <div className="de-progress-files">
        {progress.items.map((item) => (
          <div key={`${item.index}-${item.source_name}`} data-status={item.status}>
            <span className="de-progress-file-icon">
              {item.status === 'completed' ? <CheckCircle2 size={14} /> : item.status === 'failed' ? <AlertCircle size={14} /> : item.status === 'processing' ? <LoaderCircle className="ws-spin" size={14} /> : item.index}
            </span>
            <span className="de-progress-file-name">{item.source_name}<em>{item.scenario_name}</em></span>
            <small>
              {item.status === 'queued' ? '等待处理' : item.status === 'processing'
                ? `${stageLabel(item.stage, progress.method)} · ${durationLabel(item.stage_elapsed_seconds || 0)}`
                : item.status === 'completed' ? `${item.statistics?.entities || 0} 实体 · ${item.statistics?.relationships || 0} 关系`
                : item.error || '处理失败'}
            </small>
          </div>
        ))}
      </div>
      {!terminal ? <div className="de-progress-note">百分比按已完成文件、文本分块和处理阶段的真实里程碑计算；Kimi 单次非流式调用期间会显示阶段等待时间，不虚构模型内部百分比。</div> : null}
    </div>
  );
}

function GraphPreview({ entities, relationships }: { entities: Entity[]; relationships: Relationship[] }) {
  const nodes = entities.slice(0, 14);
  const positioned = useMemo(() => {
    const centerX = 430;
    const centerY = 235;
    const xRadius = 320;
    const yRadius = 155;
    return nodes.map((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
      return {
        ...node,
        x: centerX + Math.cos(angle) * xRadius,
        y: centerY + Math.sin(angle) * yRadius,
      };
    });
  }, [nodes]);
  const byId = Object.fromEntries(positioned.map((node) => [node.id, node]));
  const visibleRelationships = relationships.filter((item) => byId[item.source] && byId[item.target]);

  if (!nodes.length) {
    return <div className="de-empty-preview"><Network size={28} /><span>本次没有抽取到可预览的实体</span></div>;
  }

  return (
    <div className="de-graph-preview">
      <svg viewBox="0 0 860 470" role="img" aria-label="实体关系图谱预览">
        <defs>
          <filter id="de-glow"><feGaussianBlur stdDeviation="3" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        {visibleRelationships.map((relation) => {
          const source = byId[relation.source];
          const target = byId[relation.target];
          return (
            <g key={relation.id}>
              <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} className="de-graph-edge" />
              <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 7} className="de-graph-edge-label">{relation.type}</text>
            </g>
          );
        })}
        {positioned.map((node, index) => (
          <g key={node.id} transform={`translate(${node.x},${node.y})`}>
            <circle r="25" className={`de-graph-node de-graph-node--${index % 4}`} filter="url(#de-glow)" />
            <text y="43" textAnchor="middle" className="de-graph-node-title">{node.text.slice(0, 13)}</text>
            <text y="58" textAnchor="middle" className="de-graph-node-type">{node.type}</text>
          </g>
        ))}
      </svg>
      {entities.length > nodes.length ? <div className="de-graph-note">预览显示前 {nodes.length} 个实体；导出文件包含全部 {entities.length} 个。</div> : null}
    </div>
  );
}

function SettingsView({ settings, onSettingsChanged }: { settings: LLMSettings; onSettingsChanged: (settings: LLMSettings) => void }) {
  const [model, setModel] = useState(settings.model);
  const [baseUrl, setBaseUrl] = useState(settings.base_url);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);

  async function saveSettings(clearApiKey = false) {
    setSaving(true);
    setMessage(null);
    try {
      const updated = await apiRequest<LLMSettings>('/api/settings/llm', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: 'kimi',
          display_name: 'Kimi K3',
          model,
          base_url: baseUrl,
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
          clear_api_key: clearApiKey,
        }),
      });
      setApiKey('');
      onSettingsChanged(updated);
      if (clearApiKey) {
        setMessage({ text: '已清除数据库中的 API Key' });
      } else if (updated.has_api_key) {
        try {
          const tested = await apiRequest<{ model: string }>('/api/settings/llm/test', { method: 'POST' });
          setMessage({ text: `配置已保存，连接成功：${tested.model}` });
        } catch (testError) {
          setMessage({ text: `配置已保存，但连接测试失败：${testError instanceof Error ? testError.message : String(testError)}`, error: true });
        }
      } else {
        setMessage({ text: '连接参数已保存；尚未配置 API Key' });
      }
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    setMessage(null);
    try {
      const tested = await apiRequest<{ model: string; model_count: number }>('/api/settings/llm/test', { method: 'POST' });
      setMessage({ text: `连接成功：${tested.model} 可用，共检测到 ${tested.model_count} 个模型` });
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="de-scroll de-settings-page">
      <div className="de-settings-intro">
        <div className="de-settings-mark"><Sparkles size={25} /></div>
        <div>
          <span className="ws-eyebrow">Default semantic model</span>
          <h2>Kimi K3 模型配置</h2>
          <p>文档抽取默认使用 Kimi K3。密钥提交给后端后会加密写入 SQLite，浏览器和查询接口都不会再返回密钥原文。</p>
        </div>
        <span className={`ws-pill ${settings?.has_api_key ? 'ws-pill--green' : 'ws-pill--amber'}`}>
          {settings?.has_api_key ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
          {settings?.has_api_key ? '密钥已配置' : '等待配置密钥'}
        </span>
      </div>

      <div className="de-settings-grid">
        <section className="ws-card de-settings-form">
          <div className="de-card-heading"><KeyRound size={17} /><div><h3>连接参数</h3><span>当前活动配置</span></div></div>
          <label className="de-field">
            <span>模型服务</span>
            <input className="ws-input" value="Kimi（OpenAI 兼容协议）" disabled />
          </label>
          <label className="de-field">
            <span>模型名称</span>
            <input className="ws-input mono" value={model} onChange={(event) => setModel(event.target.value)} />
          </label>
          <label className="de-field">
            <span>BASE URL</span>
            <input className="ws-input mono" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
            <span className="de-region-presets">
              <button type="button" data-active={baseUrl.includes('moonshot.cn')} onClick={() => setBaseUrl('https://api.moonshot.cn/v1')}>中国站</button>
              <button type="button" data-active={baseUrl.includes('moonshot.ai')} onClick={() => setBaseUrl('https://api.moonshot.ai/v1')}>国际站</button>
            </span>
          </label>
          <label className="de-field">
            <span>API Key</span>
            <input
              className="ws-input mono"
              type="password"
              value={apiKey}
              autoComplete="new-password"
              placeholder={settings?.has_api_key ? '已保存；留空不会覆盖原密钥' : '输入 Kimi API Key'}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </label>
          <div className="de-settings-actions">
            <button className="ws-btn ws-btn--primary" disabled={saving || !model.trim() || !baseUrl.trim()} onClick={() => void saveSettings(false)}>
              {saving ? <LoaderCircle className="ws-spin" size={15} /> : <Save size={15} />}
              保存配置
            </button>
            <button className="ws-btn ws-btn--ghost" disabled={saving || testing || !settings?.has_api_key} onClick={() => void testConnection()}>
              {testing ? <LoaderCircle className="ws-spin" size={15} /> : <CheckCircle2 size={15} />}
              测试连接
            </button>
            {settings?.has_api_key ? (
              <button className="ws-btn ws-btn--danger" disabled={saving} onClick={() => void saveSettings(true)}>清除密钥</button>
            ) : null}
            {message ? <span className={message.error ? 'de-message de-message--error' : 'de-message'}>{message.text}</span> : null}
          </div>
        </section>

        <aside className="de-security-card">
          <ShieldCheck size={22} />
          <h3>密钥如何保存</h3>
          <div className="de-security-step"><strong>1</strong><span>页面通过后端 API 提交密钥，不写进前端代码和场景 JSON。</span></div>
          <div className="de-security-step"><strong>2</strong><span>后端使用 Fernet 加密，再将密文写入持久化 SQLite。</span></div>
          <div className="de-security-step"><strong>3</strong><span>抽取时才在服务端内存中解密，任务结果和导出文件不包含密钥。</span></div>
          <div className="de-storage-path"><Database size={14} /><code>/data/settings.db</code></div>
        </aside>
      </div>
    </div>
  );
}

const EMPTY_SCENARIO_CONFIG: Record<string, unknown> = {
  scenario_name: '新场景模板',
  method: 'llm',
  provider: 'kimi',
  model: 'kimi-k3',
  base_url: 'https://api.moonshot.cn/v1',
  entity_confidence: 0.55,
  relation_confidence: 0.55,
  chunk_size: 5000,
  chunk_overlap: 300,
  extract_temporal_bounds: true,
  entity_types: [
    { name: 'BUSINESS_ENTITY', label: '业务对象', description: '需要从文档中识别的核心业务对象', aliases: [], patterns: [] },
  ],
  relation_types: [],
};

function TemplateCenter() {
  const [templates, setTemplates] = useState<ScenarioTemplate[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ScenarioWrite | null>(null);
  const [configText, setConfigText] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);

  async function loadTemplates(preferredId?: string) {
    const payload = await apiRequest<{ templates: ScenarioTemplate[] }>('/api/scenarios');
    setTemplates(payload.templates);
    const requestedId = preferredId || selectedId;
    const selected = payload.templates.find((item) => item.id === requestedId) || payload.templates[0];
    setSelectedId(selected?.id || null);
    if (selected) {
      setDraft({ name: selected.name, description: selected.description, category: selected.category, enabled: selected.enabled, config: selected.config });
      setConfigText(JSON.stringify(selected.config, null, 2));
    }
    return payload.templates;
  }

  useEffect(() => {
    let cancelled = false;
    apiRequest<{ templates: ScenarioTemplate[] }>('/api/scenarios')
      .then((payload) => {
        if (cancelled) return;
        setTemplates(payload.templates);
        const selected = payload.templates[0];
        setSelectedId(selected?.id || null);
        if (selected) {
          setDraft({ name: selected.name, description: selected.description, category: selected.category, enabled: selected.enabled, config: selected.config });
          setConfigText(JSON.stringify(selected.config, null, 2));
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) setMessage({ text: error instanceof Error ? error.message : String(error), error: true });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  function selectTemplate(template: ScenarioTemplate) {
    setSelectedId(template.id);
    setDraft({ name: template.name, description: template.description, category: template.category, enabled: template.enabled, config: template.config });
    setConfigText(JSON.stringify(template.config, null, 2));
    setMessage(null);
  }

  function createBlank() {
    const blank: ScenarioWrite = {
      name: '新场景模板',
      description: '说明这个模板适用于哪些材料，以及希望从中得到什么结果。',
      category: '通用',
      enabled: true,
      config: structuredClone(EMPTY_SCENARIO_CONFIG),
    };
    setSelectedId(null);
    setDraft(blank);
    setConfigText(JSON.stringify(blank.config, null, 2));
    setMessage({ text: '正在新建模板，填写完成后点击保存' });
  }

  async function saveTemplate() {
    if (!draft) return;
    setSaving(true);
    setMessage(null);
    try {
      const config = JSON.parse(configText) as Record<string, unknown>;
      const payload = { ...draft, config };
      const saved = await apiRequest<ScenarioTemplate>(selectedId ? `/api/scenarios/${selectedId}` : '/api/scenarios', {
        method: selectedId ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      await loadTemplates(saved.id);
      setMessage({ text: `模板已保存为 v${saved.version}` });
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setSaving(false);
    }
  }

  async function duplicateTemplate() {
    if (!selectedId) return;
    setSaving(true);
    try {
      const copied = await apiRequest<ScenarioTemplate>(`/api/scenarios/${selectedId}/duplicate`, { method: 'POST' });
      await loadTemplates(copied.id);
      setMessage({ text: '已创建独立副本，可以安全修改' });
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setSaving(false);
    }
  }

  async function deleteTemplate() {
    const selected = templates.find((item) => item.id === selectedId);
    if (!selected || selected.built_in) return;
    if (!window.confirm(`确认删除“${selected.name}”吗？历史抽取结果不会被删除。`)) return;
    setSaving(true);
    try {
      await apiRequest<null>(`/api/scenarios/${selected.id}`, { method: 'DELETE' });
      const remaining = await loadTemplates();
      if (!remaining.length) createBlank();
      setMessage({ text: '自定义模板已删除，历史结果仍保留模板名称与版本标识' });
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true });
    } finally {
      setSaving(false);
    }
  }

  const selected = templates.find((item) => item.id === selectedId);
  return (
    <div className="de-template-page">
      <aside className="de-template-library">
        <div className="de-template-library-head">
          <div><span className="ws-eyebrow">Scenario catalogue</span><h2>场景模板</h2><p>模板决定抽取目标；一次任务可以多选。</p></div>
          <button className="ws-btn ws-btn--primary" onClick={createBlank}><Plus size={14} />新建</button>
        </div>
        <div className="de-template-list">
          {loading ? <div className="de-template-empty"><LoaderCircle className="ws-spin" size={18} />正在加载模板</div> : null}
          {templates.map((template) => (
            <button key={template.id} data-active={selectedId === template.id} data-enabled={template.enabled} onClick={() => selectTemplate(template)}>
              <span className="de-template-card-top"><strong>{template.name}</strong><i>{template.built_in ? '内置' : '自定义'}</i></span>
              <span>{template.description}</span>
              <small><b>{template.category}</b><em>v{template.version}</em><em>{template.enabled ? '已启用' : '已停用'}</em></small>
            </button>
          ))}
        </div>
      </aside>

      <main className="de-template-editor">
        {!draft ? <div className="de-template-empty"><BookCopy size={30} />选择一个模板，或新建模板</div> : <>
          <div className="de-template-editor-head">
            <div><span className="ws-eyebrow">{selected ? `${selected.id} · v${selected.version}` : 'Unsaved template'}</span><h2>{selected ? '编辑场景模板' : '新建场景模板'}</h2></div>
            <div>
              {selectedId ? <button className="ws-btn ws-btn--ghost" disabled={saving} onClick={() => void duplicateTemplate()}><Copy size={14} />复制</button> : null}
              {selected && !selected.built_in ? <button className="ws-btn ws-btn--danger" disabled={saving} onClick={() => void deleteTemplate()}><Trash2 size={14} />删除</button> : null}
              <button className="ws-btn ws-btn--primary" disabled={saving || !draft.name.trim()} onClick={() => void saveTemplate()}>{saving ? <LoaderCircle className="ws-spin" size={14} /> : <Save size={14} />}保存模板</button>
            </div>
          </div>
          <div className="de-template-form">
            <label className="de-field"><span>模板名称</span><input className="ws-input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
            <label className="de-field"><span>业务分类</span><input className="ws-input" value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} /></label>
            <label className="de-field de-template-description"><span>适用范围</span><textarea className="ws-textarea" value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
            <label className="de-template-switch"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span><strong>允许用于新任务</strong><small>停用后历史结果不受影响，但抽取页不能再选择</small></span></label>
            <label className="de-field de-template-config"><span>抽取结构与参数（JSON）</span><textarea className="ws-textarea" value={configText} onChange={(event) => setConfigText(event.target.value)} spellCheck={false} /></label>
          </div>
          {message ? <div className={message.error ? 'de-template-message de-template-message--error' : 'de-template-message'}>{message.error ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />}{message.text}</div> : null}
        </>}
      </main>
    </div>
  );
}

function ExtractionView({ settings }: { settings: LLMSettings | null }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [templates, setTemplates] = useState<ScenarioTemplate[]>([]);
  const [selectedScenarioIds, setSelectedScenarioIds] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [method, setMethod] = useState<'llm' | 'regex'>('llm');
  const [activeJobId, setActiveJobId] = useState<string | null>(() => readRememberedActiveJob());
  const [busy, setBusy] = useState(() => Boolean(readRememberedActiveJob()));
  const [error, setError] = useState('');
  const [payload, setPayload] = useState<ExtractionPayload | null>(null);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [previewTab, setPreviewTab] = useState<PreviewTab>('graph');
  const [resultScenarioId, setResultScenarioId] = useState('all');
  const [selectedDocument, setSelectedDocument] = useState(0);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    apiRequest<{ templates: ScenarioTemplate[] }>('/api/scenarios?enabled_only=true')
      .then(({ templates: available }) => {
        setTemplates(available);
        const initial = available.find((item) => item.id === 'procurement-compliance') || available[0];
        if (initial) setSelectedScenarioIds([initial.id]);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (activeJobId) return () => { cancelled = true; };
    apiRequest<{ jobs: JobProgress[] }>('/api/extraction-jobs/active')
      .then(({ jobs }) => {
        if (cancelled || !jobs.length) return;
        setProgress(jobs[0]);
        setBusy(true);
        setActiveJobId(jobs[0].job_id);
        rememberActiveJob(jobs[0].job_id);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [activeJobId]);

  useEffect(() => {
    if (!activeJobId) return undefined;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const latest = await apiRequest<JobProgress>(`/api/extractions/${activeJobId}/status`);
        if (cancelled) return;
        setProgress(latest);
        setError('');
        const terminal = ['completed', 'partial', 'failed'].includes(latest.status);
        if (!terminal) {
          timer = window.setTimeout(() => void poll(), 1000);
          return;
        }
        if (!latest.result_ready) {
          setBusy(false);
          setError(latest.message || '任务未生成可预览的结果，请重新提交');
          setActiveJobId(null);
          rememberActiveJob(null);
          return;
        }
        const result = await apiRequest<ExtractionResult>(`/api/extractions/${activeJobId}`);
        if (cancelled) return;
        setPayload({
          job_id: activeJobId,
          status: latest.status as ExtractionPayload['status'],
          result,
          items: latest.items,
        });
        setPreviewTab('graph');
        setSelectedDocument(0);
        setBusy(false);
        setActiveJobId(null);
        rememberActiveJob(null);
      } catch (reason) {
        if (cancelled) return;
        setError(`暂时无法读取任务进度，正在自动重试：${reason instanceof Error ? reason.message : String(reason)}`);
        timer = window.setTimeout(() => void poll(), 2000);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeJobId]);

  function addFiles(incoming: File[]) {
    setFiles((current) => {
      const merged = [...current];
      for (const candidate of incoming) {
        const duplicate = merged.some((item) => item.name === candidate.name && item.size === candidate.size && item.lastModified === candidate.lastModified);
        if (!duplicate) merged.push(candidate);
      }
      return merged.slice(0, 20);
    });
  }

  async function runExtraction() {
    if (!files.length) {
      setError('请先选择一份业务文档');
      return;
    }
    if (method === 'llm' && !settings?.has_api_key) {
      setError('Kimi K3 API Key 尚未配置，请先打开“模型配置”保存密钥');
      return;
    }
    if (!selectedScenarioIds.length) {
      setError('请至少选择一个场景模板');
      return;
    }
    setBusy(true);
    setError('');
    setPayload(null);
    setProgress(null);
    setResultScenarioId('all');
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append('files', file));
      formData.append('scenario_ids_json', JSON.stringify(selectedScenarioIds));
      formData.append('method', method);
      const submitted = await apiRequest<BatchSubmission>('/api/extractions/batch', { method: 'POST', body: formData });
      setProgress(submitted.progress);
      rememberActiveJob(submitted.job_id);
      setActiveJobId(submitted.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setBusy(false);
    }
  }

  const result = payload?.result;
  const visibleEntities = useMemo(
    () => (result?.entities || []).filter((entity) => resultScenarioId === 'all' || entity.metadata?.scenario_refs?.some((item) => item.id === resultScenarioId)),
    [result, resultScenarioId],
  );
  const visibleRelationships = useMemo(
    () => (result?.relationships || []).filter((relationship) => resultScenarioId === 'all' || relationship.metadata?.scenario_refs?.some((item) => item.id === resultScenarioId)),
    [result, resultScenarioId],
  );
  const entityById = useMemo(() => Object.fromEntries(visibleEntities.map((entity) => [entity.id, entity])), [visibleEntities]);

  return (
    <div className="de-page">
      <aside className="de-sidebar">
        <div className="de-sidebar-header">
          <span className="ws-eyebrow">Extraction pipeline</span>
          <h2>新建抽取任务</h2>
          <p>上传原始材料，得到带来源证据的候选实体和关系。</p>
        </div>

        <div className="de-sidebar-body">
          <div className="de-step-card">
            <span className="de-step-number">01</span>
            <div className="de-step-title">选择文档</div>
            <button
              type="button"
              className={`de-dropzone${dragging ? ' de-dropzone--dragging' : ''}${files.length ? ' de-dropzone--ready' : ''}`}
              onClick={() => inputRef.current?.click()}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                addFiles(Array.from(event.dataTransfer.files));
              }}
            >
              {files.length ? <FileText size={22} /> : <UploadCloud size={22} />}
              <strong>{files.length ? `已选择 ${files.length} 个文件` : '点击或拖入多个文件'}</strong>
              <span>{files.length ? '可继续追加，最多 20 个' : 'PDF · DOCX · TXT · HTML · MD'}</span>
            </button>
            <input
              ref={inputRef}
              className="de-file-input"
              type="file"
              multiple
              accept=".pdf,.docx,.html,.htm,.txt,.md,.markdown"
              onChange={(event) => {
                addFiles(Array.from(event.target.files || []));
                event.target.value = '';
              }}
            />
            {files.length ? (
              <div className="de-file-list">
                {files.map((file, index) => (
                  <div key={`${file.name}-${file.size}-${file.lastModified}`}><span><strong>{index + 1}</strong>{file.name}</span><button type="button" title="移除文件" onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}><X size={12} /></button></div>
                ))}
              </div>
            ) : null}
          </div>

          <div className="de-step-card">
            <span className="de-step-number">02</span>
            <div className="de-step-title">选择场景模板 <small>可多选</small></div>
            <div className="de-extraction-templates">
              {templates.map((template) => {
                const active = selectedScenarioIds.includes(template.id);
                const entities = Array.isArray(template.config.entity_types) ? template.config.entity_types.length : 0;
                const relationships = Array.isArray(template.config.relation_types) ? template.config.relation_types.length : 0;
                return <button key={template.id} data-active={active} onClick={() => {
                  setError('');
                  setSelectedScenarioIds((current) => {
                    if (active) return current.filter((id) => id !== template.id);
                    if (current.length >= 8) {
                      setError('一次最多选择 8 个场景模板');
                      return current;
                    }
                    return [...current, template.id];
                  });
                }}>
                  <span>{active ? <CheckCircle2 size={13} /> : <Layers3 size={13} />}</span>
                  <strong>{template.name}</strong>
                  <small>{entities} 类实体 · {relationships} 类关系</small>
                </button>;
              })}
              {!templates.length ? <div className="de-template-loading"><LoaderCircle className="ws-spin" size={13} />正在加载可用模板</div> : null}
            </div>
            <div className="de-template-selection-summary">已选择 {selectedScenarioIds.length} 个模板，将执行 {files.length * selectedScenarioIds.length} 个“文档 × 模板”任务</div>
          </div>

          <div className="de-step-card">
            <span className="de-step-number">03</span>
            <div className="de-step-title">选择抽取方式</div>
            <div className="de-method-picker">
              <button data-active={method === 'llm'} onClick={() => setMethod('llm')}><Sparkles size={14} /><span><strong>Kimi K3</strong><small>理解自然语言</small></span></button>
              <button data-active={method === 'regex'} onClick={() => setMethod('regex')}><ScanText size={14} /><span><strong>规则抽取</strong><small>按正则命中</small></span></button>
            </div>
            {method === 'llm' ? (
              <div className={`de-model-status${settings?.has_api_key ? ' de-model-status--ready' : ''}`}>
                <span />{settings?.has_api_key ? `${settings.model} · 已配置` : 'Kimi 密钥未配置'}
              </div>
            ) : null}
          </div>
        </div>

        <div className="de-run-area">
          {error ? <div className="de-run-error"><AlertCircle size={14} />{error}</div> : null}
          <button className="ws-btn ws-btn--primary de-run-button" disabled={busy || !templates.length || !selectedScenarioIds.length} onClick={() => void runExtraction()}>
            {busy ? <LoaderCircle className="ws-spin" size={16} /> : <Sparkles size={16} />}
            {busy ? `正在处理 ${progress?.total || files.length * selectedScenarioIds.length} 个任务…` : `开始抽取${selectedScenarioIds.length > 1 ? `（${selectedScenarioIds.length} 个场景）` : ''}`}
          </button>
        </div>
      </aside>

      <main className="de-preview-pane">
        {!result ? (
          <div className="de-welcome-preview">
            {progress ? <JobProgressPanel progress={progress} /> : <>
              <div className="de-preview-orbit"><ScanText size={34} /></div>
              <span className="ws-eyebrow">Candidate knowledge preview</span>
              <h2>抽取结果会在这里预览</h2>
              <p>先检查原文解析，再核对实体和关系证据，确认后下载 JSON 或 CSV 导入知识图谱。</p>
              <div className="de-flow-line"><span>原始文档</span><i>→</i><span>文本分块</span><i>→</i><span>实体</span><i>→</i><span>关系</span><i>→</i><span>导出</span></div>
            </>}
          </div>
        ) : (
          <div className="de-result">
            <div className="de-result-topbar">
              <div>
                <span className="ws-eyebrow">{payload.status} · {result.run.model || result.run.method}</span>
                <h2>{result.documents.length > 1 ? `${result.documents.length} 份文档的合并结果` : result.documents[0]?.name || '批量抽取未得到结果'}</h2>
              </div>
              <div className="de-downloads">
                <a className="ws-btn ws-btn--ghost" href={`${API_BASE}/api/extractions/${payload.job_id}/export/json`}><FileJson size={14} />下载 JSON</a>
                <a className="ws-btn ws-btn--ghost" href={`${API_BASE}/api/extractions/${payload.job_id}/export/csv`}><Download size={14} />下载 CSV</a>
              </div>
            </div>

            <div className="de-metrics">
              <div><strong>{result.statistics.documents}</strong><span>成功文档</span></div>
              <div><strong>{result.statistics.entities}</strong><span>实体节点</span></div>
              <div><strong>{result.statistics.relationships}</strong><span>关系边</span></div>
              <div><strong>{result.statistics.documents_failed || 0}</strong><span>失败文档</span></div>
            </div>

            {result.scenarios?.length ? (
              <div className="de-result-scenarios">
                <button data-active={resultScenarioId === 'all'} onClick={() => setResultScenarioId('all')}><Layers3 size={13} /><span><strong>合并结果</strong><small>{result.entities.length} 实体 · {result.relationships.length} 关系</small></span></button>
                {result.scenarios.map((scenario) => (
                  <button key={scenario.id} data-active={resultScenarioId === scenario.id} onClick={() => setResultScenarioId(scenario.id)}>
                    <BookCopy size={13} /><span><strong>{scenario.name}</strong><small>{scenario.entities} 实体 · {scenario.relationships} 关系 · v{scenario.version}</small></span>
                  </button>
                ))}
              </div>
            ) : null}

            {payload.items?.length ? (
              <div className="de-batch-status">
                {payload.items.map((item) => (
                  <div key={`${item.index}-${item.source_name}-${item.scenario_id}`} data-status={item.status}>
                    {item.status === 'completed' ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                    <span>{item.source_name}<em>{item.scenario_name}</em></span>
                    <small>{item.status === 'completed' ? `${item.statistics?.entities || 0} 实体 · ${item.statistics?.relationships || 0} 关系` : item.error}</small>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="de-preview-tabs">
              {([
                ['graph', '图谱预览'],
                ['text', '原文预览'],
                ['entities', `实体 ${visibleEntities.length}`],
                ['relationships', `关系 ${visibleRelationships.length}`],
              ] as Array<[PreviewTab, string]>).map(([id, label]) => (
                <button key={id} data-active={previewTab === id} onClick={() => setPreviewTab(id)}>{label}</button>
              ))}
            </div>

            <div className="de-preview-content">
              {previewTab === 'graph' ? <GraphPreview entities={visibleEntities} relationships={visibleRelationships} /> : null}
              {previewTab === 'text' ? <div className="de-document-preview">
                {result.documents.length > 1 ? <div className="de-document-tabs">{result.documents.map((document, index) => <button key={document.id || `${document.name}-${index}`} data-active={selectedDocument === index} onClick={() => setSelectedDocument(index)}>{document.name}</button>)}</div> : null}
                <pre className="de-text-preview">{result.documents[selectedDocument]?.text_preview || '没有可预览的文本'}</pre>
              </div> : null}
              {previewTab === 'entities' ? (
                <div className="de-table-wrap"><table><thead><tr><th>类型</th><th>实体</th><th>来源模板</th><th>置信度</th><th>原文证据</th></tr></thead><tbody>
                  {visibleEntities.map((entity) => <tr key={entity.id}><td><span className="ws-pill ws-pill--accent">{entity.type}</span></td><td>{entity.text}</td><td className="de-scenario-source">{scenarioNames(entity.metadata)}</td><td className="mono">{confidence(entity.confidence)}</td><td className="de-evidence">{evidence(entity.metadata)}</td></tr>)}
                </tbody></table></div>
              ) : null}
              {previewTab === 'relationships' ? (
                <div className="de-table-wrap"><table><thead><tr><th>起点</th><th>关系</th><th>终点</th><th>来源模板</th><th>置信度</th><th>原文证据</th></tr></thead><tbody>
                  {visibleRelationships.map((relation) => <tr key={relation.id}><td>{entityById[relation.source]?.text || relation.source}</td><td><span className="ws-pill ws-pill--purple">{relation.type}</span></td><td>{entityById[relation.target]?.text || relation.target}</td><td className="de-scenario-source">{scenarioNames(relation.metadata)}</td><td className="mono">{confidence(relation.metadata?.confidence ?? relation.weight)}</td><td className="de-evidence">{evidence(relation.metadata)}</td></tr>)}
                </tbody></table></div>
              ) : null}
            </div>

            {result.warnings?.length ? <div className="de-result-warnings"><AlertCircle size={15} /><div>{result.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div></div> : null}
          </div>
        )}
      </main>
    </div>
  );
}

export function DocumentExtractionWorkspace({ view }: { view: View }) {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [settingsError, setSettingsError] = useState('');

  useEffect(() => {
    apiRequest<LLMSettings>('/api/settings/llm')
      .then(setSettings)
      .catch((error: unknown) => setSettingsError(error instanceof Error ? error.message : String(error)));
  }, []);

  if (settingsError) {
    return <div className="ws-empty"><AlertCircle className="ws-empty-icon" size={30} /><div className="ws-empty-title">文档抽取服务暂不可用</div><div className="ws-empty-body">{settingsError}<br />请确认 9004 端口的服务已经启动。</div></div>;
  }

  if (view === 'settings' && !settings) {
    return <div className="ws-empty"><LoaderCircle className="ws-spin ws-empty-icon" size={30} /><div className="ws-empty-title">正在读取模型配置</div></div>;
  }

  return view === 'settings'
    ? <SettingsView settings={settings as LLMSettings} onSettingsChanged={setSettings} />
    : view === 'templates'
      ? <TemplateCenter />
      : <ExtractionView settings={settings} />;
}
