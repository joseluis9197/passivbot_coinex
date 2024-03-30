import asyncio
import json
import os
from uuid import uuid4
from procedures import load_ccxt_version, print_async_exception, make_get_filepath, utc_ms
import ccxt.async_support as ccxt

ccxt_version_req = load_ccxt_version()
assert ccxt.__version__ == ccxt_version_req, f"ccxt version mismatch, expected {ccxt_version_req}, got {ccxt.__version__}"

class CoinExBot:
    def __init__(self, config: dict):
        self.exchange = "coinex"
        self.market_type = config.get("market_type", "spot")
        self.cc = getattr(ccxt, "coinex")({
            "apiKey": config["apiKey"],
            "secret": config["secret"],
        })
        self.symbol = config["symbol"]
        self.config = config

    async def fetch_market_info(self):
        fname = make_get_filepath("caches/coinex_market_info.json")
        if os.path.exists(fname):
            with open(fname) as f:
                info = json.load(f)
            if utc_ms() - info["dump_ts"] < 1000 * 60 * 60 * 24:
                return info["info"]
        markets = await self.cc.load_markets()
        info = {"info": markets, "dump_ts": utc_ms()}
        with open(fname, "w") as f:
            json.dump(info, f)
        return markets

    async def fetch_open_orders(self):
        return await self.cc.fetch_open_orders(symbol=self.symbol)

    async def create_order(self, order_type, side, amount, price=None):
        if order_type == "limit":
            return await self.cc.create_limit_order(self.symbol, side, amount, price)
        elif order_type == "market":
            return await self.cc.create_market_order(self.symbol, side, amount)

    async def cancel_order(self, order_id):
        return await self.cc.cancel_order(order_id, symbol=self.symbol)

    async def fetch_balance(self):
        return await self.cc.fetch_balance()

    async def fetch_ticker(self):
        return await self.cc.fetch_ticker(symbol=self.symbol)

    async def main(self):
        await self.cc.load_markets()
        market_info = await self.fetch_market_info()
        print(market_info[self.symbol])
        open_orders = await self.fetch_open_orders()
        print(open_orders)
        balance = await self.fetch_balance()
        print(balance)
        ticker = await self.fetch_ticker()
        print(ticker)

if __name__ == "__main__":
    config = {
        "apiKey": "your_api_key",
        "secret": "your_api_secret",
        "symbol": "BTC/USDT"
    }
    bot = CoinExBot(config)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot.main())
