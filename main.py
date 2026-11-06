# main.py — поиск реальных фото в Openverse и Wikimedia Commons (без ключей) + Pollinations
import os, json, logging, random, re, time
import feedparser
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9172067181:AAHRPly5-XRdX3ccc7CMSodoek7_RygjuxQ"
ADMIN_ID = 1470714545

# 👇 ПУБЛИЧНЫЙ КАНАЛ, ИЗ КОТОРОГО ДЗЕН БЕРЁТ ПОСТЫ
PUBLIC_CHANNEL_ID = "@ваш_публичный_канал"

# 👇 БОЛЬШЕ НЕ НУЖНЫ КЛЮЧИ PEXELS И HUGGING FACE!

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
]

SEEN_FILE = "seen.json"
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)
# =============================================

# ... (функции load_seen, save_seen, rewrite_with_pollinations, download_image,
# send_telegram, extract_image_from_rss, fetch_rss, scrape_telegram_channel —
# остаются без изменений, вставьте их из предыдущего рабочего кода) ...

# ---------- ПОИСК В OPENVERSE (БЕЗ КЛЮЧА) ----------
def search_openverse(query: str, prefix: str):
    """Ищет реальное фото в Openverse. Без регистрации, лимит ~1 запрос/сек."""
    try:
        # Очищаем запрос
        clean_query = re.sub(r"[^\w\s]", "", query)[:100]
        # Openverse API (анонимный доступ)
        params = {
            "q": clean_query,
            "license": "cc0,pdm,by,by-sa", # Свободные лицензии
            "page_size": 1,
        }
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params=params,
            timeout=20,
            headers={"User-Agent": "NewsBot/1.0 (contact: admin@example.com)"} # Вежливый User-Agent
        )
        r.raise_for_status()
        data = r.json()
        if data.get("results"):
            img_url = data["results"][0].get("url")
            if img_url:
                logging.info(f"Openverse: найдено фото по запросу '{clean_query}'")
                return download_image(img_url, f"{prefix}_openverse")
    except Exception as e:
        logging.warning(f"Openverse search error: {e}")
    return None


# ---------- ПОИСК В WIKIMEDIA COMMONS (БЕЗ КЛЮЧА) ----------
def search_wikimedia(query: str, prefix: str):
    """Ищет реальное фото в Wikimedia Commons. Без регистрации, без ключа."""
    try:
        clean_query = re.sub(r"[^\w\s]", "", query)[:100]
        # MediaWiki API для поиска файлов
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{clean_query} filetype:bitmap",
            "gsrnamespace": 6, # Пространство имён "Файл"
            "gsrlimit": 1,
            "prop": "imageinfo",
            "iiprop": "url",
        }
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params=params,
            timeout=20,
            headers={"User-Agent": "NewsBot/1.0 (contact: admin@example.com)"}
        )
        r.raise_for_status()
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            imageinfo = page.get("imageinfo", [])
            if imageinfo:
                img_url = imageinfo[0].get("url")
                if img_url:
                    logging.info(f"Wikimedia: найдено фото по запросу '{clean_query}'")
                    return download_image(img_url, f"{prefix}_wikimedia")
    except Exception as e:
        logging.warning(f"Wikimedia search error: {e}")
    return None


# ---------- ГЕНЕРАЦИЯ (РЕЗЕРВНЫЙ ВАРИАНТ) ----------
def generate_via_pollinations(news_context: str, prefix: str):
    """Генерация через Pollinations (если реальные фото не найдены)."""
    clean = " ".join(news_context.replace("\n", " ").split())[:400]
    prompt = (
        f"Photorealistic editorial news photograph illustrating: {clean}. "
        "Documentary style, natural lighting, realistic environment. "
        "8k resolution, highly detailed, cinematic. "
        "NO cartoon, NO illustration, NO abstract shapes, NO plastic skin, "
        "NO exaggerated anatomy, NO text, NO watermarks."
    )
    encoded = quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1280&height=720&model=flux-realism&enhance=true&nologo=true&safe=true"
    )
    try:
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
            path = os.path.join(TEMP_DIR, f"{prefix}_pollinations.jpg")
            with open(path, "wb") as f:
                f.write(r.content)
            logging.info("Изображение сгенерировано через Pollinations.")
            return path
    except Exception as e:
        logging.warning(f"Pollinations generation error: {e}")
    return None


# ---------- КАСКАДНЫЙ ВЫБОР ИЗОБРАЖЕНИЯ ----------
def get_best_image(item: dict, body: str):
    """
    Каскадный поиск картинки:
    1. Оригинал из источника (RSS/Telegram)
    2. Openverse (реальные фото, без ключа)
    3. Wikimedia Commons (реальные фото, без ключа)
    4. Pollinations (генерация)
    """
    uid_prefix = item["uid"].replace("/", "_")[:30]

    # 1. Оригинал
    if item.get("image_url"):
        img = download_image(item["image_url"], f"{uid_prefix}_orig")
        if img:
            logging.info("Использовано оригинальное фото из источника.")
            return img

    # Ключевые слова для поиска (первые 8 слов из тела)
    search_query = " ".join(body.split()[:8]) if body else item["text"][:80]

    # 2. Openverse
    logging.info(f"Ищем реальное фото в Openverse: '{search_query}'")
    img = search_openverse(search_query, uid_prefix)
    if img:
        return img
    time.sleep(1) # Лимит Openverse — 1 запрос/сек

    # 3. Wikimedia Commons
    logging.info(f"Ищем реальное фото в Wikimedia: '{search_query}'")
    img = search_wikimedia(search_query, uid_prefix)
    if img:
        return img

    # 4. Генерация (последний резерв)
    logging.info("Реальные фото не найдены, генерируем через Pollinations...")
    return generate_via_pollinations(body or item["text"], uid_prefix)


# ---------- ГЛАВНАЯ ЛОГИКА (без изменений) ----------
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

    for item in batch:
        title, body = rewrite_with_pollinations(item["text"])
        if not title:
            continue

        img = get_best_image(item, body) # 👈 Новая каскадная функция
        full_post = f"**{title}**\n\n{body}"

        send_telegram(full_post, img, chat_id=ADMIN_ID)

        if PUBLIC_CHANNEL_ID and PUBLIC_CHANNEL_ID != "@ваш_публичный_канал":
            send_telegram(full_post, img, chat_id=PUBLIC_CHANNEL_ID)
            logging.info(f"Опубликовано в канал: {title[:50]}")
        else:
            logging.warning("PUBLIC_CHANNEL_ID не настроен — пост ушёл только админу.")

        sent += 1

    save_seen(seen)
    logging.info(f"Готово. Опубликовано: {sent}")

if __name__ == "__main__":
    main()
