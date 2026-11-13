// npm install --no-save playwright && npx playwright install chromium (if needed).
// PLAYWRIGHT_MODULE may point to an existing installation for offline environments.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
async function main(){
  const browser=await chromium.launch({headless:true,...(process.env.ATLAS_BROWSER_CHANNEL?{channel:process.env.ATLAS_BROWSER_CHANNEL}:{})});
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const failures=[];
  page.on('pageerror',error=>failures.push(error.message));
  await page.goto(process.env.ATLAS_URL||'http://127.0.0.1:8000');
  await page.locator('.document-card').first().waitFor();
  if(await page.locator('#documents .document-card').count()!==6)throw new Error('Expected six active seed documents');
  await page.locator('#search').fill('RCA-101');
  await page.locator('#search-form button').click();
  await page.waitForFunction(()=>document.querySelector('#result-count').textContent==='1 documents');
  await page.locator('#documents .document-card').first().click();
  await page.locator('#document-dialog[open]').waitFor();
  if(!(await page.locator('#document-content').innerText()).includes('Root cause'))throw new Error('Source sections missing');
  await page.locator('#ask-document').click();
  await page.locator('#chat-form button').click();
  await page.locator('.message.assistant .citation').first().waitFor();
  await page.locator('#chat-input').fill('SOP-101 steps');
  await page.locator('#chat-form button').click();
  await page.waitForFunction(()=>document.querySelectorAll('.message.assistant').length===2);
  if(!(await page.locator('.message.assistant').last().innerText()).includes('5. Confirm'))throw new Error('Complete SOP not rendered');
  await page.locator('[data-view="reviews"]').click();
  await page.locator('#review-list .document-card').first().waitFor();
  if(await page.locator('#review-list .document-card').count()!==4)throw new Error('Review queue missing');
  await page.locator('[data-view="knowledge"]').click();
  await page.locator('#search').fill('');
  await page.locator('#search-form button').click();
  await page.waitForFunction(()=>document.querySelector('#result-count').textContent==='6 documents');
  fs.mkdirSync(path.join(__dirname,'../docs'),{recursive:true});
  await page.screenshot({path:path.join(__dirname,'../docs/library-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(__dirname,'../docs/library-mobile.png'),fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Mobile viewport overflow');
  await browser.close();
  if(failures.length)throw new Error(failures.join('\n'));
  console.log('PASS: library, exact search, source dialog, grounded chat, RCA/SOP switching, reviews, mobile layout; no browser errors.');
}
main().catch(error=>{console.error(error);process.exit(1);});
