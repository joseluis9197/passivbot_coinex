import asyncio
import traceback
import os
import json

from uuid import uuid4
from njit_funcs import calc_diff
from passivbot import Bot, logging
from procedures import print_async_exception, utc_ms, make_get_filepath
from pure_funcs import determine_pos_side_ccxt, floatify, calc_hash, ts_to_date_utc

import ccxt.async_support as ccxt


from procedures import load_ccxt_version

# Verification of the ccxt version for compatibility
ccxt_version_req = load_ccxt_version()
assert (
    ccxt.__version__ == ccxt_version_req
), f"Currently ccxt {ccxt.__version__} is installed. Please pip reinstall requirements.txt or install ccxt v{ccxt_version_req} manually"

class CoinExBot(Bot):
    def __init__(self, config: dict):
        self.exchange = "coinex"
        self.market_type = config.get("market_type", "linear_perpetual")
        self.inverse = config.get("inverse", False)
        self.max_n_orders_per_batch = 7
        self.max_n_cancellations_per_batch = 10

        super().__init__(config)
      # Configure the connection to the CoinEx API.
        self.cc = ccxt.coinex({
            "apiKey": self.key,
            "secret": self.secret,
        })

    def init_market_type(self):
        supported_symbols = [market["symbol"] for market in self.cc.fetch_markets()]
        if self.symbol not in supported_symbols:
            raise Exception(f"Unsupported symbol {self.symbol} for CoinEx")

async def fetch_market_info_from_cache(self):
    fname = make_get_filepath("caches/coinex_market_info.json")
    info = None
    try:
        if os.path.exists(fname):
            info = json.load(open(fname))
            logging.info("loaded CoinEx market info from cache")
        # Check if the information is outdated (more than 24 hours).
        if info is None or utc_ms() - info["dump_ts"] > 1000 * 60 * 60 * 24:
           # Update the information from the exchange.
            info = {"info": await self.cc.fetch_markets(), "dump_ts": utc_ms()}
            json.dump(info, open(fname, "w"), indent=4)
            logging.info("dumped CoinEx market info to cache")
    except Exception as e:
        logging.error(f"failed to load CoinEx market info from cache {e}")
        traceback.print_exc()
        if info is None:
            info = {"info": await self.cc.fetch_markets(), "dump_ts": utc_ms()}
            json.dump(info, open(fname, "w"), indent=4)
            logging.info("dumped CoinEx market info to cache")
    return info["info"]
async def _init(self):
    info = await self.fetch_market_info_from_cache()
    found = False
    for elm in info:
        if elm["symbol"] == self.symbol:  
            found = True
            self.max_leverage = elm.get("limits", {}).get("leverage", {}).get("max", None)
            self.coin = elm.get("base", None)  
            self.quote = elm.get("quote", None) 
            self.price_step = self.config["price_step"] = elm.get("precision", {}).get("price", None)
            self.qty_step = self.config["qty_step"] = elm.get("precision", {}).get("amount", None)
            self.min_qty = self.config["min_qty"] = elm.get("limits", {}).get("amount", {}).get("min", None)
            self.min_cost = self.config["min_cost"] = elm.get("limits", {}).get("cost", {}).get("min", 0.1)  # Asumiendo un valor predeterminado
            self.margin_coin = self.quote
            break
    if not found:
        raise Exception(f"Unsupported symbol {self.symbol} for CoinEx")

    await super()._init()  

async def fetch_ticker(self, symbol=None):
    ticker = None
    try:
        symbol_to_fetch = self.symbol if symbol is None else symbol
        ticker = await self.cc.fetch_ticker(symbol_to_fetch)
        return ticker
    except Exception as e:
        logging.error(f"error fetching ticker for {symbol_to_fetch}: {e}")
        return None

async def init_order_book(self):
    return await self.fetch_ticker()

async def fetch_open_orders(self) -> [dict]:
    open_orders = None
    try:
        open_orders = await self.cc.fetch_open_orders(symbol=self.symbol, limit=50)
        
        return [
            {
                "order_id": o["id"],  # Unique identifier of the order
                "custom_id": o.get("info", {}).get("clientOrderId", ""),  # Custom customer ID, if available
                "symbol": o["symbol"],  # Symbol of the market
                "price": o.get("price"),  # Price of the order
                "qty": o["amount"],  # Quantity of the order
                "type": o["type"],  # Type of the order, for example, limit or market
                "side": o["side"],  # Side of the order, buy or sell
                "timestamp": o["timestamp"],  # Time mark of the order
            }
            for o in open_orders
        ]
    except Exception as e:
        logging.error(f"error fetching open orders {e}")
        traceback.print_exc()
        return []

async def get_server_time(self):
    server_time = None
    try:
        # Try to get the time from the server directly if CCXT supports it for CoinEx
        server_time = await self.cc.fetch_time()
        return server_time
    except Exception as e:
        logging.error(f"error fetching server time: {e}")
        traceback.print_exc()
        return None

async def fetch_position(self) -> dict:
    position = {
        "long": {"size": 0.0, "price": 0.0, "liquidation_price": 0.0},
        "short": {"size": 0.0, "price": 0.0, "liquidation_price": 0.0},
        "wallet_balance": 0.0,
        "equity": 0.0,
    }
    try:
        # Suponiendo que fetch_balance puede ser utilizado directamente como en el ejemplo.
        balance = await self.cc.fetch_balance()
        # La implementación específica para obtener detalles de la posición puede variar.
        # A continuación se muestra un enfoque genérico; ajusta según la API de CoinEx.
        positions = await self.cc.fetch_positions()
        for p in positions:
            if p["symbol"] == self.symbol:
                side = "long" if p["size"] > 0 else "short"
                position[side] = {
                    "size": abs(p["size"]),
                    "price": p.get("entry_price", 0.0),
                    "liquidation_price": p.get("liquidation_price", 0.0),
                }
        position["wallet_balance"] = balance.get(self.quote, {}).get("total", 0.0)
        # Asume que 'equity' necesita ser calculado o extraído específicamente; ajusta según sea necesario.
        return position
    except Exception as e:
        logging.error(f"Error fetching position or balance: {e}")
        traceback.print_exc()
        return None

