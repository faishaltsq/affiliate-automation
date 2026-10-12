import unittest
from unittest.mock import patch, MagicMock

try:
    from src.scraper.product_extractor import extract_product_from_html, extract_product_metadata
except ImportError:
    extract_product_from_html = None
    extract_product_metadata = None

SAMPLE_OG_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta property="og:title" content="Baseus Bowie M2+ ANC Wireless Earbuds" />
    <meta property="og:image" content="https://images.example.com/products/baseus-m2.jpg" />
    <meta property="og:description" content="Earbuds TWS dengan active noise cancelling 42dB dan baterai 30 jam." />
    <meta property="product:price:amount" content="349000" />
</head>
<body>
    <h1>Baseus Bowie M2+</h1>
</body>
</html>
"""

SAMPLE_TWITTER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="twitter:title" content="Keychron K2 V2 Wireless Keyboard" />
    <meta name="twitter:image" content="https://images.example.com/keychron-k2.png" />
    <meta name="description" content="Mechanical keyboard 75% hot swappable Bluetooth." />
</head>
</html>
"""

class TestProductExtractor(unittest.TestCase):
    def test_extract_og_metadata(self):
        self.assertIsNotNone(extract_product_from_html, "Module src.scraper.product_extractor not yet implemented")
        data = extract_product_from_html(SAMPLE_OG_HTML, "https://vt.tiktok.com/ZSsample/")
        self.assertEqual(data["name"], "Baseus Bowie M2+ ANC Wireless Earbuds")
        self.assertEqual(data["image_url"], "https://images.example.com/products/baseus-m2.jpg")
        self.assertIn("active noise cancelling", data["description"])
        self.assertEqual(data["affiliate_url"], "https://vt.tiktok.com/ZSsample/")

    def test_extract_twitter_fallback_metadata(self):
        self.assertIsNotNone(extract_product_from_html, "Module src.scraper.product_extractor not yet implemented")
        data = extract_product_from_html(SAMPLE_TWITTER_HTML, "https://tokopedia.link/sample")
        self.assertEqual(data["name"], "Keychron K2 V2 Wireless Keyboard")
        self.assertEqual(data["image_url"], "https://images.example.com/keychron-k2.png")
        self.assertIn("Mechanical keyboard", data["description"])

    def test_extract_missing_fields_graceful_defaults(self):
        self.assertIsNotNone(extract_product_from_html, "Module src.scraper.product_extractor not yet implemented")
        minimal_html = "<html><head><title>Gadget Store</title></head></html>"
        data = extract_product_from_html(minimal_html, "https://example.com/prod")
        self.assertEqual(data["name"], "Gadget Store")
        self.assertEqual(data["image_url"], "")
        self.assertEqual(data["affiliate_url"], "https://example.com/prod")

if __name__ == "__main__":
    unittest.main()

