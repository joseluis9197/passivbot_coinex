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
            self.min_cost = self.config["min_cost"] = elm.get("limits", {}).get("cost", {}).get("min", 0.1)  # Assuming a default value
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
        balance = await self.cc.fetch_balance()
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
        return position
    except Exception as e:
        logging.error(f"Error fetching position or balance: {e}")
        traceback.print_exc()
        return None
async def execute_orders(self, orders: [dict]) -> [dict]:
    return await self.execute_multiple(
        orders, self.execute_order, "creations", self.max_n_orders_per_batch
    )
async def execute_order(self, order: dict) -> dict:
    executed = None
    try:
        executed = await self.cc.create_limit_order(
            market=order["symbol"] if "symbol" in order else self.symbol,
            side=order["side"],
            amount=str(order["qty"]),
            price=str(order["price"]),
            type="limit", # Asumiendo que es una orden límite
            effect_type=order.get("effect_type", 1),
            option=order.get("option", 0),
            client_id=order.get("client_id", ""),
        )
        return {
            "order_id": executed["data"]["order_id"],
            "status": "created",
            "price": executed["data"]["price"],
            "amount": executed["data"]["amount"],
            "side": executed["data"]["side"],
        }
    except Exception as e:
        logging.error(f"Error executing order {order}: {e}")
        return {"error": str(e)}

async def execute_multiple(self, orders: [dict], func, type_: str, max_n_executions: int):
        if not orders:
            return []
        executions = []
        for order in sorted(orders, key=lambda x: calc_diff(x["price"], self.price))[
            :max_n_executions
        ]:
            execution = None
            try:
                execution = asyncio.create_task(func(order))
                executions.append((order, execution))
            except Exception as e:
                logging.error(f"error executing {type_} {order} {e}")
                print_async_exception(execution)
                traceback.print_exc()
        results = []
        for execution in executions:
            result = None
            try:
                result = await execution[1]
                results.append(result)
            except Exception as e:
                logging.error(f"error executing {type_} {execution} {e}")
                print_async_exception(result)
                traceback.print_exc()
        return results

async def execute_cancellations(self, orders: [dict]) -> [dict]:
    if not orders:
        return []
    order_ids = "p".join([str(order["order_id"]) for order in orders])
    market = self.symbol 
    
    try:
        response = await self.cc.v1_order_cancel_batch(market=market, order_ids=order_ids)
        results = []
        for order_info in response["data"]:
            if order_info["code"] == 0:  
                results.append({
                    "order_id": order_info["order"]["order_id"],
                    "status": "cancelled",
                })
            else:
                pass
        return results
    except Exception as e:
        logging.error(f"Error executing cancellations: {e}")
        return []
async def execute_cancellation(self, order: dict) -> dict:
    executed = None
    try:
        response = await self.cc.request(
            'POST',
            '/perpetual/v1/order/cancel',
            data={
                "market": self.symbol,
                "order_id": order["order_id"]
            }
        )
        return {
            "symbol": self.symbol,
            "side": order["side"],  
            "order_id": order["order_id"],
            "position_side": order.get("position_side", ""),
            "qty": order.get("qty", ""),
            "price": order.get("price", ""),
        }
    except Exception as e:
        logging.error(f"Error cancelling order {order['order_id']}: {e}")
        return {}
async def fetch_account(self):
    try:
        balance_info = await self.cc.fetch_balance(params={"type": "account"})
        return balance_info
    except Exception as e:
        logging.error(f"Error fetching account balance: {e}")
        return {}

async def fetch_ticks(self, symbol, do_print=True):
    try:
        ticker = await self.cc.fetch_ticker(symbol)
        if do_print:
            print(ticker)
        return ticker
    except Exception as e:
        logging.error(f"Error fetching ticker for {symbol}: {e}")
        return {}
async def fetch_ohlcvs(self, symbol: str, interval="1day", limit=10):
    try:
        type_map = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1hour",
            "4h": "4hour",
            "6h": "6hour",
            "1d": "1day",
            "1w": "1week",
        }
        coinex_interval = type_map.get(interval, "1day")  
        response = await self.cc.request(
            method="GET",
            url="/market/kline",
            params={
                "market": symbol,
                "type": coinex_interval,
                "limit": limit
            }
        )
        
        if response["code"] == 0:
            return response["data"]
        else:
            logging.error(f"Error fetching OHLCV: {response['message']}")
            return []
    except Exception as e:
        logging.error(f"Error fetching OHLCV for {symbol} with interval {interval}: {e}")
        return []
async def transfer_from_derivatives_to_spot(self, coin: str, amount: float):
    try:
        response = await self.cc.request(
            "POST",
            "/contract/balance/transfer",
            data={
                "access_id": "tu_access_id",
                "tonce": str(int(time.time() * 1000)),
                "transfer_side": "out",  # "out" para de Futuros a Spot
                "coin_type": coin,
                "amount": str(amount)
            }
        )
        if response["code"] == 0:
            print("Transferencia exitosa.")
            return response["data"]
        else:
            print(f"Error in the transfer: {response['message']}")
            return None
    except Exception as e:
        logging.error(f"Error transferring from derivatives to spot: {e}")
        return None
async def get_all_income(self, asset: str, start_time: int, end_time: int, business='trade', page=1, limit=100):
    try:
        response = await self.cc.request(
            "GET",
            "/account/balance/history",
            params={
                "access_id": "tu_access_id",
                "asset": asset,
                "business": business,
                "start_time": start_time,
                "end_time": end_time,
                "page": page,
                "limit": limit,
                "tonce": str(int(time.time() * 1000)),
            }
        )
        if response["code"] == 0:
            print("Successful consultation.")
            return response["data"]
        else:
            print(f"Error en la consulta: {response['message']}")
            return None
    except Exception as e:
        logging.error(f"Error fetching user operation history: {e}")
        return None
async def fetch_income(self, asset: str, start_time: int = None, end_time: int = None):
    income = []
    try:
        params = {
            "access_id": "tu_access_id",
            "asset": asset,
            "business": "trade", 
            "start_time": start_time,
            "end_time": end_time,
            "page": 1,
            "limit": 100,  
            "tonce": str(int(time.time() * 1000)),
        }
        response = await self.cc.request("GET", "/account/balance/history", params=params)
        if response["code"] == 0:
            for entry in response["data"]["data"]:
                income.append({
                    "timestamp": entry["time"],
                    "asset": entry["asset"],
                    "change": entry["change"],
                    "balance": entry["balance"],
                    "type": entry["business"],
                })
            return income
        else:
            logging.error(f"Error fetching income: {response['message']}")
            return []
    except Exception as e:
        logging.error(f"Exception fetching income: {e}")
        return []
async def fetch_latest_fills(self):
    fetched = None
    try:
        fetched = await self.cc.request("market/user_deals", {"market": self.symbol})
        fills = [
            {
                "order_id": elm["id"],
                "symbol": self.symbol,  
                "custom_id": elm.get("client_id", None), 
                "price": elm["price"],
                "qty": elm["amount"],
                "type": elm.get("type"),  
                "reduce_only": None,  
                "side": "sell" if elm["type"] == 1 else "buy", 
                "position_side": None,  
                "timestamp": elm["time"],
            }
            for elm in fetched["data"]
            if elm["amount"] != 0.0  
        ]
        return sorted(fills, key=lambda x: x["timestamp"])
    except Exception as e:
        logging.error(f"error fetching latest fills {e}")
        print(fetched)  
        traceback.print_exc()

async def fetch_fills(
            self,
            market: str,
            limit: int = 200,
            from_id: int = None,
            start_time: int = None,
            end_time: int = None,
        ):
        params = {
            "access_id": self.api_key,
            "market": market,
            "limit": limit,
            **({"start_time": start_time} if start_time else {}),
            **({"end_time": end_time} if end_time else {}),
        }
        
        url = self.base_url + "market/user_deals"
        
        async with self.session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                return data['data'] if 'data' in data else []
            else:
                return []
async def init_exchange_config(self):
        try:
            res = await self.cc.request('perpetual/v1/position/leverage', {
                "market": self.symbol,
                "leverage": self.leverage,
            }, method="POST")
            logging.info(f"Leverage set for {self.symbol} to {self.leverage}: {res}")
        except Exception as e:
            logging.error(f"Error setting leverage for {self.symbol}: {e}")
