import pytest
from src.clipper.story_analyzer import natural_sort_paths, select_story_footages

def test_natural_sort_paths():
    files = ["jual emas 1.mp4", "jual emas 10.mp4", "jual emas 11.mp4", "jual emas 2.mp4"]
    sorted_files = natural_sort_paths(files)
    assert sorted_files == ["jual emas 1.mp4", "jual emas 2.mp4", "jual emas 10.mp4", "jual emas 11.mp4"]

def test_select_story_footages_filters_unrelated_subjects():
    transcripts_by_file = {
        "jual emas 1.mp4": "Halo saya beli anting-anting kecil disini.",
        "jual emas 2.mp4": "Aku masih muda bang, mau tanya harga doang.",
        "jual emas 10.mp4": "Ibu saya bantu cek emasnya. Ini surat emasnya ternyata bermasalah dan kadarnya palsu.",
        "jual emas 11.mp4": "Jadi sekali lagi di Jual Emas kita terima emas apapun kondisinya tanpa potongan.",
    }
    title = "JUAN - OWNERS JUAL EMAS INDONESIA (Nangkep Penjual Emas Palsu - Part 2)"
    selected = select_story_footages(title, transcripts_by_file)
    assert "jual emas 10.mp4" in selected
    assert "jual emas 11.mp4" in selected
    assert "jual emas 1.mp4" not in selected
    assert "jual emas 2.mp4" not in selected
