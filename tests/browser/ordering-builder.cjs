const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium, firefox, webkit } = require(process.env.VEYRA_PLAYWRIGHT_MODULE || "playwright-core");
const [engine, fixturePath] = process.argv.slice(2);
const type = { chrome: chromium, firefox, webkit }[engine];
assert(type && fixturePath, "Pass a browser engine and disposable fixture JSON path.");
const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"))[engine];
const origin = process.env.VEYRA_BUILDER_TEST_ORIGIN || "http://127.0.0.1:8112";
const rgb = hex => "rgb(" + [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)).join(", ") + ")";
const palette = {accent:"#105140", background:"#e5effa", text:"#133454", secondary:"#405770"};
const neighborPalette = {accent:"#6d2839", background:"#fff2df", text:"#25222a", secondary:"#5a463d"};
const responsiveWidths = [320,375,768,1024,1440];
const samples = {accent:[".store-header","backgroundColor"], background:["body","backgroundColor"],
  text:[".store-product-heading h2","color"], secondary:[".store-product .field-hint","color"]};
const waitText = (scope, selector, text) => scope.locator(selector).filter({hasText:text}).first().waitFor();
const asset = (scope, selector) => scope.locator(selector).evaluate(async image => {
  await image.decode();
  if (!image.naturalWidth) throw new Error("Image did not render.");
  return image.getAttribute("src");
});
const fit = async (page, widths) => {
  for (const width of widths) {
    await page.setViewportSize({width, height:970});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Page overflows at " + width);
  }
};
async function css(scope, selector, property, expected) {
  let actual;
  for (let attempt = 0; attempt < 140; attempt += 1) {
    // Live menu refresh replaces product nodes. Read the currently rendered node each time.
    try {
      actual = await scope.locator(selector).first().evaluate((node, prop) =>
        node.isConnected ? getComputedStyle(node)[prop] : undefined, property);
    } catch (error) {
      if (!/execution context|navigation/i.test(error.message)) throw error;
    }
    if (actual === expected) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  const context = await scope.locator("body").evaluate(node => ({
    customized: node.dataset.orderingCustomized,
    text: getComputedStyle(node).getPropertyValue("--store-text").trim(),
    ink: getComputedStyle(node).getPropertyValue("--ink").trim(),
    preview: node.hasAttribute("data-render-preview"),
  }));
  assert.equal(actual, expected, selector + " " + property + ", " + JSON.stringify(context));
}
const theme = (scope, name, color) => css(scope, ...samples[name], rgb(color));
const computedPalette = page => page.evaluate(selectors =>
  Object.fromEntries(Object.entries(selectors).map(([key,[selector,property]]) =>
    [key,getComputedStyle(document.querySelector(selector))[property]])), samples);
async function strictGuest(browser) {
  const context = await browser.newContext({viewport:{width:768,height:970}, reducedMotion:"reduce"});
  await context.route(/\/order\/(?:table|pickup)\/[^/]+\/(?:cart\/)?$/, async route => {
    if (!route.request().isNavigationRequest()) return route.continue();
    const response = await route.fetch();
    const headers = response.headers();
    // Model a proxy with an independent policy that permits same-origin stylesheets only.
    headers["content-security-policy"] = headers["content-security-policy"] + ", style-src 'self'";
    await route.fulfill({response, headers});
  });
  assert.deepEqual(await context.cookies(),[]);
  return context;
}
async function png(page, color) {
  const data = await page.evaluate(color => {
    const canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 100;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 256, 100);
    return canvas.toDataURL("image/png").split(",")[1];
  }, color);
  return {name:"builder-test.png", mimeType:"image/png", buffer:Buffer.from(data,"base64")};
}

(async () => {
  const launch = engine === "chrome" && process.env.VEYRA_CHROME_EXECUTABLE
    ? {executablePath:process.env.VEYRA_CHROME_EXECUTABLE} : {};
  const browser = await type.launch(launch);
  const owner = await browser.newPage({viewport:{width:engine === "webkit" ? 390 : 1440,height:970}, reducedMotion:"reduce"});
  const customer = await browser.newContext({viewport:{width:390,height:900}, reducedMotion:"reduce"});
  const guest = await customer.newPage();
  const takeaway = await customer.newPage();
  const errors = [];
  for (const page of [owner, guest, takeaway]) {
    page.setDefaultTimeout(30000);
    page.on("pageerror", error => errors.push(error.name));
  }
  const editorPath = fixture.root + "settings/ordering-page/";
  const frame = owner.frameLocator("[data-builder-preview]");
  const input = name => owner.locator("[name=" + name + "]");
  const button = name => owner.getByRole("button", {name, exact:true});
  const section = name => owner.locator("summary").filter({hasText:name}).first();
  const set = async values => { for (const [key,value] of Object.entries(values)) await input(key).fill(value); };
  const updated = () => waitText(owner,"[data-preview-status]","Preview up to date");
  const save = async () => {
    await button("Save draft").click();
    await owner.getByText(/Draft saved/).first().waitFor();
    await waitText(owner,"[data-preview-status]","Showing the saved design");
  };
  const publish = async () => {
    await owner.getByRole("link",{name:"Publish",exact:true}).click();
    await button("Publish draft").click();
    await owner.getByText(/Ordering page published/).first().waitFor();
  };
  const colors = async (scope, expected = palette) => {
    for (const [key,value] of Object.entries(expected)) await theme(scope,key,value);
  };
  const assertPublished = async page => {
    await waitText(page,".store-brand","Forage & Fern");
    await colors(page);
    await css(page,".store-page-banner","objectPosition","22% 73%");
    await css(page,".store-product","flexDirection","column");
    await css(page,".store-product","borderTopStyle","solid");
    await css(page,".store-product-detail summary","borderTopLeftRadius","999px");
    await css(page,".store-order-link","backgroundColor",rgb(palette.accent));
    await asset(page,".store-logo");
    await asset(page,".store-page-banner");
    await waitText(page,".store-cover-caption h2","Autumn menu");
    await waitText(page,"#store-information p","8 Riverside Road");
    assert.deepEqual(await page.locator(".store-product-heading h2").allTextContents(),["Iced pandan","Filter coffee"]);
    assert.deepEqual(await page.locator("[data-menu-category]:not([data-menu-category=''])").allTextContents(),
      ["Single origin coffees","Chilled seasonal drinks"]);
    assert.equal(await page.locator(".store-product-image").count(),1);
    assert.equal(await page.locator(".store-product-description").count(),2);
  };
  try {
    const neighbor = fixture.neighbor;
    const neighborEditor = neighbor.root + "settings/ordering-page/";
    const neighborOwner = await browser.newPage();
    await neighborOwner.goto(origin+"/login/?next="+encodeURIComponent(neighborEditor));
    await neighborOwner.locator("[name=email]").fill(neighbor.email);
    await neighborOwner.locator("[name=password]").fill(neighbor.password);
    await neighborOwner.getByRole("button",{name:"Sign in",exact:true}).click();
    await neighborOwner.waitForURL(url => url.pathname === neighborEditor);
    await neighborOwner.locator("summary").filter({hasText:"Appearance"}).click();
    for (const [key,value] of Object.entries(neighborPalette)) await neighborOwner.locator("[name="+key+"]").fill(value);
    await neighborOwner.locator("[name=display_name]").fill("Neighbor published");
    await neighborOwner.getByRole("button",{name:"Save draft",exact:true}).click();
    await neighborOwner.getByRole("link",{name:"Publish",exact:true}).click();
    await neighborOwner.getByRole("button",{name:"Publish draft",exact:true}).click();
    await neighborOwner.getByText(/Ordering page published/).first().waitFor();
    await neighborOwner.locator("[name=display_name]").fill("Neighbor private draft");
    await neighborOwner.getByRole("button",{name:"Save draft",exact:true}).click();
    await neighborOwner.getByText(/Draft saved/).first().waitFor();
    await neighborOwner.close();

    const neighborGuest = await customer.newPage();
    const checkNeighbor = async () => {
      await neighborGuest.goto(origin+neighbor.menu,{waitUntil:"domcontentloaded"});
      await waitText(neighborGuest,".store-brand","Neighbor published");
      await colors(neighborGuest,neighborPalette);
      assert.equal(await neighborGuest.getByText("Neighbor private draft",{exact:true}).count(),0);
    };
    await checkNeighbor();
    await owner.goto(origin + "/login/?next=" + encodeURIComponent(editorPath));
    await set({email:fixture.email,password:fixture.password});
    await button("Sign in").click();
    await owner.waitForURL(url => url.pathname === editorPath);
    assert.equal((await owner.request.get(origin+neighborEditor+"preview/")).status(),404);
    for (const [page,url] of [[guest,fixture.menu],[takeaway,fixture.pickup]]) {
      await page.goto(origin+url);
      await waitText(page,"[data-store-connection]",/Menu synchronized|Menu prices/);
    }
    await section("Appearance").click();
    for (const [key,value] of Object.entries(palette)) {
      await input(key).fill(value);
      await updated();
      await theme(frame,key,value);
      assert.equal(await owner.locator('output[for="id_' + key + '"]').textContent(), value.toUpperCase());
    }
    await input("secondary").fill("#eeeeee");
    await waitText(owner,"[data-preview-status]",/Secondary text.*4\.5:1/);
    for (const [name,color] of [["Olive and cream","#374534"],["Deep plum","#592E46"],["Warm ivory","#392A22"]]) {
      await owner.locator("[data-color-preset]").selectOption({label:name});
      await updated();
      await theme(frame,"accent",color);
    }
    const dark = {accent:"#e7bf70",background:"#1c2631",text:"#f5f4ee",secondary:"#b4c1cc"};
    await set(dark);
    await updated();
    for (const [key,value] of Object.entries(dark)) await theme(frame,key,value);
    await css(frame,".store-header","color","rgb(0, 0, 0)");
    await set(palette);
    await updated();
    if (engine === "chrome") {
      const guarded = owner.waitForEvent("dialog", {timeout:8000}).then(async dialog => {
        assert.equal(dialog.type(),"beforeunload");
        await dialog.dismiss();
      });
      await Promise.all([guarded, owner.getByRole("link",{name:"Settings",exact:true}).last().click().catch(() => {})]);
      assert.equal(await input("secondary").inputValue(), palette.secondary);
    }
    await set({display_name:"Forage & Fern",tagline:"Coffee by the river",introduction:"Small menu for generous tables."});
    const cover = await png(owner,"#4e3259");
    await input("logo").setInputFiles(await png(owner,"#37674a"));
    await waitText(owner,"[data-upload-feedback]","Preview ready");
    await section("Cover & banner").click();
    await input("image").setInputFiles({name:"broken.png",mimeType:"image/png",buffer:Buffer.from("invalid image fixture")});
    await waitText(owner,"[data-preview-status]",/Banner image.*image/i);
    await input("image").setInputFiles(cover);
    await set({banner_alt:"Hills at dusk",banner_title:"Autumn menu",banner_description:"Small plates",focal_x:"22",focal_y:"73"});
    await input("caption").selectOption("overlay");
    await updated();
    await waitText(frame,".store-cover-caption h2","Autumn menu");
    await waitText(frame,".store-cover-caption p","Small plates");
    assert.equal(await frame.locator(".store-page-banner").getAttribute("alt"),"Hills at dusk");
    await css(frame,".store-page-banner","objectPosition","22% 73%");
    assert.match(await asset(frame,".store-logo"),/^data:image\/webp/);
    assert.match(await asset(frame,".store-page-banner"),/^data:image\/webp/);

    await input("button_style").selectOption("pill");
    await input("card_style").selectOption("outlined");
    await section("Layout").click();
    await owner.getByRole("radio",{name:/Editorial/}).check();
    await updated();
    await css(frame,".store-product","flexDirection","column");
    await css(frame,".store-product","borderTopStyle","solid");
    await css(frame,".store-product-detail summary","borderTopLeftRadius","999px");
    await owner.getByRole("radio",{name:/Compact/}).check();
    await input("card_style").selectOption("raised");
    await updated();
    await frame.locator('[data-menu-layout="compact"][data-menu-card="raised"]').waitFor();
    assert.notEqual(await frame.locator(".store-product").first().evaluate(el => getComputedStyle(el).boxShadow),"none");
    await owner.getByRole("radio",{name:/Classic/}).check();
    await input("button_style").selectOption("rounded");
    await input("card_style").selectOption("divided");
    await updated();
    await css(frame,".store-product","flexDirection","row");
    await css(frame,".store-product","borderTopStyle","none");

    await section("Menu presentation").click();
    const products = owner.getByRole("region",{name:"Products",exact:true});
    const categories = owner.getByRole("region",{name:"Categories",exact:true});
    await products.getByRole("searchbox").fill("coffee");
    await products.getByText("1 matches.").waitFor();
    await products.getByRole("searchbox").clear();
    await categories.getByRole("button",{name:"Move Single origin coffees up"}).click();
    await products.getByRole("button",{name:"Move Filter coffee up"}).click();
    await products.getByRole("checkbox",{name:"Feature Iced pandan"}).check();
    await products.getByRole("checkbox",{name:"Hide Toast"}).check();
    await categories.getByRole("checkbox",{name:"Hide Baked pastries"}).check();
    await updated();
    assert.deepEqual(await frame.locator(".store-product-heading h2").allTextContents(),["Iced pandan","Filter coffee"]);
    assert.equal(await frame.locator(".store-product-image").count(),1);
    await input("show_images").uncheck();
    await input("show_descriptions").uncheck();
    await updated();
    assert.equal(await frame.locator(".store-product-image,.store-product-description").count(),0);
    for (const name of ["show_images","show_descriptions"]) {
      const toggle = input(name);
      await toggle.press("Space");
      assert.equal(await toggle.isChecked(),true,name + " was restored");
    }

    await section("Restaurant information").click();
    await set({address:"8 Riverside Road",phone:"+62 21 555 0123",website:"https://example.invalid/menu",
      instagram:"https://example.invalid/instagram",facebook:"https://example.invalid/facebook",opening_note:"Tuesday to Sunday"});
    await updated();
    assert.equal(await frame.locator("#store-information").isVisible(),false);
    await input("show_information").check();
    await updated();
    await waitText(frame,"#store-information p","8 Riverside Road");
    await waitText(frame,"#store-information p","Tuesday to Sunday");
    for (const [name,target] of [["Website","menu"],["Instagram","instagram"],["Facebook","facebook"]]) {
      assert.equal(await frame.getByRole("link",{name,exact:true}).getAttribute("href"),"https://example.invalid/"+target);
    }
    assert.equal(await frame.getByRole("link",{name:"+62 21 555 0123",exact:true}).getAttribute("href"),"tel:+62215550123");
    await owner.getByRole("combobox",{name:"Preview location",exact:true}).selectOption({label:"Riverside"});
    await waitText(frame,".store-location","Riverside");
    await Promise.all([
      owner.waitForResponse(response => response.url().endsWith("/preview/") && response.request().method() === "POST"),
      button("Preview guest menu").click(),
    ]);
    await updated();
    for (const [name,width] of [["Desktop",1120],["Tablet",768],["Mobile",390]]) {
      await button(name).click();
      assert.equal(await frame.locator("body").evaluate(() => innerWidth),width);
    }
    await owner.getByRole("radio",{name:/Editorial/}).check();
    await input("button_style").selectOption("pill");
    await input("card_style").selectOption("outlined");
    await updated();
    await fit(owner,[...responsiveWidths,engine === "webkit" ? 390 : 1440]);
    await button("Mobile").click();
    const evidence = path.dirname(fixturePath);
    await owner.locator("[data-builder-preview]").screenshot({path:path.join(evidence,engine+"-builder-preview.png")});
    await save();
    await owner.reload();
    await waitText(owner,"[data-preview-status]","Showing the saved design");
    for (const [name,value] of Object.entries({...palette,display_name:"Forage & Fern",
      layout:"editorial",card_style:"outlined",button_style:"pill",focal_x:"22",focal_y:"73",
      address:"8 Riverside Road",banner_alt:"Hills at dusk",banner_title:"Autumn menu"})) {
      const field = name === "layout" ? owner.locator("[name=layout]:checked") : input(name);
      const actual = await field.inputValue();
      assert.equal(Object.hasOwn(palette,name) ? actual.toLowerCase() : actual,value,name);
    }
    await colors(frame);
    assert.equal(await guest.getByText("Forage & Fern",{exact:true}).count(),0);
    const defaultTheme = await guest.locator("link#store-design").getAttribute("href");
    await publish();
    const publicLink = await owner.getByRole("link",{name:/View public page/}).getAttribute("href");
    assert.match(publicLink,/^\/order\/(table|pickup)\//);
    const isolated = await strictGuest(browser);
    const fresh = await isolated.newPage();
    const freshPickup = await isolated.newPage();
    for (const [page,url] of [[fresh,publicLink],[freshPickup,fixture.pickup]]) {
      const response = await page.goto(origin+url,{waitUntil:"domcontentloaded"});
      assert.match(response.headers()["cache-control"],/no-store/);
      assert.match(response.headers()["content-security-policy"],/style-src 'self'/);
      await assertPublished(page);
      const stylesheetUrl = await page.locator("link#store-design").getAttribute("href");
      assert.match(stylesheetUrl,/\/theme\/[0-9a-f]{24}\.css$/);
      const stylesheet = await page.request.get(origin+stylesheetUrl);
      assert.match(stylesheet.headers()["cache-control"],/max-age=300/);
      assert.match(stylesheet.headers()["content-type"],/^text\/css/);
      await page.reload({waitUntil:"domcontentloaded"});
      await assertPublished(page);
      await fit(page,responsiveWidths);
    }
    for (const page of [guest,takeaway]) {
      await assertPublished(page);
      assert.notEqual(await page.locator("link#store-design").getAttribute("href"),defaultTheme);
      await page.reload();
      await assertPublished(page);
    }
    await checkNeighbor();
    const proof = {selected:palette,freshTable:await computedPalette(fresh),freshPickup:await computedPalette(freshPickup),
      responsiveWidths,neighbor:await computedPalette(neighborGuest)};
    await fresh.setViewportSize({width:1024,height:900});
    await fresh.screenshot({path:path.join(evidence,engine+"-builder-public.png")});

    const oldMedia = [await asset(guest,".store-logo"),await asset(guest,".store-page-banner")];
    await input("logo").setInputFiles(await png(owner,"#594932"));
    await section("Appearance").click();
    await input("accent").fill("#592e46");
    await section("Layout").click();
    await owner.getByRole("radio",{name:/Compact/}).check();
    await section("Menu presentation").click();
    await input("show_images").uncheck();
    await input("show_descriptions").uncheck();
    await section("Restaurant information").click();
    await input("show_information").uncheck();
    await section("Cover & banner").click();
    await input("image").setInputFiles(await png(owner,"#264b71"));
    await input("caption").selectOption("solid");
    await updated();
    await css(frame,".store-cover-caption","backgroundColor","rgba(0, 0, 0, 0)");
    await save();
    await theme(guest,"accent",palette.accent);
    await publish();
    await guest.waitForFunction(oldMedia => [".store-logo",".store-page-banner"].every(
      (selector,index) => document.querySelector(selector)?.getAttribute("src") !== oldMedia[index]), oldMedia);
    await asset(guest,".store-page-banner");
    await theme(guest,"accent","#592E46");
    await fresh.reload();
    await fresh.locator('[data-menu-layout="compact"]').waitFor();
    await theme(fresh,"accent","#592E46");
    assert.equal(await fresh.locator(".store-product-image,.store-product-description").count(),0);
    assert.equal(await fresh.locator("#store-information").isVisible(),false);
    await asset(fresh,".store-logo");
    await asset(fresh,".store-page-banner");
    await checkNeighbor();
    await input("remove_logo").check();
    await section("Cover & banner").click();
    await input("remove_image").check();
    await updated();
    await frame.locator(".store-logo").waitFor({state:"detached"});
    await frame.locator(".store-page-banner").waitFor({state:"detached"});
    await save();
    await publish();
    await guest.locator(".store-logo").waitFor({state:"detached"});
    await takeaway.locator(".store-page-banner").waitFor({state:"detached"});

    await section("Unpublish design").click();
    await button("Unpublish design").click();
    await owner.getByText(/Design unpublished/).first().waitFor();
    for (const page of [guest,takeaway]) await page.locator('[data-ordering-customized="false"]').waitFor();
    await fresh.reload();
    await fresh.locator('[data-ordering-customized="false"]').waitFor();
    await waitText(fresh,".store-brand","Builder fixture");
    assert.equal(await fresh.locator(".store-logo,.store-page-banner").count(),0);
    await section("Publication history").click();
    owner.once("dialog",dialog => dialog.accept());
    await owner.getByRole("listitem").filter({has:owner.getByText("Version 1",{exact:true})}).getByRole("button",{name:"Restore as draft"}).click();
    await owner.getByText(/Version restored to draft/).first().waitFor();
    await asset(frame,".store-logo");
    await colors(frame);
    await guest.locator(".store-logo").waitFor({state:"detached"});
    await publish();
    await asset(guest,".store-page-banner");
    await colors(takeaway);
    const restoredContext = await strictGuest(browser);
    const restored = await restoredContext.newPage();
    await restored.goto(origin+publicLink);
    await assertPublished(restored);
    proof.restored = await computedPalette(restored);
    await restored.screenshot({path:path.join(evidence,engine+"-builder-restored.png")});
    fs.writeFileSync(path.join(evidence,engine+"-builder-computed.json"),JSON.stringify(proof,null,2));
    await checkNeighbor();
    for (const page of [guest,takeaway]) {
      const coffee = page.locator(".store-product").filter({has:page.getByRole("heading",{name:"Filter coffee",exact:true})});
      await coffee.getByText("View product",{exact:true}).click();
      await coffee.getByRole("button",{name:"Add to order",exact:true}).click();
      await page.getByRole("link",{name:/Review your order · 1/}).click();
      await theme(page,"background",palette.background);
      await waitText(page,"[data-store-connection]",/Menu synchronized|Menu prices/);
      await page.getByRole("button",{name:"Place order",exact:true}).click();
      await page.waitForURL(/\/status\/$/);
      await page.getByRole("heading",{name:"Your order",exact:true}).waitFor();
    }
    assert.deepEqual(errors,[]);
    console.log(engine + " passed palette, validation, media, presentation, viewports, publication and restoration.");
  } finally { await browser.close(); }
})().catch(error => {
  console.error((error.stack || error.message).replace(/http\S+/g,"[local URL]"));
  process.exitCode = 1;
});
