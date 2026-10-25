"""RAG-генератор ответа - метод ИИ №6.

Схема (СРО1 п. 1.3, СРО2 п. 2.4): запрос -> эмбеддинг -> поиск релевантных отзывов ->
ответ строится ТОЛЬКО на найденном контексте, с обязательными ссылками на отзывы.
Если контекста недостаточно - явный отказ (принцип «безопасность по умолчанию»).

Генератор здесь шаблонный (extractive grounded generation): он собирает ответ из цитат отзывов и
чисел профиля товара, поэтому не может «придумать» отсутствующую характеристику. Класс
GroundedGenerator имеет единый метод compose(); вместо него можно подключить LLM, которой
передаются те же `context` и `facts`, не меняя остальные модули.
"""
import numpy as np

SIM_MIN = 0.12        # минимальная косинусная близость отзыва к вопросу
MIN_MENTIONS = 3      # минимум упоминаний аспекта для содержательного ответа
TOP_K = 5


def retrieve(query_vec, review_matrix, review_meta, k=TOP_K, sim_min=SIM_MIN):
    """Возвращает k отзывов с косинусной близостью >= sim_min (формула (2); векторы нормированы)."""
    if len(review_meta) == 0:
        return []
    sims = review_matrix @ query_vec
    order = np.argsort(-sims)[:k]
    return [{**review_meta[i], "sim": float(sims[i])} for i in order if sims[i] >= sim_min]


def _pct(x):
    return f"{round(x * 100)}%"


class GroundedGenerator:
    def compose(self, title, question_aspects, profile, quotes, price=None, want_price=False):
        """quotes: {'positive': [{review_id, clause}], 'negative': [...]} - цитаты из отзывов.
        Возвращает (text, sources[list of review_id], grounded: bool)."""
        parts, sources = [], []
        if want_price and price is not None:
            parts.append(f"Цена {title}: {price:,} ₸ (данные каталога).".replace(",", " "))
        aspects = question_aspects or [a for a, _ in sorted(profile.items(), key=lambda kv: -kv[1]["n_mentions"])[:3]]
        aspects = [a for a in aspects if a in profile and profile[a]["n_mentions"] >= MIN_MENTIONS]
        if not aspects:
            if parts:  # ответили на фактический вопрос из БД
                return " ".join(parts), sources, True
            return (f"По {title} в отзывах недостаточно данных, чтобы ответить на этот вопрос. "
                    "Могу показать общие характеристики из карточки или передать вас оператору."), sources, False
        for a in aspects:
            info = profile[a]
            seg = f"{a.capitalize()}: {_pct(info['pos_raw'])} положительных упоминаний (учтено {round(info['n_mentions'])})."
            q_pos = [q for q in quotes.get("positive", []) if q.get("aspect") == a][:1]
            q_neg = [q for q in quotes.get("negative", []) if q.get("aspect") == a][:1]
            for q in q_pos:
                seg += f" Хвалят: «{q['clause']}» [отзыв #{q['review_id']}]."
                sources.append(q["review_id"])
            for q in q_neg:
                seg += f" Критикуют: «{q['clause']}» [отзыв #{q['review_id']}]."
                sources.append(q["review_id"])
            parts.append(seg)
        return f"{title}. " + " ".join(parts), sorted(set(sources)), True
