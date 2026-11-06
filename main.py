# main.py — с фильтром рекламы и автопубликацией в канал для Дзена
import os, json, logging, random, re
import feedparser
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9172067181:AAHRPly5-XRdX3ccc7CMSodoek7_RygjuxQ"
ADMIN_ID = 1470714545

# 👇 ПУБЛИЧНЫЙ КАНАЛ, ИЗ КОТОРОГО ДЗЕН БЕРЁТ ПОСТЫ
# Замените на ваш канал. Бот должен быть админом этого канала!
PUBLIC_CHANNEL_ID = "@ваш_публичный_канал"

# 👇 КЛЮЧ PEXELS (получить бесплатно на https://www.pexels.com/api/)
PEXELS_API_KEY = "ВАШ_КЛЮЧ_PEXELS_СЮДА"

# 👇 СКОЛЬКО СТАТЕЙ ЗА ОДИН ЗАПУСК
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


# ---------- ФИЛЬТР РЕКЛАМЫ ----------
# Жёсткие маркеры — сразу реклама, без исключений
AD_HARD = [
    "промокод", "#реклама", "#промо", "#партнер", "#партнёр",
    "по вопросам рекламы", "реклама в канале",
    "казино", "букмекер", "1xbet", "ставки на спорт",
    "бинарные опционы", "заработок в интернете",
    "пассивный доход", "финансовая пирамида",
    "розыгрыш", "выиграй приз",
]

# Мягкие маркеры — считаем: если их 2+ в тексте, то это реклама
AD_SOFT = [
    "подпишись", "подписывайся", "переходи по ссылке", "жми на ссылку",
    "успей", "только сегодня", "ограниченное предложение",
    "промо", "акция", "скидка",
]


def is_ad(text: str) -> tuple[bool, str]:
    """Проверяет, является ли текст рекламой. Возвращает (это_реклама, причина)."""
    if not text:
        return False, ""
    low = text.lower()

    # 1. Жёсткие маркеры
    for kw in AD_HARD:
        if kw in low:
            return True, f"слово '{kw}'"

    # 2. Хэштеги рекламы
    if re.search(r"#реклам|#промо|#партн", low):
        return True, "хэштег рекламы"

    # 3. Слишком много ссылок
    links = re.findall(r"https?://\S+|t\.me/\S+", low)
    if len(links) >= 3:
        return True, f"{len(links)} ссылок"

    # 4. Комбинация мягких маркеров
    soft_hits = [kw for kw in AD_SOFT if kw in low]
    if len(soft_hits) >= 2:
        return True, f"рекламные слова: {', '.join(soft_hits[:3])}"

    # 5. Короткий текст со ссылкой (типичный формат рекламной вставки)
    if len(text) < 200 and links:
        return True, "короткий текст со ссылкой"

    return False, ""


# ---------- БАЗА ----------
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-3000:], f, ensure_ascii=False, indent=2)


# ---------- ПЕРЕПИСЫВАНИЕ ----------
def rewrite_with_pollinations(text: str):
    if not text or len(text) < 40:
        return None, None
    prompt = (
        "Ты — редактор новостного канала. Перепиши новость своими словами.\n"
        "- Первое предложение — цепляющий заголовок до 120 символов.\n"
        "- Затем 3–5 коротких абзацев, живой язык, без канцелярита.\n"
        "- Добавь короткий аналитический комментарий: почему это важно.\n"
        "- Не выдумывай факты.\n\n"
        f"Новость: {text[:3000]}"
    )
    try:
        r = requests.post(
            "https://text.pollinations.ai/openai",
            json={
                "model": "openai",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
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


# ---------- КАРТИНКИ ----------
def download_image(url, prefix):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
            ext = r.headers["Content-Type"].split("/")[-1].split(";")[0]
            if ext == "jpeg":
                ext = "jpg"
            path = os.path.join(TEMP_DIR, f"{prefix}.{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except Exception as e:
        logging.warning(f"Download image: {e}")
    return None


def search_stock_photo(query: str, prefix: str):
    if not PEXELS_API_KEY or PEXELS_API_KEY == "ВАШ_КЛЮЧ_PEXELS_СЮДА":
        return None
    try:
        headers = {"Authorization": PEXELS_API_KEY}
        clean_query = re.sub(r"[^\w\s]", "", query)[:100]
        params = {"query": clean_query, "per_page": 1, "orientation": "landscape"}
        r = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("photos"):
            photo_url = data["photos"][0]["src"]["large2x"]
            return download_image(photo_url, f"{prefix}_stock")
    except Exception as e:
        logging.warning(f"Pexels search error: {e}")
    return None


def generate_image(news_context: str, prefix: str):
    clean = " ".join(news_context.replace("\n", " ").split())[:400]
    prompt = (
        f"Photorealistic editorial news photograph illustrating: {clean}. "
        "Documentary style, natural lighting, realistic environment. "
        "8k resolution, highly detailed, cinematic. "
        "NO cartoon, NO illustration, NO abstract shapes, NO plastic skin, "
        "NO exaggerated anatomy, NO text, NO watermarks."
    )
    encoded = quote(prompt)
    try:
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1280&height=720&model=flux-realism&enhance=true&nologo=true&safe=true"
        )
        r = requests.get(url, timeout=120)
        if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
            path = os.path.join(TEMP_DIR, f"{prefix}_gen.jpg")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except Exception as e:
        logging.warning(f"Generate image error: {e}")
    return None


def get_best_image(item: dict, body: str):
    uid_prefix = item["uid"].replace("/", "_")[:30]

    # 1. Оригинал из источника
    if item.get("image_url"):
        img = download_image(item["image_url"], f"{uid_prefix}_orig")
        if img:
            return img

    # 2. Стоковое фото с Pexels
    search_query = " ".join(body.split()[:5]) if body else item["text"][:50]
    img = search_stock_photo(search_query, uid_prefix)
    if img:
        return img

    # 3. Генерация
    return generate_image(body or item["text"], uid_prefix)


# ---------- ОТПРАВКА ----------
def send_telegram(text, image_path=None, chat_id=None, mark_ad=False):
    """Отправляет сообщение (по умолчанию — админу)."""
    target = chat_id if chat_id else ADMIN_ID
    prefix = "🚫 *РЕКЛАМА (не публикую)*\n\n" if mark_ad else ""
    full_text = prefix + text
    try:
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as photo:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": target, "caption": full_text[:1024], "parse_mode": "Markdown"},
                    files={"photo": photo},
                    timeout=30,
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": target, "text": full_text[:4000], "parse_mode": "Markdown"},
                timeout=30,
            )
    except Exception as e:
        logging.error(f"Telegram send: {e}")


# ---------- СБОР RSS ----------
def extract_image_from_rss(entry):
    if hasattr(entry, "media_content"):
        for m in entry.media_content:
            if "image" in m.get("type", "") or m.get("medium") == "image":
                return m.get("url")
    if hasattr(entry, "media_thumbnail"):
        return entry.media_thumbnail[0].get("url")
    if hasattr(entry, "enclosures"):
        for enc in entry.enclosures:
            if "image" in enc.get("type", ""):
                return enc.get("href")
    return None


def fetch_rss(feed_url, seen, posts_batch):
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:3]:
            uid = entry.get("id") or entry.get("link")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            posts_batch.append({
                "uid": uid,
                "text": f"{entry.title}. {entry.get('summary', '')}",
                "image_url": extract_image_from_rss(entry),
            })
    except Exception as e:
        logging.error(f"RSS {feed_url}: {e}")


# ---------- СБОР TELEGRAM ----------
def scrape_telegram_channel(channel, limit=3):
    url = f"https://t.me/s/{channel}"
    posts = []
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return posts
        soup = BeautifulSoup(r.text, "html.parser")
        messages = soup.find_all("div", class_="tgme_widget_message")
        for msg in messages[-limit:]:
            text_div = msg.find("div", class_="tgme_widget_message_text")
            text = text_div.get_text(separator="\n", strip=True) if text_div else ""
            if len(text) < 80:
                continue

            # Помечаем посты с рекламными метками Telegram (is_ad в самом канале)
            # Это дополнительный фильтр — если пост помечен в самом Telegram как реклама
            if msg.find("div", class_="tgme_widget_message_ad"):
                continue

            photos = []
            photo_wrap = msg.find("a", class_="tgme_widget_message_photo_wrap")
            if photo_wrap and photo_wrap.get("style"):
                m = re.search(r"url\(['\"]?(.*?)['\"]?\)", photo_wrap["style"])
                if m:
                    photos.append(m.group(1))
            date_link = msg.find("a", class_="tgme_widget_message_date")
            link = date_link.get("href") if date_link else None
            post_id = msg.get("data-post", "")
            posts.append({
                "uid": link or f"{channel}_{post_id}",
                "text": text,
                "image_url": photos[0] if photos else None,
            })
    except Exception as e:
        logging.error(f"TG {channel}: {e}")
    return posts


# ---------- ГЛАВНАЯ ЛОГИКА ----------
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
        # 🚫 ФИЛЬТР РЕКЛАМЫ — до переписывания, чтобы не тратить токены
        is_ad_post, reason = is_ad(item["text"])
        if is_ad_post:
            logging.info(f"РЕКЛАМА отброшена ({reason}): {item['text'][:60]}...")
            # Отправляем админу для информации, но НЕ публикуем в канал
            send_telegram(
                f"Источник: {item['uid']}\n\n{item['text'][:800]}\n\n_Причина: {reason}_",
                chat_id=ADMIN_ID,
                mark_ad=True,
            )
            ads_skipped += 1
            continue

        title, body = rewrite_with_pollinations(item["text"])
        if not title:
            continue

        # Проверяем переписанный текст тоже (на случай если нейросеть вставила рекламу)
        is_ad_rewritten, reason2 = is_ad(title + " " + body)
        if is_ad_rewritten:
            logging.warning(f"Реклама после рерайта: {reason2}")
            continue

        img = get_best_image(item, body)
        full_post = f"**{title}**\n\n{body}"

        # 1. Отправляем АДМИНУ для просмотра
        send_telegram(full_post, img, chat_id=ADMIN_ID)

        # 2. Публикуем В ПУБЛИЧНЫЙ КАНАЛ → оттуда Дзен заберёт автоматически
        if PUBLIC_CHANNEL_ID and PUBLIC_CHANNEL_ID != "@ваш_публичный_канал":
            send_telegram(full_post, img, chat_id=PUBLIC_CHANNEL_ID)
            logging.info(f"Опубликовано в канал: {title[:50]}")
        else:
            logging.warning("PUBLIC_CHANNEL_ID не настроен — пост ушёл только админу.")

        sent += 1

    save_seen(seen)
    logging.info(f"Готово. Опубликовано: {sent}. Рекламы отфильтровано: {ads_skipped}")


if __name__ == "__main__":
    main()
