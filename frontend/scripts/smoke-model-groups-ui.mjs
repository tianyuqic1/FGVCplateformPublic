import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
const models = [
 {id:"a",name:"DINO 模型 A",dataset_id:"birds",dataset_name:"鸟类识别",dataset_version_id:"v1",dataset_version_number:1},
 {id:"b",name:"ImageNet 模型 B",dataset_id:"birds",dataset_name:"鸟类识别",dataset_version_id:"v1",dataset_version_number:1},
 {id:"c",name:"模型 C",dataset_id:"birds",dataset_name:"鸟类识别",dataset_version_id:"v2",dataset_version_number:2},
 {id:"d",name:"模型 D",dataset_id:"flowers",dataset_name:"花卉识别",dataset_version_id:"v1",dataset_version_number:1},
];
try {
 const page=await browser.newPage({viewport:{width:1500,height:1000}});page.setDefaultTimeout(15000);
 const errors=[];page.on("pageerror",e=>errors.push(e.message));
 await page.route(url => url.pathname.startsWith("/api/"), async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==="/api/model-versions") return route.fulfill({json:{model_versions:models}});
  if(path==="/api/model-version-comparisons") return route.fulfill({json:{comparison:{model_versions:[models[0],models[2]],comparable:false,warnings:[]}}});
  return route.fulfill({json:{datasets:[]}});
 });
 const base=process.env.SMOKE_BASE_URL || "http://localhost:5173";
 await page.goto(`${base}/models`);
 await page.getByRole("button",{name:/鸟类识别/}).click();
 await page.getByRole("button",{name:/数据版本 v1/}).click();
 await page.getByRole("checkbox",{name:"选择 DINO 模型 A"}).check();
 await page.getByRole("checkbox",{name:"选择 ImageNet 模型 B"}).check();
 assert.ok(await page.getByRole("button",{name:"比较 2",exact:true}).isEnabled());
 await page.locator(".model-dataset-groups").screenshot({path:"/tmp/finevision-model-groups-desktop.png"});
 await page.getByRole("button",{name:/数据版本 v2/}).click();
 assert.ok(await page.getByRole("button",{name:"比较",exact:true}).isDisabled());
 await page.getByRole("checkbox",{name:"选择 模型 C"}).check();
 await page.getByRole("button",{name:/花卉识别/}).click();
 assert.ok(await page.getByRole("button",{name:"比较",exact:true}).isDisabled());
 await page.getByRole("button",{name:/数据版本 v1/}).click();
 await page.setViewportSize({width:390,height:1000});
 assert.ok(await page.locator(".model-dataset-groups").evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.goto(`${base}/models/compare?ids=a,c`);
 await page.getByText("比较不可用",{exact:true}).waitFor();
 assert.equal(await page.locator(".fv-chart").count(),0);
 assert.deepEqual(errors,[]);
 console.log("Model group expansion, scoped selection/reset, mobile overflow and direct-link rejection passed.");
} finally { await browser.close(); }
