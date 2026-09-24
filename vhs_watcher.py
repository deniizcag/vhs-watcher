"""
VHS Berlin - "Sprachtest zur Einbürgerung" yer takip botu.
Kurulum:
    pip install playwright requests
    playwright install chromium
Çalıştırma:
    TELEGRAM_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python vhs_watcher.py
"""
import asyncio
import json
import os
import re
from pathlib import Path

import requests
from playwright.async_api import async_playwright, Page

BASE = "https://www.vhsit.berlin.de/VHSKURSE/BusinessPages/"
STATE_FILE = Path("vhs_state.json")

SEARCH_TEXT = "Einbürgerung Sprachtest"
# Serbest metin araması hazırlık kurslarını da getirebilir; sadece sınavın kendisini takip et.
KEYWORDS = ["Sprachtest zur Einbürgerung"]

# Listedeki ">" (sonraki sayfa) butonu
NEXT_BUTTON = 'input[name="ctl00$Content$ILDataGrid1$ctl01$ctl04"]'

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")


async def do_search(page: Page):
    await page.goto(BASE + "CourseList.aspx")
    search_box = page.get_by_role("textbox", name="Bitte Suchtext eingeben.")
    # Arama kutusu bu sayfada yoksa soldaki "Kurssuche" menüsüne geç
    if not await search_box.count():
        async with page.expect_navigation():
            await page.get_by_role("link", name="Kurssuche").first.click()
    await search_box.fill(SEARCH_TEXT)
    async with page.expect_navigation():
        await page.get_by_role("button", name="Suchen").click()
    await page.wait_for_load_state("networkidle")


async def get_page_info(page: Page):
    text = await page.inner_text("body")
    m = re.search(r"Seite\s+(\d+)\s+von\s+(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (1, 1)


async def scrape_current_page(page: Page) -> dict:
    courses = {}
    links = page.locator('a[href*="CourseDetail.aspx?id="]')
    for i in range(await links.count()):
        link = links.nth(i)
        href = await link.get_attribute("href")
        cid = re.search(r"id=(\d+)", href).group(1)
        title = " ".join((await link.inner_text()).split())
        row = link.locator("xpath=ancestor::tr[1]")
        row_text = " ".join((await row.inner_text()).split()) if await row.count() else title
        courses[cid] = {
            "title": title,
            "row": row_text,
            "full": "belegt" in row_text.lower(),
            "url": BASE + href.split("BusinessPages/")[-1],
        }
    return courses


async def go_next(page: Page) -> bool:
    btn = page.locator(NEXT_BUTTON).first
    if not await btn.count() or await btn.is_disabled():
        return False
    async with page.expect_navigation():
        await btn.click()
    await page.wait_for_load_state("networkidle")
    return True


async def scrape_all() -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await do_search(page)

        all_courses = {}
        current, total = await get_page_info(page)
        print(f"{total} sayfa bulundu.")
        while True:
            all_courses.update(await scrape_current_page(page))
            if current >= total:
                break
            if not await go_next(page):
                print(f"UYARI: {current}/{total}. sayfada '>' butonuna basılamadı.")
                break
            new_current, total = await get_page_info(page)
            if new_current == current:
                break
            current = new_current

        await browser.close()
    return all_courses


def matches(c: dict) -> bool:
    return not KEYWORDS or any(k.lower() in c["title"].lower() for k in KEYWORDS)


def notify(msg: str):
    print(msg)
    if not (TG_TOKEN and TG_CHAT):
        print("UYARI: TELEGRAM_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil, Telegram'a gönderilmedi.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": msg[:4000], "disable_web_page_preview": True},
            timeout=20,
        )
        if r.ok:
            print("Telegram: gönderildi.")
        else:
            print(f"Telegram HATASI {r.status_code}: {r.text}")
    except requests.RequestException as e:
        print(f"Telegram bağlantı hatası: {e}")


def main():
    old = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    scraped = asyncio.run(scrape_all())
    new = {k: v for k, v in scraped.items() if matches(v)}
    print(f"Toplam {len(scraped)} sonuç, {len(new)} tanesi Einbürgerung sınavı.")

    alerts = []
    for cid, c in new.items():
        if cid not in old and not c["full"]:
            alerts.append(f"🆕 Yeni sınav, yer var:\n{c['row']}\n{c['url']}")
        elif cid in old and old[cid]["full"] and not c["full"]:
            alerts.append(f"✅ Yer açıldı:\n{c['row']}\n{c['url']}")

    if alerts:
        notify("\n\n".join(alerts))
    else:
        print("Değişiklik yok.")

    STATE_FILE.write_text(json.dumps(new, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
