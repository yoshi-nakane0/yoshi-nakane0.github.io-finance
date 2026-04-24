import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const CHART_URL =
  "https://jp.tradingview.com/chart/pnyZf6WV/?symbol=SPREADEX%3ANIKKEI";
const OUTPUT_PATH = "storageState.json";
const SAVE_INTERVAL_MS = 5000;

const browser = await chromium.launch({ headless: false });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  locale: "ja-JP",
  timezoneId: "Asia/Tokyo",
});
const page = await context.newPage();

await page.goto(CHART_URL, { waitUntil: "domcontentloaded" });

console.log("\n=== TradingViewへログインしてください ===");
console.log("1. 開いたブラウザでTradingViewに手動ログイン");
console.log("2. チャートがログイン済み状態で表示されたら、ブラウザを閉じる");
console.log(
  `   （セッション情報は5秒ごとに ${OUTPUT_PATH} へ自動保存されます）\n`
);

let saveCount = 0;
const saveState = async () => {
  try {
    const state = await context.storageState();
    writeFileSync(OUTPUT_PATH, JSON.stringify(state, null, 2), "utf8");
    saveCount++;
    console.log(`[保存 #${saveCount}] ${new Date().toLocaleTimeString("ja-JP")}`);
  } catch (err) {
    console.error("保存失敗:", err.message);
  }
};

const intervalId = setInterval(saveState, SAVE_INTERVAL_MS);

browser.on("disconnected", () => {
  clearInterval(intervalId);
  console.log("\nブラウザが閉じられました。最終セッション情報は保存済みです。");
  process.exit(0);
});
