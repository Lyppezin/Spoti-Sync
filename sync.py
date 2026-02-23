import os
import re
import json
import time
import logging
from datetime import datetime

from mutagen import File
from tqdm import tqdm
from rich.logging import RichHandler
from difflib import SequenceMatcher

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException


# ---------------- CONFIG ----------------

with open("config.json", "r") as f:
    CONFIG = json.load(f)

CLIENT_ID = CONFIG["client_id"]
CLIENT_SECRET = CONFIG["client_secret"]
REDIRECT_URI = CONFIG["redirect_uri"]
MUSIC_FOLDER = CONFIG["music_folder"]
BASE_PLAYLIST_NAME = CONFIG["playlist_name"]
MIN_SIMILARITY = CONFIG["min_similarity"]

TOKEN_FILE = "spotify_token.json"
CACHE_FILE = "cache.json"
LOG_FILE = "offline_sync.log"


# ---------------- LOG ----------------

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        RichHandler(),
        logging.FileHandler(LOG_FILE)
    ]
)


# ---------------- AUTH ----------------

def get_spotify():
    auth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope="playlist-modify-public playlist-modify-private",
        cache_path=TOKEN_FILE,
        open_browser=False
    )

    if not auth.get_cached_token():
        url = auth.get_authorize_url()
        print("\nAbra no navegador:\n")
        print(url)
        response = input("\nCole a URL final aqui:\n")
        code = auth.parse_response_code(response)
        auth.get_access_token(code)

    return spotipy.Spotify(auth_manager=auth)


# ---------------- CACHE ----------------

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


# ---------------- UTILS ----------------

def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def normalize_artist(artist):
    if not artist:
        return ""

    separators = [",", "&", "feat", "Feat", "ft.", "Ft."]
    for sep in separators:
        if sep in artist:
            artist = artist.split(sep)[0]

    return artist.strip()


def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ---------------- SCAN ----------------

def scan_folder(folder):
    tracks = []

    for root, _, files in os.walk(folder):
        for file in files:
            path = os.path.join(root, file)
            filename = os.path.splitext(file)[0]

            title = None
            artist = None

            # tentativa metadata
            try:
                audio = File(path, easy=True)
                if audio:
                    title = audio.get("title", [None])[0]
                    artist = audio.get("artist", [None])[0]
            except Exception:
                pass

            # fallback nome do arquivo
            if not title or not artist:
                if " - " in filename:
                    parts = filename.split(" - ", 1)
                    title = parts[0]
                    artist = parts[1]
                else:
                    title = filename
                    artist = ""

            if title:
                artist = normalize_artist(artist)
                tracks.append((clean(title), clean(artist)))

    return list(set(tracks))


# ---------------- SEARCH ----------------

def search_track(sp, title, artist, cache):
    key = f"{title}::{artist}"

    if key in cache:
        return cache[key]

    if artist:
        query = f'track:"{title}" artist:"{artist}"'
    else:
        query = f'track:"{title}"'

    for _ in range(3):
        try:
            results = sp.search(q=query, type="track", limit=5, market="BR")
            items = results.get("tracks", {}).get("items", [])

            best_uri = None
            best_score = 0

            for item in items:
                score = similarity(title, item["name"])
                if score > best_score:
                    best_score = score
                    best_uri = item["uri"]

            if best_score >= MIN_SIMILARITY:
                cache[key] = best_uri
                return best_uri

            break

        except SpotifyException as e:
            if e.http_status == 429:
                wait = int(e.headers.get("Retry-After", 3))
                time.sleep(wait)
            else:
                time.sleep(2)

    cache[key] = None
    return None


# ---------------- PLAYLIST ----------------

def create_playlist(sp, uris):
    today = datetime.now().strftime("%d.%m.%Y")
    name = f"{BASE_PLAYLIST_NAME} {today} - {len(uris)} músicas"

    user_id = sp.current_user()["id"]
    playlist = sp.user_playlist_create(user_id, name)

    for i in range(0, len(uris), 100):
        sp.playlist_add_items(playlist["id"], uris[i:i+100])

    logging.info(f"Playlist criada: {name}")


# ---------------- MAIN ----------------

def main():
    sp = get_spotify()
    cache = load_cache()

    logging.info("Escaneando músicas...")
    tracks = scan_folder(MUSIC_FOLDER)

    if not tracks:
        logging.warning("Nenhuma música encontrada.")
        return

    logging.info("Buscando no Spotify...")

    uris = []

    for title, artist in tqdm(tracks):
        uri = search_track(sp, title, artist, cache)
        if uri:
            uris.append(uri)

    uris = list(set(uris))

    if not uris:
        logging.warning("Nenhuma música encontrada no Spotify.")
        return

    create_playlist(sp, uris)
    save_cache(cache)


if __name__ == "__main__":
    main()