# main.py — с усиленным фильтром рекламы, переводом и отправкой в канал
import os, json, logging, random, re
import feedparser
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from deep_translator import GoogleTranslator # 👈 Добавлен импорт для перевода

logging.basicConfig(level=logging.INFO)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9172067181:AAHRPly5-XRdX3ccc7CMSodoek7_RygjuxQ"
ADMIN_ID = 1470714545
PUBLIC_CHANNEL_ID = "@ваш_публичный_канал" # 👈 Убедитесь, что бот админ в канале!

MAX_POSTS_PER_RUN = 10

RSS_FEEDS = [
    "https://ria.ru/export/rss2/index.xml",
    "https://tass.ru/rss/v2.xml",
    "https://lenta.ru/rss/news",
    "https://www.kommersant.ru/RSS/news.xml",
    "http://www.cbr.ru/rss/RssNews",
    "http://www.cbr.ru/rss/RssPress",
    "https://feeds.feedburner.com/OilPriceNews",
]

TELEGRAM_CHANNELS = [
    "mash", "readovkanews", "rian_ru", "bazabazon",
    "ostorozhno_novosti", "shot_shot", "rt_russian", "rbc_news",
    "tass_agency", "kommersant", "yurasumy", "medvedev_telegram",
    "vv_volodin", "novosti_efir", "moscowmap", "toporlive",
    "ecotopor", "davydovin", "skurlatovlive",
    "truexanewsua", "nikolaevskiy_vanek", "timesofukraine",
    "insiderUKR", "lachen_pyshe", "voynareal", "ukraine_sich",
    "realny_kyiv", "unian_ua", "tsn_ua",
    "centralbank_russia", "alfabank", "bankrollo", "markettwits",
    "igotosochi", "Kot.Finance", "birzhevik_official",
    "birzhevikstocksofficial1", "oilgasofficial1", "papagaz",
    "neftegazchannel", "oil_capital", "lngchannel", "OilGasGame",
    "woth_the_world", # 👈 Добавлен англоязычный канал (будет переведен)
]

SEEN_FILE = "seen.json"
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)
# =============================================

# ... (функции load_seen, save_seen, download_image, search_stock_photo,
# generate_image, get_best_image, send_telegram, extract_image_from_rss,
# fetch_rss — остаются без изменений) ...

# ---------- УСИЛЕННЫЙ ФИЛЬТР РЕКЛАМЫ ----------
AD_HARD = [
    "промокод", "#реклама", "#advertisement", "erid:", "реклама в канале",
    "казино", "букмекер", "1xbet", "ставки на спорт",
    "бинарные опционы", "заработок в интернете", "пассивный доход",
    "финансовая пирамида", "розыгрыш", "выиграй приз",
    "биржевик", "биржевик | акции", "oilgasofficial", # 👈 Явные названия рекламных каналов
]

AD_SOFT = [
    "подпишись", "подписывайся", "переходи по ссылке", "жми на ссылку",
    "успей", "только сегодня", "ограниченное предложение",
    "промо", "акция", "скидка",
]

def is_ad(text: str) -> tuple[bool, str]:
    if not text:
        return False, ""
    low = text.lower()

    for kw in AD_HARD:
        if kw in low:
            return True, f"слово '{kw}'"

    if re.search(r"#реклам|#advertis|erid:", low):
        return True, "рекламный хэштег/маркер"

    links = re.findall(r"https?://\S+|t\.me/\S+", low)
    if len(links) >= 3:
        return True, f"{len(links)} ссылок"

    soft_hits = [kw for kw in AD_SOFT if kw in low]
    if len(soft_hits) >= 2:
        return True, f"рекламные слова: {', '.join(soft_hits[:3])}"

    if len(text) < 200 and links:
        return True, "короткий текст со ссылкой"

    return False, ""


# ---------- ПЕРЕВОД ТЕКСТА ----------
def translate_to_russian(text: str) -> str:
    """Переводит текст на русский, если он на другом языке."""
    if not text or len(text.strip()) < 20:
        return text
    try:
        # Определяем язык и переводим, если это не русский
        # GoogleTranslator сам определит исходный язык
        translated = GoogleTranslator(source='auto', target='ru').translate(text)
        logging.info("Текст переведен на русский.")
        return translated
    except Exception as e:
        logging.warning(f"Ошибка перевода: {e}. Оставляем исходный текст.")
        return text


# ---------- ПЕРЕПИСЫВАНИЕ (С ПЕРЕВОДОМ) ----------
def rewrite_with_pollinations(text: str):
    if not text or len(text) < 40:
        return None, None

    # 👇 ПЕРЕВОДИМ ТЕКСТ ПЕРЕД ОТПРАВКОЙ В НЕЙРОСЕТЬ
    text_ru = translate_to_russian(text)

    prompt = (
        "Ты — редактор новостного канала. Перепиши новость своими словами.\n"
        "- Первое предложение — цепляющий заголовок до 120 символов.\n"
        "- Затем 3–5 коротких абзацев, живой язык, без канцелярита.\n"
        "- Добавь короткий аналитический комментарий: почему это важно.\n"
        "- Не выдумывай факты.\n\n"
        f"Новость: {text_ru[:3000]}"
    )
    try:
        r = requests.post(
            "https://text.pollinations.ai/openai",
            json={"model": "openai", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
            timeout=120,
        )
        r.raise_for_status()
        full_text = r.json()["choices"][0]["message"]["content"].strip()
        lines = full_text.split("\n", 1)
        title = lines[0].strip().strip("*# ")[:120]
        body = lines[1].strip() if len(lines) > 1 else full_text
        return title, body
    except Exception as e:
        logging.error(f"Pollinations: {e}")
        return None, None


# ... (остальные функции и main() остаются без изменений, кроме вызова send_telegram в канал) ...

def main():
    seen = load_seen()
    candidates = []

    for feed_url in RSS_FEEDS:
        fetch_rss(feed_url, seen, candidates)

    channels = TELEGRAM_CHANNELS.copy()
    random.shuffle(channels)
    for channel in channels[:15]:
        candidates.extend(scrape_telegram_channel(channel, limit=2))

    logging.info(f"Кандидатов собрано: {len(candidates)}")

    random.shuffle(candidates)
    batch = candidates[:MAX_POSTS_PER_RUN]

    sent = 0
    ads_skipped = 0

    for item in batch:
        is_ad_post, reason = is_ad(item["text"])
        if is_ad_post:
            logging.info(f"РЕКЛАМА отброшена ({reason}): {item['text'][:60]}...")
            send_telegram(
                f"Источник: {item['uid']}\n\n{item['text'][:800]}\n\n_Причина: {reason}_",
                chat_id=ADMIN_ID, mark_ad=True,
            )
            ads_skipped += 1
            continue

        title, body = rewrite_with_pollinations(item["text"]) # Перевод происходит внутри
        if not title:
            continue

        is_ad_rewritten, reason2 = is_ad(title + " " + body)
        if is_ad_rewritten:
            logging.warning(f"Реклама после рерайта: {reason2}")
            continue

        img = get_best_image(item, body)
        full_post = f"**{title}**\n\n{body}"

        # Отправка админу
        send_telegram(full_post, img, chat_id=ADMIN_ID)

        # Отправка в публичный канал (для Дзена)
        if PUBLIC_CHANNEL_ID and PUBLIC_CHANNEL_ID != "@ваш_публичный_канал":
            success = send_telegram(full_post, img, chat_id=PUBLIC_CHANNEL_ID)
            if success:
                logging.info(f"Опубликовано в канал: {title[:50]}")
            else:
                logging.error(f"НЕ УДАЛОСЬ опубликовать в канал. Проверьте права бота!")
        else:
            logging.warning("PUBLIC_CHANNEL_ID не настроен — пост ушёл только админу.")

        sent += 1

    save_seen(seen)
    logging.info(f"Готово. Опубликовано: {sent}. Рекламы отфильтровано: {ads_skipped}")

if __name__ == "__main__":
    main()
