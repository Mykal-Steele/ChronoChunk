"""Unit tests for the parts of the music player that need no voice connection."""
from src.music_manager import spotify_query_from_page


def test_spotify_page_gives_title_and_artist():
    page = ('<meta property="og:title" content="Never Gonna Give You Up"/>'
            '<meta property="og:description" content="Rick Astley · Whenever You Need Somebody · Song · 1987"/>')
    assert spotify_query_from_page(page) == "Never Gonna Give You Up Rick Astley"


def test_spotify_page_text_is_unescaped():
    page = '<meta property="og:title" content="Rock &amp; Roll"/><meta property="og:description" content="AC/DC · Song"/>'
    assert spotify_query_from_page(page) == "Rock & Roll AC/DC"


def test_spotify_page_without_a_title_gives_nothing():
    assert spotify_query_from_page("<html><title>Spotify – Web Player</title></html>") == ""
