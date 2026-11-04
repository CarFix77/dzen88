# main.py — полная версия со всеми функциями
import os, json, logging, random, re
import feedparser
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9172067181:AAHRPly5-XRdX3ccc7CMSodoek7_RygjuxQ"
ADMIN_ID = 1470714545

MAX_POSTS_PER_RUN = 8   # сколько максимум постов присылать за один запуск

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
    # Россия
    "mash", "readovkanews", "rian_ru", "bazabazon",
    "ostorozhno_novosti", "shot_shot", "rt_russian", "rbc_news",
    "tass_agency", "kommersant", "yurasumy", "medvedev_telegram",
    "vv_volodin", "novosti_efir", "moscowmap", "toporlive",
    "ecotopor", "davydovin", "skurlatovlive",
    # Украина
    "truexanewsua", "nikolaevskiy_vanek", "timesofukraine",
    "insiderUKR", "lachen_pyshe", "voynareal", "ukraine_sich",
    "realny_kyiv", "unian_ua", "tsn_ua",
    # Финансы, нефть и газ
    "centralbank_russia", "alfabank", "bankrollo", "markettwits",
    "igotosochi", "Kot.Finance", "birzhevik_official",
    "birzhevikstocksofficial1", "oilgasofficial1", "papagaz",
    "neftegazchannel", "oil_capital", "lngchannel", "OilGasGame",
]

SEEN_FILE = "seen.json"
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)
# =============================================


# ---------- БАЗА «УЖЕ ОТПРАВЛЕНО» ----------
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


# ---------- ПЕРЕПИСЫВАНИЕ ТЕКСТА ----------
def rewrite_with_pollinations(text: str):
    if not text or len(text) < 40:
        return None, None

    prompt = (
        "Ты — редактор новостного канала. Перепиши новость своими словами.\n"
        "- Первое предложение — цепляющий заголовок до 120 символов.\n"
        "- Затем 3–5 коротких абзацев, живой язык, без канцелярита.\n"
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


# ---------- РАБОТА С КАРТИНКАМИ ----------
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


def generate_image(prompt, prefix):
    encoded = quote(prompt + ", photorealistic, news illustration")
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=675&nologo=true&model=flux"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            path = os.path.join(TEMP_DIR, f"{prefix}.jpg")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except Exception as e:
        logging.warning(f"Generate image: {e}")
    return None


# ---------- ОТПРАВКА В TELEGRAM ----------
def send_telegram(text, image_path=None):
    try:
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as photo:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": ADMIN_ID, "caption": text[:1024]},
                    files={"photo": photo},
                    timeout=30,
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": ADMIN_ID, "text": text[:4000], "parse_mode": "Markdown"},
                timeout=30,
            )
    except Exception as e:
        logging.error(f"Telegram send: {e}")


# ---------- ЧТЕНИЕ RSS ----------
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


# ---------- ЧТЕНИЕ TELEGRAM ЧЕРЕЗ t.me/s/ ----------
def scrape_telegram_channel(channel, limit=3):
    url = f"https://t.me/s/{channel}"
    posts = []
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            logging.warning(f"TG {channel}: HTTP {r.status_code}")
            return posts

        soup = BeautifulSoup(r.text, "html.parser")
        messages = soup.find_all("div", class_="tgme_widget_message")

        for msg in messages[-limit:]:
            text_div = msg.find("div", class_="tgme_widget_message_text")
            text = text_div.get_text(separator="\n", strip=True) if text_div else ""
            if len(text) < 80:
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

    # 1. RSS
    for feed_url in RSS_FEEDS:
        fetch_rss(feed_url, seen, candidates)

    # 2. Telegram (перемешиваем, чтобы за неделю прошли все)
    channels = TELEGRAM_CHANNELS.copy()
    random.shuffle(channels)
    for channel in channels:
        candidates.extend(scrape_telegram_channel(channel, limit=2))

    logging.info(f"Кандидатов собрано: {len(candidates)}")

    # 3. Ограничение и перемешивание
    random.shuffle(candidates)
    batch = candidates[:MAX_POSTS_PER_RUN]

    sent = 0
    for item in batch:
        title, body = rewrite_with_pollinations(item["text"])
        if not title:
            continue

        img = download_image(item["image_url"], item["uid"].replace("/", "_")[:30])
        if not img:
            img = generate_image(title, item["uid"].replace("/", "_")[:30])

        send_telegram(f"**{title}**\n\n{body}", img)
        sent += 1
        logging.info(f"Отправлено: {title[:50]}")

    save_seen(seen)
    logging.info(f"Готово. Отправлено постов: {sent}")


if __name__ == "__main__":
    main()
