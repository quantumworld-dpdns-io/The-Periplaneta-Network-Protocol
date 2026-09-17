"use client";

/**
 * Two languages, one dictionary.
 *
 * Every string the interface shows lives here, in English and Traditional
 * Chinese, so a missing translation is a compile error rather than a blank. The
 * choice is kept in localStorage; there is no server involved.
 *
 * Scientific terms keep their English form inside the Chinese text where that is
 * how they are actually written in the field (kdr, LD50, ΔΔG), because
 * translating them would make the page harder to read for the people most likely
 * to read it, not easier.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type Lang = "en" | "zh";

type Entry = { en: string; zh: string };

export const DICT = {
  // --- chrome ---------------------------------------------------------------
  appName: { en: "The Periplaneta Protocol", zh: "蟑螂協定" },
  tagline: {
    en: "Insecticide resistance evolution in an interacting cockroach colony",
    zh: "會互相影響的蟑螂群落中，殺蟲劑抗性如何演化",
  },
  navOverview: { en: "Overview", zh: "總覽" },
  navColony: { en: "Colony", zh: "群落" },
  navEvolution: { en: "Evolution", zh: "演化" },
  navStrategy: { en: "Strategy", zh: "用藥策略" },
  navChemistry: { en: "Chemistry", zh: "化學" },
  navNeural: { en: "Neural", zh: "神經訊號" },
  navParameters: { en: "Parameters", zh: "參數來源" },
  navInterop: { en: "Interop", zh: "交付格式" },
  language: { en: "中文", zh: "English" },

  // --- shared ---------------------------------------------------------------
  run: { en: "Run", zh: "執行" },
  running: { en: "Running…", zh: "執行中…" },
  reset: { en: "Reset", zh: "重設" },
  export: { en: "Export", zh: "匯出" },
  whatThisMeans: { en: "What this means", zh: "這代表什麼" },
  howToReproduce: { en: "Reproduce this from a terminal", zh: "在終端機重現這個結果" },
  beScepticalOf: { en: "Be sceptical of", zh: "請對這些保持懷疑" },
  tookSeconds: { en: "took", zh: "耗時" },
  seconds: { en: "s", zh: " 秒" },
  apiDown: {
    en: "Cannot reach the model server. Start it with: just api",
    zh: "連不到模型伺服器。用 just api 啟動它。",
  },
  loading: { en: "Loading…", zh: "載入中…" },
  noBackendNote: {
    en: "This page is a snapshot exported by the model. The other pages run the model live.",
    zh: "這一頁是模型匯出的快照。其他頁面會即時執行模型。",
  },

  // --- controls -------------------------------------------------------------
  strategy: { en: "Strategy", zh: "策略" },
  generations: { en: "Generations", zh: "世代數" },
  populationSize: { en: "Adults", zh: "成蟲數" },
  exposedFraction: { en: "Share of colony exposed", zh: "接觸到藥劑的比例" },
  doseLd50: { en: "Dose (multiples of LD50)", zh: "劑量（LD50 的倍數）" },
  founderFrequency: { en: "Starting resistance", zh: "起始抗性頻率" },
  fitnessCost: { en: "Fitness cost of resistance", zh: "抗性的適應度代價" },
  linkedLoci: { en: "Link kdr and cyp6 (5 cM apart)", zh: "kdr 與 cyp6 連鎖（相距 5 cM）" },
  seed: { en: "Seed", zh: "亂數種子" },
  seeds: { en: "Repeats", zh: "重複次數" },
  colonySize: { en: "Individuals", zh: "個體數" },
  hours: { en: "Hours simulated", zh: "模擬時數" },
  harborages: { en: "Harborages", zh: "巢穴數" },
  resources: { en: "Food sites", zh: "食物點" },
  bait: { en: "Bait", zh: "餌劑" },
  noBait: { en: "none", zh: "不放" },
  stations: { en: "Baited stations", zh: "下餌站數" },
  ligand: { en: "Insecticide", zh: "殺蟲劑" },
  mutation: { en: "Mutation", zh: "突變" },
  useVqe: { en: "Run VQE (slower)", zh: "執行 VQE（較慢）" },
  burden: { en: "Internal dose (LD50 units)", zh: "體內劑量（LD50 單位）" },

  // --- overview -------------------------------------------------------------
  theQuestion: { en: "The question", zh: "研究問題" },
  theAnswer: { en: "The answer", zh: "答案" },
  overviewIntro: {
    en: "German cockroaches become resistant to insecticides fast. This project simulates a colony whose individuals actually interact, poisons it, and follows what happens to their genes over a hundred generations. Every page below runs that model live.",
    zh: "德國蟑螂對殺蟲劑產生抗性的速度很快。這個專案模擬一個成員之間會互相影響的群落，對它施藥，並追蹤上百個世代之間基因發生了什麼事。以下每一頁都會即時執行這個模型。",
  },
  startHere: { en: "Start here", zh: "從這裡開始" },
  overviewGuide: {
    en: "If you only look at one page, look at Strategy: it is the question the project was built to answer. Colony shows why the answer depends on the animals influencing one another.",
    zh: "如果只看一頁，看「用藥策略」，那是這個專案要回答的問題。「群落」則說明為什麼答案取決於個體之間互相影響。",
  },

  // --- colony ---------------------------------------------------------------
  colonyTitle: { en: "The colony and its contact network", zh: "群落與接觸網路" },
  colonyExplain: {
    en: "Cockroaches shelter in crevices, come out at night to feed, and are drawn to each other and to the droppings of others. Nobody tells them who to touch: contacts happen because they want the same refuges and the same food. That is what makes this a network of animals rather than a diagram. Put a bait down and the poison travels along it.",
    zh: "蟑螂躲在縫隙裡，夜間外出覓食，並且會被同伴以及同伴的排泄物吸引。沒有人指定牠們要接觸誰：接觸之所以發生，是因為牠們想要同樣的躲藏處和同樣的食物。這才讓它成為一個「動物的網路」，而不只是一張示意圖。放下餌劑，毒物就會沿著這個網路傳開。",
  },
  contacts: { en: "Contact pairs", zh: "接觸配對數" },
  density: { en: "Network density", zh: "網路密度" },
  meanDegree: { en: "Mean partners", zh: "平均接觸對象數" },
  isolated: { en: "Never touched anyone", zh: "從未接觸他人" },
  largestComponent: { en: "Largest connected group", zh: "最大連通群" },
  dead: { en: "Killed", zh: "死亡" },
  fedAtBait: { en: "Fed at the bait", zh: "在餌站進食" },
  secondaryKill: { en: "Killed without ever feeding", zh: "未曾進食卻死亡" },
  legendHarborage: { en: "harborage", zh: "巢穴" },
  legendFood: { en: "food", zh: "食物" },
  legendRoach: { en: "brighter = more contacts", zh: "越亮＝接觸越多" },

  // --- evolution ------------------------------------------------------------
  evolutionTitle: { en: "One run, generation by generation", zh: "單次執行，逐代追蹤" },
  evolutionExplain: {
    en: "Each line is how common one resistance gene is in the colony. Spraying the same product every generation drives its matching gene to fixation, meaning every animal carries it and the product stops working. Stop spraying and the gene fades, because carrying resistance costs something. That cost is the whole reason rotating products can work.",
    zh: "每一條線代表某個抗性基因在群落中的普遍程度。每一代都噴同一種藥，對應的基因會被推到固定，也就是每一隻都帶有它，那種藥就失效了。停止用藥後基因會衰退，因為帶著抗性是有代價的。這個代價正是輪替用藥之所以可能有效的原因。",
  },
  alleleFrequency: { en: "Gene frequency", zh: "基因頻率" },
  generation: { en: "Generation", zh: "世代" },
  tryThis: { en: "Try this", zh: "試試看" },
  evolutionTry: {
    en: "Set the fitness cost to zero and switch to untreated. Resistance stops fading. That single control is why the headline answer depends on a number nobody has measured.",
    zh: "把適應度代價設為零並切換到「不用藥」，抗性就不再衰退。光是這一個控制項，就說明了為什麼主要結論取決於一個沒有人量過的數字。",
  },

  // --- strategy -------------------------------------------------------------
  strategyTitle: { en: "Rotation, mixture, or one product", zh: "輪替、混合，還是單一用藥" },
  strategyExplain: {
    en: "All arms apply the same total amount of insecticide per generation, so a three-way mixture gives each product a third of a dose. Without that matching, the comparison would just reward whoever sprays most.",
    zh: "所有組別每一代施用的藥量總和相同，所以三種混合時每種只有三分之一劑量。若不這樣對齊，比較結果只會獎勵用藥最多的那一組。",
  },
  resistantRuns: { en: "Runs that became resistant", zh: "出現抗性的次數" },
  medianGen: { en: "Median generation", zh: "中位世代" },
  peakTargetSite: { en: "Peak resistance reached", zh: "抗性最高點" },
  meanSurvival: { en: "Survived each treatment", zh: "每次施藥的存活率" },
  never: { en: "never", zh: "從未" },
  lowerIsBetter: { en: "lower is better control", zh: "越低代表防治越好" },
  strategyTradeoff: {
    en: "The mixture kills the most and selects the worst. At a third of a dose each product is strong enough to weed out susceptible animals but too weak to kill resistant ones, so it selects for every resistance mechanism at once.",
    zh: "混合用藥殺得最多，但選汰出的抗性也最嚴重。每種只有三分之一劑量時，足以淘汰感受性個體，卻殺不死抗性個體，於是三種抗性機制同時被選汰出來。",
  },

  // --- chemistry ------------------------------------------------------------
  chemistryTitle: { en: "What the mutation does to the drug", zh: "突變對藥物做了什麼" },
  chemistryExplain: {
    en: "Resistance often works by changing the exact spot the insecticide grabs onto. ΔΔG measures how much weaker that grip becomes. A published resistance ratio is already a measurement of it, so that is the reference every calculation is scored against. Positive means weaker binding, which means resistance.",
    zh: "抗性常常是靠改變殺蟲劑抓附的那個位點來達成的。ΔΔG 衡量的就是這個抓附變弱了多少。已發表的抗性倍數本身就是對它的量測，所以那就是所有計算要被對照的基準。數值為正代表結合變弱，也就是抗性。",
  },
  backend: { en: "Method", zh: "方法" },
  maturity: { en: "How much to trust it", zh: "可信程度" },
  impliedRR: { en: "Implied resistance ratio", zh: "推得的抗性倍數" },
  vsReference: { en: "Error vs measurement", zh: "與量測值的誤差" },
  signWrong: { en: "wrong direction", zh: "方向相反" },
  chemistryHonest: {
    en: "Neither calculation is good enough to drive the science, and they fail in opposite directions. The simple physical model sees only shape, so the bulkier mutant binds worse. The quantum model sees only electrons, so the same mutant binds better. The real effect is the shape of the channel, which neither captures.",
    zh: "兩種計算都還不足以支撐科學結論，而且錯的方向相反。簡單物理模型只看形狀，所以較大的突變型結合較差；量子模型只看電子，所以同一個突變型反而結合較好。真正的效應來自通道的幾何形狀，兩者都抓不到。",
  },

  // --- neural ---------------------------------------------------------------
  neuralTitle: { en: "What a poisoned neuron should look like", zh: "中毒的神經元應該長什麼樣" },
  neuralExplain: {
    en: "This is the only part of the project an experiment can check directly. Put an electrode on a dosed cockroach and see whether the firing does what is predicted here. The numbers are firing rate relative to normal.",
    zh: "這是整個專案唯一可以被實驗直接檢驗的部分。在中毒的蟑螂身上放一支電極，看牠的放電是否如這裡預測的那樣。表中數字是相對於正常狀態的放電率。",
  },
  wouldFalsify: { en: "What would prove this wrong", zh: "什麼會證明這是錯的" },
  cannotTellApart: { en: "What it cannot tell apart", zh: "它分辨不出來的事" },

  // --- live -----------------------------------------------------------------
  liveTitle: { en: "Live colony", zh: "即時群落" },
  liveExplain: {
    en: "This is a colony running right now, not a recording. The heat is real model state: the pheromone the animals deposit and follow, the insecticide lying on the floor, or simply where they are. Interfere with it while it runs and watch what happens over the next simulated hours.",
    zh: "這是一個正在執行中的群落，不是錄影。熱區顯示的是模型真實的狀態：牠們留下並追隨的費洛蒙、地面上的藥劑殘留，或單純是牠們的所在位置。你可以在執行中對它動手，看接下來幾個模擬小時發生什麼事。",
  },
  layer: { en: "Heat layer", zh: "熱區圖層" },
  layerPheromone: { en: "aggregation pheromone", zh: "聚集費洛蒙" },
  layerResidue: { en: "insecticide residue", zh: "藥劑殘留" },
  layerDensity: { en: "where they are", zh: "個體分布" },
  play: { en: "Play", zh: "播放" },
  pause: { en: "Pause", zh: "暫停" },
  restart: { en: "Restart", zh: "重新開始" },
  speed: { en: "Speed", zh: "速度" },
  simSecondsPerFrame: { en: "sim-seconds per frame", zh: "每格模擬秒數" },
  lights: { en: "Lights", zh: "光照" },
  lightsAuto: { en: "day/night cycle", zh: "日夜循環" },
  lightsDark: { en: "hold night", zh: "維持夜間" },
  lightsLight: { en: "hold day", zh: "維持白天" },
  clearBait: { en: "Take bait away", zh: "撤餌" },
  immigrate: { en: "Let 20 in", zh: "放 20 隻進來" },
  trap: { en: "Trap 20", zh: "捕捉 20 隻" },
  aggregation: { en: "Attraction to each other", zh: "彼此吸引的強度" },
  baitStrength: { en: "Bait strength", zh: "餌劑濃度" },
  simulatedTime: { en: "Simulated time", zh: "模擬時間" },
  night: { en: "night", zh: "夜間" },
  day: { en: "day", zh: "白天" },
  alive: { en: "Alive", zh: "存活" },
  resting: { en: "In harborage", zh: "在巢穴" },
  foraging: { en: "Out feeding", zh: "外出覓食" },
  returning: { en: "Heading home", zh: "返巢中" },
  eventLog: { en: "What you did", zh: "你做過的事" },
  liveTry: {
    en: "Put fipronil down and switch the heat layer to insecticide residue. You will see it spread out of the bait station along the paths the animals actually take, and then reach the harborages they carry it back to.",
    zh: "放下 fipronil，然後把熱區圖層切到「藥劑殘留」。你會看到它從餌站沿著蟑螂真正走的路線擴散開來，接著抵達牠們帶回去的巢穴。",
  },
  liveDead: { en: "Killed", zh: "死亡" },
  peakValue: { en: "peak", zh: "峰值" },
  stationVisits: { en: "number = animals that have used that station", zh: "數字＝用過該站的蟑螂數" },
  clickToBait: { en: "click a food site to bait it", zh: "點食物點即可下餌" },
  baitBusiest: { en: "Bait the busiest", zh: "對最忙的站下餌" },
  connecting: { en: "Connecting to the model server…", zh: "正在連線到模型伺服器…" },

  // --- parameters -----------------------------------------------------------
  parametersTitle: { en: "Where every number comes from", zh: "每個數字的來源" },
  parametersExplain: {
    en: "A parameter here either cites a published measurement or is declared an assumption with the range it should be swept over. The code refuses to accept one without the other. Most of them are assumptions, and that is the honest state of this model rather than a detail.",
    zh: "這裡的每個參數，要嘛引用已發表的量測，要嘛明確標示為假設並附上應該掃描的範圍。程式碼不接受兩者皆無的參數。其中大多數是假設，這是這個模型誠實的現況，不是小細節。",
  },
  fromLiterature: { en: "Measured, with a source", zh: "有出處的量測值" },
  assumed: { en: "Assumed, awaiting a sensitivity analysis", zh: "假設值，尚待敏感度分析" },
  sweptOver: { en: "swept over", zh: "掃描範圍" },
} as const satisfies Record<string, Entry>;

export type Key = keyof typeof DICT;

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({
  lang: "en",
  setLang: () => {},
});

const STORAGE_KEY = "periplaneta.lang";

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("en");

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "en" || saved === "zh") setLangState(saved);
      else if (navigator.language?.toLowerCase().startsWith("zh")) setLangState("zh");
    } catch {
      /* private browsing, or storage blocked: English is a fine default */
    }
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      window.localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* nothing to do; the choice just will not persist */
    }
  }, []);

  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLang() {
  return useContext(Ctx);
}

/** `t("navColony")` returns the string in the current language. */
export function useT() {
  const { lang } = useLang();
  return useCallback((key: Key) => DICT[key][lang], [lang]);
}
