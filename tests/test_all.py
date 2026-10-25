"""Автотесты проекта (unittest). Запуск:  python -m unittest discover -s tests -v

Требуют подготовленной БД и моделей:  python -m scripts.setup
API проверяется через Flask-шим (те же обработчики ROUTES, что и в FastAPI-адаптере app/main.py).
"""
import unittest

import numpy as np

from ai import antifraud
from ai import intents as I
from ai.preprocess import clean, mask_personal_data, split_clauses
from ai.recommender import K_SMOOTH, N_MIN, build_profiles, score_product
from ai.sentiment import SentimentModel, analyze_review, find_aspects


class TestPreprocess(unittest.TestCase):
    def test_clean_removes_html_and_urls(self):
        self.assertEqual(clean("<b>Супер</b> http://x.kz Ёлка"), "супер елка")

    def test_clauses_split_on_contrast(self):
        cl = split_clauses("Камера отличная, но батарея садится к обеду")
        self.assertEqual(len(cl), 2)
        self.assertIn("камера", cl[0])
        self.assertIn("батарея", cl[1])

    def test_mask_personal_data(self):
        m = mask_personal_data("Мой номер +7 701 123 45 67, почта a.b@mail.kz, ИИН 123456789012")
        self.assertNotIn("701", m)
        self.assertNotIn("mail.kz", m)
        self.assertNotIn("123456789012", m)
        self.assertIn("[телефон]", m)
        self.assertIn("[email]", m)
        self.assertIn("[иин]", m)


class TestEntities(unittest.TestCase):
    PRODUCTS = [{"product_id": 1, "title": "iPhone 15 128GB", "brand": "Apple"},
                {"product_id": 2, "title": "Samsung Galaxy S24 FE", "brand": "Samsung"},
                {"product_id": 3, "title": "Xiaomi 14T Pro", "brand": "Xiaomi"},
                {"product_id": 4, "title": "Apple AirPods Pro 2", "brand": "Apple"}]

    def test_budget_formats(self):
        self.assertEqual(I.extract_budget("до 300 000 ₸"), 300000)
        self.assertEqual(I.extract_budget("бюджет 300к"), 300000)
        self.assertEqual(I.extract_budget("до 150 тысяч"), 150000)
        self.assertEqual(I.extract_budget("до 1.5 млн"), 1500000)
        self.assertIsNone(I.extract_budget("iphone 15"))  # 15 - номер модели, не цена

    def test_aspects_and_homonyms(self):
        self.assertEqual(I.extract_aspects("хорошая камера и автономность"), ["камера", "автономность"])
        self.assertNotIn("цена", I.extract_aspects("стоит ли брать iphone"))
        self.assertNotIn("производительность", find_aspects("быстро заряжается"))

    def test_product_matching(self):
        self.assertEqual(I.match_products("как батарея у iphone 15", self.PRODUCTS), [1])
        self.assertEqual(set(I.match_products("сравни iphone 15 и galaxy s24 fe", self.PRODUCTS)), {1, 2})
        self.assertEqual(I.match_products("airpods pro", self.PRODUCTS), [4])
        self.assertEqual(I.match_products("какая погода", self.PRODUCTS), [])


class TestRecommenderMath(unittest.TestCase):
    def test_smoothing_formula_1(self):
        # товар: 8 положительных из 10, средняя по категории mu = 0.5 (один товар -> mu = его доля)
        rows = [(1, "c", "камера", "positive", 1.0)] * 8 + [(1, "c", "камера", "negative", 1.0)] * 2
        rows += [(2, "c", "камера", "positive", 1.0)] * 2 + [(2, "c", "камера", "negative", 1.0)] * 8
        prof = build_profiles(rows)
        mu = 10 / 20
        self.assertAlmostEqual(prof[1]["камера"]["pos_share"], (8 + K_SMOOTH * mu) / (10 + K_SMOOTH), places=3)
        self.assertAlmostEqual(prof[1]["камера"]["pos_raw"], 0.8, places=3)

    def test_score_formula_3_reliability(self):
        prof = {"a": {"pos_share": 0.9, "n_mentions": N_MIN * 2}, "b": {"pos_share": 0.9, "n_mentions": N_MIN / 5}}
        s, _ = score_product(prof, {"a": 1, "b": 1})
        self.assertAlmostEqual(s, 0.5 * 0.9 * 1.0 + 0.5 * 0.9 * 0.2, places=4)  # у аспекта b достоверность 0.2

    def test_fake_weight_zero_for_suspicious(self):
        rows = [(1, "c", "камера", "positive", antifraud.review_weight(0.9))] * 5
        self.assertEqual(build_profiles(rows), {})


class TestAntifraud(unittest.TestCase):
    def test_duplicates_and_burst_detected(self):
        e = np.array([[1.0, 0.0]] * 8 + [[0.0, 1.0]], dtype=np.float32)
        revs = [{"product_id": 1, "rating": 5, "text": "Супер всё отлично", "date": "2026-10-01T10:00:00", "is_verified": 0} for _ in range(8)]
        revs.append({"product_id": 1, "rating": 4, "text": "Камера хорошая, батарея держит два дня уверенно", "date": "2026-09-01T10:00:00", "is_verified": 1})
        s = antifraud.score_reviews(revs, e)
        self.assertTrue(all(x >= antifraud.EXCLUDE_ABOVE for x in s[:8]))
        self.assertLess(s[8], 0.3)


class TestModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = SentimentModel()

    def test_probabilities_sum_to_one(self):
        p = self.m.predict_proba(["камера отличная", "сильно греется", "купил вчера"])
        self.assertTrue(np.allclose(p.sum(axis=1), 1.0))

    def test_negation_changes_sentiment(self):
        labs, _ = self.m.predict(["не греется вообще", "сильно греется"])
        self.assertEqual(labs, ["positive", "negative"])

    def test_absa_mixed_review(self):
        r = analyze_review(self.m, "Камера отличная, но батарея садится к обеду")
        got = {(a["aspect"], a["sentiment"]) for a in r["aspects"]}
        self.assertIn(("камера", "positive"), got)
        self.assertIn(("автономность", "negative"), got)

    # Независимая проверка на формулировках, которых НЕТ в шаблонах обучающей выборки.
    # Набор маленький и написан вручную - это проверка здравого смысла, а не научная оценка качества.
    SANITY = [("фотографии получаются просто шикарные", "positive"), ("снимки выходят размытыми и тусклыми", "negative"),
              ("работает без единого подвисания", "positive"), ("постоянно подвисает при запуске приложений", "negative"),
              ("заряда хватает на пару суток", "positive"), ("к вечеру батарея на нуле", "negative"),
              ("за такие деньги это отличная покупка", "positive"), ("слишком дорого для того что предлагает", "negative"),
              ("экран очень яркий", "positive"), ("звук плоский и тихий", "negative"),
              ("не нагревается даже под нагрузкой", "positive"), ("очень сильно нагревается", "negative"),
              ("корпус сделан на совесть", "positive"), ("задняя крышка скрипит", "negative"),
              ("курьер приехал вовремя", "positive"), ("доставку ждал две недели", "negative")]

    def test_sanity_set_beats_chance(self):
        labs, _ = self.m.predict([t for t, _ in self.SANITY])
        acc = sum(l == y for l, (_, y) in zip(labs, self.SANITY)) / len(self.SANITY)
        print(f"\n[sanity] точность на {len(self.SANITY)} вручную написанных клаузах: {acc:.2f}")
        self.assertGreater(acc, 0.5)


class TestIntents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = I.IntentClassifier()

    def test_core_intents(self):
        cases = {"подбери смартфон до 200 000 с хорошей камерой": "подбор", "сравни iphone 15 и pixel 8a": "сравнение",
                 "как быстро доставят заказ": "доставка_возврат", "привет": "приветствие"}
        for text, want in cases.items():
            self.assertEqual(self.c.predict(text)[0], want, text)

    def test_operator_rule_has_priority(self):
        self.assertEqual(self.c.predict("хочу пожаловаться, позовите оператора")[0], "поддержка")


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.dev_flask import create_app
        cls.c = create_app().test_client()

    def login(self, email, pwd):
        return {"Authorization": "Bearer " + self.c.post("/api/auth/login", json={"email": email, "password": pwd}).get_json()["token"]}

    def test_products_filters(self):
        j = self.c.get("/api/products?category=наушники&price_max=100000").get_json()
        self.assertTrue(j["items"])
        self.assertTrue(all(p["category"] == "наушники" and p["price"] <= 100000 for p in j["items"]))

    def test_bad_params(self):
        self.assertEqual(self.c.get("/api/products?price_max=abc").status_code, 400)
        self.assertEqual(self.c.get("/api/products/99999").status_code, 404)

    def test_product_detail_has_profile(self):
        j = self.c.get("/api/products/1").get_json()
        self.assertTrue(j["aspects"])
        self.assertIn("conclusion", j)

    def test_suspicious_reviews_excluded(self):
        j = self.c.get("/api/products/4").get_json()  # Redmi Note 13 Pro: накрученные отзывы
        self.assertGreaterEqual(j["n_excluded_suspicious"], 5)

    def test_compare_validation(self):
        self.assertEqual(self.c.post("/api/compare", json={"ids": [1]}).status_code, 400)
        self.assertEqual(self.c.post("/api/compare", json={"ids": [1, 2, 3, 4, 5]}).status_code, 400)
        self.assertEqual(self.c.post("/api/compare", json={"ids": [1, 2]}).status_code, 200)

    def test_auth_and_roles(self):
        self.assertEqual(self.c.get("/api/seller/analytics").status_code, 401)
        self.assertEqual(self.c.get("/api/seller/analytics", headers=self.login("buyer@demo.kz", "buyer123")).status_code, 403)
        self.assertEqual(self.c.get("/api/seller/analytics", headers=self.login("seller@demo.kz", "seller123")).status_code, 200)
        self.assertEqual(self.c.post("/api/auth/login", json={"email": "buyer@demo.kz", "password": "wrong"}).status_code, 401)

    def test_seller_alert_for_heating(self):
        a = self.c.get("/api/seller/analytics", headers=self.login("seller@demo.kz", "seller123")).get_json()
        self.assertTrue(any(x["aspect"] == "нагрев" and x["title"] == "Xiaomi 14T Pro" for x in a["alerts"]))

    def test_chat_validation_and_flow(self):
        self.assertEqual(self.c.post("/api/chat", json={"message": "   "}).status_code, 400)
        self.assertEqual(self.c.post("/api/chat", json={"message": "а" * 1001}).status_code, 400)
        j = self.c.post("/api/chat", json={"message": "подбери смартфон до 300 000 ₸ с хорошей камерой"}).get_json()
        self.assertEqual(j["intent"], "подбор")
        self.assertTrue(j["products"])
        self.assertTrue(all(p["price"] <= 300000 for p in j["products"]))
        j2 = self.c.post("/api/chat", json={"message": "а что с экраном", "dialog_id": j["dialog_id"]}).get_json()
        self.assertTrue(j2["sources"], "ответ должен содержать ссылки на отзывы")
        self.assertIn("отзыв #", j2["reply"])

    def test_refusal_when_no_data(self):
        j = self.c.post("/api/chat", json={"message": "есть ли nfc у iphone 15 128gb"}).get_json()
        self.assertFalse(j.get("grounded", True))
        self.assertEqual(j["sources"], [])

    def test_ambiguous_model_asks_to_clarify(self):
        j = self.c.post("/api/chat", json={"message": "есть ли nfc у iphone 15"}).get_json()
        self.assertTrue(j.get("clarify"))
        self.assertIn("Уточните", j["reply"])

    def test_catalog_has_30_products(self):
        self.assertEqual(len(self.c.get("/api/products").get_json()["items"]), 30)

    def test_seller_products_and_reviews(self):
        h = self.login("seller@demo.kz", "seller123")
        self.assertEqual(self.c.get("/api/seller/products").status_code, 401)
        self.assertEqual(self.c.get("/api/seller/reviews", headers=self.login("buyer@demo.kz", "buyer123")).status_code, 403)
        ps = self.c.get("/api/seller/products", headers=h).get_json()["products"]
        self.assertEqual(len(ps), 30)
        self.assertTrue(any(p["alerts"] for p in ps))
        neg = self.c.get("/api/seller/reviews?sentiment=negative&limit=20", headers=h).get_json()
        self.assertTrue(neg["reviews"] and all(r["overall"] == "negative" for r in neg["reviews"]))
        sus = self.c.get("/api/seller/reviews?suspicious=1", headers=h).get_json()
        self.assertTrue(sus["total"] > 0 and all(r["suspicious"] for r in sus["reviews"]))
        asp = self.c.get("/api/seller/reviews?aspect=нагрев&aspect_sentiment=negative", headers=h).get_json()
        self.assertTrue(asp["reviews"] and all(any(a["aspect"] == "нагрев" for a in r["aspects"]) for r in asp["reviews"]))

    def test_analyze_text(self):
        j = self.c.post("/api/analyze", json={"text": "Камера отличная, но сильно греется"}).get_json()
        got = {a["aspect"]: a["sentiment"] for a in j["aspects"]}
        self.assertEqual(got.get("камера"), "positive"); self.assertEqual(got.get("нагрев"), "negative")
        self.assertEqual(self.c.post("/api/analyze", json={"text": "ок"}).status_code, 400)

    def test_seller_sensitivity(self):
        h = self.login("seller@demo.kz", "seller123")
        hi = self.c.get("/api/seller/analytics?sensitivity=high", headers=h).get_json()["alerts"]
        lo = self.c.get("/api/seller/analytics?sensitivity=low", headers=h).get_json()["alerts"]
        self.assertGreaterEqual(len(hi), len(lo))

    def test_gibberish_asks_clarification(self):
        j = self.c.post("/api/chat", json={"message": "фывапр ячсмит"}).get_json()
        self.assertTrue(j.get("clarify"))

    def test_operator_escalation_logged_masked(self):
        h = self.login("buyer@demo.kz", "buyer123")
        j = self.c.post("/api/chat", json={"message": "соедините с оператором, мой телефон +7 701 123 45 67"}, headers=h).get_json()
        self.assertTrue(j["escalate"])
        msgs = self.c.get(f"/api/chat/dialogs/{j['dialog_id']}", headers=h).get_json()["items"]
        self.assertNotIn("701", msgs[0]["text"])

    def test_review_flow_updates_profile(self):
        h = self.login("buyer@demo.kz", "buyer123")
        bad = self.c.post("/api/reviews", json={"product_id": 1, "rating": 9, "text": "нормально"}, headers=h)
        self.assertEqual(bad.status_code, 400)
        before = self.c.get("/api/products/1/reviews").get_json()["items"]
        r = self.c.post("/api/reviews", json={"product_id": 1, "rating": 5, "text": "Камера просто великолепная, батарея держит два дня", "wait": True}, headers=h)
        self.assertEqual(r.status_code, 200)
        rid = r.get_json()["review_id"]
        after = self.c.get("/api/products/1/reviews").get_json()["items"]
        mine = next(x for x in after if x["review_id"] == rid)
        self.assertTrue(mine["aspects"])
        self.assertEqual(len(after), min(20, len(before) + 1))


class TestRules(unittest.TestCase):
    def test_complaints_and_negation(self):
        from ai.rules import rule_sentiment as r
        self.assertEqual(r("после часа игры очень горячий"), "negative")
        self.assertEqual(r("даже в играх не нагревается"), "positive")
        self.assertEqual(r("не греется вообще"), "positive")
        self.assertEqual(r("батарея не держит заряд"), "negative")
        self.assertIsNone(r("камера отличная"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
