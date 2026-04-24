import { chromium } from "playwright";
import { Dropbox } from "dropbox";
import { writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const {
  TRADINGVIEW_STORAGE_STATE,
  APP_KEY,
  APP_SECRET,
  DROPBOX_REFRESH_TOKEN,
} = process.env;

const required = {
  TRADINGVIEW_STORAGE_STATE,
  APP_KEY,
  APP_SECRET,
  DROPBOX_REFRESH_TOKEN,
};
for (const [name, value] of Object.entries(required)) {
  if (!value) {
    throw new Error(`Environment variable ${name} is required.`);
  }
}

const CHART_URL =
  "https://jp.tradingview.com/chart/pnyZf6WV/?symbol=SPREADEX%3ANIKKEI";

const TIMEFRAMES = [
  { label: "1h", interval: "60" },
  { label: "4h", interval: "240" },
  { label: "1D", interval: "D" },
  { label: "1W", interval: "W" },
];

const jst = new Date(Date.now() + 9 * 60 * 60 * 1000);
const pad = (n) => String(n).padStart(2, "0");
const dateStr = `${jst.getUTCFullYear()}-${pad(jst.getUTCMonth() + 1)}-${pad(jst.getUTCDate())}`;
const hhmm = `${pad(jst.getUTCHours())}${pad(jst.getUTCMinutes())}`;

const stateFile = join(tmpdir(), `tv_state_${Date.now()}.json`);
writeFileSync(stateFile, TRADINGVIEW_STORAGE_STATE, "utf8");

const browser = await chromium.launch({ headless: true });
let screenshots = [];

try {
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
    storageState: stateFile,
  });
  const page = await context.newPage();

  await page.goto(CHART_URL, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(8000);

  const signInVisible = await page
    .locator('button:has-text("サインイン"), button:has-text("Sign in")')
    .first()
    .isVisible()
    .catch(() => false);
  if (signInVisible) {
    throw new Error(
      "TradingView session expired. Run login.js locally and update TRADINGVIEW_STORAGE_STATE secret."
    );
  }

  await page.mouse.click(960, 540);
  await page.waitForTimeout(500);

  for (const tf of TIMEFRAMES) {
    await page.keyboard.type(tf.interval, { delay: 100 });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(4000);

    let buffer;
    try {
      buffer = await page.screenshot({ type: "png", fullPage: false });
    } catch (err) {
      console.warn(`Screenshot failed for ${tf.label}, retrying...`);
      await page.waitForTimeout(2000);
      buffer = await page.screenshot({ type: "png", fullPage: false });
    }
    screenshots.push({ label: tf.label, buffer });
    console.log(`Captured ${tf.label} (${buffer.length} bytes)`);
  }
} finally {
  await browser.close();
  try {
    unlinkSync(stateFile);
  } catch {}
}

const dbx = new Dropbox({
  clientId: APP_KEY,
  clientSecret: APP_SECRET,
  refreshToken: DROPBOX_REFRESH_TOKEN,
});

await Promise.all(
  screenshots.map(({ label, buffer }) =>
    dbx.filesUpload({
      path: `/TradingView/${dateStr}/${label}_${hhmm}.png`,
      contents: buffer,
      mode: { ".tag": "overwrite" },
    })
  )
);

console.log(
  `Uploaded ${screenshots.length} screenshots to /TradingView/${dateStr}/ (${hhmm} JST)`
);
