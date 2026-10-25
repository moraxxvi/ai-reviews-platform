"""Проверка интерфейса в headless-Chromium (Playwright): основные экраны из СРО2 + чат + кабинет продавца.
Запуск при работающем сервере:  python -m tests.ui_check http://127.0.0.1:8765 /путь/к/папке/для/скриншотов
"""
import sys
from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
out = sys.argv[2] if len(sys.argv) > 2 else "."
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 860})
    pg.on("console", lambda m: errors.append(("console", m.text)) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(("pageerror", str(e))))

    def shot(name):
        pg.wait_for_timeout(500)
        pg.screenshot(path=f"{out}/{name}.png")

    pg.goto(base); pg.wait_for_selector(".hero"); pg.fill("#try-text", "Камера отличная, но сильно греется и батарея садится к обеду"); pg.get_by_text("Разобрать отзыв").click(); pg.wait_for_selector(".try-out .tag"); assert pg.locator(".try-out .tag").count() >= 3; shot("01_home")
    pg.goto(base + "/#/catalog"); pg.wait_for_selector(".card"); shot("02_catalog")
    n_cards = pg.locator(".card").count()
    pg.fill("#q", "батарея держит долго"); pg.press("#q", "Enter"); pg.wait_for_selector(".card"); shot("03_search")
    pg.goto(base + "/#/catalog?category=смартфоны"); pg.wait_for_selector(".card")
    pg.locator(".card").nth(0).get_by_text("Сравнить").click(); pg.locator(".card").nth(1).get_by_text("Сравнить").click()
    pg.goto(base + "/#/product/3"); pg.wait_for_selector(".panel.ai"); shot("04_product")
    pg.goto(base + "/#/compare"); pg.wait_for_selector("table.cmp"); shot("05_compare")
    # чат
    pg.click("#chat-fab"); pg.fill("#chat-input", "Мне нужен смартфон до 300 000 ₸ с хорошей камерой и автономностью"); pg.press("#chat-input", "Enter")
    pg.wait_for_selector(".pcard"); pg.fill("#chat-input", "а как у него с нагревом"); pg.press("#chat-input", "Enter")
    pg.wait_for_selector(".src"); shot("06_chat")
    # продавец
    pg.click("#chat-close")
    pg.click("#auth-btn"); pg.fill("#auth-email", "seller@demo.kz"); pg.fill("#auth-pass", "seller123"); pg.click("#auth-submit")
    pg.goto(base + "/#/seller"); pg.wait_for_selector(".kpis"); shot("07_seller")
    alert = pg.locator(".alert").count()
    # разделы кабинета: каждая кнопка должна работать
    pg.get_by_text("Все аспекты").click(); assert pg.locator(".bar-row").count() > 5, "Все аспекты не раскрылись"
    pg.locator(".alert a").first.click(); pg.wait_for_selector(".rv"); assert pg.locator(".rv").count() > 0; shot("07b_seller_reviews")
    pg.locator(".side a", has_text="Товары").click(); pg.wait_for_selector("table.cmp tbody tr"); n_rows = pg.locator("table.cmp tbody tr").count(); shot("07c_seller_products")
    pg.locator("table.cmp tbody tr").nth(2).get_by_text("Аналитика", exact=True).click(); pg.wait_for_selector(".kpis"); shot("07d_seller_analytics")
    pg.locator(".side a", has_text="Отзывы").click(); pg.wait_for_selector(".rv")
    pg.select_option(".toolbar select >> nth=1", "negative"); pg.wait_for_timeout(500); assert pg.locator(".rv .tag.good", has_text="положительный").count() == 0
    pg.locator(".side a", has_text="Настройки").click(); pg.select_option("#set-sens", "high"); pg.get_by_text("Сохранить").click()
    pg.locator(".side a", has_text="AI-аналитика").click(); pg.wait_for_selector(".kpis"); shot("07e_seller_analytics_all")
    # мобильная ширина
    pg.set_viewport_size({"width": 390, "height": 800}); pg.goto(base + "/#/catalog"); pg.wait_for_selector(".card"); shot("08_mobile")
    overflow = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    b.close()

print("карточек в каталоге:", n_cards, "| строк в таблице товаров:", n_rows, "| предупреждений у продавца:", alert, "| горизонтальный скролл на 390px:", overflow)
print("ошибки консоли/JS:", errors or "нет")
