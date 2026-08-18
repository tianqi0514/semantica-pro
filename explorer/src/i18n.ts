import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { zhCN } from "./locales/zh-CN";

export const LANGUAGE_STORAGE_KEY = "semantica-explorer-language";
export type SupportedLanguage = "zh-CN" | "en";

function readInitialLanguage(): SupportedLanguage {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return stored === "en" ? "en" : "zh-CN";
  } catch {
    return "zh-CN";
  }
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: {} },
    "zh-CN": { translation: zhCN },
  },
  lng: readInitialLanguage(),
  fallbackLng: "en",
  keySeparator: false,
  nsSeparator: false,
  interpolation: { escapeValue: false },
  returnNull: false,
});

document.documentElement.lang = i18n.language === "en" ? "en" : "zh-CN";

export function changeLanguage(language: SupportedLanguage) {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // The language still applies for this session when storage is unavailable.
  }
  document.documentElement.lang = language;
  return i18n.changeLanguage(language);
}

export default i18n;
