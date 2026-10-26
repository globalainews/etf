import os
import unittest

import tdx_price_update as tdx


class TdxPricePathTests(unittest.TestCase):
    def test_new_shanghai_etf_prefixes_use_sh_day_files(self):
        for code in ("520500", "526000", "530000", "551000"):
            self.assertEqual(
                tdx.code_to_day_path(code),
                os.path.join(tdx.TDX_BASE, "sh", "lday", f"sh{code}.day"),
            )

    def test_existing_shanghai_and_shenzhen_rules_remain(self):
        self.assertTrue(tdx.code_to_day_path("589720").endswith("sh589720.day"))
        self.assertTrue(tdx.code_to_day_path("159915").endswith("sz159915.day"))


if __name__ == "__main__":
    unittest.main()
