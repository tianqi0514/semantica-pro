import { useEffect } from "react";
import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n, { changeLanguage, type SupportedLanguage } from "./i18n";

const translatedText = new WeakMap<Text, { source: string; applied: string }>();
const translatedAttributes = new WeakMap<Element, Map<string, { source: string; applied: string }>>();
const LOCALIZED_ATTRIBUTES = ["aria-label", "placeholder", "title"] as const;

const dynamicTranslations: Array<[RegExp, (...values: string[]) => string]> = [
  [/^(\d[\d,.]*) nodes? · (\d[\d,.]*) edges?$/, (_all, nodes, edges) => `${nodes} 个节点 · ${edges} 条边`],
  [/^(\d[\d,.]*) nodes?$/, (_all, count) => `${count} 个节点`],
  [/^(\d[\d,.]*) edges?$/, (_all, count) => `${count} 条边`],
  [/^(\d[\d,.]*) relationships?$/, (_all, count) => `${count} 条关系`],
  [/^(\d[\d,.]*) active$/, (_all, count) => `${count} 个活跃节点`],
  [/^(\d[\d,.]*) types?$/, (_all, count) => `${count} 种类型`],
  [/^(\d[\d,.]*) rel\. types?$/, (_all, count) => `${count} 种关系类型`],
  [/^(\d[\d,.]*) direct neighbors in the full graph$/, (_all, count) => `完整图谱中有 ${count} 个直接邻居`],
  [/^(\d[\d,.]*) direct neighbors highlighted$/, (_all, count) => `已高亮 ${count} 个直接邻居`],
  [/^(\d[\d,.]*) source fields?$/, (_all, count) => `${count} 个来源字段`],
  [/^(\d[\d,.]*) steps?$/, (_all, count) => `${count} 个步骤`],
  [/^(\d[\d,.]*) ontologies$/, (_all, count) => `${count} 个本体/词表`],
  [/^(\d[\d,.]*) vocabulary schemes?$/, (_all, count) => `${count} 个词表方案`],
  [/^(\d[\d,.]*) flagged pairs?$/, (_all, count) => `${count} 组疑似重复实体`],
  [/^(\d[\d,.]*) rules? fired$/, (_all, count) => `触发 ${count} 条规则`],
  [/^(\d[\d,.]*) edges? added$/, (_all, count) => `新增 ${count} 条关系`],
  [/^(\d[\d,.]*) hops?$/, (_all, count) => `${count} 跳`],
  [/^direct · (\d[\d,.]*) hops?$/, (_all, count) => `直接关联 · ${count} 跳`],
  [/^near · (\d[\d,.]*) hops?$/, (_all, count) => `较近 · ${count} 跳`],
  [/^mid-range · (\d[\d,.]*) hops?$/, (_all, count) => `中距离 · ${count} 跳`],
  [/^distant · (\d[\d,.]*) hops?$/, (_all, count) => `远距离 · ${count} 跳`],
  [/^Closely related via (\d[\d,.]*) intermediate node\(s\) — high confidence\.$/, (_all, count) => `通过 ${count} 个中间节点紧密关联——高置信度。`],
  [/^(\d[\d,.]*) lower-priority neighbors are collapsed in the current view\.$/, (_all, count) => `当前视图已折叠 ${count} 个低优先级邻居。`],
  [/^(\d[\d,.]*) aggregated structural bundles? visible\.$/, (_all, count) => `当前显示 ${count} 个聚合结构束。`],
  [/^Loading nodes (\d[\d,.]*) of (\d[\d,.]*)$/, (_all, loaded, total) => `正在加载节点 ${loaded} / ${total}`],
  [/^Loading nodes (\d[\d,.]*)$/, (_all, loaded) => `正在加载节点 ${loaded}`],
  [/^Loading edges (\d[\d,.]*) of (\d[\d,.]*)$/, (_all, loaded, total) => `正在加载边 ${loaded} / ${total}`],
  [/^Loading edges (\d[\d,.]*)$/, (_all, loaded) => `正在加载边 ${loaded}`],
  [/^(\d+)% coverage$/, (_all, value) => `覆盖率 ${value}%`],
  [/^(\d[\d,.]*) nodes within (\d+) hops$/, (_all, count, hops) => `${hops} 跳内有 ${count} 个节点`],
  [/^Imported (\d[\d,.]*) nodes · (\d[\d,.]*) edges$/, (_all, nodes, edges) => `已导入 ${nodes} 个节点 · ${edges} 条边`],
  [/^Imported (\d[\d,.]*) nodes · (\d[\d,.]*) edges from (.+)$/, (_all, nodes, edges, file) => `已从 ${file} 导入 ${nodes} 个节点 · ${edges} 条边`],
  [/^Reasoning inferred (\d[\d,.]*) facts · (\d[\d,.]*) edges added$/, (_all, facts, edges) => `推理得到 ${facts} 条事实 · 新增 ${edges} 条关系`],
  [/^Showing (\d[\d,.]*) of (\d[\d,.]*)$/, (_all, shown, total) => `显示 ${shown} / ${total}`],
  [/^Found (\d[\d,.]*) results?$/, (_all, count) => `找到 ${count} 条结果`],
  [/^(\d[\d,.]*) events?$/, (_all, count) => `${count} 条事件`],
  [/^(\d[\d,.]*) vocabulary schemes? loaded$/, (_all, count) => `已加载 ${count} 个词表方案`],
  [/^Stage (\d+)\/(\d+)$/, (_all, current, total) => `阶段 ${current}/${total}`],
  [/^Temporal Scrubber · (.+)$/, (_all, range) => `时序游标 · ${range}`],
];

function isSkippedNode(node: Node) {
  const parent = node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement;
  return Boolean(parent?.closest("code, pre, script, style, .monaco-editor, [data-i18n-skip='true']"));
}

function translateCompactText(source: string) {
  const direct = i18n.t(source, { defaultValue: source });
  if (direct !== source) return direct;
  for (const [pattern, replacer] of dynamicTranslations) {
    const match = source.match(pattern);
    if (match) return replacer(...match);
  }
  return source;
}

function localizeString(source: string, language: string) {
  if (language === "en" || !/[A-Za-z]/.test(source)) return source;
  const compact = source.replace(/\s+/g, " ").trim();
  if (!compact) return source;
  const translated = translateCompactText(compact);
  if (translated === compact) return source;
  const leading = source.match(/^\s*/)?.[0] ?? "";
  const trailing = source.match(/\s*$/)?.[0] ?? "";
  return `${leading}${translated}${trailing}`;
}

function localizeTextNode(node: Text, language: string) {
  if (isSkippedNode(node)) return;
  const current = node.data;
  const previous = translatedText.get(node);
  const source = previous && current === previous.applied ? previous.source : current;
  const applied = localizeString(source, language);
  translatedText.set(node, { source, applied });
  if (current !== applied) node.data = applied;
}

function localizeElementAttributes(element: Element, language: string) {
  if (isSkippedNode(element)) return;
  const records = translatedAttributes.get(element) ?? new Map();
  for (const attribute of LOCALIZED_ATTRIBUTES) {
    const current = element.getAttribute(attribute);
    if (current === null) continue;
    const previous = records.get(attribute);
    const source = previous && current === previous.applied ? previous.source : current;
    const applied = localizeString(source, language);
    records.set(attribute, { source, applied });
    if (current !== applied) element.setAttribute(attribute, applied);
  }
  translatedAttributes.set(element, records);
}

function localizeTree(root: Node, language: string) {
  if (root.nodeType === Node.TEXT_NODE) {
    localizeTextNode(root as Text, language);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE || isSkippedNode(root)) return;
  localizeElementAttributes(root as Element, language);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();
  while (current) {
    if (current.nodeType === Node.TEXT_NODE) localizeTextNode(current as Text, language);
    else localizeElementAttributes(current as Element, language);
    current = walker.nextNode();
  }
}

export function I18nDomBridge() {
  const { i18n: activeI18n } = useTranslation();
  const language = activeI18n.language === "en" ? "en" : "zh-CN";

  useEffect(() => {
    document.documentElement.lang = language;
    localizeTree(document.body, language);
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "characterData") localizeTree(mutation.target, language);
        if (mutation.type === "attributes") localizeTree(mutation.target, language);
        for (const addedNode of mutation.addedNodes) localizeTree(addedNode, language);
      }
    });
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: [...LOCALIZED_ATTRIBUTES],
      childList: true,
      characterData: true,
      subtree: true,
    });
    return () => observer.disconnect();
  }, [language]);

  return null;
}

export function LanguageSwitcher() {
  const { i18n: activeI18n } = useTranslation();
  const language: SupportedLanguage = activeI18n.language === "en" ? "en" : "zh-CN";

  return (
    <div className="language-switcher" data-i18n-skip="true" role="group" aria-label={language === "zh-CN" ? "界面语言" : "Interface language"}>
      <Languages size={15} aria-hidden="true" />
      <button type="button" data-active={language === "zh-CN"} onClick={() => void changeLanguage("zh-CN")} title="简体中文">中</button>
      <button type="button" data-active={language === "en"} onClick={() => void changeLanguage("en")} title="English">EN</button>
    </div>
  );
}
